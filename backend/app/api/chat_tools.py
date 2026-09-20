"""P6 AI 会话只读工具注册表 + 会话作用域快照（契约 docs/contracts/p6-ai-mcp.md）。

红线（违反即返工）：
- 全部只读：handler 只做 /api/v1 既有 service/查询函数的薄封装
  （analysis/students/scores/homework/imports + _queries 的统一可读事实
  口径），绝不另写查询逻辑绕过 §1.4.1 五条件投影门，绝不触发表写入。
- 域投影：每个工具声明 domains；execute/tools_for_domain 按
  snapshot.data_domain 裁剪——teaching 会话拿不到教学班对比工具，
  homeroom 会话拿不到单科对比工具，越权工具调用返回错误文本。
- 跨学年/学期时间语义（唯一解析实现）：academic_year_id/year_offset 与
  term_id/term_offset 二选一；学年按 start_date 排序、以快照学年为锚；
  学期源 = ws_homework_semester（学期设置页/作业看板同一事实源；P1 Term
  表真实部署无数据且无维护入口，不再作为学期来源），term 锚点 = is_current=1
  的学期（无则含快照 as_of 的学期，再无则最新学期）；偏移只在非假期子序列
  上计数（名称含 暑假/寒假/假期 的条目不占编号，目录标 vacation=true 且
  term_offset=null，term_id 仍可直传查假期数据）；都不传=快照学年
  （与既有行为兼容）。get_academic_years 输出学年/学期目录与偏移标注
  （挂不上学年目录的作业学期进顶层 unmatched_semesters，绝不静默丢弃），
  配合系统提示第 6 条翻译规则让模型听懂「上学年/上学期/上上学年/这学期」。
  teaching 跨年由 _teaching_ctx_for_year 显式解析该学年同学科班集合，
  绝不把本学年班 id 传给其他学年。
- 入参再校验：person_id/subject 等先过会话快照作用域，越界返回模型可读
  中文错误文本，绝不让异常栈穿透到模型。exam_name 由 _resolve_exam_name
  统一解析：精确命中直接用（候选清单列有同名，或 service 存在性门可答
  ——_exam_gate_in_year 兜住候选按查询时点成员过滤而漏列的考试）；唯一
  子串命中自动解析并在响应顶层 exam_resolved 注记全名；多场命中返回候选
  清单（名字+日期、按日期降序、最多 10 条）；零命中报不在范围并列出可用
  考试。模糊匹配候选与所选学年钉死、与 get_exam_list 完全同源
  （list_shared_exams service）；但完整精确考试名经 _exam_year_id_of 跨学
  年唯一定位到该考试所在学年（显式指年绝不改指、同名多学年不猜），响应
  数据正确性优先。
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


def _format_exam_catalog(exams: list, limit: int = 10) -> str:
    """候选/可用考试清单的模型可读文本（名字+日期，每行一条，最多 limit 条）。
    exams 直接用 service 天然序（有日期按日期降序、无日期排后）。"""
    return "\n".join(
        f"- {e['exam_name']}（考试日期：{e.get('exam_date') or '未知'}）"
        for e in exams[:limit]
    )


def _exam_candidates(db, snapshot: dict, year_id: int) -> list:
    """候选考试清单：与 get_exam_list 工具完全同源（list_shared_exams
    service，域/班/学年钉法照抄 _tool_get_exam_list）——考试名模糊匹配的
    候选由此生成，绝不另写查询逻辑。调用方必须先解析年份再传 year_id
    （候选限定在所选学年）。"""
    from app.api.imports import list_shared_exams

    if snapshot["mode"] == "homeroom":
        result = _call_service(
            list_shared_exams,
            mode="homeroom",
            academic_year_id=year_id,
            # 行政班跨年延续：恒钉快照行政班
            class_id=snapshot["class_ids"][0],
            teaching_class_id=None,
            subject=None,
            db=db,
        )
    else:
        # Q01：单班会话显式传 teaching_class_id（绝不缺省成"全部所教班"
        # 并集）；跨年时本学年班 id 绝不传给其他学年 → 传 None 并钉住
        # 任教学科（service 按该学年同学科班集合解析）。
        result = _call_service(
            list_shared_exams,
            mode="teaching",
            academic_year_id=year_id,
            class_id=None,
            teaching_class_id=(
                _single_class_id(snapshot)
                if year_id == snapshot["academic_year_id"]
                else None
            ),
            subject=snapshot.get("subject"),
            db=db,
        )
    return _dump(result).get("exams", [])


def _resolve_exam_name(
    db, snapshot: dict, args: dict, year_id: int
) -> Tuple[Optional[str], Optional[str], Optional[dict]]:
    """exam_name 统一解析（工具层唯一实现，含模糊匹配）。返回
    (exam_name, resolved_note, err)：err 非 None 时调用方直接返回；
    resolved_note 非 None 时调用方把它作为 ``exam_resolved`` 注记合进
    有数据返回的响应顶层。

    - 精确命中 → (原名, None, None)：既有行为完全不变（响应无注记）。
      精确命中 = 候选清单列有同名，或 service 存在性门可答
      （_exam_gate_in_year——候选清单按【查询时点成员】过滤，会漏列
      service 实际可答的考试：考试维度读端点按【考试时点成员】解析人群
      （F08），当期成员为空/考后变动时精确名曾被误判「不在范围」）；
    - 无精确但子串唯一命中（用户片段是考试名的子串，如「期中」）→
      (全名, 全名, None)：自动解析为该场，响应顶层注明 exam_resolved；
    - 多场子串命中 → (None, None, invalid_scope_param 候选清单)：每条
      考试名+考试日期、按日期降序、最多 10 条，注明选一场后重试；
    - 零命中 → (None, None, resource_out_of_scope 可读错误)：注明不在
      当前会话范围并列出该范围可用考试（最多 10 条），模型可一步自纠。
    exam_name 缺失 → invalid_scope_param（沿用原 _require_exam_name 语义）。
    DomainError（如该学年班未配置）原样向上抛，由 execute_session_tool
    统一转译为模型可读错误。
    """
    raw = (args.get("exam_name") or "").strip()
    if not raw:
        return None, None, {
            "error": "invalid_scope_param",
            "detail": "exam_name 必填",
        }
    exams = _exam_candidates(db, snapshot, year_id)
    if any(e["exam_name"] == raw for e in exams) or _exam_gate_in_year(
        db, snapshot, year_id, raw
    ):
        return raw, None, None
    matches = [e for e in exams if raw in e["exam_name"]]
    if len(matches) == 1:
        resolved = matches[0]["exam_name"]
        return resolved, resolved, None
    if len(matches) > 1:
        return None, None, {
            "error": "invalid_scope_param",
            "detail": (
                f"考试名「{raw}」匹配到 {len(matches)} 场考试，无法唯一确定，"
                f"请选一场后用完整考试名重试：\n{_format_exam_catalog(matches)}"
            ),
        }
    if exams:
        return None, None, {
            "error": "resource_out_of_scope",
            "detail": (
                f"考试「{raw}」不在当前会话范围；当前范围可用考试如下"
                f"（可用完整名称重试）：\n{_format_exam_catalog(exams)}"
            ),
        }
    return None, None, {
        "error": "resource_out_of_scope",
        "detail": (
            f"考试「{raw}」不在当前会话范围（当前范围所选学年暂无已导入考试）"
        ),
    }


# ────────────────────── 跨学年/学期时间语义（唯一解析实现） ──────────────────────
# 用户说「上学年/上学期/上上学年/这学期」时，模型只需传 year_offset /
# term_offset；具体 id 换算在这唯一一份辅助里完成（新工具与既有工具共用）。


def _ordered_years(db) -> list:
    """全部学年（数据源 = shared.list_academic_years service，同源）按
    start_date 升序重排——service 返回降序，重排仅为 offset 换算定序，
    不另写查询。"""
    from app.api.shared import list_academic_years

    years = _dump(_call_service(list_academic_years, db=db))["years"]
    return sorted(years, key=lambda item: (item["start_date"], item["id"]))


def _ordered_terms(db) -> list:
    """全部作业学期拍平（数据源 = ws_homework_semester 表，学期设置页/
    作业看板同一事实源）按 start_date 升序——跨学年的 term_offset（如 -1
    落到上一学年第二学期）由此天然成立。

    直接 db.query 该表而非 _call_service homework.list_homework_semesters：
    该 service 按单学年解析、无表数据时回退「推导学期」（id=null，无法作
    为 term_id 直传），且返回条目不带 academic_year_id——既满足不了
    get_academic_years 按学年挂接与 unmatched_semesters 识别，也满足不了
    term_id 直传。目录类直读在本文件已有先例（_frozen_links 直读
    HomeroomTeachingLink/LinkedStudent），不属于业务事实投影门范畴。"""
    from app.db.workspace_models import WsHomeworkSemester

    rows = (
        db.query(WsHomeworkSemester)
        .order_by(WsHomeworkSemester.start_date.asc(), WsHomeworkSemester.id.asc())
        .all()
    )
    return [
        {
            "id": row.id,
            "academic_year_id": row.academic_year_id,
            "name": row.name,
            "start_date": row.start_date.isoformat(),
            "end_date": row.end_date.isoformat(),
            "is_current": bool(row.is_current),
            "mode": row.mode,
        }
        for row in rows
    ]


def _is_vacation_term(name) -> bool:
    """假期条目判定：名称含 暑假/寒假/假期（学期设置页常建「XX学年暑假」
    等作业真空区间）。假期保留在目录里（查假期作业仍有用，term_id 可直传），
    但不参与「上/下学期」相对换算——否则「上学期」会解析到假期真空区间。"""
    name = str(name or "")
    return "暑假" in name or "寒假" in name or "假期" in name


def _term_anchor_index(terms: list, as_of_iso: str) -> Optional[int]:
    """学期锚点（唯一实现，_resolve_term_arg 与 get_academic_years 共用）：
    调用方传入【非假期子序列】（假期条目已被 _is_vacation_term 剔除）。
    is_current=1 的学期优先（与学期设置页「当前学期」同一口径）；无则取含
    快照 as_of 的学期；再无则最新学期；空 → None。"""
    index = next((i for i, term in enumerate(terms) if term.get("is_current")), None)
    if index is not None:
        return index
    index = next(
        (
            i
            for i, term in enumerate(terms)
            if term["start_date"] <= as_of_iso <= term["end_date"]
        ),
        None,
    )
    if index is None and terms:
        return len(terms) - 1
    return index


def _int_or_error(value, label: str):
    if isinstance(value, bool) or not isinstance(value, int):
        return None, {"error": "invalid_scope_param", "detail": f"{label} 必须是整数"}
    return value, None


def _resolve_year_arg(db, snapshot: dict, args: dict):
    """year 参数二选一解析（工具层唯一实现）：

    - ``academic_year_id``（int）直传；
    - ``year_offset``（int，0=本学年、-1=上学年、-2=上上学年）由服务端把
      全部学年按 start_date 排序、以快照学年为锚换算成具体 id；越界返回
      可读错误并列出全部可用学年名称；
    - 都不传 → None（调用方用 snapshot["academic_year_id"]，与既有行为
      完全兼容）；
    - 同传 → 可读错误 dict。
    """
    year_id = args.get("academic_year_id")
    offset = args.get("year_offset")
    if year_id is not None and offset is not None:
        return {
            "error": "invalid_scope_param",
            "detail": "academic_year_id 与 year_offset 只能提供一个",
        }
    if year_id is None and offset is None:
        return None
    if year_id is not None:
        checked, err = _int_or_error(year_id, "academic_year_id")
        return checked if err is None else err
    offset, err = _int_or_error(offset, "year_offset")
    if err:
        return err
    years = _ordered_years(db)
    anchor_index = next(
        (
            index
            for index, year in enumerate(years)
            if year["id"] == snapshot.get("academic_year_id")
        ),
        None,
    )
    if anchor_index is None:
        return {
            "error": "invalid_scope_param",
            "detail": "快照学年不在学年目录中，无法换算 year_offset",
        }
    target = anchor_index + offset
    if target < 0 or target >= len(years):
        catalog = "、".join(year["name"] for year in years)
        return {
            "error": "invalid_scope_param",
            "detail": (
                f"year_offset={offset} 超出可用学年范围"
                f"（当前学年 {years[anchor_index]['name']}）；可用学年：{catalog}"
            ),
        }
    return years[target]["id"]


def _resolve_term_arg(db, snapshot: dict, args: dict):
    """term 参数二选一解析（仅 get_student_notes 等需要学期的工具）：

    - ``term_id``（int）直传 = ws_homework_semester.id（学期设置页同一 id），
      可直指假期条目（暑假/寒假）查假期数据；``term_offset``（int，0=本学期、
      -1=上一学期）在【非假期子序列】上排平换算（假期条目不占编号——否则
      「上学期」会落到作业真空的假期区间），锚点 = is_current 的非假期学期
      （无则含快照 as_of 的非假期学期，再无则最新非假期学期）——跨学年时
      自动落到上一学年第二学期；
    - 都不传 → None；同传/非法/无学期 → 可读错误 dict。
    """
    term_id = args.get("term_id")
    offset = args.get("term_offset")
    if term_id is not None and offset is not None:
        return {
            "error": "invalid_scope_param",
            "detail": "term_id 与 term_offset 只能提供一个",
        }
    if term_id is None and offset is None:
        return None
    if term_id is not None:
        checked, err = _int_or_error(term_id, "term_id")
        return checked if err is None else err
    offset, err = _int_or_error(offset, "term_offset")
    if err:
        return err
    terms = _ordered_terms(db)
    work = [term for term in terms if not _is_vacation_term(term["name"])]
    if not work:
        return {
            "error": "invalid_scope_param",
            "detail": "库中尚未建立学期，无法换算 term_offset",
        }
    anchor_index = _term_anchor_index(work, _as_of(snapshot).isoformat())
    if anchor_index is None:
        return {
            "error": "invalid_scope_param",
            "detail": "库中尚未建立学期，无法换算 term_offset",
        }
    target = anchor_index + offset
    if target < 0 or target >= len(work):
        catalog = "、".join(term["name"] for term in work)
        return {
            "error": "invalid_scope_param",
            "detail": (
                f"term_offset={offset} 超出可用学期范围"
                f"（当前学期 {work[anchor_index]['name']}）；可用学期：{catalog}"
            ),
        }
    return work[target]["id"]


def _year_id_of(db, snapshot: dict, args: dict) -> Tuple[Optional[int], Optional[dict]]:
    """既有工具参数化共用入口：解析 year 参数；无参 → 快照学年。
    返回 (year_id, error)；error 非 None 时调用方直接返回。"""
    resolved = _resolve_year_arg(db, snapshot, args)
    if isinstance(resolved, dict):
        return None, resolved
    return (resolved if resolved is not None else snapshot["academic_year_id"]), None


def _exam_gate_in_year(db, snapshot: dict, year_id: int, exam_name: str) -> bool:
    """「这场考试在 year_id 学年对当前会话而言 service 实际可答吗」——
    精确名放行/跨学年定位的服务端存在性判据。复用 analysis 读端点同一
    扇存在性门（_exam_exists_for_homeroom / _exam_exists_for_teaching，
    域+学年级、不限班级），绝不另写第二套过滤；数据读取仍全部由 service
    在其自身门禁（班级钉定/成员口径/投影门）内产出——本判据只决定精确
    名是否放行，放行后 service 答什么工具就回什么（含合法空态），与页面
    端点逐字段同源（A01），工具层不放大权限。

    为什么候选清单之外还需要这扇门：候选（_exam_candidates /
    readable_exam_summaries）按【查询时点成员】过滤（F09），而考试维度
    读端点按【考试时点成员】解析人群（F08 members_at）——当期成员为空
    或考后变动时（新学年名册未导入、as_of 晚于名册生效日等），候选会
    漏列 service 本可正常回答的考试，精确名曾被误判「不在当前会话范围」。

    homeroom 非快照学年拿不到「该学年当期解析的关联」，投影门退为本域
    存在性（从严：宁可让模型显式指年重试，绝不借快照关联跨年放大范围）。
    """
    from types import SimpleNamespace

    from app.api.analysis import (
        _exam_exists_for_homeroom,
        _exam_exists_for_teaching,
    )

    if snapshot["mode"] == "homeroom":
        return _exam_exists_for_homeroom(
            db,
            SimpleNamespace(
                academic_year_id=year_id,
                link_id=(
                    snapshot.get("link_id")
                    if year_id == snapshot["academic_year_id"]
                    else None
                ),
            ),
            exam_name,
        )
    return _exam_exists_for_teaching(db, year_id, exam_name)


def _exam_year_id_of(db, snapshot: dict, args: dict) -> Tuple[Optional[int], Optional[dict]]:
    """考试名工具专用学年入口（_year_id_of 之上的一层；仅解析 exam_name
    的 handler 使用，其余工具仍直接用 _year_id_of，行为不变）。

    学年钉定是为模糊匹配消歧（候选清单限定所选学年）；但用户给出完整
    【精确考试名】时，它在教师可见范围内落在哪个学年是客观事实——响应
    数据正确性优先，直接定位到该考试所在学年：

    1. 先照常 _year_id_of 解析（显式 academic_year_id / year_offset /
       缺省快照学年；解析错误原样返回）；
    2. 无 exam_name（strip 后空）或用户显式给了学年参数 → 原样返回
       year_id（显式指年 = 用户自己圈定范围，绝不擅自改指其他学年；
       无考试名则无可定位。exam_names 多项列表工具 get_rank_frequency
       即属前者：逐项解析仍在 _resolve_exam_name 于既定学年内进行）；
    3. 精确名定位：当前 year_id 内已可答（候选精确同名或 service 存在性
       门，同 _resolve_exam_name 精确判据）→ 返回原 year_id；否则遍历
       全部学年收集可答学年（单学年候选解析抛 DomainError → 跳过该学年
       继续不向上传）；
    4. 可答学年恰好 1 个 → 返回该学年 id（跨学年定位）；0 个或 ≥2 个 →
       返回原 year_id 沿用既有错误路径（同名多学年不猜）。

    跨学年定位不产生 exam_resolved 注记：响应回显考试名与数据，模型
    可自见。
    """
    year_id, err = _year_id_of(db, snapshot, args)
    if err:
        return None, err
    raw = (args.get("exam_name") or "").strip()
    if not raw:
        return year_id, None
    if args.get("academic_year_id") is not None or args.get("year_offset") is not None:
        return year_id, None

    def answerable(year: int) -> bool:
        try:
            if any(e["exam_name"] == raw for e in _exam_candidates(db, snapshot, year)):
                return True
        except DomainError:
            return False
        return _exam_gate_in_year(db, snapshot, year, raw)

    if answerable(year_id):
        return year_id, None
    matched = [
        year["id"]
        for year in _ordered_years(db)
        if year["id"] != year_id and answerable(year["id"])
    ]
    if len(matched) == 1:
        return matched[0], None
    return year_id, None


def _teaching_ctx_for_year(db, snapshot: dict, year_id: int):
    """teaching 会话按目标学年重建 ctx（跨年参数化，Q01 同约束）：

    - 目标学年 == 快照学年 → _teaching_ctx_of（原行为不变）；
    - 跨年 → 绝不把本学年教学班 id 传给其他学年（班 id 本就属于特定学年，
      service 会 404）；改用 build_teaching_params 显式解析【该学年同任教
      学科】教学班集合后构建 ctx——学科钉住快照任教学科，绝不扩大到非任
      教学科。该学年本学科确无班（含延续回退落空）→ 可读中文错误。
    """
    if year_id == snapshot["academic_year_id"]:
        return _teaching_ctx_of(db, snapshot)
    from app.api import current_teacher_id
    from app.core.context import resolve_workspace_context
    from app.core.errors import WorkspaceNotConfigured

    try:
        params, _subject, _class_ids = q.build_teaching_params(
            db, year_id, None, snapshot["subject"]
        )
    except WorkspaceNotConfigured:
        # build_teaching_params 的英文 409 换成模型可读中文（范围不变：
        # 显式学科 + 该学年班集合，未发生任何扩大）
        raise InvalidScopeParam(
            "该学年无任教学科教学班",
            details={"academic_year_id": year_id, "subject": snapshot.get("subject")},
        )
    return resolve_workspace_context(db, current_teacher_id(db), "teaching", params)


def _optional_int(args: dict, name: str):
    """可选整数参数：缺省 None；类型非法 → 可读错误。"""
    value = args.get(name)
    if value is None:
        return None, None
    return _int_or_error(value, name)


def _optional_iso_date(args: dict, name: str):
    """可选 ISO 日期参数（from/to 等）：缺省 None；格式非法 → 可读错误。"""
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


def _teaching_subject_guard(snapshot: dict, subject: Optional[str]):
    """teaching 会话学科守卫（与 get_scores_table 同一红线）：任教学科之外
    一律拒绝；homeroom 原样放行（班主任可见全科）。返回 (subject, error)，
    error 非 None 时调用方直接返回。"""
    if snapshot["mode"] != "teaching":
        return subject, None
    if subject is not None and subject != snapshot.get("subject"):
        return None, _out_of_scope(
            f"该学科不在当前会话范围（当前任教学科：{snapshot.get('subject')}）"
        )
    return snapshot.get("subject"), None


# ────────────────────── 只读工具的 handler ──────────────────────


def _tool_search_students(db, snapshot: dict, args: dict) -> dict:
    """学生搜索：students list service 同源名册（homeroom_roster /
    teaching_roster），再收敛到会话快照成员集合 + 姓名/学号子串匹配。
    跨年：按解析出的学年查名册（学年只影响别名学年优先），成员收敛
    仍用快照 member_person_ids（红线：入参再过会话快照作用域）。"""
    year_id, err = _year_id_of(db, snapshot, args)
    if err:
        return err
    needle = (args.get("q") or "").strip()
    if snapshot["mode"] == "homeroom":
        # homeroom：班恒钉快照行政班（行政班跨年延续=同一班行，查的是
        # 教师本人班级历史）
        roster = q.homeroom_roster(
            db, snapshot["class_ids"][0], _as_of(snapshot), year_id
        )
    else:
        if year_id == snapshot["academic_year_id"]:
            scope_ids = list(snapshot["class_ids"])
        else:
            # 跨年教学：该学年同学科教学班集合，绝不复用本学年班 id
            scope_ids = q.teaching_class_ids_for_subject(
                db, year_id, snapshot.get("subject")
            )
        roster = q.teaching_roster(db, scope_ids, _as_of(snapshot), year_id)
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
    year_id, err = _year_id_of(db, snapshot, args)
    if err:
        return err
    if snapshot["mode"] == "homeroom":
        from app.api.students import homeroom_student_profile

        result = _call_service(
            homeroom_student_profile,
            person_id=args["person_id"],
            academic_year_id=year_id,
            # 行政班跨年延续：恒钉快照行政班，查教师本人班级历史
            class_id=snapshot["class_ids"][0],
            term_id=None,
            db=db,
        )
    else:
        from app.api.students import teaching_student_profile

        result = _call_service(
            teaching_student_profile,
            person_id=args["person_id"],
            academic_year_id=year_id,
            # Q01：单班会话钉住该班（并集会话由 service 按同学科并集重建，
            # 语义与快照一致，多轮重验保证期间无漂移）。跨年时本学年班 id
            # 绝不传给其他学年（service 会 404）→ 传 None，service 按该学年
            # 同学科班集合解析（单教师应用=本人该学年历史班）。
            teaching_class_id=(
                _single_class_id(snapshot)
                if year_id == snapshot["academic_year_id"]
                else None
            ),
            term_id=None,
            db=db,
        )
    return _dump(result)


def _tool_get_exam_list(db, snapshot: dict, args: dict) -> dict:
    year_id, err = _year_id_of(db, snapshot, args)
    if err:
        return err
    # 与考试名模糊匹配（_exam_candidates）完全同源：同一 service、同一钉法
    return {"exams": _exam_candidates(db, snapshot, year_id)}


def _tool_get_exam_stats(db, snapshot: dict, args: dict) -> dict:
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    exam_name, resolved, err = _resolve_exam_name(db, snapshot, args, year_id)
    if err:
        return err
    if snapshot["mode"] == "homeroom":
        from app.api.analysis import homeroom_exam_stats

        result = _call_service(
            homeroom_exam_stats,
            exam_name=exam_name,
            academic_year_id=year_id,
            class_id=snapshot["class_ids"][0],
            term_id=None,
            db=db,
        )
    else:
        from app.api.analysis import teaching_exam_stats

        result = _call_service(
            teaching_exam_stats,
            exam_name=exam_name,
            # 跨年不传本学年班 id（service 404）；该 service 无学科参数，
            # 学科由目标学年教学班推导（单教师应用=本人该学年任教学科；
            # 多学科学年 service 422 可读报错，绝不猜测）
            teaching_class_id=(
                _single_class_id(snapshot)
                if year_id == snapshot["academic_year_id"]
                else None
            ),
            academic_year_id=year_id,
            term_id=None,
            db=db,
        )
    data = _dump(result)
    if resolved:
        data["exam_resolved"] = resolved
    return data


def _tool_get_scores_table(db, snapshot: dict, args: dict) -> dict:
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    exam_name, resolved, err = _resolve_exam_name(db, snapshot, args, year_id)
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
            academic_year_id=year_id,
            term_id=None,
            exam_name=exam_name,
            db=db,
        )
        data = _dump(result)
    else:
        # Q01：query_scores 路由不接受班参（teaching 缺省=并集），单班
        # 会话薄调它会扩成全部所教班。改按快照班级重建 ctx，经同一
        # readable_facts 门复刻端点装配（不自写 SQL、聚合前限制班级）；
        # 跨年由 _teaching_ctx_for_year 显式解析该学年同学科班集合。
        from app.api.schemas import TeachingScoreRow, TeachingScoresResponse
        from app.api.students import _metadata

        ctx = _teaching_ctx_for_year(db, snapshot, year_id)
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
    if resolved:
        data["exam_resolved"] = resolved
    return data


def _tool_get_homework_summary(db, snapshot: dict, args: dict) -> dict:
    group_by = args.get("group_by") or "week"
    if group_by not in ("day", "week", "month"):
        return {"error": "invalid_scope_param", "detail": "group_by 仅支持 day/week/month"}
    year_id, err = _year_id_of(db, snapshot, args)
    if err:
        return err

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

    same_year = year_id == snapshot["academic_year_id"]
    if snapshot["mode"] == "homeroom":
        result = _call_service(
            homework_dashboard,
            mode="homeroom",
            class_id=snapshot["class_ids"][0],
            teaching_class_id=None,
            academic_year_id=year_id,
            subject=None,
            homework_type=None,
            group_by=group_by,
            db=db,
        )
    else:
        # Q01：单班会话显式传 teaching_class_id，绝不缺省成全部所教班并集；
        # 跨年时改传 None 并钉住任教学科（该学年同学科班集合由 service 解析）
        result = _call_service(
            homework_dashboard,
            mode="teaching",
            class_id=None,
            teaching_class_id=(
                _single_class_id(snapshot) if same_year else None
            ),
            academic_year_id=year_id,
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
    year_id, err = _year_id_of(db, snapshot, args)
    if err:
        return err
    from app.api.homework import homework_student

    result = _call_service(
        homework_student,
        person_id=args["person_id"],
        mode=snapshot["mode"],
        class_id=(
            snapshot["class_ids"][0] if snapshot["mode"] == "homeroom" else None
        ),
        # Q01：teaching 单班会话钉住该班（并集会话缺省语义一致）；跨年时
        # 本学年班 id 绝不传给其他学年 → None（service 按该学年班集合解析）
        teaching_class_id=(
            _single_class_id(snapshot)
            if snapshot["mode"] == "teaching"
            and year_id == snapshot["academic_year_id"]
            else None
        ),
        academic_year_id=year_id,
        db=db,
    )
    return _dump(result)


def _tool_get_homework_correlation(db, snapshot: dict, args: dict) -> dict:
    """成绩 × 作业提交率皮尔逊相关（p5-homework §4；A01 薄封装 correlation
    service，绝不另写查询）。

    - homeroom：Y = total_type 总分名次（total_type 缺省主三门）；X = 指定
      subject 学科的作业提交率（学科必传——总分与哪科作业的关系需明确）。
    - teaching：Y = 任教学科单科班内名次；subject 钉住会话任教学科
      （模型传什么都不扩大范围）。
    - r=null（n<5 或零方差）时 caveats 注明，响应自然带"不构成因果"。
    """
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    exam_name, resolved, err = _resolve_exam_name(db, snapshot, args, year_id)
    if err:
        return err
    subject = (args.get("subject") or "").strip()
    if snapshot["mode"] == "teaching":
        subject = snapshot.get("subject") or subject
    if not subject:
        return {
            "error": "invalid_scope_param",
            "detail": "subject 必填（如 物理）；teaching 会话默认用任教学科",
        }
    from app.api.homework import homework_correlation

    result = _call_service(
        homework_correlation,
        mode=snapshot["mode"],
        class_id=(
            snapshot["class_ids"][0] if snapshot["mode"] == "homeroom" else None
        ),
        # Q01：teaching 单班会话钉住该班（并集会话缺省语义一致）；跨年时
        # 改传 None 并保留钉住的 subject（service 按该学年同学科班集合解析）
        teaching_class_id=(
            _single_class_id(snapshot)
            if snapshot["mode"] == "teaching"
            and year_id == snapshot["academic_year_id"]
            else None
        ),
        academic_year_id=year_id,
        subject=subject,
        homework_type=(args.get("homework_type") or "").strip() or None,
        exam_name=exam_name,
        total_type=(args.get("total_type") or "").strip() or "主三门",
        db=db,
    )
    data = _dump(result)
    if resolved:
        data["exam_resolved"] = resolved
    return data


def _tool_get_class_comparison(db, snapshot: dict, args: dict) -> dict:
    """教学班对比：路由 teaching_class_compare 不接受班参（恒并集），Q01
    下按快照班级重建 ctx 并复刻端点装配——直接复用端点自己的聚合助手
    （_queries/readable_facts 门），聚合前限制班级，不自写 SQL。跨年由
    _teaching_ctx_for_year 显式解析该学年同学科班集合。"""
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    exam_name, resolved, err = _resolve_exam_name(db, snapshot, args, year_id)
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

    ctx = _teaching_ctx_for_year(db, snapshot, year_id)
    class_ids = list(ctx.class_ids)
    if not _exam_exists_for_teaching(db, ctx.academic_year_id, exam_name):
        raise ResourceOutOfScope(
            "exam not found in teaching domain", details={"exam_name": exam_name}
        )
    # F08 考试维度成员口径：与端点一致按考试时点名册解析
    member_ids = _exam_member_ids(db, ctx, exam_name)
    labels = _class_labels(db, class_ids)
    classes = []
    small_sample = False
    for tc_id in class_ids:
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
    data = _dump(ClassCompareResponse(classes=classes, small_sample=small_sample))
    if resolved:
        data["exam_resolved"] = resolved
    return data


# ────────────────────── 新增 15 个只读工具的 handler ──────────────────────


def _tool_get_class_averages(db, snapshot: dict, args: dict) -> dict:
    """官方全年级班级均分表（ADR-025）：只读 workspace_class_average 导入
    事实，不用本班学生成绩反推。"""
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    exam_name, resolved, err = _resolve_exam_name(db, snapshot, args, year_id)
    if err:
        return err
    from app.api.analysis import homeroom_class_averages

    result = _call_service(
        homeroom_class_averages,
        exam_name=exam_name,
        academic_year_id=year_id,
        class_id=snapshot["class_ids"][0],  # 行政班跨年延续：恒钉本人班
        term_id=None,
        db=db,
    )
    data = _dump(result)
    if resolved:
        data["exam_resolved"] = resolved
    return data


def _tool_get_weekly_focus(db, snapshot: dict, args: dict) -> dict:
    """本周关注（四维信号加权）：“本周”天然以当前日期为基准，不收时间参数。"""
    from app.api.analysis import homeroom_weekly_focus

    result = _call_service(
        homeroom_weekly_focus,
        academic_year_id=snapshot["academic_year_id"],
        class_id=snapshot["class_ids"][0],
        term_id=None,
        db=db,
    )
    return _dump(result)


def _tool_get_exam_focus(db, snapshot: dict, args: dict) -> dict:
    """单场考试重点关注：进退步/波动/偏科/临界。"""
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    exam_name, resolved, err = _resolve_exam_name(db, snapshot, args, year_id)
    if err:
        return err
    from app.api.analysis import homeroom_exam_focus

    result = _call_service(
        homeroom_exam_focus,
        exam_name=exam_name,
        academic_year_id=year_id,
        class_id=snapshot["class_ids"][0],
        term_id=None,
        db=db,
    )
    data = _dump(result)
    if resolved:
        data["exam_resolved"] = resolved
    return data


def _tool_get_student_trends(db, snapshot: dict, args: dict) -> dict:
    """跨学年分段趋势：响应按学年分组输出（ctx 学年只定成员与投影门，
    快照学年即可）；person 已过快照成员守卫，service 内再校验一次。"""
    guard = _member_guard(snapshot, args.get("person_id"))
    if guard:
        return guard
    from app.api.analysis import homeroom_trends

    result = _call_service(
        homeroom_trends,
        person_id=args["person_id"],
        academic_year_id=snapshot["academic_year_id"],
        class_id=snapshot["class_ids"][0],
        term_id=None,
        db=db,
    )
    return _dump(result)


def _tool_get_rank_metrics(db, snapshot: dict, args: dict) -> dict:
    """可用名次指标发现（frequency 模式，供 get_rank_frequency 选 metric）。"""
    from app.api.analysis import homeroom_rank_metrics

    result = _call_service(
        homeroom_rank_metrics,
        mode="frequency",
        academic_year_id=snapshot["academic_year_id"],
        class_id=snapshot["class_ids"][0],
        term_id=None,
        db=db,
    )
    return _dump(result)


def _tool_get_rank_frequency(db, snapshot: dict, args: dict) -> dict:
    """名次/百分位/等级分频次统计：exam_names 为逗号分隔多场考试名，每项
    经 _resolve_exam_name 逐项解析（任一项多命中/零命中 → 注明「第 N 项」
    的可读错误）；任一项模糊命中时响应顶层 exam_resolved 注记解析后全名
    序列（逗号连接，与入参同序）。"""
    metric = (args.get("metric") or "").strip()
    if not metric:
        return {
            "error": "invalid_scope_param",
            "detail": "metric 必填（先用 get_rank_metrics 查可用指标）",
        }
    exam_names = (args.get("exam_names") or "").strip()
    if not exam_names:
        return {
            "error": "invalid_scope_param",
            "detail": 'exam_names 必填，多场考试用英文逗号分隔（如 "2025期中,2025期末"）',
        }
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    items = [item.strip() for item in exam_names.split(",") if item.strip()]
    resolved_items: List[str] = []
    fuzzy_resolved = False
    for index, item in enumerate(items, start=1):
        one, note, item_err = _resolve_exam_name(
            db, snapshot, {"exam_name": item}, year_id
        )
        if item_err:
            # 任一项多命中/零命中：注明第 N 项后原样返回可读错误
            item_err = dict(item_err)
            item_err["detail"] = f"exam_names 第 {index} 项「{item}」：" + str(
                item_err.get("detail")
            )
            return item_err
        resolved_items.append(one)
        if note:
            fuzzy_resolved = True
    from app.api.analysis import homeroom_rank_frequency

    result = _call_service(
        homeroom_rank_frequency,
        metric=metric,
        exam_names=",".join(resolved_items),
        academic_year_id=year_id,
        class_id=snapshot["class_ids"][0],
        term_id=None,
        db=db,
    )
    data = _dump(result)
    if fuzzy_resolved:
        data["exam_resolved"] = ",".join(resolved_items)
    return data


def _tool_get_rank_range(db, snapshot: dict, args: dict) -> dict:
    """年级名次区间筛选（如 主三门 1–100 名名单）。"""
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    exam_name, resolved, err = _resolve_exam_name(db, snapshot, args, year_id)
    if err:
        return err
    metric = (args.get("metric") or "").strip()
    if not metric:
        return {
            "error": "invalid_scope_param",
            "detail": "metric 必填（先用 get_rank_metrics 查可用指标）",
        }
    rank_min, err = _optional_int(args, "rank_min")
    if err:
        return err
    rank_max, err = _optional_int(args, "rank_max")
    if err:
        return err
    from app.api.analysis import homeroom_rank_range

    result = _call_service(
        homeroom_rank_range,
        exam_name=exam_name,
        metric=metric,
        rank_min=rank_min if rank_min is not None else 1,
        rank_max=rank_max if rank_max is not None else 100,
        academic_year_id=year_id,
        class_id=snapshot["class_ids"][0],
        term_id=None,
        db=db,
    )
    data = _dump(result)
    if resolved:
        data["exam_resolved"] = resolved
    return data


def _tool_get_rank_distribution(db, snapshot: dict, args: dict) -> dict:
    """单场考试名次分布（每 40 名一档）。"""
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    exam_name, resolved, err = _resolve_exam_name(db, snapshot, args, year_id)
    if err:
        return err
    from app.api.analysis import homeroom_rank_distribution

    result = _call_service(
        homeroom_rank_distribution,
        exam_name=exam_name,
        academic_year_id=year_id,
        class_id=snapshot["class_ids"][0],
        term_id=None,
        db=db,
    )
    data = _dump(result)
    if resolved:
        data["exam_resolved"] = resolved
    return data


def _tool_get_score_bands(db, snapshot: dict, args: dict) -> dict:
    """高分/临界/薄弱段位分布。阈值口径为年级名次：metric 缺省 total
    （score 模式当前必 409 不可计算——绝不把名次阈值当分数比较）；subject
    缺省由 service 报可读错误（total 模式传 total_type 如 主三门）。"""
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    exam_name, resolved, err = _resolve_exam_name(db, snapshot, args, year_id)
    if err:
        return err
    from app.api.analysis import homeroom_bands

    result = _call_service(
        homeroom_bands,
        exam_name=exam_name,
        subject=(args.get("subject") or "").strip() or None,
        metric=(args.get("metric") or "").strip() or "total",
        academic_year_id=year_id,
        class_id=snapshot["class_ids"][0],
        term_id=None,
        db=db,
    )
    data = _dump(result)
    if resolved:
        data["exam_resolved"] = resolved
    return data


def _tool_get_homework_warnings(db, snapshot: dict, args: dict) -> dict:
    """作业缺交/连续缺交预警（service 默认区间=当前学期；跨年查询传对应
    academic_year_id；schema 对模型暴露 from/to，handler 映射为 service 的
    date_from/date_to）。"""
    year_id, err = _year_id_of(db, snapshot, args)
    if err:
        return err
    range_from, err = _optional_iso_date(args, "from")
    if err:
        return err
    range_to, err = _optional_iso_date(args, "to")
    if err:
        return err
    min_missing, err = _optional_int(args, "min_missing")
    if err:
        return err
    min_streak, err = _optional_int(args, "min_streak")
    if err:
        return err
    subject, err = _teaching_subject_guard(
        snapshot, (args.get("subject") or "").strip() or None
    )
    if err:
        return err
    from app.api.homework import homework_warnings

    result = _call_service(
        homework_warnings,
        mode=snapshot["mode"],
        class_id=(
            snapshot["class_ids"][0] if snapshot["mode"] == "homeroom" else None
        ),
        # Q01：teaching 单班会话钉住该班；跨年时本学年班 id 绝不传给其他
        # 学年 → None，service 按钉住的 subject 解析该学年班集合
        teaching_class_id=(
            _single_class_id(snapshot)
            if snapshot["mode"] == "teaching"
            and year_id == snapshot["academic_year_id"]
            else None
        ),
        academic_year_id=year_id,
        subject=subject,
        homework_type=(args.get("homework_type") or "").strip() or None,
        min_missing=min_missing if min_missing is not None else 2,
        min_streak=min_streak,
        date_from=range_from.isoformat() if range_from else None,
        date_to=range_to.isoformat() if range_to else None,
        db=db,
    )
    return _dump(result)


def _tool_get_homework_assignments(db, snapshot: dict, args: dict) -> dict:
    """作业批次分页列表（assigned_date 降序，返回含 assignment_id，配合
    get_homework_assignment_detail 查逐人明细）；schema 暴露 from/to，
    handler 映射为 service 的 from_date/to_date。"""
    range_from, err = _optional_iso_date(args, "from")
    if err:
        return err
    range_to, err = _optional_iso_date(args, "to")
    if err:
        return err
    limit, err = _optional_int(args, "limit")
    if err:
        return err
    subject, err = _teaching_subject_guard(
        snapshot, (args.get("subject") or "").strip() or None
    )
    if err:
        return err
    from app.api.homework import homework_assignments_list

    result = _call_service(
        homework_assignments_list,
        mode=snapshot["mode"],
        class_id=(
            snapshot["class_ids"][0] if snapshot["mode"] == "homeroom" else None
        ),
        teaching_class_id=(
            _single_class_id(snapshot) if snapshot["mode"] == "teaching" else None
        ),
        academic_year_id=snapshot["academic_year_id"],
        subject=subject,
        homework_type=(args.get("homework_type") or "").strip() or None,
        from_date=range_from.isoformat() if range_from else None,
        to_date=range_to.isoformat() if range_to else None,
        limit=limit if limit is not None else 100,
        db=db,
    )
    return _dump(result)


def _tool_get_homework_assignment_detail(db, snapshot: dict, args: dict) -> dict:
    """单批次逐人明细；归属校验由 service 的 _load_accessible_assignment
    承担（越界 404 → 统一转模型可读错误文本）。"""
    assignment_id, err = _int_or_error(args.get("assignment_id"), "assignment_id")
    if err:
        return err
    from app.api.homework import homework_assignment_detail

    result = _call_service(
        homework_assignment_detail,
        assignment_id=assignment_id,
        mode=snapshot["mode"],
        class_id=(
            snapshot["class_ids"][0] if snapshot["mode"] == "homeroom" else None
        ),
        teaching_class_id=(
            _single_class_id(snapshot) if snapshot["mode"] == "teaching" else None
        ),
        academic_year_id=snapshot["academic_year_id"],
        db=db,
    )
    return _dump(result)


def _tool_get_student_notes(db, snapshot: dict, args: dict) -> dict:
    """成长/谈话档案（仅教师手动档案，系统内部标记由 service 排除）；
    支持学年与学期相对时间（year_offset/term_offset）。

    学期参数语义（时间语义批次修正）：term_id/term_offset 指向
    ws_homework_semester（学期设置页同一事实源）。service list_notes 的
    term_id 属 P1 学期体系（真实部署无数据、无维护入口），不再透传——
    解析出作业学期后在工具层按学期起止日期过滤 service 返回的档案 date
    （与 get_homework_summary 的 from/to 工具层过滤同一模式）。"""
    guard = _member_guard(snapshot, args.get("person_id"))
    if guard:
        return guard
    year_id, err = _year_id_of(db, snapshot, args)
    if err:
        return err
    term_id = _resolve_term_arg(db, snapshot, args)
    if isinstance(term_id, dict):
        return term_id
    term = None
    if term_id is not None:
        terms = _ordered_terms(db)
        term = next((item for item in terms if item["id"] == term_id), None)
        if term is None:
            catalog = "、".join(item["name"] for item in terms) or "（无）"
            return {
                "error": "invalid_scope_param",
                "detail": f"term_id={term_id} 不存在；可用学期：{catalog}",
            }
    from app.api.students_mgmt import list_notes

    result = _call_service(
        list_notes,
        mode=snapshot["mode"],
        person_id=args["person_id"],
        academic_year_id=year_id,
        # 与 Q01 钉班一致：homeroom 恒钉行政班；teaching 单班钉班、
        # 跨年不传本学年班 id
        class_id=(
            snapshot["class_ids"][0] if snapshot["mode"] == "homeroom" else None
        ),
        teaching_class_id=(
            _single_class_id(snapshot)
            if snapshot["mode"] == "teaching"
            and year_id == snapshot["academic_year_id"]
            else None
        ),
        # P1 Term 表学期真实无数据：绝不透传 term_id（过滤见 docstring）
        term_id=None,
        subject=None,
        db=db,
    )
    data = _dump(result)
    if term is not None:
        start = date.fromisoformat(term["start_date"])
        end = date.fromisoformat(term["end_date"])
        kept = []
        for note in data.get("notes", []):
            try:
                note_date = date.fromisoformat(note["date"])
            except (KeyError, TypeError, ValueError):
                continue
            if start <= note_date <= end:
                kept.append(note)
        data["notes"] = kept
        # 附上生效学期（模型可核对区间；不传学期参数时无此键=不过滤；
        # term_id 直传假期条目时 vacation=true，区间即假期起止）
        data["term"] = {
            "id": term["id"],
            "name": term["name"],
            "start_date": term["start_date"],
            "end_date": term["end_date"],
            "vacation": _is_vacation_term(term["name"]),
        }
    return data


def _tool_get_academic_years(db, snapshot: dict, args: dict) -> dict:
    """学年+学期目录（时间语义锚点）：学期源 = ws_homework_semester
    （学期设置页同一事实源，学期名即用户维护名称如「2026学年第一学期」，
    模型可按名称直接匹配取起止日期）；每条学年/学期带 is_current 与
    year_offset/term_offset 标注。假期条目（暑假/寒假/假期）仍列出并标
    ``vacation: true``，但 ``term_offset: null`` 不占编号——「上/下学期」
    偏移只在非假期子序列上计数（两者共用同一实现）。作业学期挂的
    academic_year_id 对不上学年目录时不静默丢弃，收入顶层
    unmatched_semesters。"""
    years = _ordered_years(db)
    terms = _ordered_terms(db)
    work = [term for term in terms if not _is_vacation_term(term["name"])]
    anchor_year_id = snapshot.get("academic_year_id")
    anchor_year_index = next(
        (i for i, year in enumerate(years) if year["id"] == anchor_year_id), None
    )
    anchor_term_index = _term_anchor_index(work, _as_of(snapshot).isoformat())
    # 偏移只在非假期子序列上计数；假期条目查不到映射 → term_offset=null
    term_offset_by_id = {
        term["id"]: index - anchor_term_index
        for index, term in enumerate(work)
    }
    current_term_id = (
        work[anchor_term_index]["id"] if anchor_term_index is not None else None
    )
    known_year_ids = {year["id"] for year in years}
    unmatched = [term for term in terms if term["academic_year_id"] not in known_year_ids]

    def _term_view(term: dict) -> dict:
        return {
            "id": term["id"],
            "name": term["name"],
            "start_date": term["start_date"],
            "end_date": term["end_date"],
            "vacation": _is_vacation_term(term["name"]),
            "is_current": term["id"] == current_term_id,
            "term_offset": term_offset_by_id.get(term["id"]),
        }

    return {
        "current_date": snapshot.get("as_of"),
        "current_academic_year_id": anchor_year_id,
        "current_term_id": current_term_id,
        "years": [
            {
                "id": year["id"],
                "name": year["name"],
                "start_date": year["start_date"],
                "end_date": year["end_date"],
                "is_current": year["id"] == anchor_year_id,
                "year_offset": (
                    index - anchor_year_index
                    if anchor_year_index is not None
                    else None
                ),
                "terms": [
                    _term_view(term)
                    for term in terms
                    if term["academic_year_id"] == year["id"]
                ],
            }
            for index, year in enumerate(years)
        ],
        # 挂不上学年目录的作业学期（如目录外学年被删的孤儿行）：原样列出，
        # 绝不静默丢弃——模型可据此告知用户目录与学期设置不一致
        "unmatched_semesters": [
            {
                "id": term["id"],
                "academic_year_id": term["academic_year_id"],
                "name": term["name"],
                "start_date": term["start_date"],
                "end_date": term["end_date"],
                "is_current": term["is_current"],
                "vacation": _is_vacation_term(term["name"]),
                "term_offset": term_offset_by_id.get(term["id"]),
            }
            for term in unmatched
        ],
    }


def _tool_get_exam_students(db, snapshot: dict, args: dict) -> dict:
    """单场考试逐人分科+总分矩阵（含跨域冲突注记）——“全班这次考多少分”
    的宽表视角。与 get_scores_table 分工：后者逐人分数行（教学域单科），
    本工具班主任域分科矩阵并附名次注记列。"""
    year_id, err = _exam_year_id_of(db, snapshot, args)
    if err:
        return err
    exam_name, resolved, err = _resolve_exam_name(db, snapshot, args, year_id)
    if err:
        return err
    from app.api.analysis import homeroom_exam_students

    result = _call_service(
        homeroom_exam_students,
        exam_name=exam_name,
        academic_year_id=year_id,
        class_id=snapshot["class_ids"][0],
        term_id=None,
        db=db,
    )
    data = _dump(result)
    subject = (args.get("subject") or "").strip()
    if subject and "error" not in data:
        # 按学科收窄视图（投影与冲突裁决已在 service 内执行，仅收窄展示）
        for student in data.get("students", []):
            student["scores"] = {
                key: value
                for key, value in (student.get("scores") or {}).items()
                if key == subject
            }
    if resolved:
        data["exam_resolved"] = resolved
    return data


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
# exam_name 统一模糊匹配语义（工具层 _resolve_exam_name 同一实现）：
# 精确命中直接用；唯一子串命中自动解析并在响应 exam_resolved 注明；
# 多命中返回候选清单；零命中报不在范围并列出可用考试。
_EXAM_NAME_DESC = (
    "考试名称，支持部分名称模糊匹配（如“期中”）；唯一命中自动解析"
    "并在响应 exam_resolved 注明，多命中返回候选清单"
)
_EXAM_SCHEMA = {
    "type": "object",
    "properties": {"exam_name": {"type": "string", "description": _EXAM_NAME_DESC}},
    "required": ["exam_name"],
}

# 跨学年相对时间参数（描述统一：二选一、缺省本学年；服务端 _resolve_year_arg
# 唯一解析）。既有 9 工具与多数新工具共用。
_YEAR_ARGS_SCHEMA = {
    "academic_year_id": {
        "type": "integer",
        "description": "可选，学年 ID；与 year_offset 二选一，缺省当前学年",
    },
    "year_offset": {
        "type": "integer",
        "description": "可选，相对学年偏移：0=本学年，-1=上学年，-2=上上学年；与 academic_year_id 二选一",
    },
}
# 学期相对时间参数（仅 get_student_notes）。学期 = ws_homework_semester
# （学期设置页同一 ID 体系）。
_TERM_ARGS_SCHEMA = {
    "term_id": {
        "type": "integer",
        "description": "可选，学期 ID（ws_homework_semester.id，学期设置页同一 ID）；与 term_offset 二选一",
    },
    "term_offset": {
        "type": "integer",
        "description": "可选，相对学期偏移：0=本学期，-1=上一学期（假期条目如暑假不占编号，-1 跳过假期；跨学年自动落到上一学年第二学期）；与 term_id 二选一",
    },
}


def _schema_with_year_args(properties: dict, required: Optional[list] = None) -> dict:
    """在既有 properties 上追加跨学年参数（保持既有键不变）。"""
    schema = {
        "type": "object",
        "properties": {**properties, **_YEAR_ARGS_SCHEMA},
    }
    if required:
        schema["required"] = list(required)
    return schema


TOOL_REGISTRY: List[WsToolSpec] = [
    WsToolSpec(
        name="search_students",
        description="按姓名/学号关键字搜索当前会话范围内的学生名单（含 person_id/姓名/学号/座号）",
        input_schema=_schema_with_year_args(
            {"q": {"type": "string", "description": "姓名或学号关键字，可留空返回全部"}}
        ),
        domains=("homeroom", "teaching"),
        handler=_tool_search_students,
    ),
    WsToolSpec(
        name="get_student_profile",
        description="查询单个学生的成绩画像（学科/考试/分数；仅当前会话域内学科口径）",
        input_schema=_schema_with_year_args(
            {"person_id": _PERSON_SCHEMA["properties"]["person_id"]},
            required=["person_id"],
        ),
        domains=("homeroom", "teaching"),
        handler=_tool_get_student_profile,
    ),
    WsToolSpec(
        name="get_exam_list",
        description="列出当前会话范围内可读的考试（名称/日期/科目集合）",
        input_schema=_schema_with_year_args({}),
        domains=("homeroom", "teaching"),
        handler=_tool_get_exam_list,
    ),
    WsToolSpec(
        name="get_exam_stats",
        description="查询单场考试的班内统计（均分/最高最低/有效与缺考人数；班主任视角含全科与总分，教师视角仅任教学科）",
        input_schema=_schema_with_year_args(
            {"exam_name": _EXAM_SCHEMA["properties"]["exam_name"]},
            required=["exam_name"],
        ),
        domains=("homeroom", "teaching"),
        handler=_tool_get_exam_stats,
    ),
    WsToolSpec(
        name="get_scores_table",
        description="查询单场考试的逐人成绩表（homeroom 含全科+总分，teaching 仅任教学科）",
        input_schema=_schema_with_year_args(
            {
                "exam_name": {"type": "string", "description": _EXAM_NAME_DESC},
                "subject": {
                    "type": "string",
                    "description": "可选，按学科过滤（teaching 会话仅接受任教学科）",
                },
            },
            required=["exam_name"],
        ),
        domains=("homeroom", "teaching"),
        handler=_tool_get_scores_table,
    ),
    WsToolSpec(
        name="get_homework_summary",
        description="按日/周/月汇总作业批次与缺交情况，可用 from/to（ISO 日期）限定区间",
        input_schema=_schema_with_year_args(
            {
                "from": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                "to": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                "group_by": {"type": "string", "enum": ["day", "week", "month"]},
            }
        ),
        domains=("homeroom", "teaching"),
        handler=_tool_get_homework_summary,
    ),
    WsToolSpec(
        name="get_homework_student",
        description="查询单个学生的作业事件流与连续缺交情况",
        input_schema=_schema_with_year_args(
            {"person_id": _PERSON_SCHEMA["properties"]["person_id"]},
            required=["person_id"],
        ),
        domains=("homeroom", "teaching"),
        handler=_tool_get_homework_student,
    ),
    WsToolSpec(
        name="get_homework_correlation",
        description=(
            "计算「作业提交率 × 考试名次」的皮尔逊相关，回答"
            "“交作业越勤的学生名次是否越靠前”“缺交和成绩有没有关系”。"
            "homeroom 口径 Y=指定总分口径名次（total_type 默认主三门，需传 subject "
            "指定哪科作业）；teaching 口径 Y=任教学科单科班内名次（固定会话学科）。"
            "X=作业提交率，可按 homework_type 过滤。exam_name 必传"
            "（支持部分名称模糊匹配，如“期中”）。相关仅描述统计关联、"
            "不构成因果；n<5 或零方差时 r 为 null。"
        ),
        input_schema=_schema_with_year_args(
            {
                "exam_name": {"type": "string", "description": _EXAM_NAME_DESC},
                "subject": {
                    "type": "string",
                    "description": "作业学科（homeroom 必填如 物理；teaching 固定任教学科）",
                },
                "homework_type": {
                    "type": "string",
                    "description": "可选，按作业种类过滤提交率（如 练习册）",
                },
                "total_type": {
                    "type": "string",
                    "description": "homeroom 可选，总分口径（默认主三门）",
                },
            },
            required=["exam_name"],
        ),
        domains=("homeroom", "teaching"),
        handler=_tool_get_homework_correlation,
    ),
    WsToolSpec(
        name="get_class_comparison",
        description="按所教教学班对比单场考试的班均分（教师专属视角）",
        input_schema=_schema_with_year_args(
            {"exam_name": _EXAM_SCHEMA["properties"]["exam_name"]},
            required=["exam_name"],
        ),
        domains=("teaching",),
        handler=_tool_get_class_comparison,
    ),
    # ────────────────── 新增 15 个只读工具（跨学年时间语义） ──────────────────
    WsToolSpec(
        name="get_class_averages",
        description=(
            "查询单场考试的官方全年级班级均分表（随考试导入的班级均分事实，"
            "非本班成绩反推）：各班各科/各总分口径均分、总分名次、本班班号；"
            "口径由该场考试实际导入内容动态生成。班主任专属。"
        ),
        input_schema=_schema_with_year_args(
            {"exam_name": {"type": "string", "description": _EXAM_NAME_DESC}},
            required=["exam_name"],
        ),
        domains=("homeroom",),
        handler=_tool_get_class_averages,
    ),
    WsToolSpec(
        name="get_weekly_focus",
        description=(
            "查询班主任本周关注名单（四维信号加权：连续缺交、本周缺交激增、"
            "最近考试进退步/波动/偏科/临界、谈话跟进待办）；“本周”天然以"
            "当前日期为基准。班主任专属。"
        ),
        input_schema={"type": "object", "properties": {}},
        domains=("homeroom",),
        handler=_tool_get_weekly_focus,
    ),
    WsToolSpec(
        name="get_exam_focus",
        description=(
            "查询单场考试的重点关注学生：明显进退步、波动风险、临界/薄弱段、"
            "严重偏科、稳定优秀（含前次名次与名次变化）。班主任专属。"
        ),
        input_schema=_schema_with_year_args(
            {"exam_name": {"type": "string", "description": _EXAM_NAME_DESC}},
            required=["exam_name"],
        ),
        domains=("homeroom",),
        handler=_tool_get_exam_focus,
    ),
    WsToolSpec(
        name="get_student_trends",
        description=(
            "查询单个学生的跨学年分段成绩趋势（按学年分组的历次考试"
            "分数/等第/名次点）。班主任专属。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "person_id": _PERSON_SCHEMA["properties"]["person_id"]
            },
            "required": ["person_id"],
        },
        domains=("homeroom",),
        handler=_tool_get_student_trends,
    ),
    WsToolSpec(
        name="get_rank_metrics",
        description=(
            "查询当前年级可用的名次统计指标清单（metric 值/名称/口径类型），"
            "供 get_rank_frequency / get_rank_range 选参。班主任专属。"
        ),
        input_schema={"type": "object", "properties": {}},
        domains=("homeroom",),
        handler=_tool_get_rank_metrics,
    ),
    WsToolSpec(
        name="get_rank_frequency",
        description=(
            "按指标统计多场考试的名次档/百分位/等第频次（每生每档次数），"
            "exam_names 为逗号分隔的多场考试名（如 \"2025期中,2025期末\"），"
            "每项支持部分名称模糊匹配。"
            "metric 先用 get_rank_metrics 查。班主任专属。"
        ),
        input_schema=_schema_with_year_args(
            {
                "metric": {
                    "type": "string",
                    "description": "指标（get_rank_metrics 返回的 value，如 total:主三门）",
                },
                "exam_names": {
                    "type": "string",
                    "description": (
                        "逗号分隔的多场考试名，如 \"2025期中,2025期末\"；每项支持部分名称"
                        "模糊匹配（如“期中”），任一项多命中/零命中会报错并注明第几项，"
                        "解析成功且含模糊项时响应 exam_resolved 注明逐项解析结果"
                    ),
                },
            },
            required=["metric", "exam_names"],
        ),
        domains=("homeroom",),
        handler=_tool_get_rank_frequency,
    ),
    WsToolSpec(
        name="get_rank_range",
        description=(
            "筛选举年名次落在 [rank_min, rank_max] 区间的学生（如 主三门 "
            "1–100 名名单），含班内名次换算。metric 先用 get_rank_metrics 查。"
            "班主任专属。"
        ),
        input_schema=_schema_with_year_args(
            {
                "exam_name": {"type": "string", "description": _EXAM_NAME_DESC},
                "metric": {
                    "type": "string",
                    "description": "指标（get_rank_metrics 返回的 value）",
                },
                "rank_min": {"type": "integer", "description": "可选，名次下限（默认 1）"},
                "rank_max": {"type": "integer", "description": "可选，名次上限（默认 100）"},
            },
            required=["exam_name", "metric"],
        ),
        domains=("homeroom",),
        handler=_tool_get_rank_range,
    ),
    WsToolSpec(
        name="get_rank_distribution",
        description=(
            "查询单场考试全班的年级名次分布（总分各口径、每 40 名一档计数）。"
            "班主任专属。"
        ),
        input_schema=_schema_with_year_args(
            {"exam_name": {"type": "string", "description": _EXAM_NAME_DESC}},
            required=["exam_name"],
        ),
        domains=("homeroom",),
        handler=_tool_get_rank_distribution,
    ),
    WsToolSpec(
        name="get_score_bands",
        description=(
            "查询高分段/临界段/薄弱段学生名单（段位阈值口径为年级名次；"
            "subject 传总分口径如 主三门）。当前范围无名次数据时返回不可"
            "计算错误，绝不把名次阈值当分数比较。班主任专属。"
        ),
        input_schema=_schema_with_year_args(
            {
                "exam_name": {"type": "string", "description": _EXAM_NAME_DESC},
                "subject": {
                    "type": "string",
                    "description": "总分口径（total_type，如 主三门）；缺省报可读错误",
                },
                "metric": {
                    "type": "string",
                    "enum": ["total", "score"],
                    "description": "可选，段位口径（默认 total；score 当前不可计算）",
                },
            },
            required=["exam_name"],
        ),
        domains=("homeroom",),
        handler=_tool_get_score_bands,
    ),
    WsToolSpec(
        name="get_homework_warnings",
        description=(
            "查询作业缺交/连续缺交/负面评价/忘带预警名单（按人聚合，"
            "含最近缺交记录）；可用 subject/min_missing/min_streak 过滤，"
            "from/to（ISO 日期）限定区间，缺省为当前学期。"
        ),
        input_schema=_schema_with_year_args(
            {
                "subject": {
                    "type": "string",
                    "description": "可选，按学科过滤（teaching 会话仅接受任教学科）",
                },
                "homework_type": {
                    "type": "string",
                    "description": "可选，按作业种类过滤",
                },
                "min_missing": {"type": "integer", "description": "可选，最少缺交次数（默认 2）"},
                "min_streak": {"type": "integer", "description": "可选，最小连续缺交次数"},
                "from": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                "to": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
            }
        ),
        domains=("homeroom", "teaching"),
        handler=_tool_get_homework_warnings,
    ),
    WsToolSpec(
        name="get_homework_assignments",
        description=(
            "查询作业批次列表（按布置日期降序，含已交/缺交/请假计数与 "
            "assignment_id）；可用 subject/homework_type/from/to/limit 过滤。"
            "拿到 assignment_id 后可用 get_homework_assignment_detail 查逐人明细。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "description": "可选，按学科过滤（teaching 会话仅接受任教学科）",
                },
                "homework_type": {
                    "type": "string",
                    "description": "可选，按作业种类/名称过滤（如 练习册）",
                },
                "from": {"type": "string", "description": "起始日期 YYYY-MM-DD"},
                "to": {"type": "string", "description": "结束日期 YYYY-MM-DD"},
                "limit": {"type": "integer", "description": "可选，返回条数（默认 100）"},
            },
        },
        domains=("homeroom", "teaching"),
        handler=_tool_get_homework_assignments,
    ),
    WsToolSpec(
        name="get_homework_assignment_detail",
        description=(
            "查询单个作业批次的逐人明细（应交名单与每人的收交状态/评价/"
            "出勤异常/忘带标记）；assignment_id 来自 get_homework_assignments。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "assignment_id": {
                    "type": "integer",
                    "description": "作业批次 ID（get_homework_assignments 返回）",
                }
            },
            "required": ["assignment_id"],
        },
        domains=("homeroom", "teaching"),
        handler=_tool_get_homework_assignment_detail,
    ),
    WsToolSpec(
        name="get_student_notes",
        description=(
            "查询单个学生的成长/谈话档案（教师手动记录：谈话/观察/家访/"
            "家长沟通/奖惩等，按日期降序）；支持学年与学期相对时间参数。"
        ),
        input_schema={
            "type": "object",
            "properties": {
                "person_id": _PERSON_SCHEMA["properties"]["person_id"],
                **_YEAR_ARGS_SCHEMA,
                **_TERM_ARGS_SCHEMA,
            },
            "required": ["person_id"],
        },
        domains=("homeroom", "teaching"),
        handler=_tool_get_student_notes,
    ),
    WsToolSpec(
        name="get_academic_years",
        description=(
            "查询全部学年与学期目录（含起止日期、是否当前、year_offset/"
            "term_offset 相对偏移标注）；学期即学期设置页维护的作业学期，"
            "名称形如「2026学年第一学期」，可按名称直接匹配取起止日期。"
            "用于把「上学年/上学期」等相对时间换算成参数，或取学期起止"
            "日期换算作业区间 from/to。"
        ),
        input_schema={"type": "object", "properties": {}},
        domains=("homeroom", "teaching"),
        handler=_tool_get_academic_years,
    ),
    WsToolSpec(
        name="get_exam_students",
        description=(
            "查询单场考试全班逐人「分科+总分」矩阵（每人各科分数与各总分"
            "口径一行的宽表，含跨域冲突注记）；与 get_scores_table 分工："
            "后者逐人分数行，本工具班主任分科矩阵含名次注记。班主任专属。"
        ),
        input_schema=_schema_with_year_args(
            {
                "exam_name": {"type": "string", "description": _EXAM_NAME_DESC},
                "subject": {
                    "type": "string",
                    "description": "可选，预留按学科收窄（当前矩阵已含全科）",
                },
            },
            required=["exam_name"],
        ),
        domains=("homeroom",),
        handler=_tool_get_exam_students,
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
