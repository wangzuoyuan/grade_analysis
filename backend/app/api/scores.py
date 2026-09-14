"""/api/v1 成绩查询（契约 §1.4 + §1.4.1；v2.1/F09 统一可读事实口径）。

- 两种模式都经 _queries.readable_facts 取数：本域事实 + 经 §1.4.1 五
  条件门的对侧投影行 + 冲突规则，不自建第二套投影逻辑。
- homeroom 模式：本班全科+总分（source_domain="homeroom"）；active link
  下经统一投影门的 teaching 域任教学科事实按冲突规则入响应：H 域无该场
  → 投影 T 行（source_domain="teaching"，person_id 用 H 域身份）；两域
  均有且值不同 → 保留 H 行并附 shared_conflict，绝不静默取 T 值（R5）。
- teaching 模式：仅任教学科；行结构无 total_type 键（用独立模型），
  绝无其他学科/总分，绝无 H 域学生。linked 学生 H 域同学科事实经同一
  投影门反向投影（T 域已有同场则不投影、不附字段）。
- 无关教学班（如 T8）的行在任何 homeroom 响应中不可见。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api import _queries as q
from app.api import current_teacher_id, domain_endpoint
from app.api.schemas import ScoresResponse, ScoreRow, TeachingScoreRow, TeachingScoresResponse
from app.api.students import _metadata
from app.core.context import VALID_MODES, resolve_workspace_context
from app.core.errors import InvalidScopeParam
from app.db.models import get_db

router = APIRouter(tags=["scores"])


@router.get("/scores")
@domain_endpoint
def query_scores(
    mode: Optional[str] = None,
    academic_year_id: Optional[int] = None,
    term_id: Optional[int] = None,
    exam_name: Optional[str] = None,
    db: Session = Depends(get_db),
) -> "ScoresResponse | TeachingScoresResponse":
    """两种模式的双形态响应：homeroom=ScoresResponse（全科+总分行），
    teaching=TeachingScoresResponse（仅任教学科行，无 total_type 键）。
    用返回类型联合而非 response_model：pydantic v2 智能联合对【模型实例】
    先做 isinstance 短路，不会给 teaching 行补 total_type 默认值；
    OpenAPI 则以 anyOf 呈现两种模型（各自 json_schema_extra 带 H6/T6/T8
    样例）。"""
    if mode is None or mode not in VALID_MODES:
        raise InvalidScopeParam(
            "mode must be 'homeroom' or 'teaching'", details={"param": "mode"}
        )
    teacher_id = current_teacher_id(db)

    if mode == "homeroom":
        ctx = resolve_workspace_context(
            db,
            teacher_id,
            "homeroom",
            {"academic_year_id": academic_year_id, "term_id": term_id},
        )
        # 统一可读事实口径（契约 p3 §1.4 v2.1/F09）：本域事实 + 经 §1.4.1
        # 五条件门的 teaching 投影行 + 冲突注记，全在 readable_facts 内
        # 裁决（考试时点成员交集、冲突保留本域值，绝不静默取 T 值）。
        entries = q.readable_facts(db, ctx, exam_name)

        rows = []
        for e in sorted(
            entries,
            key=lambda x: (
                x.person_id,
                x.fact.subject or "",
                x.fact.total_type or "",
            ),
        ):
            row = ScoreRow(
                person_id=e.person_id,
                name=None,  # 统一在下方补名
                subject=e.fact.subject,
                score=e.fact.score,  # 缺考保持 null，绝不转 0
                grade_score=e.fact.grade_score,
                total_type=e.fact.total_type,
                source_domain=e.source_domain,
            )
            if e.conflict is not None:
                row.shared_conflict = e.conflict
            rows.append(row)
        all_ids = sorted({r.person_id for r in rows})
        name_map = q.names_for(db, all_ids)
        for r in rows:
            r.name = name_map.get(r.person_id)
        return ScoresResponse(
            metadata=_metadata(
                ctx, fact_revisions=[e.fact.data_revision for e in entries]
            ),
            rows=rows,
        )

    # teaching 模式：仅任教学科，"全部所教班"为同学年同学科成员并集。
    # 统一可读事实口径（F09）：T 本域行 + linked 学生 H 域同学科事实经
    # 同一投影门反向投影（T 域已有同场则不投影、不附字段）。
    params, subject, class_ids = q.build_teaching_params(db, academic_year_id, None, None, term_id)
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    entries = q.readable_facts(db, ctx, exam_name)

    rows = [
        TeachingScoreRow(
            person_id=e.person_id,
            name=None,
            subject=e.fact.subject,
            score=e.fact.score,  # 缺考保持 null
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
    return TeachingScoresResponse(
        metadata=_metadata(
            ctx, fact_revisions=[e.fact.data_revision for e in entries]
        ),
        rows=rows,
    )
