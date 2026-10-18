"""/api/v1 导入端点（P3 真实化，契约 p3-imports-analysis.md §1）+ 共享考试列表（§1.4）。

- preview（multipart/form-data）：真实解析上传的 .xlsx——homeroom 按行政班
  年级走 H 解析器全科+总分；teaching 走 T 解析器并只保留任教学科列。
  零业务写入（仅 import_batch 台账行）：规范化 rows + 范围 + 双侧成员
  快照全部 JSON 存 scope_json，content_digest 存各文件 sha256 逗号串。
- confirm（{token, revise, identity_confirmations}）：token 单次消费（R4，
  复用 shared._load_preview_batch——shared 的加载函数可直接 import 复用，
  未复制逻辑）；范围/成员漂移 → 409 零写入；整批健康检查（F05）：任一
  文件解析失败/类型未知 → 整批 409 列出失败文件，绝不部分入库；同
  exam_name 不同 exam_date（批内或与库内）→ 整批 409（v2.1 边界裁决）；
  身份解析（F06/v2.2-G06）：本学年 alias 命中直接接续；历史学年 alias
  命中与未证实覆盖文件日期的 NULL 学年命中只出 identity_candidates，须
  identity_confirmations 逐项显式确认（伪造 person → 422）或
  identity_new_aliases 显式新建（跨学年同号异名也归候选，可消歧；历
  史学生记录不动），同名不消歧、**本学年**同号异名整文件 409 拒绝；
  NULL 学年登记仅在自身有效期明确覆盖目标文件日期
  时按已知身份命中。成员有效期（F07）取 max(学年 start_date, 文件日期)，
  绝不取今天；一切接续既有人路径补登本学年 alias 并补建目标班学籍/
  成员行（G06：不再只给候选确认分支补学籍）。写入分两阶段——先只读预检
  （健康检查/日期冲突/文件内与库内同学号不同名/自然键值冲突），无冲突
  才单事务落库（E05 原子：任一步失败全量回滚、batch 保持 pending 可重试）。
- GET /shared/exams：该域该学年当前班级范围已导入考试（§1.4）。

兼容路径：P1 契约 §1.5 的 JSON preview（files 为 {filename,
content_digest} 描述、无文件内容）仍被接受，等价于"无可解析文件"的
空导入（items 恒空、confirm imported=0），仅用于 R4 token 生命周期
语义回归；P3 前端一律走 multipart。
"""

import hashlib
import json
import secrets
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel
from sqlalchemy import or_, update
from sqlalchemy.orm import Session

from app.api import _queries as q
from app.api import current_teacher_id, domain_endpoint
from app.api.schemas import (
    ImportConfirmExam,
    ImportIdentityCandidate,
    ImportsConfirmResponse,
    ImportsPreviewItem,
    ImportsPreviewResponse,
    ImportsPreviewRequest,
    ImportNewItem,
    SharedExamEntry,
    SharedExamsResponse,
)
from app.api.shared import _load_preview_batch
from app.core.context import VALID_MODES, WorkspaceContext, resolve_workspace_context
from app.core.errors import (
    DomainError,
    InvalidScopeParam,
    LinkVersionConflict,
)
from app.db.models import get_db
from app.db.workspace_models import (
    AcademicYear,
    AdministrativeClass,
    Enrollment,
    ImportBatch,
    ScoreFact,
    TeachingClass,
    TeachingClassMember,
    WorkspaceClassAverage,
    WsStudentAlias,
    WsStudentIdentity,
)
from app.ingest import ws_parse

router = APIRouter(tags=["imports"])


# ────────────────────────────── 作用域解析（preview / confirm / shared-exams 共用） ──────────────────────────────


def _form_int(value, name: str) -> Optional[int]:
    if value is None or value == "":
        return None
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise InvalidScopeParam(f"{name} must be an integer", details={"param": name})


def _resolve_import_scope(
    db: Session,
    teacher_id: int,
    mode: str,
    academic_year_id: Optional[int],
    class_id: Optional[int],
    teaching_class_id: Optional[int],
    subject: Optional[str],
):
    """按契约 §1.1 解析导入范围，返回 (ctx, target_teaching_class_id)。

    - homeroom：class_id 缺省教师绑定班；显式时校验存在（404）并经
      resolve_workspace_context 验证绑定（非绑定班 404）。
    - teaching：teaching_class_id 缺省该学科唯一教学班，多班必须显式（422）；
      显式时经 build_teaching_params 校验学年/学科一致。"""
    if mode == "homeroom":
        params: dict = {"academic_year_id": academic_year_id}
        if class_id is not None:
            q.check_admin_class_exists(db, class_id)
            params["class_id"] = class_id
        ctx = resolve_workspace_context(db, teacher_id, "homeroom", params)
        return ctx, None

    if teaching_class_id is None:
        ay = q.resolve_year(db, academic_year_id)
        resolved_subject = q.teaching_subject_for_year(db, ay.id, subject)
        ids = q.teaching_class_ids_for_subject(db, ay.id, resolved_subject)
        if len(ids) > 1:
            # 缺省只允许"唯一教学班"自动命中，多班必须显式（契约 §1.1）
            raise InvalidScopeParam(
                "teaching_class_id is required when multiple teaching classes exist",
                details={"param": "teaching_class_id", "teaching_class_ids": ids},
            )
        teaching_class_id = ids[0] if ids else None
    params, _subject, class_ids = q.build_teaching_params(
        db, academic_year_id, teaching_class_id, subject
    )
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    return ctx, (class_ids[0] if class_ids else None)


def _resolve_skeleton_scope(db: Session, teacher_id: int, mode: str):
    """P1 骨架（JSON 兼容路径）的作用域解析：homeroom 默认绑定班；
    teaching 为"全部所教班"并集口径（P3 前旧语义，保留给兼容路径与
    其 confirm 重解析，保证快照/重解析口径一致不误报漂移）。"""
    if mode == "homeroom":
        ctx = resolve_workspace_context(db, teacher_id, "homeroom", {})
    else:
        params, _subject, class_ids = q.build_teaching_params(db, None, None, None)
        ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    return ctx


def _scope_snapshot(
    ctx: WorkspaceContext, target_teaching_class_id: Optional[int], transport: str
) -> dict:
    """导入作用域快照（confirm 漂移比对的事实源；成员为双侧当期集合）。
    transport 记录签发路径：multipart 走 P3 scope 解析，json 兼容路径
    confirm 重解析时须回到骨架并集口径，否则会误报 class_ids 漂移。"""
    return {
        "kind": "import_preview",
        "transport": transport,
        "mode": ctx.mode,
        "academic_year_id": ctx.academic_year_id,
        "class_ids": list(ctx.class_ids),
        "subject": ctx.subject,
        "link_id": ctx.link_id,
        "link_version": ctx.link_version,
        "member_person_ids": list(ctx.member_person_ids),
        "target_teaching_class_id": target_teaching_class_id,
    }


# ────────────────────────────── 身份解析（两域同规则，契约 §1.1/§1.2 v2.1：F06/F07；v2.2：G06） ──────────────────────────────


def _current_alias_row(
    db: Session,
    domain: str,
    alias_value: str,
    academic_year_id: int,
    file_date: Optional[date],
):
    """本学年 alias 命中行（契约 §1.1 v2.2/G06：NULL 学年不再是任意目标
    学年的通行证）。命中条件 = 明确标注目标学年，或该行 academic_year_id
    为 NULL 且其自身有效期明确覆盖目标文件日期。NULL 行"明确覆盖"的
    证据口径：valid_from 非空且 <= 文件日期（起点有显式登记才可证明
    "事发时已在册"），且 valid_to 为空（尚无离册证据）或 >= 文件日期；
    文件日期未知时无法证实任何覆盖 → NULL 行不命中，进入候选。"""
    base = db.query(WsStudentAlias).filter(
        WsStudentAlias.data_domain == domain,
        WsStudentAlias.alias_value == alias_value,
    )
    row = (
        base.filter(WsStudentAlias.academic_year_id == academic_year_id)
        .order_by(WsStudentAlias.id.asc())
        .first()
    )
    if row is not None:
        return row
    if file_date is None:
        return None
    return (
        base.filter(
            WsStudentAlias.academic_year_id.is_(None),
            WsStudentAlias.valid_from.isnot(None),
            WsStudentAlias.valid_from <= file_date,
            or_(
                WsStudentAlias.valid_to.is_(None),
                WsStudentAlias.valid_to >= file_date,
            ),
        )
        .order_by(WsStudentAlias.id.asc())
        .first()
    )


def _historical_alias_rows(db: Session, domain: str, alias_value: str, academic_year_id: int):
    """非本学年命中的历史证据行（近学年优先，NULL 学年殿后）。

    v2.2/G06：除明确标注其他学年的登记外，未标学年（NULL）且未按有效期
    证实覆盖文件日期的登记同样并入候选（basis='unmarked_alias'）——未知
    学年并不是已证明的本学年身份。这些行是"跨学年同号"的候选证据，但
    同名新生/学号回收/跨届重号无法区分，因此绝不据此自动接续（F06），
    只生成候选清单等待显式确认。"""
    return (
        db.query(WsStudentAlias)
        .filter(
            WsStudentAlias.data_domain == domain,
            WsStudentAlias.alias_value == alias_value,
            or_(
                WsStudentAlias.academic_year_id.is_(None),
                WsStudentAlias.academic_year_id != academic_year_id,
            ),
        )
        .order_by(WsStudentAlias.academic_year_id.desc(), WsStudentAlias.id.asc())
        .all()
    )


def _resolve_file_students(
    db: Session,
    domain: str,
    academic_year_id: int,
    students: list,
    file_date: Optional[date],
):
    """一个文件的学生身份只读解析（契约 §1.1 v2.1/v2.2）。

    返回 (alias→identity_id 映射, 新学号列表, 库内撞号冲突文本列表,
    历史身份候选列表)。file_date 为该文件考试日期（未知传 None），
    仅用于判定 NULL 学年 alias 的有效期覆盖（v2.2/G06）。
    - 本学年 alias 命中（含 NULL 学年但有效期明确覆盖文件日期）→ 直接
      解析为该 identity（known，可自动接续）；
    - 历史学年 alias 命中 / 未证实覆盖的 NULL 学年命中 → 仅生成
      identity_candidates（person_id/学年/依据），不进 known 也不进
      new_students，confirm 须逐项显式确认（接续 identity_confirmations
      或显式新建 identity_new_aliases，二者皆可消歧，绝不自动认定）；
    - 无匹配 → new_students；
    - 撞号 = **本学年在册命中**（_current_alias_row）但 display_name 与
      文件姓名不同（H 撞号防呆：preview 计 warning，confirm 整文件 409
      拒绝）。跨学年/未标学年的历史同号即便姓名不同也只算候选——那是
      学号被新一届学生复用的正常形态，由用户在候选清单里显式选择
      接续还是新建，历史学生记录不动。"""
    identity_by_alias: dict = {}
    new_students: list = []
    conflicts: list = []
    candidates: list = []
    ids_needed = []
    matched_rows: dict = {}
    current_matched: set = set()
    for stu in students:
        alias = stu["alias"]
        row = _current_alias_row(db, domain, alias, academic_year_id, file_date)
        if row is not None:
            identity_by_alias[alias] = row.identity_id
            matched_rows[alias] = row
            current_matched.add(alias)
            ids_needed.append(row.identity_id)
            continue
        hist = _historical_alias_rows(db, domain, alias, academic_year_id)
        if hist:
            matched_rows[alias] = hist[0]  # 主候选：撞号检测的比对对象
            ids_needed.append(hist[0].identity_id)
            seen_persons: set = set()
            for h in hist:
                if h.identity_id in seen_persons:
                    continue  # 同一人多年登记只出一个候选（近学年优先）
                seen_persons.add(h.identity_id)
                ids_needed.append(h.identity_id)
                candidates.append(
                    {
                        "alias": alias,
                        "name": None,
                        "person_id": h.identity_id,
                        "academic_year_id": h.academic_year_id,
                        "academic_year_name": None,
                        # v2.2/G06：NULL 学年登记单独标 basis，与明确标注
                        # 其他学年的历史登记可区分（前端可提示"未标学年"）
                        "basis": (
                            "unmarked_alias"
                            if h.academic_year_id is None
                            else "history_alias"
                        ),
                    }
                )
        else:
            new_students.append({"name": stu.get("name"), "alias": alias})

    names = q.names_for(db, sorted(set(ids_needed)))
    year_ids = {c["academic_year_id"] for c in candidates}
    year_names: dict = {}
    if year_ids:
        year_names = {
            y.id: y.name
            for y in db.query(AcademicYear).filter(AcademicYear.id.in_(year_ids)).all()
        }
    for c in candidates:
        c["name"] = names.get(c["person_id"])
        c["academic_year_name"] = year_names.get(c["academic_year_id"])
    for stu in students:
        if stu["alias"] not in current_matched:
            continue  # 历史命中（跨学年/未标学年）不构成撞号：留在候选清单待显式消歧
        row = matched_rows[stu["alias"]]
        display = names.get(row.identity_id)
        if display and stu.get("name") and display != stu["name"]:
            conflicts.append(
                f"同学号不同姓名：{stu['alias']}（库内「{display}」≠ 文件「{stu['name']}」）"
            )
    return identity_by_alias, new_students, conflicts, candidates


def _exam_date_conflicts(db: Session, domain: str, academic_year_id: int, file_exams: list) -> list:
    """考试日期一致性（契约 §1.1 v2.1 边界裁决）。

    file_exams 为 [(exam_name, iso_date_str)]（忽略无名/无日期项）。
    返回冲突明细：批内同 exam_name 不同 exam_date，或与库内已有事实
    同 exam_name 不同 exam_date——preview 计 warnings，confirm 整批 409，
    绝不静默合并、不当作修订。"""
    conflicts: list = []
    seen: dict = {}
    for name, iso in file_exams:
        if not name or not iso:
            continue
        if name in seen and seen[name] != iso:
            conflicts.append(
                {
                    "scope": "batch",
                    "exam_name": name,
                    "dates": sorted({seen[name], iso}),
                }
            )
        seen.setdefault(name, iso)
    for name, iso in seen.items():
        rows = (
            db.query(ScoreFact.exam_date)
            .filter(
                ScoreFact.data_domain == domain,
                ScoreFact.academic_year_id == academic_year_id,
                ScoreFact.exam_name == name,
                ScoreFact.exam_date.isnot(None),
            )
            .distinct()
            .all()
        )
        differing = sorted({r[0].isoformat() for r in rows} - {iso})
        if differing:
            conflicts.append(
                {
                    "scope": "database",
                    "exam_name": name,
                    "exam_date": iso,
                    "existing_dates": differing,
                }
            )
    return conflicts


# ────────────────────────────── preview（multipart / P1 JSON 兼容） ──────────────────────────────


async def imports_preview(request: Request, db: Session = Depends(get_db)):
    """multipart/form-data 为契约 §1.1 主路径；application/json 保留 P1
    骨架形态（无文件内容 → 空导入）供 token 生命周期回归。外壳必须 async
    才能 await request.form()；DomainError 统一由内层 sync 函数上的
    domain_endpoint 转契约 JSON（外壳自身不得抛 DomainError，否则绕过
    统一错误处理变 500）。"""
    content_type = (request.headers.get("content-type") or "").split(";")[0].strip().lower()
    if content_type == "application/json":
        # 解析/校验都放内层 sync 函数，异常才能被 domain_endpoint 捕获
        return _preview_json_compat(await request.body(), db)
    form = await request.form()
    return _preview_multipart(form, db)


@domain_endpoint
def _preview_json_compat(raw_body: bytes, db: Session) -> ImportsPreviewResponse:
    """P1 契约 §1.5 JSON 形态兼容：files 仅为 {filename, content_digest}
    描述、无文件内容可解析，等价"空导入"（items 恒空、confirm imported=0）。"""
    try:
        body = json.loads(raw_body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise InvalidScopeParam("request body must be valid JSON") from exc
    try:
        req = ImportsPreviewRequest.model_validate(body)
    except Exception as exc:
        raise InvalidScopeParam(
            "request body must match {mode, files:[{filename, content_digest}]}"
        ) from exc
    if req.mode not in VALID_MODES:
        raise InvalidScopeParam(
            "mode must be 'homeroom' or 'teaching'", details={"param": "mode"}
        )
    teacher_id = current_teacher_id(db)
    ctx = _resolve_skeleton_scope(db, teacher_id, req.mode)
    snapshot = _scope_snapshot(ctx, None, transport="json")
    snapshot["files"] = [
        {"filename": f.filename, "kind": "unknown", "parsed_ok": False,
         "message": "P1 JSON 兼容路径：无文件内容可解析", "rows": []}
        for f in req.files
    ]
    token = secrets.token_hex(16)
    expires_at = datetime.utcnow() + timedelta(minutes=q.PREVIEW_TTL_MINUTES)
    db.add(
        ImportBatch(
            token=token,
            data_domain=ctx.data_domain,
            scope_json=json.dumps(snapshot, ensure_ascii=False),
            content_digest="|".join(f.content_digest or "" for f in req.files) or None,
            status="pending",
            expires_at=expires_at,
        )
    )
    db.commit()
    return ImportsPreviewResponse(
        token=token, expires_at=expires_at.isoformat(), mode=req.mode, items=[]
    )


@domain_endpoint
def _preview_multipart(form, db: Session) -> ImportsPreviewResponse:
    mode = form.get("mode")
    if mode not in VALID_MODES:
        raise InvalidScopeParam(
            "mode must be 'homeroom' or 'teaching'", details={"param": "mode"}
        )
    uploads = [f for f in form.getlist("files") if getattr(f, "filename", None)]
    if not uploads:
        raise InvalidScopeParam(
            "files must contain at least one .xlsx", details={"param": "files"}
        )
    academic_year_id = _form_int(form.get("academic_year_id"), "academic_year_id")
    class_id = _form_int(form.get("class_id"), "class_id")
    teaching_class_id = _form_int(form.get("teaching_class_id"), "teaching_class_id")
    subject = form.get("subject") or None
    exam_name = form.get("exam_name") or None
    exam_date_raw = form.get("exam_date") or None

    teacher_id = current_teacher_id(db)

    ctx, target_tc_id = _resolve_import_scope(
        db, teacher_id, mode, academic_year_id, class_id, teaching_class_id, subject
    )
    admin_class = None
    tc_label = None
    if mode == "homeroom":
        admin_class = db.get(AdministrativeClass, ctx.class_ids[0])
    elif target_tc_id is not None:
        tc = db.get(TeachingClass, target_tc_id)
        tc_label = tc.label if tc else None

    items = []
    snapshot_files = []
    digests = []
    for upload in uploads:
        filename = upload.filename
        # UploadFile.file 是同步 SpooledTemporaryFile；本函数运行于线程池
        data = upload.file.read()
        digests.append(hashlib.sha256(data).hexdigest())
        parsed = None
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            try:
                if mode == "homeroom":
                    parsed = ws_parse.parse_homeroom_file(
                        tmp_path, filename, admin_class.grade, admin_class.class_num,
                        exam_name=exam_name, exam_date=exam_date_raw,
                    )
                else:
                    parsed = ws_parse.parse_teaching_file(
                        tmp_path, filename, ctx.subject, tc_label,
                        exam_name=exam_name, exam_date=exam_date_raw,
                    )
            except ValueError as exc:
                raise InvalidScopeParam(str(exc), details={"param": "exam_date"}) from exc
            except Exception as exc:  # 解析器异常一律降级为 unknown 条目，不中断整批
                parsed = ws_parse.ParsedImportFile(filename=filename)
                parsed.message = f"解析失败：{exc}"
        finally:
            tmp_path.unlink(missing_ok=True)

        warnings = list(parsed.warnings)
        new_students: list = []
        candidates: list = []
        if parsed.parsed_ok and parsed.kind == "student_scores":
            identity_by_alias, new_students, conflicts, candidates = _resolve_file_students(
                db, ctx.data_domain, ctx.academic_year_id, parsed.students, parsed.exam_date
            )
            # 库内撞号：preview 仅警告（与文件内撞号同一防呆语义，确认时拒绝）
            warnings.extend(conflicts)
            # known 只计本学年命中（含 NULL 学年但有效期明确覆盖文件日期）；
            # 其余历史/未标学年命中进 identity_candidates（F06/v2.2-G06），
            # 绝不自动接续，须在 confirm 时逐项 identity_confirmations
            known = len(identity_by_alias)
            if candidates:
                warnings.append(
                    f"历史学年/未标学年同号命中 {len(candidates)} 人，未自动接续："
                    "确认时须通过 identity_confirmations 逐项确认，否则整批拒绝"
                )
        else:
            known = 0

        items.append(
            ImportsPreviewItem(
                filename=filename,
                kind=parsed.kind,
                parsed_ok=parsed.parsed_ok,
                message=parsed.message,
                exam_name=parsed.exam_name,
                exam_date=parsed.exam_date.isoformat() if parsed.exam_date else None,
                subject=parsed.subject,
                class_label=parsed.class_label,
                row_count=(
                    len(parsed.rows)
                    if parsed.kind == "student_scores"
                    else len(parsed.class_averages)
                ),
                known_students=known,
                new_students=[ImportNewItem(**stu) for stu in new_students],
                identity_candidates=[
                    ImportIdentityCandidate(**c) for c in candidates
                ],
                warnings=warnings,
            )
        )
        snapshot_files.append(
            {
                "filename": filename,
                "kind": parsed.kind,
                "parsed_ok": parsed.parsed_ok,
                "exam_name": parsed.exam_name,
                "exam_date": parsed.exam_date.isoformat() if parsed.exam_date else None,
                "grade": parsed.grade,
                "rows": parsed.rows,
                "class_averages": parsed.class_averages,
                "students": parsed.students,
            }
        )

    # 考试日期一致性（契约 §1.1 v2.1）：批内或与库内同 exam_name 不同
    # exam_date → preview 计 warnings（confirm 时整批 409，不静默合并）。
    # 只查将入库的成绩明细与班级均分文件，与 confirm 的校验口径一致
    date_conflicts = _exam_date_conflicts(
        db,
        ctx.data_domain,
        ctx.academic_year_id,
        [
            (f.get("exam_name"), f.get("exam_date"))
            for f in snapshot_files
            if f.get("kind") in {"student_scores", "class_averages"}
        ],
    )
    if date_conflicts:
        for c in date_conflicts:
            if c["scope"] == "batch":
                msg = (
                    f"考试日期冲突（批内）：{c['exam_name']} 在多个文件中日期不同"
                    f"（{'、'.join(c['dates'])}），请在预览中明确选择已有考试或另建考试"
                )
            else:
                msg = (
                    f"考试日期冲突（库内）：{c['exam_name']} 库内已有日期"
                    f" {'、'.join(c['existing_dates'])}，本次为 {c['exam_date']}，请在预览中明确选择已有考试或另建考试"
                )
            for item in items:
                if item.exam_name == c["exam_name"]:
                    item.warnings.append(msg)

    snapshot = _scope_snapshot(ctx, target_tc_id, transport="multipart")
    for item, f in zip(items, snapshot_files):
        if f.get("parsed_ok"):
            f["exam_resolution"] = events.candidates(db, ctx, f.get("exam_name"), f.get("grade") or ctx.grade, f.get("exam_date"))
            item.exam_resolution = f["exam_resolution"]
    dates_by_name = {}
    for f in snapshot_files:
        dates_by_name.setdefault(f.get("exam_name"), set()).add(f.get("exam_date"))
    for item, f in zip(items, snapshot_files):
        if len(dates_by_name.get(f.get("exam_name"), set())) > 1 and f.get("exam_resolution"):
            f["batch_date_choice"] = True
            item.exam_resolution = {**f["exam_resolution"], "requires_choice": True, "selected": None,
                                    "message": "批内同名考试日期不同，请明确每份文件的考试归属"}
    snapshot["files"] = snapshot_files
    token = secrets.token_hex(16)
    expires_at = datetime.utcnow() + timedelta(minutes=q.PREVIEW_TTL_MINUTES)
    db.add(
        ImportBatch(
            token=token,
            data_domain=ctx.data_domain,
            scope_json=json.dumps(snapshot, ensure_ascii=False),
            content_digest=",".join(digests),
            status="pending",
            expires_at=expires_at,
        )
    )
    db.commit()
    return ImportsPreviewResponse(
        token=token, expires_at=expires_at.isoformat(), mode=mode, items=items
    )


router.add_api_route("/imports/preview", imports_preview, methods=["POST"])


# ────────────────────────────── confirm（契约 §1.2：单次消费 + 漂移校验 + 单事务写入） ──────────────────────────────


from app.api import exam_events as events


class ImportsConfirmRequest(BaseModel):
    token: str
    exam_choices: Dict[str, int | str] = {}
    revise: bool = False
    # F06：历史学年身份候选的显式确认 {alias_value: person_id}；
    # 存在历史候选而未逐项确认 → 409，确认的 person 须确实持有该 alias
    # 的历史登记（伪造/错配 → 422）
    identity_confirmations: Dict[str, int] = {}
    # F06 完备性补全（集成者接线）：显式声明"按新学生建档"的候选 alias——
    # 用户对历史候选的另一种合法决定。完备性 = 每个候选 alias 必须出现在
    # confirmations 或本列表；出现在本列表的不接续，按新学生建档（新身份
    # 独立入库，历史学生记录不变）。声明必须是预览候选清单里出现过的
    # alias（伪造/未在预览出现 → 422）；本学年同号异名的撞号拒绝不受
    # 本声明影响。
    identity_new_aliases: List[str] = []


def _natural_fact(db: Session, domain: str, academic_year_id: int,
                  exam_name: str, identity_id: int, subject, total_type, event_id=None, exam_date=None):
    """按自然键 (domain, 学年, 考试, 人, 学科口径, 总分口径) 查库内行。"""
    query = db.query(ScoreFact).filter(
        ScoreFact.data_domain == domain, ScoreFact.academic_year_id == academic_year_id,
        ScoreFact.identity_id == identity_id, ScoreFact.subject_key == (subject or ""),
        ScoreFact.total_key == (total_type or ""))
    legacy = (ScoreFact.exam_event_id.is_(None) & (ScoreFact.exam_name == exam_name))
    if exam_date is not None:
        legacy = legacy & (ScoreFact.exam_date == exam_date)
    query = query.filter(or_(ScoreFact.exam_event_id.in_(events.event_ids(db, event_id)), legacy)) if event_id else query.filter(legacy)
    return query.order_by(ScoreFact.id.desc()).first()



_FACT_VALUE_FIELDS = (
    "score",
    "grade_score",
    "grade_percentile",
    "xueji_rank",
    "grade_rank",
)


def _same_value(fact: ScoreFact, row: dict) -> bool:
    """幂等比较覆盖重点关注所需的排名/百分位，避免同分数重导时漏更新。"""
    return all(getattr(fact, field) == row.get(field) for field in _FACT_VALUE_FIELDS)


def _same_row_value(left: dict, right: dict) -> bool:
    return all(left.get(field) == right.get(field) for field in _FACT_VALUE_FIELDS)


def _valid_member_row(db: Session, teaching_class_id: int, identity_id: int, as_of: date):
    """教学班当前有效成员行（有效期覆盖 as_of）。"""
    return (
        db.query(TeachingClassMember)
        .filter(
            TeachingClassMember.teaching_class_id == teaching_class_id,
            TeachingClassMember.identity_id == identity_id,
            TeachingClassMember.valid_from <= as_of,
            or_(
                TeachingClassMember.valid_to.is_(None),
                TeachingClassMember.valid_to >= as_of,
            ),
        )
        .first()
    )


@router.post("/imports/confirm", response_model=ImportsConfirmResponse)
@domain_endpoint
def imports_confirm(req: ImportsConfirmRequest, db: Session = Depends(get_db)):
    batch = _load_preview_batch(db, req.token)
    snapshot = json.loads(batch.scope_json or "{}")
    if snapshot.get("kind") != "import_preview":
        raise LinkVersionConflict("token is not an import preview")
    try:
        claimed = db.execute(update(ImportBatch).where(ImportBatch.id == batch.id,
            ImportBatch.status == "pending").values(status="applying"))
        if claimed.rowcount != 1:
            raise LinkVersionConflict("导入凭据已消费，请重新预览")
        db.expire_all()
        return _apply_import_confirm(req, db, batch, snapshot)
    except Exception:
        db.rollback()
        raise


def _apply_import_confirm(req, db, batch, snapshot):
    teacher_id = current_teacher_id(db)
    mode = snapshot.get("mode")
    if mode not in VALID_MODES:
        raise LinkVersionConflict("invalid snapshot mode")
    try:
        if snapshot.get("transport") == "json":
            # P1 兼容快照：按骨架口径重解析（teaching 并集），与签发一致
            ctx = _resolve_skeleton_scope(db, teacher_id, mode)
            target_tc_id = snapshot.get("target_teaching_class_id")
        else:
            ctx, target_tc_id = _resolve_import_scope(
                db,
                teacher_id,
                mode,
                snapshot.get("academic_year_id"),
                snapshot.get("class_ids")[0]
                if mode == "homeroom" and snapshot.get("class_ids")
                else None,
                snapshot.get("target_teaching_class_id"),
                snapshot.get("subject"),
            )
    except DomainError as exc:
        if isinstance(exc, LinkVersionConflict):
            raise
        # 范围已无法解析（学年/班级/学科配置变化）→ 统一 link_version_conflict
        raise LinkVersionConflict(
            f"preview scope no longer valid: {exc.message}", details=exc.details
        ) from exc

    # 漂移校验（契约 §1.2 校验 2/3）：班级/学年/学科/成员集合任一变化 → 409
    drift = {}
    if ctx.academic_year_id != snapshot.get("academic_year_id"):
        drift["academic_year_id"] = [snapshot.get("academic_year_id"), ctx.academic_year_id]
    if list(ctx.class_ids) != list(snapshot.get("class_ids") or []):
        drift["class_ids"] = [snapshot.get("class_ids"), list(ctx.class_ids)]
    if ctx.subject != snapshot.get("subject"):
        drift["subject"] = [snapshot.get("subject"), ctx.subject]
    if ctx.link_id != snapshot.get("link_id"):
        drift["link_id"] = [snapshot.get("link_id"), ctx.link_id]
    if ctx.link_version != snapshot.get("link_version"):
        drift["link_version"] = [snapshot.get("link_version"), ctx.link_version]
    if list(ctx.member_person_ids) != list(snapshot.get("member_person_ids") or []):
        drift["member_person_ids"] = [
            snapshot.get("member_person_ids"), list(ctx.member_person_ids),
        ]
    if drift:
        raise LinkVersionConflict("preview scope changed", details={"drift": drift})

    # 整批健康检查（契约 §1.2 校验 3 / F05）：任一文件解析失败或类型未知
    # → 整批 409（列出失败文件），绝不静默过滤失败文件导入其余。
    # P1 JSON 兼容快照的文件本就是"无内容可解析"的 unknown 占位（R4 语义），
    # 不适用本检查。
    if snapshot.get("transport") == "multipart":
        failed_files = [
            {
                "filename": f.get("filename"),
                "kind": f.get("kind"),
                "message": f.get("message"),
            }
            for f in snapshot.get("files") or []
            if not f.get("parsed_ok")
            or f.get("kind") not in ("student_scores", "class_averages")
        ]
        if failed_files:
            raise LinkVersionConflict(
                "批次中存在解析失败/未知类型文件，整批拒绝（零写入）；"
                "请移除失败文件后重新预览",
                details={"failed_files": failed_files},
            )

    files = [f for f in snapshot.get("files") or [] if f.get("parsed_ok") and f.get("kind") == "student_scores"]
    average_files = [
        f for f in snapshot.get("files") or []
        if f.get("parsed_ok") and f.get("kind") == "class_averages"
    ]

    # Resolve read-only first; event creation is deferred until all preflight checks pass.
    event_choices = {}
    for f in [*files, *average_files]:
        preview = f.get("exam_resolution")
        choice = req.exam_choices.get(f["filename"])
        if f.get("batch_date_choice") and choice is None:
            raise LinkVersionConflict("批内同名考试日期不同，请明确每份文件的考试归属")
        eid = events.resolve(db, ctx, f["exam_name"], f.get("grade") or ctx.grade,
                             f.get("exam_date"), preview, choice)
        f["resolved_event_id"] = eid
        event_choices[f["filename"]] = eid
    # Old tokens retain their original date-conflict protection.
    if any("exam_resolution" not in f for f in [*files, *average_files]) and _exam_date_conflicts(
            db, ctx.data_domain, ctx.academic_year_id, [(f.get("exam_name"), f.get("exam_date")) for f in [*files, *average_files]]):
        raise LinkVersionConflict("同 exam_name 存在不同 exam_date，请重新预览并明确选择考试")

    # ── 阶段 A：只读预检 + 写入计划（任何冲突在写库前拒绝，零写入）──
    # 撞号检查以 rows 逐行姓名为准：学生聚合清单可能已合并同号行（撞号
    # 对只留在解析 warnings），rows 才是完整事实
    alias_names: dict = {}
    for f in files:
        for row in f.get("rows") or []:
            name = row.get("name")
            prev = alias_names.get(row["alias"])
            if prev is not None and name is not None and prev != name:
                raise LinkVersionConflict(
                    "同学号不同姓名，整批拒绝（防两人成绩混档）",
                    details={
                        "conflicts": [
                            {
                                "alias": row["alias"],
                                "existing_name": prev,
                                "new_name": name,
                            }
                        ]
                    },
                )
            if prev is None:
                alias_names[row["alias"]] = name

    identity_by_alias: dict = {}
    new_students: list = []
    candidate_registry: dict = {}  # alias → 历史身份候选清单（F06）
    for f in files:
        file_date = (
            date.fromisoformat(f["exam_date"]) if f.get("exam_date") else None
        )
        for stu in f.get("students") or []:
            if (
                stu["alias"] in identity_by_alias
                or stu["alias"] in candidate_registry
                or any(n["alias"] == stu["alias"] for n in new_students)
            ):
                continue
            file_map, file_new, conflicts, file_cands = _resolve_file_students(
                db, ctx.data_domain, ctx.academic_year_id, [stu], file_date
            )
            if conflicts:
                # 库内撞号：整文件 409（错误 detail 列出冲突对）。
                # 历史命中撞号（跨届同号不同名）同样在此拒绝，不得接续
                raise LinkVersionConflict(
                    "同学号不同姓名，整文件拒绝（不自动改名合并）",
                    details={"conflicts": [c for c in conflicts]},
                )
            identity_by_alias.update(file_map)
            new_students.extend(file_new)
            if file_cands:
                candidate_registry[stu["alias"]] = file_cands

    # 身份确认完备性（契约 §1.2 校验 5 / F06）：存在历史候选但
    # identity_confirmations 未逐项确认 → 409 附完整候选清单，零写入
    confirmations = req.identity_confirmations or {}
    declared_new = {a for a in (req.identity_new_aliases or []) if a}
    unconfirmed = [
        a for a in candidate_registry if a not in confirmations and a not in declared_new
    ]
    if unconfirmed:
        raise LinkVersionConflict(
            "存在历史学年身份候选，未经逐项确认拒绝接续"
            "（请在 confirm 请求携带 identity_confirmations: {alias: person_id}）",
            details={
                "identity_candidates": [
                    c for a in sorted(unconfirmed) for c in candidate_registry[a]
                ]
            },
        )
    # 确认的 person_id 必须确实持有该 alias 的历史登记（伪造/错配 → 422）。
    # v2.2/G06：未标学年（NULL）的登记同样是合法持有证据——它正是
    # unmarked_alias 候选的来源；仍排除"本学年已标记"行（那类命中本就
    # 走 known 路径，不构成候选）
    file_alias_names: dict = {}
    for f in files:
        for stu in f.get("students") or []:
            file_alias_names.setdefault(stu["alias"], stu.get("name"))
    # declared_new 只接受预览候选清单里出现过的 alias：伪造/未在预览出现
    # 的声明无效（422），绝不静默忽略，也不允许借声明绕过本学年撞号
    invalid_new = sorted(a for a in declared_new if a not in candidate_registry)
    if invalid_new:
        raise InvalidScopeParam(
            "identity_new_aliases 含未出现在候选清单的学号（历史同号候选不存在），声明无效",
            details={"invalid_aliases": invalid_new},
        )
    for alias in candidate_registry:
        if alias in declared_new:
            # 用户显式选择按新学生建档：不接续历史 identity，补进新建档
            # 队列（解析阶段该 alias 走的是 candidates 而非 new_students，
            # 不补进来写入阶段会 KeyError——对照 teaching_platform.identities
            # 的显式 append）
            new_students.append(
                {"name": file_alias_names.get(alias), "alias": alias}
            )
            continue
        person_id = confirmations[alias]
        holds = (
            db.query(WsStudentAlias)
            .filter(
                WsStudentAlias.data_domain == ctx.data_domain,
                WsStudentAlias.alias_value == alias,
                WsStudentAlias.identity_id == person_id,
                or_(
                    WsStudentAlias.academic_year_id.is_(None),
                    WsStudentAlias.academic_year_id != ctx.academic_year_id,
                ),
            )
            .first()
        )
        if holds is None:
            raise InvalidScopeParam(
                "identity_confirmations 校验失败：该 person 未持有此 alias 的历史登记",
                details={
                    "alias": alias,
                    "person_id": person_id,
                    "identity_candidates": candidate_registry[alias],
                },
            )
        # 显式确认接续：本学年成绩直接落到历史 identity 上（F06）
        identity_by_alias[alias] = person_id

    ay = db.get(AcademicYear, ctx.academic_year_id)
    inserts: list = []
    skips = 0
    revises: list = []
    value_conflicts: list = []
    in_batch_conflicts: list = []
    exams_seen: dict = {}
    # 批内自然键去重：多文件重复提交同键行时同值合并、异值计冲突。
    # 学生维度按**解析后的 identity_id** 去重（尚未建档的新学号才按 alias）：
    # 同一学生同批用新旧两个学号出现时是一行事实，按原始 alias 去重会让
    # 两行都进 inserts 撞 uq_score_fact_natural_key（4C）。
    batch_rows: dict = {}
    for f in files:
        exam_name = f.get("exam_name")
        exam_date = date.fromisoformat(f["exam_date"]) if f.get("exam_date") else None
        exams_seen.setdefault(exam_name, exam_date)
        for row in f.get("rows") or []:
            ident_id = identity_by_alias.get(row["alias"])
            person_key = ("known", ident_id) if ident_id is not None else ("alias", row["alias"])
            key = (
                (exam_name, f.get("resolved_event_id"), f.get("exam_date")),
                person_key,
                row.get("subject") or "",
                row.get("total_type") or "",
            )
            seen = batch_rows.get(key)
            if seen is not None:
                if _same_row_value(seen["row"], row):
                    seen.setdefault("aliases", {seen["row"]["alias"]}).add(row["alias"])
                    continue  # 批内同键同值：只处理一次（同一人多学号合并为一行）
                in_batch_conflicts.append(
                    {
                        "person": row.get("name") or row["alias"],
                        "aliases": sorted({seen["row"]["alias"], row["alias"]}),
                        "subject": row.get("total_type") or row.get("subject"),
                        "exam_name": exam_name,
                        "existing_score": seen["row"].get("score"),
                        "new_score": row.get("score"),
                    }
                )
                continue
            batch_rows[key] = {
                "row": row,
                "filename": f["filename"],
                "exam_name": exam_name,
                "exam_date": exam_date,
            }

    average_bindings: list = []
    average_inserts: list = []
    average_revises: list = []
    average_skips = 0
    average_rows: dict = {}
    for f in average_files:
        exam_name = f.get("exam_name")
        exam_date = date.fromisoformat(f["exam_date"]) if f.get("exam_date") else None
        exams_seen.setdefault(exam_name, exam_date)
        for row in f.get("class_averages") or []:
            key = (exam_name, f.get("resolved_event_id"), f.get("exam_date"), int(row["class_num"]))
            previous = average_rows.get(key)
            if previous is not None and previous["row"] != row:
                value_conflicts.append(
                    {
                        "kind": "class_averages",
                        "person": f"{row['class_num']}班",
                        "subject": "班级均分表",
                        "exam_name": exam_name,
                        "existing_score": None,
                        "new_score": None,
                    }
                )
                continue
            average_rows[key] = {
                "row": row,
                "filename": f["filename"],
                "exam_name": exam_name,
                "exam_date": exam_date,
                "grade": int(f["grade"]),
            }

    def _same_average_values(existing, row) -> bool:
        return (
            existing.class_type == row.get("class_type")
            and existing.teacher_name == row.get("teacher_name")
            and (existing.subject_averages or {}) == (row.get("subject_averages") or {})
            and (existing.total_averages or {}) == (row.get("total_averages") or {})
        )

    for plan in average_rows.values():
        row = plan["row"]
        # 查重按与库唯一约束 uq_workspace_class_average_natural_key
        # (data_domain, academic_year_id, event_or_name, grade, class_num)
        # 一致的自然键口径：事件路径按归并集 exam_event_id 集合定位，
        # legacy 路径按 (exam_event_id IS NULL, exam_name) 定位；不再用
        # exam_name/exam_date 过滤——改名/改日期时旧口径查不到既有行，
        # INSERT 撞唯一约束（4A）；归并集内两行同键命中时 one_or_none
        # 抛 MultipleResultsFound（4B）。集合内多行 = 归并的正常产物：
        # 同值幂等 skip、异值按修订流程统一更新或可读 409。
        base_query = db.query(WorkspaceClassAverage).filter(
            WorkspaceClassAverage.data_domain == ctx.data_domain,
            WorkspaceClassAverage.academic_year_id == ctx.academic_year_id,
            WorkspaceClassAverage.grade == plan["grade"],
            WorkspaceClassAverage.class_num == int(row["class_num"]),
        )
        eid = event_choices[plan["filename"]]
        if eid:
            matches = base_query.filter(
                WorkspaceClassAverage.exam_event_id.in_(events.event_ids(db, eid))
            ).order_by(WorkspaceClassAverage.id).all()
        else:
            matches = base_query.filter(
                WorkspaceClassAverage.exam_event_id.is_(None),
                WorkspaceClassAverage.exam_name == plan["exam_name"],
            ).order_by(WorkspaceClassAverage.id).all()
        has_event_bound = any(m.exam_event_id is not None for m in matches)
        for m in matches:
            average_bindings.append((m, plan, has_event_bound))
        if not matches:
            average_inserts.append(plan)
        elif all(_same_average_values(m, row) for m in matches):
            average_skips += 1
        elif req.revise:
            average_revises.extend((m, plan, has_event_bound) for m in matches)
        else:
            value_conflicts.append(
                {
                    "kind": "class_averages",
                    "person": f"{row['class_num']}班",
                    "subject": "班级均分表",
                    "exam_name": plan["exam_name"],
                    "existing_score": None,
                    "new_score": None,
                }
            )

    for plan in batch_rows.values():
        row = plan["row"]
        exam_name = plan["exam_name"]
        ident_id = identity_by_alias.get(row["alias"])
        if ident_id is None:
            # 新学号：库内必无同键行，直接排队插入
            inserts.append(plan)
            continue
        existing = _natural_fact(
            db, ctx.data_domain, ctx.academic_year_id, exam_name,
            ident_id, row.get("subject"), row.get("total_type"), event_choices[plan["filename"]], plan["exam_date"],
        )
        if existing is None:
            inserts.append(plan)
        elif _same_value(existing, row):
            skips += 1
        elif req.revise:
            revises.append((existing, row))
        else:
            value_conflicts.append(
                {
                    "person": row.get("name") or row["alias"],
                    "subject": row.get("total_type") or row.get("subject"),
                    "exam_name": exam_name,
                    "existing_score": existing.score,
                    "new_score": row.get("score"),
                }
            )
    if in_batch_conflicts:
        # 同一学生（同一解析后身份，可能是新旧两个学号）同批同键数值不同：
        # 批内自相矛盾无法用 revise 消解（没有"哪行是对的"的依据），
        # 整批 409 列出学号对与两值，零写入。
        raise LinkVersionConflict(
            "同一学生的多个学号同批出现且数值不同，请核对后重传",
            details={"conflicts": in_batch_conflicts},
        )
    if value_conflicts:
        # revise=false：不同值 → 整批 409 + conflicts 列表，零写入
        raise LinkVersionConflict(
            "同场同键导入数据不同（revise=false 拒绝覆写）",
            details={"conflicts": value_conflicts},
        )

    # ── 阶段 B：单事务写入（任一步失败全量回滚，batch 保持 pending）──
    students_created = 0
    members_synced = 0
    try:
        # F07 成员有效期：max(学年 start_date, 该生本批最早文件日期)；
        # 文件无日期 → 学年 start_date。绝不用"今天"冒充历史日期，
        # 否则考试时点成员门会把刚导入的历史成绩判成"当时不在班"。
        alias_first_date: dict = {}
        for plan in batch_rows.values():
            d = plan["exam_date"]
            if d is None:
                continue
            prev_d = alias_first_date.get(plan["row"]["alias"])
            if prev_d is None or d < prev_d:
                alias_first_date[plan["row"]["alias"]] = d

        def _member_valid_from(alias: str) -> date:
            first = alias_first_date.get(alias)
            return max(ay.start_date, first) if first is not None else ay.start_date

        for new in new_students:
            ident = WsStudentIdentity(
                data_domain=ctx.data_domain, display_name=new.get("name") or None
            )
            db.add(ident)
            db.flush()
            db.add(
                WsStudentAlias(
                    identity_id=ident.id,
                    alias_value=new["alias"],
                    data_domain=ctx.data_domain,
                    academic_year_id=ay.id,
                    alias_scope=str(ay.id),
                    source="import",
                    valid_from=_member_valid_from(new["alias"]),
                )
            )
            if mode == "homeroom":
                db.add(
                    Enrollment(
                        admin_class_id=ctx.class_ids[0],
                        identity_id=ident.id,
                        status="active",
                        valid_from=_member_valid_from(new["alias"]),
                    )
                )
            else:
                db.add(
                    TeachingClassMember(
                        teaching_class_id=target_tc_id,
                        identity_id=ident.id,
                        valid_from=_member_valid_from(new["alias"]),
                        source="import",
                    )
                )
                members_synced += 1
            identity_by_alias[new["alias"]] = ident.id
            students_created += 1

        # 历史学年命中：为本学年补登 alias（幂等：本学年已有则跳过）
        for f in files:
            for stu in f.get("students") or []:
                alias = stu["alias"]
                ident_id = identity_by_alias.get(alias)
                if ident_id is None:
                    continue
                exists = (
                    db.query(WsStudentAlias)
                    .filter(
                        WsStudentAlias.identity_id == ident_id,
                        WsStudentAlias.alias_value == alias,
                        WsStudentAlias.data_domain == ctx.data_domain,
                        WsStudentAlias.academic_year_id == ay.id,
                    )
                    .first()
                )
                if exists is None:
                    db.add(
                        WsStudentAlias(
                            identity_id=ident_id,
                            alias_value=alias,
                            data_domain=ctx.data_domain,
                            academic_year_id=ay.id,
                            alias_scope=str(ay.id),
                            source="import",
                        )
                    )

        # F07（v2.2/G06 扩面）：homeroom 模式下，本批所有"接续到既有人"
        # 的 alias（确认候选、NULL 学年直接命中等一切路径）都补建目标
        # 行政班学籍——只补 alias 不建 Enrollment 会造成"导入成功却看不
        # 到"，不存在"只补 alias 不补学籍"的例外。新建学生不在此列（上
        # 面建档时已随建 Enrollment）。幂等：该人已有此班任何学籍行则跳
        # 过（曾入班又离班的不静默重招）。
        if mode == "homeroom":
            new_aliases = {n["alias"] for n in new_students}
            for alias, ident_id in identity_by_alias.items():
                if alias in new_aliases:
                    continue
                exists = (
                    db.query(Enrollment)
                    .filter_by(
                        admin_class_id=ctx.class_ids[0], identity_id=ident_id
                    )
                    .first()
                )
                if exists is None:
                    db.add(
                        Enrollment(
                            admin_class_id=ctx.class_ids[0],
                            identity_id=ident_id,
                            status="active",
                            valid_from=_member_valid_from(alias),
                        )
                    )
                    members_synced += 1

        created_events = {}
        for f in [*files, *average_files]:
            eid = event_choices[f["filename"]]
            key = (f["exam_name"], f.get("grade") or ctx.grade, f.get("exam_date"))
            if eid is None:
                if key not in created_events:
                    created_events[key] = events.create_event(db, ay.id, key[1], key[0])
                eid = created_events[key]
            events.ensure_grade(db, eid, key[1])
            event_choices[f["filename"]] = eid
        for existing, plan, has_event_bound in average_bindings:
            # legacy 行补挂事件：仅当该自然键的归并集内还没有事件行占用
            # （事件键已被占时再挂会撞唯一约束——legacy 行与事件行是两个
            # 不同的库内自然键，归并后按集合语义并存）
            if existing.exam_event_id is None and not has_event_bound:
                existing.exam_event_id = event_choices[plan["filename"]]
        for plan in batch_rows.values():
            existing = _natural_fact(db, ctx.data_domain, ay.id, plan["exam_name"],
                identity_by_alias[plan["row"]["alias"]], plan["row"].get("subject"), plan["row"].get("total_type"),
                event_choices[plan["filename"]], plan["exam_date"])
            if existing is not None and existing.exam_event_id is None:
                existing.exam_event_id = event_choices[plan["filename"]]

        for plan in inserts:
            row = plan["row"]
            ident_id = identity_by_alias[row["alias"]]
            db.add(
                ScoreFact(
                    data_domain=ctx.data_domain,
                    academic_year_id=ay.id,
                    exam_name=plan["exam_name"],
                    exam_event_id=event_choices[plan["filename"]],
                    exam_date=plan["exam_date"],
                    class_ref_id=(
                        ctx.class_ids[0] if mode == "homeroom" else target_tc_id
                    ),
                    identity_id=ident_id,
                    subject=row.get("subject"),
                    total_type=row.get("total_type"),
                    score=row.get("score"),  # 缺考 NULL 保真，绝不转 0
                    grade_score=row.get("grade_score"),
                    grade_percentile=row.get("grade_percentile"),
                    xueji_rank=row.get("xueji_rank"),
                    grade_rank=row.get("grade_rank"),
                    source=f"import:{plan['filename']}",
                )
            )

        for existing, row in revises:
            existing.score = row.get("score")
            existing.grade_score = row.get("grade_score")
            existing.grade_percentile = row.get("grade_percentile")
            existing.xueji_rank = row.get("xueji_rank")
            existing.grade_rank = row.get("grade_rank")
            existing.data_revision = (existing.data_revision or 1) + 1

        for plan in average_inserts:
            row = plan["row"]
            db.add(
                WorkspaceClassAverage(
                    data_domain=ctx.data_domain,
                    academic_year_id=ay.id,
                    exam_name=plan["exam_name"],
                    exam_event_id=event_choices[plan["filename"]],
                    exam_date=plan["exam_date"],
                    grade=plan["grade"],
                    class_type=row.get("class_type"),
                    class_num=int(row["class_num"]),
                    teacher_name=row.get("teacher_name"),
                    subject_averages=row.get("subject_averages") or {},
                    total_averages=row.get("total_averages") or {},
                    source=f"import:{plan['filename']}",
                )
            )
        for existing, plan, has_event_bound in average_revises:
            row = plan["row"]
            if existing.exam_event_id is None and not has_event_bound:
                existing.exam_event_id = event_choices[plan["filename"]]
            existing.exam_date = plan["exam_date"]
            existing.class_type = row.get("class_type")
            existing.teacher_name = row.get("teacher_name")
            existing.subject_averages = row.get("subject_averages") or {}
            existing.total_averages = row.get("total_averages") or {}
            existing.source = f"import:{plan['filename']}"
            existing.data_revision = (existing.data_revision or 1) + 1

        # teaching 成员同步：文件里 known 学生（含确认接续的既有人）当前
        # 不在教学班 → 补成员行（F07：生效日同 max 规则，绝不用今天；
        # 幂等：唯一键 (teaching_class_id, identity_id, valid_from) 已存在
        # 则顺延一天新开一行）
        if mode == "teaching" and target_tc_id is not None:
            for f in files:
                for stu in f.get("students") or []:
                    ident_id = identity_by_alias.get(stu["alias"])
                    if ident_id is None or any(
                        n["alias"] == stu["alias"] for n in new_students
                    ):
                        continue
                    if _valid_member_row(db, target_tc_id, ident_id, ctx.as_of) is not None:
                        continue
                    valid_from = _member_valid_from(stu["alias"])
                    while (
                        db.query(TeachingClassMember)
                        .filter(
                            TeachingClassMember.teaching_class_id == target_tc_id,
                            TeachingClassMember.identity_id == ident_id,
                            TeachingClassMember.valid_from == valid_from,
                        )
                        .first()
                        is not None
                    ):
                        valid_from = valid_from + timedelta(days=1)
                    db.add(
                        TeachingClassMember(
                            teaching_class_id=target_tc_id,
                            identity_id=ident_id,
                            valid_from=valid_from,
                            source="import",
                        )
                    )
                    members_synced += 1

        batch.status = "confirmed"
        db.commit()
    except DomainError:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise

    return ImportsConfirmResponse(
        imported=len(inserts) + len(average_inserts),
        skipped=skips + average_skips,
        revised=len(revises) + len(average_revises),
        exams=[
            ImportConfirmExam(exam_name=name, exam_date=d.isoformat() if d else None)
            for name, d in exams_seen.items()
        ],
        students_created=students_created,
        members_synced=members_synced,
    )


# ────────────────────────────── GET /shared/exams（契约 §1.4） ──────────────────────────────


@router.get("/shared/exams", response_model=SharedExamsResponse)
@domain_endpoint
def list_shared_exams(
    mode: Optional[str] = None,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    subject: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """该域该学年、当前班级范围内已导入的考试，按考试日期降序。

    homeroom 的 subjects 为本班出现的学科并集（total_type 行不计入）；
    teaching 域事实只有任教学科，subjects 恒仅任教学科。"""
    if mode is None or mode not in VALID_MODES:
        raise InvalidScopeParam(
            "mode must be 'homeroom' or 'teaching'", details={"param": "mode"}
        )
    teacher_id = current_teacher_id(db)
    if mode == "teaching" and teaching_class_id is None:
        # 集成者修订（与 /api/v1/scores 教学模式对齐）：缺省 = 同学年同学科
        # 全部教学班并集（工作台"全部所教班"）；显式 teaching_class_id 仍走
        # _resolve_import_scope 的存在性/学年/学科校验。导入 preview/confirm
        # 的缺省语义不变（多班必须显式）——本端点只是读列表。
        params, _subject, _ids = q.build_teaching_params(db, academic_year_id, None, subject)
        ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    else:
        ctx, _target = _resolve_import_scope(
            db, teacher_id, mode, academic_year_id, class_id, teaching_class_id, subject
        )
    # F09（契约 §1.4 v2.1）：考试列表与其他读端点共用同一可读事实口径
    # （本域事实 + 经五条件门的对侧投影 + 冲突规则），不再自建第二套过滤。
    # teaching 模式由此能列出"仅 H 域有事实、但经反向投影可读"的考试。
    return SharedExamsResponse(
        exams=[SharedExamEntry(**item) for item in q.readable_exam_summaries(db, ctx)]
    )
