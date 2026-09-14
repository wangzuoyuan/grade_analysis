"""P6 AI 会话只读工具注册表 + 会话作用域快照（契约 docs/contracts/p6-ai-mcp.md）。

红线（违反即返工）：
- 全部只读：handler 只做 /api/v1 既有 service/查询函数的薄封装
  （analysis/students/scores/homework/imports + _queries 的统一可读事实
  口径），绝不另写查询逻辑绕过 §1.4.1 五条件投影门，绝不触发表写入。
- 域投影：每个工具声明 domains；execute/tools_for_domain 按
  snapshot.data_domain 裁剪——teaching 会话拿不到教学班对比工具，
  homeroom 会话拿不到单科对比工具，越权工具调用返回错误文本。
- 入参再校验：person_id/subject 等先过会话快照作用域，越界返回模型可读
  中文错误文本，绝不让异常栈穿透到模型。
- scope 快照：resolve_scope_snapshot 在服务端解析并冻结（mode/data_domain/
  academic_year_id/class_ids/subject/link_id/link_version/links/member_person_ids/
  as_of/cohort_size）；detect_scope_drift 每次发消息前重新解析比对，任一
  漂移 → 409 link_version_conflict（A02：撤销关联/换班后旧会话立即失效）。
  snapshot["request"] 只是请求原参数的留痕，供服务端重放解析，绝不作为
  权限依据。
- Q01（审核）工具范围收窄：teaching 会话按快照 class_ids 显式重建
  WorkspaceContext（_teaching_ctx_of），单班会话绝不落回"全部所教班"并集；
  路由函数不接受班参时改走同一 _queries 门函数复刻端点装配（不自写 SQL），
  聚合前限制班级，绝不取回后裁剪。
- Q02（审核）关联版本绑定：快照冻结所有可见关联
  links=[{link_id, version, status, linked_pairs, pairs}]（homeroom 侧当前
  link + teaching 侧每个可见教学班的 active link）；execute_session_tool
  每轮工具调用前重验快照，失效抛 ScopeDriftError 由流层中止本轮流（不继
  续用旧上下文执行）。
- V01（审核）配对集合指纹：linked_pairs 只是对数，删除两对再交叉
  新增两对（数量不变）不改变任何 version——快照必须同时冻结排序后的实际
  (homeroom_identity_id, teaching_identity_id) 配对集合（pairs）；对外投影
  与 409 drift 载荷一律剔除 pairs（身份映射明细不进接口层，契约 §1）。
"""

import json
from dataclasses import dataclass
from datetime import date
from typing import Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel

from app.api import _queries as q
from app.core.context import VALID_MODES, resolve_workspace_context
from app.core.errors import DomainError, InvalidScopeParam

# 工具层越界文本（契约 §2：越界返回"不在当前会话范围"文本，模型可读）
PERSON_OUT_OF_SCOPE = "该学生不在当前会话范围"
OBJECT_OUT_OF_SCOPE = "该学生/考试不在当前会话范围"


def _out_of_scope(detail: str) -> dict:
    return {"error": "resource_out_of_scope", "detail": detail}


# ────────────────────── 作用域快照：解析 / 冻结 / 漂移比对 ──────────────────────


def _frozen_links(db, ctx) -> list:
    """冻结当前上下文【所有可见关联】（Q02）：

    - homeroom 侧：当前行政班的 active link（ctx.link_id）；
    - teaching 侧：每个可见教学班的 active link（查询时点有效期内）。
    每条冻结 {link_id, version, status, linked_pairs, pairs}：version 覆盖
    取消/修改递增（cancel/share-scope 收紧都 +1）；V01 起同时冻结排序后的
    实际配对集合 pairs（[(h_id, t_id)]）——删除再交叉新增等数量不变的替换
    不改 version，只有配对集合指纹能拦住。撤销后重放解析拿不到该 link，
    links 集合必然变化 → 漂移。"""
    from app.api import _queries as _q
    from app.db.workspace_models import HomeroomTeachingLink, LinkedStudent

    frozen: list = []
    seen: set = set()

    def _freeze(link) -> None:
        if link is None or link.id in seen:
            return
        seen.add(link.id)
        # 排序后冻结实际身份对：指纹比对的唯一事实来源（V01）
        pairs = sorted(
            (int(h), int(t))
            for h, t in (
                db.query(
                    LinkedStudent.homeroom_identity_id,
                    LinkedStudent.teaching_identity_id,
                )
                .filter(LinkedStudent.link_id == link.id)
                .all()
            )
        )
        frozen.append(
            {
                "link_id": link.id,
                "version": int(link.version or 1),
                "status": link.status,
                "linked_pairs": len(pairs),
                # pairs 是快照内部冻结明细：对外投影（scope_public_view）与
                # 409 drift 载荷一律经 _links_public 剔除，身份映射名单绝不
                # 进接口层（契约 §1：明细按需查）
                "pairs": [[h, t] for h, t in pairs],
            }
        )

    if ctx.mode == "homeroom":
        if ctx.link_id is not None:
            _freeze(db.get(HomeroomTeachingLink, ctx.link_id))
    else:
        for tc_id in ctx.class_ids:
            _freeze(
                _q.active_link_for_teaching_class(
                    db, tc_id, ctx.academic_year_id, ctx.as_of
                )
            )
    return frozen


def _snapshot_of(db, ctx, *, mode, academic_year_id, class_id, teaching_class_id, subject) -> dict:
    """把 WorkspaceContext 冻结为不可变快照 dict（契约 §0 + §0.1 Q02）。"""
    return {
        "mode": ctx.mode,
        "data_domain": ctx.data_domain,
        "academic_year_id": ctx.academic_year_id,
        "class_ids": list(ctx.class_ids),
        "subject": ctx.subject,
        "link_id": ctx.link_id,
        "link_version": ctx.link_version,
        "links": _frozen_links(db, ctx),
        "member_person_ids": list(ctx.member_person_ids),
        "as_of": ctx.as_of.isoformat(),
        "cohort_size": len(ctx.member_person_ids),
        # 请求原参数留痕：仅供 detect_scope_drift 服务端重放解析（保证
        # "显式单班会话"重放时不被扩大为并集），不是权限来源
        "request": {
            "mode": mode,
            "academic_year_id": academic_year_id,
            "class_id": class_id,
            "teaching_class_id": teaching_class_id,
            "subject": subject,
        },
    }


def resolve_scope_snapshot(
    db,
    teacher_id: int,
    mode: str,
    academic_year_id: Optional[int] = None,
    class_id: Optional[int] = None,
    teaching_class_id: Optional[int] = None,
    subject: Optional[str] = None,
) -> dict:
    """服务端解析并冻结会话作用域快照（绝不信任客户端成员/学科）。

    - homeroom：subject 入参一律忽略（班主任视角学科由关联推导，客户端
      声明的学科不是权限凭证）；class_id 缺省取教师绑定班。
    - teaching：走 build_teaching_params（teaching_class_id 缺省 = 该学年
      同学科全部所教班并集；subject 缺省由教学班推导，多学科 422）。
    - 交叉参数误用（mode 与 class_id/teaching_class_id 不匹配）→ 422。
    """
    if mode not in VALID_MODES:
        raise InvalidScopeParam(
            "mode must be 'homeroom' or 'teaching'", details={"param": "mode"}
        )
    if mode == "homeroom":
        if teaching_class_id is not None:
            raise InvalidScopeParam(
                "teaching_class_id is only valid for teaching mode",
                details={"param": "teaching_class_id"},
            )
        params: dict = {"academic_year_id": academic_year_id}
        if class_id is not None:
            params["class_id"] = class_id
        # class_id 不存在/非绑定班由 resolve_workspace_context 统一 404，
        # 不向客户端区分回错误种类（避免泄露资源存在性）
        ctx = resolve_workspace_context(db, teacher_id, "homeroom", params)
    else:
        if class_id is not None:
            raise InvalidScopeParam(
                "class_id is only valid for homeroom mode",
                details={"param": "class_id"},
            )
        params, _subject, _ids = q.build_teaching_params(
            db, academic_year_id, teaching_class_id, subject
        )
        ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    return _snapshot_of(
        db,
        ctx,
        mode=mode,
        academic_year_id=academic_year_id,
        class_id=class_id,
        teaching_class_id=teaching_class_id,
        subject=subject,
    )


def _links_public(links) -> list:
    """links 的对外形状：剔除 pairs 身份映射明细（scope 投影与 409 drift
    载荷共用——契约 §1：接口层不泄露名单，明细按需经配对端点查）。"""
    return [
        {key: value for key, value in (item or {}).items() if key != "pairs"}
        for item in (links or [])
    ]


def scope_public_view(snapshot: dict) -> dict:
    """快照的对外投影：含 cohort_size 与冻结关联集合（仅 id/版本/状态/
    配对数，不含成员明单与 pairs 身份映射明细——契约 §1：明细按需查，
    接口层不泄露名单）。"""
    keys = (
        "mode",
        "data_domain",
        "academic_year_id",
        "class_ids",
        "subject",
        "link_id",
        "link_version",
        "links",
        "as_of",
        "cohort_size",
    )
    public = {key: snapshot.get(key) for key in keys}
    if public.get("links") is not None:
        public["links"] = _links_public(public["links"])
    return public


# 逐项比对的快照字段（as_of 是冻结时间本身，不参与漂移判定；
# links 结构复杂，在 detect_scope_drift 内按指纹单独比对）
_DRIFT_KEYS = (
    "mode",
    "academic_year_id",
    "class_ids",
    "subject",
    "link_id",
    "link_version",
    "member_person_ids",
)


def _links_fingerprint(links) -> list:
    """links 冻结集合的可排序指纹：取消/版本变/新增/移除/配对增删/同数
    配对替换（V01）任一变化都会改变指纹（Q02）。pairs 统一转 tuple，保证
    scope_json JSON 往返后的 list 与现场冻结的 list 归一可比。"""
    return sorted(
        (
            int(item.get("link_id") or 0),
            int(item.get("version") or 0),
            str(item.get("status") or ""),
            int(item.get("linked_pairs") or 0),
            tuple(tuple(pair) for pair in (item.get("pairs") or [])),
        )
        for item in (links or [])
    )


def detect_scope_drift(db, teacher_id: int, snapshot: dict) -> dict:
    """重放解析当前作用域并与快照逐项比对；返回漂移 dict（空 = 一致）。

    任何字段漂移（含 link 撤销/版本变/成员集合变化）都足以让旧会话作废
    （A02）；links 集合（Q02）按 _links_fingerprint 比对，覆盖取消/版本变/
    新增/移除与配对增删。重放解析本身失败（学年/班级配置被删等）视同
    全面漂移。调用方（messages 端点）把非空结果转 409 link_version_conflict。
    """
    request = snapshot.get("request") or {}
    try:
        current = resolve_scope_snapshot(
            db,
            teacher_id,
            request.get("mode") or snapshot.get("mode"),
            academic_year_id=request.get("academic_year_id"),
            class_id=request.get("class_id"),
            teaching_class_id=request.get("teaching_class_id"),
            subject=request.get("subject"),
        )
    except DomainError as exc:
        return {"scope_unresolvable": f"{exc.code}: {exc.message}"}
    drift: Dict[str, list] = {}
    for key in _DRIFT_KEYS:
        old, new = snapshot.get(key), current.get(key)
        if key in ("class_ids", "member_person_ids"):
            if sorted(old or []) != sorted(new or []):
                drift[key] = [old, new]
        elif old != new:
            drift[key] = [old, new]
    # Q02：可见关联集合 + 成员映射指纹（不改 version 的配对增删、V01 同数
    # 配对替换都逃不掉）；drift 载荷经 _links_public 剔除 pairs 明细
    old_links, new_links = snapshot.get("links"), current.get("links")
    if _links_fingerprint(old_links) != _links_fingerprint(new_links):
        drift["links"] = {
            "frozen": _links_public(old_links),
            "current": _links_public(new_links),
        }
    return drift


# ────────────────────── Q02：多轮工具执行期间的快照重验 ──────────────────────


class ScopeDriftError(RuntimeError):
    """多轮工具执行期间快照失效（Q02）：流前一次校验不足以保证随后工具
    调用安全，execute_session_tool 每轮执行前重验，失效即抛出，由流层
    发范围失效帧并中止本轮流（旧上下文绝不继续执行）。"""


def _assert_snapshot_fresh(db, snapshot: dict, teacher_id) -> None:
    """轻量重验：与 detect_scope_drift 同一重放比对语义；漂移即抛
    ScopeDriftError。teacher_id 缺省按单教师应用约定现场解析。"""
    if teacher_id is None:
        from app.api import current_teacher_id

        teacher_id = current_teacher_id(db)
    drift = detect_scope_drift(db, teacher_id, snapshot)
    if drift:
        raise ScopeDriftError(
            "会话范围已变化（关联撤销/版本变化/成员变化），本轮工具调用终止："
            + json.dumps(drift, ensure_ascii=False, default=str)
        )


# ────────────────────── service 薄封装辅助 ──────────────────────


def _teaching_ctx_of(db, snapshot: dict):
    """按快照重建 teaching WorkspaceContext（Q01）：显式钉住快照冻结的
    班级集合与学科，绝不缺省回 build_teaching_params 的"全部所教班"并集
    （单班会话被工具扩大成并集即 Q01 缺陷）。"""
    from app.api import current_teacher_id
    from app.core.context import resolve_workspace_context

    params = {
        "academic_year_id": snapshot["academic_year_id"],
        "teaching_class_id": list(snapshot["class_ids"]),
        "subject": snapshot["subject"],
    }
    return resolve_workspace_context(
        db, current_teacher_id(db), "teaching", params
    )


def _single_class_id(snapshot: dict):
    """单班会话返回该班 id，并集会话返回 None（路由函数缺省=并集，
    与快照重验后的并集语义一致；Q01 红线：单班绝不允许走缺省）。"""
    class_ids = snapshot.get("class_ids") or []
    return class_ids[0] if len(class_ids) == 1 else None


def _call_service(func, **kwargs):
    """调用 /api/v1 service 函数本体（functools.wraps 把原函数留在
    __wrapped__；绕过 domain_endpoint 装饰器让 DomainError 保留为异常，
    由工具层统一转模型可读错误文本）。"""
    raw = getattr(func, "__wrapped__", func)
    return raw(**kwargs)


def _dump(result):
    if isinstance(result, BaseModel):
        return result.model_dump(mode="json")
    return result


def _as_of(snapshot: dict) -> date:
    return date.fromisoformat(snapshot["as_of"])


def _member_guard(snapshot: dict, person_id) -> Optional[dict]:
    """person_id 必须在会话快照成员集合内（入参再过作用域校验）。"""
    if person_id is None or person_id not in set(snapshot.get("member_person_ids") or []):
        return _out_of_scope(PERSON_OUT_OF_SCOPE)
    return None


def _require_exam_name(args: dict) -> Tuple[Optional[str], Optional[dict]]:
    exam_name = (args.get("exam_name") or "").strip()
    if not exam_name:
        return None, {"error": "invalid_scope_param", "detail": "exam_name 必填"}
    return exam_name, None


# ────────────────────── 8 个只读工具的 handler ──────────────────────


def _tool_search_students(db, snapshot: dict, args: dict) -> dict:
    """学生搜索：students list service 同源名册（homeroom_roster /
    teaching_roster），再收敛到会话快照成员集合 + 姓名/学号子串匹配。"""
    needle = (args.get("q") or "").strip()
    if snapshot["mode"] == "homeroom":
        roster = q.homeroom_roster(
            db, snapshot["class_ids"][0], _as_of(snapshot), snapshot["academic_year_id"]
        )
    else:
        roster = q.teaching_roster(
            db, snapshot["class_ids"], _as_of(snapshot), snapshot["academic_year_id"]
        )
    members = set(snapshot.get("member_person_ids") or [])
    rows = [item for item in roster if item["person_id"] in members]
    if needle:
        rows = [
            item
            for item in rows
            if needle in (item.get("name") or "")
            or (item.get("alias") and needle in item["alias"])
        ]
    return {
        "mode": snapshot["mode"],
        "cohort_size": snapshot["cohort_size"],
        "matched": len(rows),
        "students": rows,
    }


def _tool_get_student_profile(db, snapshot: dict, args: dict) -> dict:
    guard = _member_guard(snapshot, args.get("person_id"))
    if guard:
        return guard
    if snapshot["mode"] == "homeroom":
        from app.api.students import homeroom_student_profile

        result = _call_service(
            homeroom_student_profile,
            person_id=args["person_id"],
            academic_year_id=snapshot["academic_year_id"],
            class_id=snapshot["class_ids"][0],
            term_id=None,
            db=db,
        )
    else:
        from app.api.students import teaching_student_profile

        result = _call_service(
            teaching_student_profile,
            person_id=args["person_id"],
            academic_year_id=snapshot["academic_year_id"],
            # Q01：单班会话钉住该班（并集会话由 service 按同学科并集重建，
            # 语义与快照一致，多轮重验保证期间无漂移）
            teaching_class_id=_single_class_id(snapshot),
            term_id=None,
            db=db,
        )
    return _dump(result)


def _tool_get_exam_list(db, snapshot: dict, args: dict) -> dict:
    from app.api.imports import list_shared_exams

    if snapshot["mode"] == "homeroom":
        result = _call_service(
            list_shared_exams,
            mode="homeroom",
            academic_year_id=snapshot["academic_year_id"],
            class_id=snapshot["class_ids"][0],
            teaching_class_id=None,
            subject=None,
            db=db,
        )
    else:
        # Q01：单班会话显式传 teaching_class_id（走 _resolve_import_scope
        # 显式校验路径），绝不缺省成"全部所教班"并集
        result = _call_service(
            list_shared_exams,
            mode="teaching",
            academic_year_id=snapshot["academic_year_id"],
            class_id=None,
            teaching_class_id=_single_class_id(snapshot),
            subject=snapshot.get("subject"),
            db=db,
        )
    return _dump(result)


def _tool_get_exam_stats(db, snapshot: dict, args: dict) -> dict:
    exam_name, err = _require_exam_name(args)
    if err:
        return err
    if snapshot["mode"] == "homeroom":
        from app.api.analysis import homeroom_exam_stats

        result = _call_service(
            homeroom_exam_stats,
            exam_name=exam_name,
            academic_year_id=snapshot["academic_year_id"],
            class_id=snapshot["class_ids"][0],
            term_id=None,
            db=db,
        )
    else:
        from app.api.analysis import teaching_exam_stats

        result = _call_service(
            teaching_exam_stats,
            exam_name=exam_name,
            teaching_class_id=_single_class_id(snapshot),
            academic_year_id=snapshot["academic_year_id"],
            term_id=None,
            db=db,
        )
    return _dump(result)


def _tool_get_scores_table(db, snapshot: dict, args: dict) -> dict:
    exam_name, err = _require_exam_name(args)
    if err:
        return err
    subject = (args.get("subject") or "").strip() or None
    if (
        snapshot["mode"] == "teaching"
        and subject is not None
        and subject != snapshot.get("subject")
    ):
        # teaching 会话的全科/总分越权请求：任教学科之外一律拒绝
        return _out_of_scope(
            f"该学科不在当前会话范围（当前任教学科：{snapshot.get('subject')}）"
        )
    if snapshot["mode"] == "homeroom":
        from app.api.scores import query_scores

        result = _call_service(
            query_scores,
            mode="homeroom",
            academic_year_id=snapshot["academic_year_id"],
            term_id=None,
            exam_name=exam_name,
            db=db,
        )
        data = _dump(result)
    else:
        # Q01：query_scores 路由不接受班参（teaching 缺省=并集），单班
        # 会话薄调它会扩成全部所教班。改按快照班级重建 ctx，经同一
        # readable_facts 门复刻端点装配（不自写 SQL、聚合前限制班级）。
        from app.api.schemas import TeachingScoreRow, TeachingScoresResponse
        from app.api.students import _metadata

        ctx = _teaching_ctx_of(db, snapshot)
        entries = q.readable_facts(db, ctx, exam_name)
        rows = [
            TeachingScoreRow(
                person_id=e.person_id,
                name=None,
                subject=e.fact.subject,
                score=e.fact.score,  # 缺考保持 null，绝不转 0
                source_domain=e.source_domain,
            )
            for e in sorted(
                entries, key=lambda x: (x.person_id, x.fact.subject or "")
            )
        ]
        all_ids = sorted({r.person_id for r in rows})
        name_map = q.names_for(db, all_ids)
        for r in rows:
            r.name = name_map.get(r.person_id)
        data = _dump(
            TeachingScoresResponse(
                metadata=_metadata(
                    ctx, fact_revisions=[e.fact.data_revision for e in entries]
                ),
                rows=rows,
            )
        )
    if subject is not None:
        # homeroom 会话按学科收窄输出：只保留该学科行（总分/他科行一并
        # 剔除；投影门与冲突裁决已在 service 内执行，这里仅收窄视图）
        data["rows"] = [
            row for row in data.get("rows", []) if row.get("subject") == subject
        ]
    return data


def _tool_get_homework_summary(db, snapshot: dict, args: dict) -> dict:
    group_by = args.get("group_by") or "week"
    if group_by not in ("week", "month"):
        return {"error": "invalid_scope_param", "detail": "group_by 仅支持 week/month"}

    def _parse(name: str) -> Tuple[Optional[date], Optional[dict]]:
        raw = (args.get(name) or "").strip()
        if not raw:
            return None, None
        try:
            return date.fromisoformat(raw), None
        except ValueError:
            return None, {
                "error": "invalid_scope_param",
                "detail": f"{name} 必须是 ISO 日期（YYYY-MM-DD）",
            }

    range_from, err1 = _parse("from")
    if err1:
        return err1
    range_to, err2 = _parse("to")
    if err2:
        return err2

    from app.api.homework import homework_dashboard

    if snapshot["mode"] == "homeroom":
        result = _call_service(
            homework_dashboard,
            mode="homeroom",
            class_id=snapshot["class_ids"][0],
            teaching_class_id=None,
            academic_year_id=snapshot["academic_year_id"],
            subject=None,
            homework_type=None,
            group_by=group_by,
            db=db,
        )
    else:
        # Q01：单班会话显式传 teaching_class_id，绝不缺省成全部所教班并集
        result = _call_service(
            homework_dashboard,
            mode="teaching",
            class_id=None,
            teaching_class_id=_single_class_id(snapshot),
            academic_year_id=snapshot["academic_year_id"],
            subject=snapshot.get("subject"),
            homework_type=None,
            group_by=group_by,
            db=db,
        )
    data = _dump(result)
    if range_from is not None or range_to is not None:
        kept = []
        for group in data.get("groups", []):
            try:
                label = date.fromisoformat(group["label"])
            except (KeyError, ValueError):
                continue
            if range_from is not None and label < range_from:
                continue
            if range_to is not None and label > range_to:
                continue
            kept.append(group)
        data["groups"] = kept
    data["range"] = {"from": args.get("from"), "to": args.get("to")}
    return data


def _tool_get_homework_student(db, snapshot: dict, args: dict) -> dict:
    guard = _member_guard(snapshot, args.get("person_id"))
    if guard:
        return guard
    from app.api.homework import homework_student

    result = _call_service(
        homework_student,
        person_id=args["person_id"],
        mode=snapshot["mode"],
        class_id=(
            snapshot["class_ids"][0] if snapshot["mode"] == "homeroom" else None
        ),
        # Q01：teaching 单班会话钉住该班（并集会话缺省语义一致）
        teaching_class_id=(
            _single_class_id(snapshot) if snapshot["mode"] == "teaching" else None
        ),
        academic_year_id=snapshot["academic_year_id"],
        db=db,
    )
    return _dump(result)


def _tool_get_class_comparison(db, snapshot: dict, args: dict) -> dict:
    """教学班对比：路由 teaching_class_compare 不接受班参（恒并集），Q01
    下按快照班级重建 ctx 并复刻端点装配——直接复用端点自己的聚合助手
    （_queries/readable_facts 门），聚合前限制班级，不自写 SQL。"""
    exam_name, err = _require_exam_name(args)
    if err:
        return err
    from app.api.analysis import (
        _avg,
        _class_labels,
        _exam_exists_for_teaching,
        _exam_member_ids,
        _teaching_member_rows,
    )
    from app.api.analysis_schemas import ClassCompareEntry, ClassCompareResponse
    from app.core.errors import ResourceOutOfScope

    ctx = _teaching_ctx_of(db, snapshot)
    if not _exam_exists_for_teaching(db, ctx.academic_year_id, exam_name):
        raise ResourceOutOfScope(
            "exam not found in teaching domain", details={"exam_name": exam_name}
        )
    # F08 考试维度成员口径：与端点一致按考试时点名册解析
    member_ids = _exam_member_ids(db, ctx, exam_name)
    labels = _class_labels(db, list(snapshot["class_ids"]))
    classes = []
    small_sample = False
    for tc_id in snapshot["class_ids"]:
        rows = _teaching_member_rows(
            db, ctx, [tc_id], ctx.subject, exam_name, member_ids
        )
        valid = [score for _, score, _, _, _ in rows if score is not None]
        if len(valid) < 5:
            small_sample = True
        classes.append(
            ClassCompareEntry(
                teaching_class_id=tc_id,
                class_label=labels.get(tc_id, ""),
                member_count=len(valid),
                subject_avg=_avg(valid),
                score_basis="raw",
                source="estimated",
            )
        )
    return _dump(ClassCompareResponse(classes=classes, small_sample=small_sample))


# ────────────────────── 注册表（domains 声明 + 域投影） ──────────────────────


@dataclass(frozen=True)
class WsToolSpec:
    name: str
    description: str
    input_schema: dict
    domains: Tuple[str, ...]  # 允许出现的会话域（"homeroom"/"teaching"）
    handler: Callable[[object, dict, dict], dict]


_PERSON_SCHEMA = {
    "type": "object",
    "properties": {
        "person_id": {"type": "integer", "description": "会话范围内学生身份 ID"}
    },
    "required": ["person_id"],
}
_EXAM_SCHEMA = {
    "type": "object",
    "properties": {"exam_name": {"type": "string", "description": "考试名称"}},
    "required": ["exam_name"],
}

TOOL_REGISTRY: List[WsToolSpec] = [
    WsToolSpec(
        name="search_students",
        description="按姓名/学号关键字搜索当前会话范围内的学生名单（含 person_id/姓名/学号/座号）",
        input_schema={
            "type": "object",
            "properties": {"q": {"type": "string", "description": "姓名或学号关键字，可留空返回全部"}},
        },
        domains=("homeroom", "teaching"),
        handler=_tool_search_students,
    ),
    WsToolSpec(
        name="get_student_profile",
        description="查询单个学生的成绩画像（学科/考试/分数；仅当前会话域内学科口径）",
        input_schema=_PERSON_SCHEMA,
        domains=("homeroom", "teaching"),
        handler=_tool_get_student_profile,
    ),
    WsToolSpec(
        name="get_exam_list",
        description="列出当前会话范围内可读的考试（名称/日期/科目集合）",
        input_schema={"type": "object", "properties": {}},
        domains=("homeroom", "teaching"),
        handler=_tool_get_exam_list,
    ),
    WsToolSpec(
        name="get_exam_stats",
        description="查询单场考试的班内统计（均分/最高最低/有效与缺考人数；班主任视角含全科与总分，教师视角仅任教学科）",
        input_schema=_EXAM_SCHEMA,
        domains=("homeroom", "teaching"),
        handler=_tool_get_exam_stats,
    ),
    WsToolSpec(
        name="get_scores_table",
        description="查询单场考试的逐人成绩表（homeroom 含全科+总分，teaching 仅任教学科）",
        input_schema={
            "type": "object",
            "properties": {
                "exam_name": {"type": "string", "description": "考试名称"},
                "subject": {
                    "type": "string",
                    "description": "可选，按学科过滤（teaching 会话仅接受任教学科）",
                },
            },
            "required": ["exam_name"],
        },
        domains=("homeroom", "teaching"),
        handler=_tool_get_scores_table,
    ),
    WsToolSpec(
        name="get_homework_summary",
        description="按周/月汇总作业批次与缺交情况，可用 from/to（ISO 日期）限定区间",
        input_schema={
            "type": "object",
            "properties": {
                "from": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                "to": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                "group_by": {"type": "string", "enum": ["week", "month"]},
            },
        },
        domains=("homeroom", "teaching"),
        handler=_tool_get_homework_summary,
    ),
    WsToolSpec(
        name="get_homework_student",
        description="查询单个学生的作业事件流与连续缺交情况",
        input_schema=_PERSON_SCHEMA,
        domains=("homeroom", "teaching"),
        handler=_tool_get_homework_student,
    ),
    WsToolSpec(
        name="get_class_comparison",
        description="按所教教学班对比单场考试的班均分（教师专属视角）",
        input_schema=_EXAM_SCHEMA,
        domains=("teaching",),
        handler=_tool_get_class_comparison,
    ),
]

_REGISTRY_BY_NAME: Dict[str, WsToolSpec] = {spec.name: spec for spec in TOOL_REGISTRY}

TOOL_NAMES: Tuple[str, ...] = tuple(spec.name for spec in TOOL_REGISTRY)


def tools_for_domain(domain: str) -> List[dict]:
    """按会话域投影出 Anthropic tools 形状（domains 过滤，顺序即注册顺序）。"""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "input_schema": spec.input_schema,
        }
        for spec in TOOL_REGISTRY
        if domain in spec.domains
    ]


def execute_session_tool(db, snapshot: dict, name: str, args: Optional[dict], teacher_id=None) -> dict:
    """会话内工具执行入口：域投影校验 + 快照重验（Q02：每轮工具调用前
    重放比对，失效抛 ScopeDriftError 由流层中止本轮流）+ 入参作用域校验 +
    DomainError/异常兜底转模型可读错误文本（绝不让栈穿透）。无写操作工具。"""
    spec = _REGISTRY_BY_NAME.get(name)
    domain = snapshot.get("data_domain") or snapshot.get("mode")
    if spec is None or domain not in spec.domains:
        return {
            "error": "tool_not_in_scope",
            "detail": f"工具 {name} 不在当前会话可用范围（会话域：{domain}）",
        }
    # Q02：执行前重验（撤销关联/版本变/配对增删/成员漂移 → 中止，
    # 模型绝不会再拿到旧范围数据；MCP 单次调用路径同样受此保护）
    _assert_snapshot_fresh(db, snapshot, teacher_id)
    try:
        result = spec.handler(db, snapshot, dict(args or {}))
    except DomainError as exc:
        # service 层 404（考试不在本域/学生越界等）→ 契约越界文本
        if exc.code == "resource_out_of_scope":
            return {"error": exc.code, "detail": OBJECT_OUT_OF_SCOPE}
        return {"error": exc.code, "detail": exc.message}
    except Exception as exc:  # 工具层兜底：模型只见错误文本
        return {"error": "tool_failed", "detail": str(exc)}
    return result if isinstance(result, dict) else {"result": result}


def dump_scope_snapshot(snapshot: dict) -> str:
    """快照 → ChatSession.scope_json（统一序列化入口，保证与漂移比对
    字段一致）。"""
    return json.dumps(snapshot, ensure_ascii=False)
