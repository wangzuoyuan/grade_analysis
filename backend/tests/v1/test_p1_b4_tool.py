"""P1-B4 纵向切片——AI 工具 get_diagnosis_summary 测试
（契约 docs/diagnosis-roadmap/p1-contracts.md §6.3）。

- 注册表第 25 个工具：唯一 WsToolSpec、双域可用、schema 契约形状
  （person_id 可选=单生 / 缺省=班级汇总；academic_year_id 可选）、
  description 写明只读与口径版本 p1-v1
- MCP 目录自动含第 25 个工具（P0-A2 注册表机制，ws_tool_catalog 只认
  TOOL_REGISTRY）：同名同源 + validate_required_tools 一致性门
- B1/B2 端点未部署 → not_available 可读引导（模型改用既有工具；
  合并后 import 成立即自动接线）
- 接线路径（monkeypatch 假 B1/B2 模块，签名=契约 §2/§3 冻结签名）：
  工具结果与 service 返回逐字段一致（透传零改写）——页面端点与 AI 工具
  调同一 service 同参同果，即 §6「三处同源」的结构等价断言；
  端点级三数据源逐字段比对待 B1/B2 合并后在主控侧补跑
- 作用域纪律：person_id 越界 → resource_out_of_scope；非整数参数 →
  invalid_scope_param；全程只读
"""

import sys
import types

import pytest

TOOL_NAME = "get_diagnosis_summary"


def _db():
    from app.db.models import SessionLocal

    return SessionLocal()


def _snap(db, mode, **kw):
    from app.api.chat_tools import resolve_scope_snapshot

    return resolve_scope_snapshot(db, 1, mode, **kw)


def _run(db, snapshot, name, args=None):
    from app.api.chat_tools import execute_session_tool

    return execute_session_tool(db, snapshot, name, args or {})


# ────────────────────── 注册表：第 25 个工具 ──────────────────────


def test_registry_has_diagnosis_summary_as_25th_tool():
    from app.api.chat_tools import TOOL_REGISTRY

    assert len(TOOL_REGISTRY) == 25
    # 第 25 个：注册表末位、名称唯一
    assert TOOL_REGISTRY[-1].name == TOOL_NAME
    assert [spec.name for spec in TOOL_REGISTRY].count(TOOL_NAME) == 1
    spec = TOOL_REGISTRY[-1]
    # 双域可用（班主任=全科+总分；教学=任教学科，缺总体指标由 service
    # 返回 not_computable，工具层不裁剪域）
    assert spec.domains == ("homeroom", "teaching")
    # schema 契约形状：person_id 可选（缺省=班级汇总）、academic_year_id 可选
    schema = spec.input_schema
    assert schema["type"] == "object"
    assert set(schema.get("required") or []) == set()
    props = schema["properties"]
    assert props["person_id"]["type"] == "integer"
    assert "可选" in props["person_id"]["description"]
    assert "班级汇总" in props["person_id"]["description"]
    assert props["academic_year_id"]["type"] == "integer"
    # 描述写明只读与口径版本（契约 §6.3）
    assert "只读" in spec.description
    assert "p1-v1" in spec.description
    assert "同一数据源" in spec.description


def test_domain_projection_includes_diagnosis_summary():
    from app.api.chat_tools import tools_for_domain

    homeroom = {t["name"] for t in tools_for_domain("homeroom")}
    teaching = {t["name"] for t in tools_for_domain("teaching")}
    assert TOOL_NAME in homeroom
    assert TOOL_NAME in teaching


# ────────────────────── MCP 目录：注册表机制自动含第 25 个 ──────────────────────


def test_mcp_catalog_contains_25th_tool_same_source():
    """契约 §6.3：新工具自动进入 MCP 目录（P0-A2 注册表机制）——
    目录第 25 项与应用内注册表同名同源（description/schema 同一来源）。"""
    from app.api.chat_tools import TOOL_NAMES, TOOL_REGISTRY
    from app.mcp_server import validate_required_tools, ws_tool_catalog

    catalog = ws_tool_catalog()
    assert [t["name"] for t in catalog] == list(TOOL_NAMES)
    assert len(catalog) == 25
    assert catalog[-1]["name"] == TOOL_NAME
    spec = next(s for s in TOOL_REGISTRY if s.name == TOOL_NAME)
    entry = catalog[-1]
    # 同名同源：description = 注册表原文 + MCP 调用说明；schema = 注册表
    # schema 注入必填 mode，person_id/academic_year_id 原样保留
    assert entry["description"].startswith(spec.description)
    assert "mode" in entry["description"]
    schema = entry["inputSchema"]
    assert schema["required"][0] == "mode"
    for key in ("person_id", "academic_year_id"):
        assert schema["properties"][key] == spec.input_schema["properties"][key]
    assert entry["annotations"].read_only_hint is True
    # 目录一致性门（fail closed）：目录与注册表逐项相等才放行
    validate_required_tools()


# ────────────────────── not_available 引导（B1/B2 未部署） ──────────────────────


def test_student_summary_live_after_b1_b2_merge(v1_seed):
    """B1/B2 合并接线后：单生路径返回真实特征+类型（不再是 not_available）。"""
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(db, snap, TOOL_NAME, {"person_id": v1_seed.jia_h_id})
        assert "error" not in result, result
        assert result["summary_kind"] == "student"
        assert result["calc_version"] == "p1-v1"
        assert result["features"]["calc_version"] == "p1-v1"
        assert "indicators" in result["features"]
        assert result["types"]["classification_status"] in ("classified", "insufficient_data")
    finally:
        db.close()


def test_class_summary_live_after_b1_b2_merge(v1_seed):
    """person_id 缺省=班级汇总：接线后返回逐生 types（features 包装行喂给
    classify 的入参修正由主控合并时完成，此处断言真实装配）。"""
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(db, snap, TOOL_NAME)
        assert "error" not in result, result
        assert result["summary_kind"] == "class"
        assert result["calc_version"] == "p1-v1"
        rows = result["types_by_person"]
        assert isinstance(rows, list) and rows, rows
        for row in rows:
            assert row["types"]["classification_status"] in ("classified", "insufficient_data")
    finally:
        db.close()


def test_teaching_session_live_after_merge(v1_seed):
    """教学域同样接线：仅任教学科特征，总体指标 not_computable 而非报错。"""
    db = _db()
    try:
        snap = _snap(db, "teaching")
        result = _run(db, snap, TOOL_NAME, {"person_id": v1_seed.jia_t_id})
        assert "error" not in result, result
        assert result["calc_version"] == "p1-v1"
        assert "indicators" in result["features"]
    finally:
        db.close()


# ────────────────────── 作用域纪律 ──────────────────────


def test_out_of_scope_person_rejected(v1_seed):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        # 戊只在教学域 T8，不在 homeroom 会话成员集合
        result = _run(db, snap, TOOL_NAME, {"person_id": v1_seed.wu_t_id})
        assert result["error"] == "resource_out_of_scope"
        assert "该学生不在当前会话范围" in result["detail"]
    finally:
        db.close()


def test_invalid_param_types_rejected(v1_seed):
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        bad_person = _run(db, snap, TOOL_NAME, {"person_id": "abc"})
        assert bad_person["error"] == "invalid_scope_param"
        bad_year = _run(db, snap, TOOL_NAME, {"academic_year_id": "2025-2026"})
        assert bad_year["error"] == "invalid_scope_param"
    finally:
        db.close()


# ────────────────────── 接线路径：契约冻结签名（合并后自动生效） ──────────────────────


class _FakeDiagnosis:
    """假 B1/B2（签名=契约 §2/§3 冻结签名），记录调用入参验证透传。"""

    def __init__(self, features, class_features, types_result):
        self.features = features
        self.class_features = class_features
        self.types_result = types_result
        self.student_calls = []
        self.class_calls = []
        self.classify_calls = []

    def module_features(self):
        fake = types.ModuleType("app.diagnosis.features")

        def student_features(db, scope, person_id, academic_year_id):
            self.student_calls.append((db, scope, person_id, academic_year_id))
            return self.features

        def class_features(db, scope, academic_year_id):
            self.class_calls.append((db, scope, academic_year_id))
            return self.class_features

        fake.student_features = student_features
        fake.class_features = class_features
        return fake

    def module_types(self):
        fake = types.ModuleType("app.diagnosis.types")

        def classify_student(features):
            self.classify_calls.append(features)
            return self.types_result

        fake.classify_student = classify_student
        return fake

    def install(self, monkeypatch):
        # 父包占位（__path__ 空 = namespace 包），覆盖 sys.modules 所有解析路径
        pkg = types.ModuleType("app.diagnosis")
        pkg.__path__ = []
        monkeypatch.setitem(sys.modules, "app.diagnosis", pkg)
        monkeypatch.setitem(sys.modules, "app.diagnosis.features", self.module_features())
        monkeypatch.setitem(sys.modules, "app.diagnosis.types", self.module_types())


FIXTURE_FEATURES = {
    "person_id": 9,
    "calc_version": "p1-v1",
    "indicators": {
        "current_level": {
            "exam_name": "2025期中",
            "as_of": "2025-11-06",
            "main3": {"rank": 123, "percentile": 0.21, "basis": "school", "missing_reason": None},
            "subjects": [{"subject": "语文", "percentile": 0.34, "grade_score": None}],
            "bands": {"high_score": False, "critical": False, "weak": False},
        },
        "trend": {
            "last_change": {"from": "2025期初", "to": "2025期中", "rank_change": -12},
            "direction_recent": "进步",
            "streak": {"kind": "进步", "count": 2},
            "long_term": "上升",
            "valid_exam_count": 5,
        },
        "stability": {
            "window_n": 5,
            "statistic": "range",
            "value": 0.18,
            "label": "中等波动",
            "min_points": 3,
        },
        "imbalance": {
            "subjects": [{"subject": "英语", "diff_pct_point": -22.0, "consecutive_exams": 3}],
            "severe": ["英语"],
        },
        "homework_behavior": {
            "missing_7d": 2,
            "missing_30d": 5,
            "current_streak_days": 3,
            "trend": "恶化",
            "forgot_30d": 1,
            "negative_notes_30d": 0,
            "missing_by_subject": {"数学": 3},
        },
        "teacher_attention": {
            "last_contact": {"kind": "谈话", "days_ago": 12},
            "open_follow_ups": 1,
            "done_follow_ups_30d": 2,
        },
    },
    "data_quality": {"valid_exam_count": 5, "notes": []},
}

FIXTURE_TYPES = {
    "person_id": 9,
    "calc_version": "p1-v1",
    "main_type": "持续进步型",
    "secondary_tags": ["作业风险", "明显偏科型"],
    "evidence": [
        {"type": "持续进步型", "basis": "近 3 场主三门名次 -12/-15/-9，连续进步 3 次"},
        {"type": "作业风险", "basis": "近 30 天缺交 5 次，当前连缺 3 天"},
    ],
    "classification_status": "classified",
}


def test_student_summary_passthrough_matches_service_exactly(v1_seed, monkeypatch):
    """单生汇总：工具结果与 B1/B2 service 返回逐字段一致（透传零改写）；
    调用入参=（db, 快照 scope, person_id, 快照学年）——页面端点调同一
    service 同参同果，即 §6 三处同源的结构等价。"""
    fake = _FakeDiagnosis(
        features=FIXTURE_FEATURES,
        class_features={},
        types_result=FIXTURE_TYPES,
    )
    fake.install(monkeypatch)
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(db, snap, TOOL_NAME, {"person_id": v1_seed.jia_h_id})
        assert result["summary_kind"] == "student"
        assert result["calc_version"] == "p1-v1"
        assert result["academic_year_id"] == snap["academic_year_id"]
        # 逐字段一致（§6：AI 工具与页面端点同源同果）
        assert result["features"] == FIXTURE_FEATURES
        assert result["types"] == FIXTURE_TYPES
        # service 收到的 scope=会话快照（契约 §0.2 同源机制）
        assert len(fake.student_calls) == 1
        called_db, called_scope, called_pid, called_year = fake.student_calls[0]
        assert called_db is db
        assert called_scope is snap
        assert called_pid == v1_seed.jia_h_id
        assert called_year == snap["academic_year_id"]
        # types 引擎吃的是 B1 features 原对象
        assert fake.classify_calls == [FIXTURE_FEATURES]
    finally:
        db.close()


def test_class_summary_classifies_every_student(v1_seed, monkeypatch):
    """班级汇总：class_features 每生 features 逐个过 classify_student，
    types_by_person 与之一一对应（首页类型分布卡的数据源语义）。"""
    students = [
        {
            "person_id": pid,
            "name": f"合成{pid}",
            "features": {
                "person_id": pid,
                "calc_version": "p1-v1",
                "indicators": {},
                "data_quality": {},
            },
        }
        for pid in (101, 102, 103)
    ]
    class_payload = {
        "students": students,
        "calc_version": "p1-v1",
        "class_summary": {"comparable_n": 3},
    }

    def classify_by_pid(features):
        pid = (features or {}).get("person_id")
        return {
            "person_id": pid,
            "main_type": "综合风险型" if pid == 101 else None,
            "secondary_tags": [],
            "evidence": [],
            "classification_status": (
                "classified" if pid == 101 else "insufficient_data"
            ),
        }

    fake = _FakeDiagnosis(
        features=FIXTURE_FEATURES,
        class_features=class_payload,
        types_result=None,
    )
    # 班级场景逐生分类：覆盖默认 classify 桩
    fake.module_types = lambda: _types_module(classify_by_pid, fake.classify_calls)
    fake.install(monkeypatch)
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(db, snap, TOOL_NAME)
        assert result["summary_kind"] == "class"
        assert result["class_features"] == class_payload
        assert result["calc_version"] == "p1-v1"
        assert [row["person_id"] for row in result["types_by_person"]] == [101, 102, 103]
        assert result["types_by_person"][0]["types"]["main_type"] == "综合风险型"
        assert (
            result["types_by_person"][1]["types"]["classification_status"]
            == "insufficient_data"
        )
        called_db, called_scope, called_year = fake.class_calls[0]
        assert called_db is db
        assert called_scope is snap
        assert called_year == snap["academic_year_id"]
    finally:
        db.close()


def _types_module(classify, calls):
    fake = types.ModuleType("app.diagnosis.types")

    def classify_student(features):
        calls.append(features)
        return classify(features)

    fake.classify_student = classify_student
    return fake


def test_explicit_academic_year_passthrough(v1_seed, monkeypatch):
    """显式 academic_year_id 直传 service（不钉死快照学年）。"""
    other_year = v1_seed.ay_id  # 种子仅有该学年；传显式 id 验证透传路径
    fake = _FakeDiagnosis(
        features=FIXTURE_FEATURES, class_features={}, types_result=FIXTURE_TYPES
    )
    fake.install(monkeypatch)
    db = _db()
    try:
        snap = _snap(db, "homeroom")
        result = _run(
            db, snap, TOOL_NAME,
            {"person_id": v1_seed.jia_h_id, "academic_year_id": other_year},
        )
        assert result["academic_year_id"] == other_year
        assert fake.student_calls[0][3] == other_year
    finally:
        db.close()
