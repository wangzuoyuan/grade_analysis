"""按学科成绩口径（百分位/等级分）与单人逐科总览端点的合成测试。

样本全部虚构（张三/李四、合成学年），绝不用真实库。
口径规则（2026-10-22 起，与 app/chat/tools.py 一致）：
高一全部学科用年级百分位；高二/三语数英用年级百分位，六选科用等级分。
fixture 数据形态刻意贴合生产现实：高二语数英 grade_score 全 NULL 只有
年级百分位；高二六选科只有 grade_score 无百分位；高一全科只有百分位。
"""
import json
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest

CORE = ('语文', '数学', '英语')
ELECTIVE = ('物理',)
SUBJECTS = CORE + ELECTIVE  # 化学/生物等不造成绩，天然成为「timeline 为空的科」


@pytest.fixture
def world(v1_seed, db_session):
    from app.core.context import WorkspaceContext
    from app.db import workspace_models as wm
    from app.db.models import HomeworkSetting, Teacher

    def create(grade=2, exams=4):
        db = db_session
        ay = wm.AcademicYear(name='学科口径-' + uuid4().hex[:10],
                             start_date=date(2025, 9, 1), end_date=date(2026, 8, 31))
        db.add(ay)
        db.flush()
        cls = wm.AdministrativeClass(academic_year_id=ay.id, grade=grade, class_num=6, label='合成行政班')
        db.add(cls)
        db.flush()
        setattr(db.get(Teacher, 1), f'target_class_high{grade}', 6)
        db.merge(HomeworkSetting(key='active_grade', value=str(grade)))
        people = []
        for display in ('张三', '李四'):
            person = wm.WsStudentIdentity(data_domain='homeroom', display_name=display)
            db.add(person)
            db.flush()
            people.append(person.id)
            db.add(wm.Enrollment(admin_class_id=cls.id, identity_id=person.id,
                                 valid_from=date(2025, 9, 1), status='active'))
        for subject in SUBJECTS + ('化学',):
            for i in range(exams):
                day = date(2025, 10, 1) + timedelta(days=i * 20)
                if subject != '化学':  # 化学只造作业不造成绩 → 总览端点应跳过
                    for j, pid in enumerate(people):
                        core = grade == 1 or subject in CORE
                        db.add(wm.ScoreFact(data_domain='homeroom', academic_year_id=ay.id,
                            class_ref_id=cls.id, identity_id=pid, subject=subject,
                            exam_name=f'合成{subject}测评{i + 1}', exam_date=day, score=90 - i,
                            grade_score=None if core else (50 + i * 5 if j == 0 else 90 - i * 5),
                            grade_percentile=(.8 - i * .08 if j == 0 else .4) if core else None))
                for k in range(10):
                    batch = wm.HomeworkAssignment(data_domain='homeroom', academic_year_id=ay.id,
                        class_ref_id=cls.id, subject=subject, homework_type='合成练习',
                        assigned_date=day - timedelta(days=10 - k), batch_token=uuid4().hex,
                        expected_members_json=json.dumps(people))
                    db.add(batch)
                    db.flush()
                    for j, pid in enumerate(people):
                        submitted = j == 0 and k < i + 3
                        db.add(wm.HomeworkSubmission(assignment_id=batch.id, person_id=pid,
                            submission_status='submitted' if submitted else 'missing',
                            evaluation='订正完成' if submitted else None))
        db.commit()
        ctx = WorkspaceContext(teacher_id=1, mode='homeroom', data_domain='homeroom',
            academic_year_id=ay.id, class_ids=(cls.id,), subject=None,
            member_person_ids=tuple(people), as_of=date(2026, 10, 17))
        return SimpleNamespace(db=db, ay=ay.id, cls=cls, pids=people, ctx=ctx)
    return create


def single_subject(client, w, subject, person_id=None, **extra):
    params = {'academic_year_id': w.ay, 'class_id': w.cls.id, 'subject': subject, **extra}
    if person_id is not None:
        params['person_id'] = person_id
    response = client.get('/api/v1/homeroom/diagnosis/homework-students', params=params)
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize('grade', [2, 3])
def test_grade23_core_subjects_use_percentile(client, world, grade):
    """高二/三语数英：年级百分位口径（生产库 grade_score 全 NULL 也能画线）。"""
    w = world(grade=grade)
    for subject in CORE:
        points = single_subject(client, w, subject, person_id=w.pids[0])['students'][0]['timeline']
        assert len(points) == 4
        assert points[0]['basis'] == 'grade_percentile'
        assert points[0]['achievement'] == 20  # 100*(1-0.8)
        assert points[0]['percentile'] == .8
        assert all(p['pair_reason'] is None for p in points)
        assert all(p['percentile'] is not None for p in points)


@pytest.mark.parametrize('grade', [2, 3])
def test_grade23_elective_uses_grade_score(client, world, grade):
    """高二/三六选科：等级分口径不变，且不输出 percentile。"""
    w = world(grade=grade)
    points = single_subject(client, w, '物理', person_id=w.pids[0])['students'][0]['timeline']
    assert points[0]['basis'] == 'grade_score'
    assert points[0]['achievement'] == 50
    assert points[0]['percentile'] is None


def test_grade2_core_percentile_missing_never_falls_back(client, world):
    """高二语文缺百分位 → percentile_missing，绝不回退（本就无）等级分或原始分。"""
    from app.db import workspace_models as wm
    w = world(grade=2)
    fact = w.db.query(wm.ScoreFact).filter_by(academic_year_id=w.ay, subject='语文',
        identity_id=w.pids[0], exam_name='合成语文测评2').one()
    fact.grade_percentile = None
    w.db.commit()
    points = single_subject(client, w, '语文', person_id=w.pids[0])['students'][0]['timeline']
    assert points[1]['pair_reason'] == 'percentile_missing'
    assert points[1]['achievement'] is None
    assert points[1]['basis'] == 'grade_percentile'


def test_grade1_all_subjects_use_percentile(client, world):
    """高一全部学科（含物理）都用年级百分位，现状不变。"""
    w = world(grade=1)
    for subject in SUBJECTS:
        points = single_subject(client, w, subject, person_id=w.pids[0])['students'][0]['timeline']
        assert points[0]['basis'] == 'grade_percentile'
        assert points[0]['achievement'] == 20
        assert points[0]['percentile'] == .8


def overview(client, w, **extra):
    params = {'academic_year_id': w.ay, 'class_id': w.cls.id, **extra}
    return client.get('/api/v1/homeroom/diagnosis/homework-student-subjects', params=params)


def test_subjects_overview_shape_order_and_fields(client, world):
    """单人逐科总览：规范学科序、timeline 字段齐全、空学科（化学）跳过。
    2026-10-23 起默认 window_mode=between_exams：首场窗口自本学期开始
    （fixture 无 ws_homework_semester 行 → 按学年日期二分推导，上学期
    起点=2025-09-01）。"""
    w = world()
    r = overview(client, w, person_id=w.pids[0])
    assert r.status_code == 200, r.text
    data = r.json()
    assert set(data) == {'person_id', 'name', 'window_days', 'window_mode', 'subjects', 'note'}
    assert data['person_id'] == w.pids[0]
    assert data['name'] == '张三'
    assert data['window_days'] == 14
    assert data['window_mode'] == 'between_exams'
    assert '上一次考试' in data['note'] and '本学期' in data['note']
    assert [s['subject'] for s in data['subjects']] == ['语文', '数学', '英语', '物理']
    first = data['subjects'][0]
    assert set(first) == {'subject', 'paired_n', 'total_n', 'timeline'}
    assert first['paired_n'] == 4 and first['total_n'] == 4
    point = first['timeline'][0]
    assert set(point) == {'exam_key', 'exam_name', 'exam_date', 'grade', 'basis', 'raw_score',
                          'achievement', 'percentile', 'submission_rate', 'submitted',
                          'eligible', 'pair_reason', 'window_start', 'window_end'}
    assert point['basis'] == 'grade_percentile' and point['achievement'] == 20
    assert point['exam_name'] == '合成语文测评1' and point['grade'] == 2
    assert point['eligible'] == 10 and point['submitted'] == 3
    assert point['submission_rate'] == 0.3 and point['pair_reason'] is None
    assert point['window_start'] == '2025-09-01' and point['window_end'] == '2025-09-30'
    physics = next(s for s in data['subjects'] if s['subject'] == '物理')
    assert physics['timeline'][0]['basis'] == 'grade_score'
    # 换人可用：李四名字回传
    other = overview(client, w, person_id=w.pids[1]).json()
    assert other['name'] == '李四'


def test_subjects_overview_windows_span_between_exams(client, world):
    """区间口径：窗口=上一次考试次日至本次考试前；首场自本学期开始
    （二分推导起点 2025-09-01）。考试每 20 天一场，窗口起点=前一场+1。"""
    w = world()
    data = overview(client, w, person_id=w.pids[0]).json()
    chinese = next(s for s in data['subjects'] if s['subject'] == '语文')
    assert [p['window_start'] for p in chinese['timeline']] == [
        '2025-09-01', '2025-10-02', '2025-10-22', '2025-11-11']
    assert [p['window_end'] for p in chinese['timeline']] == [
        '2025-09-30', '2025-10-20', '2025-11-09', '2025-11-29']


def test_subjects_overview_semester_rows_override_derivation(client, world):
    """作业学期设置（ws_homework_semester 手工行）优先于二分推导：
    含首场考试的学期起点成为首场窗口起点。"""
    from app.db import workspace_models as wm
    w = world()
    w.db.add(wm.WsHomeworkSemester(academic_year_id=w.ay, name='合成学期',
        start_date=date(2025, 9, 15), end_date=date(2026, 1, 31),
        is_current=1, mode='manual'))
    w.db.commit()
    data = overview(client, w, person_id=w.pids[0]).json()
    chinese = next(s for s in data['subjects'] if s['subject'] == '语文')
    assert chinese['timeline'][0]['window_start'] == '2025-09-15'
    # 应交批次都在 9-21 之后，起点后移不减少 eligible
    assert chinese['timeline'][0]['eligible'] == 10


def test_subjects_overview_fixed_mode_keeps_legacy_window(client, world):
    """window_mode=fixed 保留旧口径：首场=考试日前 window_days 天，
    后续场=与上一场+1 取晚者（不重复上一场窗口）。"""
    w = world()
    data = overview(client, w, person_id=w.pids[0], window_mode='fixed').json()
    assert data['window_mode'] == 'fixed'
    assert 'window_days 天' in data['note']
    chinese = next(s for s in data['subjects'] if s['subject'] == '语文')
    starts = [p['window_start'] for p in chinese['timeline']]
    assert starts == ['2025-09-17', '2025-10-07', '2025-10-27', '2025-11-16']


def test_subjects_overview_scope_and_param_errors(client, world, v1_seed):
    """越界 person_id 404（沿用内部领域错误）；window_days 非白名单 422；
    缺 person_id 422；教学域不提供该端点。"""
    w = world(exams=1)
    assert overview(client, w, person_id=v1_seed.jia_t_id).status_code == 404
    assert overview(client, w, person_id=w.pids[0], window_days=7).status_code == 422
    assert overview(client, w, person_id=w.pids[0], window_days=30).status_code == 200
    assert overview(client, w, person_id=w.pids[0], window_mode='bogus').status_code == 422
    assert overview(client, w).status_code == 422
    teaching = client.get('/api/v1/teaching/diagnosis/homework-student-subjects',
                          params={'person_id': w.pids[0]})
    assert teaching.status_code == 404


def test_subjects_overview_skips_subjects_without_any_facts(client, world):
    """无成绩事实的学科不进入全科总览（2026-10-22 补充）：
    学生未选修的科目（同班他人有该科成绩 → 时间线非空但全部点
    raw_score/achievement 均 None）不渲染空数据块，只列实际有成绩的学科。"""
    from app.db import workspace_models as wm
    w = world(exams=1)
    # 生物只给李四造成绩事实：张三目录里会出现该考试（时间线非空、全 score_missing）
    w.db.add(wm.ScoreFact(data_domain='homeroom', academic_year_id=w.ay,
        class_ref_id=w.cls.id, identity_id=w.pids[1], subject='生物',
        exam_name='合成生物测评1', exam_date=date(2025, 10, 1),
        score=88, grade_score=60))
    w.db.commit()
    data = overview(client, w, person_id=w.pids[0]).json()
    assert [s['subject'] for s in data['subjects']] == ['语文', '数学', '英语', '物理']
    # 李四有生物成绩事实 → 其总览包含生物
    other = overview(client, w, person_id=w.pids[1]).json()
    assert '生物' in [s['subject'] for s in other['subjects']]
