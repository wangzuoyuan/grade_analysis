"""合并版工作台模型：双域数据隔离的 P1 新表。

设计依据 ``docs/planning/03-architecture.md``：
- 单库内以 ``data_domain``（'homeroom' | 'teaching'）分区，两工作台默认隔离；
  只有显式 ``homeroom_teaching_link`` 关联的行政班↔教学班才按白名单共享。
- 学年（AcademicYear/Term）与入学届别（Cohort）分离；行政班/教学班成员
  均带有效期，历史不伪造精确起止。
- ``score_fact`` 统一存放按域分区的成绩事实：homeroom 域保留全科/总分，
  teaching 域只存任教学科；缺考存 NULL，绝不写 0。

命名说明（重要偏差）：既有 ``app.db.models`` 已占用表名 ``student_identity``
与 ``student_alias``（班主任版域内身份体系，P1 不动）。同一 MetaData 不允许
重名表，故本模块的新身份表命名为 ``ws_student_identity`` / ``ws_student_alias``，
作为带 ``data_domain`` 的跨域身份层；后续波次做身份合并时再统一收敛。

注意：新表的建表/变更统一走 Alembic（``backend/alembic``），不要依赖
``create_all`` 的隐式副作用；应用 schema 初始化见 ``app/db/schema.py``。
"""

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.orm import validates

from app.db.models import Base, engine


# ─────────────────────────────────────────────────────────────
# SQLite 外键真实开启：每个连接都要显式 PRAGMA foreign_keys=ON。
#
# R7 之后主注册点在 app.db.models 的 engine 创建处（checkout 监听，
# 应用/测试/迁移所有进程一律生效）；本函数保留供 alembic env 显式调用，
# 重复注册被 event.contains 防护，幂等无害。
# ─────────────────────────────────────────────────────────────

def _enable_sqlite_foreign_keys(dbapi_connection, connection_record, connection_proxy=None):  # noqa: ANN001
    cursor = dbapi_connection.cursor()
    try:
        cursor.execute("PRAGMA foreign_keys=ON")
    finally:
        cursor.close()


def enable_sqlite_foreign_keys(target_engine=None) -> None:
    """为 engine（默认共享 engine）注册 PRAGMA foreign_keys=ON，幂等。"""
    target_engine = target_engine or engine
    if not event.contains(target_engine, "checkout", _enable_sqlite_foreign_keys):
        event.listen(target_engine, "checkout", _enable_sqlite_foreign_keys)


# ─────────────────────────────────────────────────────────────
# 学年 / 学期 / 届别
# ─────────────────────────────────────────────────────────────

class AcademicYear(Base):
    """学年，如 '2025-2026'。名称全局唯一，起止日期明确。"""
    __tablename__ = "academic_year"
    id = Column(Integer, primary_key=True)
    name = Column(String(32), nullable=False, unique=True)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Term(Base):
    """学年内的学期，如 '上学期'。"""
    __tablename__ = "term"
    id = Column(Integer, primary_key=True)
    academic_year_id = Column(Integer, ForeignKey("academic_year.id"), nullable=False)
    name = Column(String(32), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "name", name="uq_term_year_name"),
        Index("idx_term_academic_year", "academic_year_id"),
    )


class Cohort(Base):
    """入学届别（entry_year，如 2025 表示 2025 级）。注意：届别不是年级，
    年级随学年推进变化，不得用届别冒充年级，也不得用学号/姓名/班号单独
    认定跨域同一人。"""
    __tablename__ = "cohort"
    id = Column(Integer, primary_key=True)
    entry_year = Column(Integer, nullable=False, unique=True)
    label = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ─────────────────────────────────────────────────────────────
# 域内身份（ws_ 前缀：与班主任版既有 student_identity/student_alias 区分）
# ─────────────────────────────────────────────────────────────

class WsStudentIdentity(Base):
    """域内“同一个人”的稳定内部 ID。data_domain 声明归属域；跨域同一人
    只能通过 homeroom_teaching_link 下的 linked_student 显式确认，
    同号/同名/同班号都不足以自动关联。display_name 仅为展示字段。"""
    __tablename__ = "ws_student_identity"
    id = Column(Integer, primary_key=True)
    data_domain = Column(String(16), nullable=False)
    display_name = Column(String(64), nullable=True)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_ws_identity_domain",
        ),
    )


class WsStudentAlias(Base):
    """学号等别名在某个数据域、某学年内的登记。学号保留字符串与前导零；
    唯一性不是裸学号全局唯一，而是 (identity, alias, domain, 学年)。

    alias_scope 为非空冗余列：SQLite 唯一约束把 NULL 视为互不相等，
    academic_year_id 为 NULL 的行会绕过含 NULL 的唯一键，因此用
    alias_scope 存 academic_year_id 的字符串或 'none' 参与唯一约束，
    写入时由应用层与 academic_year_id 同步维护。"""
    __tablename__ = "ws_student_alias"
    id = Column(Integer, primary_key=True)
    identity_id = Column(Integer, ForeignKey("ws_student_identity.id"), nullable=False)
    alias_value = Column(Text, nullable=False)
    data_domain = Column(String(16), nullable=False)
    source = Column(String(32), nullable=True)
    school = Column(String(64), nullable=True)
    academic_year_id = Column(Integer, ForeignKey("academic_year.id"), nullable=True)
    alias_scope = Column(String(16), nullable=False, default="none")
    valid_from = Column(Date, nullable=True)
    valid_to = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_ws_alias_domain",
        ),
        UniqueConstraint(
            "identity_id", "alias_value", "data_domain", "alias_scope",
            name="uq_ws_alias_identity_value",
        ),
        Index("idx_ws_alias_identity", "identity_id"),
    )


# ─────────────────────────────────────────────────────────────
# 行政班与教学班（成员均带有效期）
# ─────────────────────────────────────────────────────────────

class AdministrativeClass(Base):
    """行政班：某学年某年级某班号（UNIQUE）。年级 grade 是当下年级，
    不是入学届别。"""
    __tablename__ = "administrative_class"
    id = Column(Integer, primary_key=True)
    academic_year_id = Column(Integer, ForeignKey("academic_year.id"), nullable=False)
    grade = Column(Integer, nullable=False)  # 1=高一, 2=高二, 3=高三
    class_num = Column(Integer, nullable=False)
    label = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("academic_year_id", "grade", "class_num", name="uq_admin_class"),
        Index("idx_admin_class_year", "academic_year_id"),
    )


class Enrollment(Base):
    """学生在行政班中的在校记录（含座号与状态）。
    status: 'active' 在班 | 'transferred' 转班 | 'graduated' 毕业；
    离班写 valid_to + 状态，绝不物理删除。"""
    __tablename__ = "enrollment"
    id = Column(Integer, primary_key=True)
    admin_class_id = Column(Integer, ForeignKey("administrative_class.id"), nullable=False)
    identity_id = Column(Integer, ForeignKey("ws_student_identity.id"), nullable=False)
    seat_no = Column(Integer, nullable=True)
    status = Column(String(16), nullable=False, default="active")
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint("admin_class_id", "identity_id", "valid_from", name="uq_enrollment"),
        Index("idx_enrollment_identity", "identity_id"),
    )


class TeachingClass(Base):
    """教学班：某学年某学科某标签（同名不同学年独立）。"""
    __tablename__ = "teaching_class"
    id = Column(Integer, primary_key=True)
    academic_year_id = Column(Integer, ForeignKey("academic_year.id"), nullable=False)
    subject = Column(String(32), nullable=False)
    label = Column(String(64), nullable=False)
    sort_order = Column(Integer, nullable=False, default=0)
    status = Column(String(16), nullable=False, default="active", server_default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("status IN ('active', 'inactive')", name="ck_teaching_class_status"),
        UniqueConstraint("academic_year_id", "subject", "label", name="uq_teaching_class"),
        Index("idx_teaching_class_year", "academic_year_id"),
    )


class TeachingClassMember(Base):
    """教学班成员及有效期。source 记录名册来源（manual/导入/关联投影），
    默认 manual。"""
    __tablename__ = "teaching_class_member"
    id = Column(Integer, primary_key=True)
    teaching_class_id = Column(Integer, ForeignKey("teaching_class.id"), nullable=False)
    identity_id = Column(Integer, ForeignKey("ws_student_identity.id"), nullable=False)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    source = Column(String(32), nullable=False, default="manual")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "teaching_class_id", "identity_id", "valid_from",
            name="uq_teaching_member",
        ),
        Index("idx_teaching_member_identity", "identity_id"),
    )


# ─────────────────────────────────────────────────────────────
# 行政班 ↔ 教学班 的受控关联（最高优先级边界）
# ─────────────────────────────────────────────────────────────

class HomeroomTeachingLink(Base):
    """行政班与教学班的显式关联。只有本表 active 且在有效期内的关联，
    才允许跨域读取白名单数据（share_categories，逗号分隔，默认
    'roster,current_subject_score'）。同班号/同名/同学号不得自动建关联；
    撤销写 status='cancelled' + cancelled_at，并递增 version（乐观锁），
    不物理删除。"""
    __tablename__ = "homeroom_teaching_link"
    id = Column(Integer, primary_key=True)
    admin_class_id = Column(Integer, ForeignKey("administrative_class.id"), nullable=False)
    teaching_class_id = Column(Integer, ForeignKey("teaching_class.id"), nullable=False)
    academic_year_id = Column(Integer, ForeignKey("academic_year.id"), nullable=False)
    subject = Column(String(32), nullable=False)
    valid_from = Column(Date, nullable=False)
    valid_to = Column(Date, nullable=True)
    share_categories = Column(Text, nullable=False, default="roster,current_subject_score")
    # 显式历史授权日期（契约 §1.2.2）：考试日期早于 share_history_from
    # ?? valid_from 的事实不得跨域共享；NULL 等于 valid_from（默认不开放
    # 生效前历史），显式授权可早于 valid_from。考试日期未知（NULL）的
    # 事实一律不共享。
    share_history_from = Column(Date, nullable=True)
    status = Column(String(16), nullable=False, default="active")
    version = Column(Integer, nullable=False, default=1)
    cancelled_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "admin_class_id", "teaching_class_id", "academic_year_id", "subject",
            name="uq_htl_class_pair",
        ),
        Index("idx_htl_admin_class", "admin_class_id"),
        Index("idx_htl_teaching_class", "teaching_class_id"),
    )


class LinkedStudent(Base):
    """关联班下“同一个人”的跨域映射：homeroom 侧 person ↔ teaching 侧
    person，必须由本人确认（confirm_basis 记录依据，如
    'manual_confirm:2025-09'）。绝不自动按学号/姓名合并。"""
    __tablename__ = "linked_student"
    id = Column(Integer, primary_key=True)
    link_id = Column(Integer, ForeignKey("homeroom_teaching_link.id"), nullable=False)
    homeroom_identity_id = Column(Integer, ForeignKey("ws_student_identity.id"), nullable=False)
    teaching_identity_id = Column(Integer, ForeignKey("ws_student_identity.id"), nullable=False)
    confirm_basis = Column(String(64), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "link_id", "homeroom_identity_id", "teaching_identity_id",
            name="uq_linked_student",
        ),
        Index("idx_linked_student_link", "link_id"),
    )


# ─────────────────────────────────────────────────────────────
# 迁移台账 / 来源映射
# ─────────────────────────────────────────────────────────────

class MigrationRun(Base):
    """一次双库迁移演练的台账（run_token 幂等键，stats_json 存计数摘要）。"""
    __tablename__ = "migration_run"
    id = Column(Integer, primary_key=True)
    run_token = Column(String(64), nullable=False)
    kind = Column(String(32), nullable=False)
    started_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    finished_at = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False)
    stats_json = Column(Text, nullable=True)


class SourceMap(Base):
    """旧库来源行 → 合并库目标行的映射。(source_fingerprint, source_table,
    source_pk) 唯一：同一来源行只能有一条映射记录；“同来源行只能有一个
    有效映射”由应用层切换 active 实现（SQLite 唯一约束表达不了部分唯一）。"""
    __tablename__ = "source_map"
    id = Column(Integer, primary_key=True)
    source_fingerprint = Column(String(128), nullable=False)
    source_table = Column(String(64), nullable=False)
    source_pk = Column(String(64), nullable=False)
    target_table = Column(String(64), nullable=False)
    target_id = Column(Integer, nullable=False)
    active = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        UniqueConstraint(
            "source_fingerprint", "source_table", "source_pk",
            name="uq_source_map_origin",
        ),
        Index("idx_source_map_target", "target_table", "target_id"),
    )


# ─────────────────────────────────────────────────────────────
# 成绩事实与导入批次
# ─────────────────────────────────────────────────────────────

class ScoreFact(Base):
    """按域分区的成绩事实。class_ref_id 为多态引用（不建数据库外键）：
    data_domain='homeroom' 时指 administrative_class.id，
    data_domain='teaching' 时指 teaching_class.id。

    score 可空：缺考存 NULL，读取侧显示“—”，绝不写 0。
    自然唯一键 = (data_domain, academic_year_id, exam_name, identity_id,
    subject 口径, total 口径)。subject/total_type 本身可空，而 SQLite 的
    UNIQUE 对 NULL 互不相等，无法直接进唯一键，故加非空冗余列
    subject_key/total_key（默认 ''，subject/total_type 为空时存 ''）。

    key 列由 ORM 自动同步（@validates 设值即同步 + before_insert/
    before_update flush 时以 subject/total_type 为准强制覆写），业务代码
    只写 subject/total_type 即可；显式设置 key 列不会生效（会被同步覆盖）。
    仅绕过 ORM 的 raw SQL / core 批量插入需要自行维护 key 列。"""
    __tablename__ = "score_fact"
    id = Column(Integer, primary_key=True)
    data_domain = Column(String(16), nullable=False)
    academic_year_id = Column(Integer, ForeignKey("academic_year.id"), nullable=False)
    exam_name = Column(String(128), nullable=False)
    exam_date = Column(Date, nullable=True)
    # 原库考试日期可能只有 YYYY-MM。exam_date 仅在原始值为完整日时填写；
    # source_exam_date/precision 保留事实，避免为共享门伪造某一天。
    source_exam_date = Column(String(16), nullable=True)
    exam_date_precision = Column(String(8), nullable=False, default="day", server_default="day")
    class_ref_id = Column(Integer, nullable=True)
    identity_id = Column(Integer, ForeignKey("ws_student_identity.id"), nullable=False)
    subject = Column(String(32), nullable=True)
    total_type = Column(String(32), nullable=True)
    subject_key = Column(String(32), nullable=False, default="")
    total_key = Column(String(32), nullable=False, default="")
    score = Column(Float, nullable=True)
    # P3 §1.3：等级分/赋分（原始分仍在 score；缺考两者皆 NULL）
    grade_score = Column(Float, nullable=True)
    # 旧班主任版重点关注口径：总分行保留学籍/年级名次与年级百分位，
    # 单科行保留年级百分位。缺值保持 NULL，绝不按班内分数伪造年级口径。
    grade_percentile = Column(Float, nullable=True)
    xueji_rank = Column(Integer, nullable=True)
    grade_rank = Column(Integer, nullable=True)
    source = Column(String(32), nullable=False, default="p1_seed")
    data_revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_score_fact_domain",
        ),
        UniqueConstraint(
            "data_domain", "academic_year_id", "exam_name", "identity_id",
            "subject_key", "total_key",
            name="uq_score_fact_natural_key",
        ),
        Index("idx_score_fact_scope", "data_domain", "academic_year_id", "identity_id"),
        Index("idx_score_fact_class_ref", "data_domain", "class_ref_id"),
    )

    @validates("subject", "total_type")
    def _sync_fact_key_on_set(self, key, value):
        """subject/total_type 一经赋值（含构造 kwargs）立即同步对应 key 列。"""
        key_col = "subject_key" if key == "subject" else "total_key"
        setattr(self, key_col, value or "")
        return value


class WorkspaceClassAverage(Base):
    """导入的全年级班级均分表。

    这类数据不是某个学生的成绩事实，也不能从当前行政班的 ScoreFact
    反推。按工作台、学年、考试和班号独立保存，供班主任考试详情展示
    各班真实均分及班级排名。JSON 字段保留不同年级各自的学科与总分口径。
    """

    __tablename__ = "workspace_class_average"
    id = Column(Integer, primary_key=True)
    data_domain = Column(String(16), nullable=False)
    academic_year_id = Column(Integer, ForeignKey("academic_year.id"), nullable=False)
    exam_name = Column(String(128), nullable=False)
    exam_date = Column(Date, nullable=True)
    grade = Column(Integer, nullable=False)
    class_type = Column(String(32), nullable=True)
    class_num = Column(Integer, nullable=False)
    teacher_name = Column(String(64), nullable=True)
    subject_averages = Column(JSON, nullable=False, default=dict)
    total_averages = Column(JSON, nullable=False, default=dict)
    source = Column(String(255), nullable=False, default="import")
    data_revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_workspace_class_average_domain",
        ),
        UniqueConstraint(
            "data_domain", "academic_year_id", "exam_name", "grade", "class_num",
            name="uq_workspace_class_average_natural_key",
        ),
        Index(
            "idx_workspace_class_average_scope",
            "data_domain", "academic_year_id", "exam_name", "grade",
        ),
    )


class SourceArchiveRecord(Base):
    """未安全业务化的来源行只读归档。

    归档也是迁移去向，不能以 verify 的 not_migrated 标签替代；业务读路径
    不读取本表。payload_json 保留原行，供后续人工转换/审计使用。
    """
    __tablename__ = "source_archive_record"
    id = Column(Integer, primary_key=True)
    source_fingerprint = Column(String(128), nullable=False)
    source_table = Column(String(64), nullable=False)
    source_pk = Column(String(64), nullable=False)
    data_domain = Column(String(16), nullable=False)
    archive_reason = Column(String(128), nullable=False)
    payload_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("data_domain IN ('homeroom', 'teaching')", name="ck_archive_domain"),
        UniqueConstraint("source_fingerprint", "source_table", "source_pk", name="uq_archive_origin"),
        Index("idx_archive_origin", "source_fingerprint", "source_table"),
    )


class SourceProjectionMap(Base):
    """一条来源记录可投影到多个目标对象的可审计映射。"""
    __tablename__ = "source_projection_map"
    id = Column(Integer, primary_key=True)
    data_domain = Column(String(16), nullable=False)
    source_fingerprint = Column(String(128), nullable=False)
    source_table = Column(String(64), nullable=False)
    source_pk = Column(String(128), nullable=False)
    target_table = Column(String(64), nullable=False)
    target_id = Column(Integer, nullable=False)
    projection_kind = Column(String(32), nullable=False)
    status = Column(String(16), nullable=False, default="projected")
    reason = Column(String(128), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    __table_args__ = (UniqueConstraint("data_domain", "source_fingerprint", "source_table", "source_pk", "target_table", "target_id", "projection_kind", name="uq_projection_origin_target"),)

@event.listens_for(ScoreFact, "before_insert")
@event.listens_for(ScoreFact, "before_update")
def _sync_score_fact_keys_on_flush(mapper, connection, target):  # noqa: ANN001
    """flush 兜底：无论对象如何构造/修改，落库前以 subject/total_type 为准
    强制覆写 key 列（显式设置的 key 值不生效）。"""
    target.subject_key = target.subject or ""
    target.total_key = target.total_type or ""


class PendingImportRow(Base):
    """迁移待核实区（P7 契约 v2 §4.1 Q04）：被隔离来源行的持久暂存。

    隔离必须有真实门：撞号同名、占位/临时学号（_anon:/TMP）、未知学年、
    教师任教学科未配置等**不能安全转换**的来源行绝不写入
    Enrollment/ScoreFact/HomeworkSubmission 等业务表，而是原行完整
    （raw_json）落本表等待人工确认；业务读路径天然查不到这些行。
    确认导入后由迁移管线补写业务表并删除本表登记（幂等）。
    (source_fingerprint, source_table, source_pk) 定位唯一来源行，
    与 SourceMap 的映射键同构，但本表行没有也不该有业务行映射。"""

    __tablename__ = "pending_import_row"
    id = Column(Integer, primary_key=True)
    source_fingerprint = Column(String(128), nullable=False)
    source_table = Column(String(64), nullable=False)
    source_pk = Column(String(64), nullable=False)
    data_domain = Column(String(16), nullable=False)
    reason = Column(String(128), nullable=False)
    raw_json = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_pending_import_domain",
        ),
        Index("idx_pending_import_origin", "source_fingerprint", "source_table", "source_pk"),
    )


class ImportBatch(Base):
    """两阶段导入的预览批次：token 幂等键，scope_json 记录解析出的
    作用域快照，content_digest 防内容漂移；确认时校验状态与有效期。"""
    __tablename__ = "import_batch"
    id = Column(Integer, primary_key=True)
    token = Column(String(64), nullable=False, unique=True)
    data_domain = Column(String(16), nullable=False)
    scope_json = Column(Text, nullable=True)
    content_digest = Column(String(128), nullable=True)
    status = Column(String(16), nullable=False, default="pending")
    created_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_import_batch_domain",
        ),
    )


# ─────────────────────────────────────────────────────────────
# 作业批次与逐人状态（契约 §2.1，R10 冻结最小契约；完整录入/预警属 P5）
# ─────────────────────────────────────────────────────────────

class HomeworkAssignment(Base):
    """作业布置批次（契约 §2.1，R10 冻结最小契约；完整录入/预警规则属 P5）。

    subject（学科）与 homework_type（作业种类）分列，绝不混用；
    batch_token 为幂等键：重试同 token 不新增批次，一天同科同种类多份
    作业各自持不同 token。expected_members_json 为应交成员快照（提交率
    分母以此为准，不取当天班级人数）。"""
    __tablename__ = "homework_assignment"
    id = Column(Integer, primary_key=True)
    data_domain = Column(String(16), nullable=False)
    # class_ref_id 多态引用（不建数据库外键，同 ScoreFact 语义）：
    # data_domain='homeroom' 时指 administrative_class.id，
    # data_domain='teaching' 时指 teaching_class.id。
    class_ref_id = Column(Integer, nullable=False)
    academic_year_id = Column(Integer, ForeignKey("academic_year.id"), nullable=False)
    subject = Column(String(32), nullable=False)
    homework_type = Column(String(32), nullable=False)
    assigned_date = Column(Date, nullable=False)
    due_date = Column(Date, nullable=True)
    batch_token = Column(String(64), nullable=False, unique=True)
    expected_members_json = Column(Text, nullable=False)
    revision = Column(Integer, nullable=False, default=1)
    status = Column(String(16), nullable=False, default="active")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_homework_assignment_domain",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_homework_assignment_status",
        ),
        Index("idx_homework_assignment_scope", "data_domain", "class_ref_id"),
    )


class HomeworkSubmission(Base):
    """逐人作业例外（契约 §2.1）：同人同批次唯一；无缺交/请假记录默认
    已交；evaluation 另列，不得从评价内容推断缺交。"""
    __tablename__ = "homework_submission"
    id = Column(Integer, primary_key=True)
    assignment_id = Column(Integer, ForeignKey("homework_assignment.id"), nullable=False)
    person_id = Column(Integer, ForeignKey("ws_student_identity.id"), nullable=False)
    submission_status = Column(String(16), nullable=False)
    evaluation = Column(Text, nullable=True)
    submitted_at = Column(DateTime, nullable=True)
    revision = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "submission_status IN ('submitted', 'missing', 'excused', 'unknown')",
            name="ck_homework_submission_status",
        ),
        UniqueConstraint("assignment_id", "person_id", name="uq_homework_submission"),
        Index("idx_homework_submission_assignment", "assignment_id"),
    )


# ─────────────────────────────────────────────────────────────
# 档案 / 学期（P4/P5 契约表，集成者随 P3 批次预建，迁移 0005）
# ─────────────────────────────────────────────────────────────

class WsStudentNote(Base):
    """工作台域内学生档案（P4 契约 §4，N01 隔离红线）。

    homeroom 域档案（谈话/家访/家长沟通/奖惩等私密内容）绝不进入 teaching
    工作台；反向亦然。关联班**不共享档案**（ADR-005 白名单无 notes）。
    person_id 即 ws 域身份，无学号聚合问题。"""
    __tablename__ = "ws_student_note"
    id = Column(Integer, primary_key=True)
    data_domain = Column(String(16), nullable=False)
    person_id = Column(Integer, ForeignKey("ws_student_identity.id"), nullable=False)
    date = Column(Date, nullable=False)
    category = Column(String(16), nullable=False)  # 谈话/观察/家访/家长沟通/奖惩/其他
    content = Column(Text, nullable=False)
    follow_up = Column(Text, nullable=True)
    follow_up_done = Column(Integer, nullable=False, default=0)
    source = Column(String(32), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    # Q06 回退增量依赖 updated_at 越界发现"已存在行的修改"；档案 PATCH
    # 走 ORM 属性赋值，onupdate 自动触碰（缺此列时修改会静默漏出增量清单）
    updated_at = Column(
        DateTime, nullable=True, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    __table_args__ = (
        CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_ws_note_domain",
        ),
        Index("idx_ws_note_person", "data_domain", "person_id"),
        Index("idx_ws_note_date", "date"),
    )


class WsHomeworkSemester(Base):
    """作业学期（P5 契约 §5）：auto=按日期自动推算，manual=手工日期；
    重复编辑/重叠返回 4xx（H06），不 500。"""
    __tablename__ = "ws_homework_semester"
    id = Column(Integer, primary_key=True)
    academic_year_id = Column(Integer, ForeignKey("academic_year.id"), nullable=False)
    name = Column(String(32), nullable=False)
    start_date = Column(Date, nullable=False)
    end_date = Column(Date, nullable=False)
    is_current = Column(Integer, nullable=False, default=0)
    mode = Column(String(8), nullable=False, default="auto")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint("mode IN ('auto', 'manual')", name="ck_ws_semester_mode"),
        UniqueConstraint("academic_year_id", "name", name="uq_ws_semester_year_name"),
        Index("idx_ws_semester_year", "academic_year_id"),
    )


class HomeworkStatsExclusion(Base):
    """作业统计排除（ADR-023）：行存在即该生在本班被排除作业统计——
    缺交不计入看板、排行与预警；相关性与个人明细/批次明细保留（沿用
    老教学版 ClassRoster.excluded 语义，记录永不删除，只影响聚合展示）。
    class_ref_id 按 data_domain 解释：homeroom=administrative_class.id，
    teaching=teaching_class.id；排除仅作用于该班自身，不跨班传播。"""
    __tablename__ = "homework_stats_exclusion"
    id = Column(Integer, primary_key=True)
    data_domain = Column(String(16), nullable=False)
    class_ref_id = Column(Integer, nullable=False)
    identity_id = Column(Integer, ForeignKey("ws_student_identity.id"), nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "data_domain IN ('homeroom', 'teaching')",
            name="ck_hw_stats_exclusion_domain",
        ),
        UniqueConstraint(
            "data_domain", "class_ref_id", "identity_id",
            name="uq_hw_stats_exclusion",
        ),
        Index("idx_hw_stats_exclusion_class", "data_domain", "class_ref_id"),
    )


# ─────────────────────────────────────────────────────────────
# AI 会话（P6 契约 p6-ai-mcp.md，集成者随二轮返工批次预建，迁移 0006）
# ─────────────────────────────────────────────────────────────

class ChatSession(Base):
    """AI/MCP 会话的不可变作用域快照（P6 契约 §1，A02 红线）。

    创建时服务端解析 scope 并冻结为 scope_json（mode/data_domain/
    academic_year_id/class_ids/subject/link_id/link_version/
    member_person_ids/as_of）；每次请求重新解析比对，漂移（link 撤销/
    版本变/成员变）→ 409，绝不信任客户端成员列表。"""
    __tablename__ = "chat_session"
    id = Column(Integer, primary_key=True)
    type = Column(String(8), nullable=False, default="chat")  # chat | mcp
    scope_json = Column(Text, nullable=False)
    status = Column(String(8), nullable=False, default="open")
    created_at = Column(DateTime, default=datetime.utcnow)
    closed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        CheckConstraint("type IN ('chat', 'mcp')", name="ck_chat_session_type"),
        CheckConstraint("status IN ('open', 'closed')", name="ck_chat_session_status"),
    )


class ChatMessage(Base):
    """P6 服务端会话历史（契约 p6-ai-mcp §0.1 Q03，迁移 0007）。

    会话内多轮对话与工具事件的持久化：messages 端点组装"历史（截断）+ 本次输入"；
    范围漂移后历史禁止复用（409），但保留只读。"""
    __tablename__ = "chat_message"
    id = Column(Integer, primary_key=True)
    session_id = Column(Integer, ForeignKey("chat_session.id"), nullable=False)
    role = Column(String(16), nullable=False)  # user | assistant
    content = Column(Text, nullable=False)
    tool_events_json = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("idx_chat_message_session", "session_id"),
    )


# 契约面向别名：P1 契约（docs/contracts/p1-api.md §2）以 StudentIdentity /
# StudentAlias 指称域内身份/别名；实现因既有 H 表占用同名表名而加 ws_ 前缀
# （ws_student_identity / ws_student_alias）。保留契约名作为模块级别名，
# 供 API 层与测试按契约引用，不改任何表结构。
StudentIdentity = WsStudentIdentity
StudentAlias = WsStudentAlias
