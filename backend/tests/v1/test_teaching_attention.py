"""Real scoped API and pure-rule regressions with independent synthetic worlds."""
import json
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from uuid import uuid4

import pytest


@pytest.fixture
def attention_world(v1_seed, db_session):
    from app.core.context import WorkspaceContext
    from app.db import workspace_models as wm
    def build(grade=2, n=3):
        db, today = db_session, date.today()
        ay = wm.AcademicYear(name="合成关注-" + uuid4().hex, start_date=today-timedelta(days=100), end_date=today+timedelta(days=100))
        db.add(ay); db.flush()
        cls = wm.TeachingClass(academic_year_id=ay.id, grade=grade, subject="物理", label="合成关注班")
        db.add(cls); db.flush()
        people = []
        for label in ("A", "B", "C"):
            p = wm.WsStudentIdentity(data_domain="teaching", display_name="合成关注学生"+label)
            db.add(p); db.flush(); people.append(p.id)
            db.add(wm.TeachingClassMember(teaching_class_id=cls.id, identity_id=p.id, valid_from=ay.start_date))
        facts = []
        for i in range(n):
            day = today-timedelta(days=21-i*7)
            exam = wm.TeachingExamScope(academic_year_id=ay.id, grade=grade, subject="物理",
                exam_name="合成关注测评"+str(i), exam_date=day)
            db.add(exam); db.flush()
            for j, pid in enumerate(people):
                f = wm.ScoreFact(data_domain="teaching", academic_year_id=ay.id, class_ref_id=cls.id,
                    identity_id=pid, subject="物理", exam_name=exam.exam_name, exam_date=day,
                    teaching_exam_id=exam.id, score=99 if j==0 else 20,
                    grade_score=(92-i*6 if j==0 else 45) if j!=2 else None,
                    grade_percentile=(.1+i*.06 if j==0 else .8) if j!=2 else None)
                db.add(f); facts.append(f)
        batches = []
        for i in range(2):
            a = wm.HomeworkAssignment(data_domain="teaching", academic_year_id=ay.id,
                class_ref_id=cls.id, subject="物理", homework_type="合成练习", assigned_date=today-timedelta(days=2-i),
                batch_token=uuid4().hex, expected_members_json=json.dumps(people))
            db.add(a); db.flush(); batches.append(a)
            db.add(wm.HomeworkSubmission(assignment_id=a.id, person_id=people[0], submission_status="missing"))
            db.add(wm.HomeworkSubmission(assignment_id=a.id, person_id=people[2], submission_status="submitted", evaluation="差，没有过程"))
        db.commit()
        ctx = WorkspaceContext(teacher_id=1, mode="teaching", data_domain="teaching",
            academic_year_id=ay.id, class_ids=(cls.id,), member_person_ids=tuple(people), subject="物理", as_of=today)
        return SimpleNamespace(db=db, today=today, ay=ay, cls=cls, pids=people, facts=facts, batches=batches, ctx=ctx)
    return build


def params(w):
    return dict(academic_year_id=w.ay.id, teaching_class_id=w.cls.id)


def summary(client, w):
    r=client.get("/api/v1/teaching/diagnosis/attention", params=params(w))
    assert r.status_code==200, r.text
    return r.json()


@pytest.mark.parametrize("grade", [1,2,3])
def test_selects_reasons_not_low_score_and_deduplicates(client, attention_world, grade):
    w=attention_world(grade)
    d=summary(client,w)
    assert d["counts"]["all"]==2
    assert len({s["person_id"] for s in d["students"]})==len(d["students"])
    a=next(s for s in d["students"] if s["person_id"]==w.pids[0])
    assert a["priority"]==10 and set(a["categories"])=={"learning","homework"}
    assert {s["code"] for s in a["signals"]}=={"continuous_decline","missing"}
    assert w.pids[1] not in [s["person_id"] for s in d["students"]]  # stable low, no goal
    assert next(g for g in d["gaps"] if g["person_id"]==w.pids[2])["reason"] in ("grade_score_missing","percentile_missing")
    assert a["timeline"][-1]["basis"]==("grade_percentile" if grade==1 else "grade_score")


def test_one_exam_missing_and_real_zero_no_bridging(client, attention_world):
    w=attention_world(n=1)
    assert not any("learning" in s["categories"] for s in summary(client,w)["students"])
    assert any(g["reason"]=="one_exam" for g in summary(client,w)["gaps"])
    w=attention_world()
    w.facts[3].score=None
    w.db.commit()
    assert not any(x["code"]=="continuous_decline" for s in summary(client,w)["students"] for x in s["signals"])
    w.facts[3].score=0; w.facts[3].grade_score=0
    w.db.commit()
    a=next(s for s in summary(client,w)["students"] if s["person_id"]==w.pids[0])
    assert a["timeline"][1]["achievement"]==0


@pytest.mark.parametrize("problem", ["month","same_day","grade_conflict","unknown_grade"])
def test_unreliable_dates_or_grade_do_not_create_score_risk(client, attention_world, problem):
    w=attention_world()
    if problem=="month":
        w.facts[-3].exam_date_precision="month"
    elif problem=="same_day":
        from app.db.workspace_models import TeachingExamScope
        day=w.facts[3].exam_date
        for f in w.facts[-3:]:
            f.exam_date=day
            w.db.get(TeachingExamScope,f.teaching_exam_id).exam_date=day
    elif problem=="grade_conflict": w.cls.grade=1
    else:
        w.cls.grade=None
        for f in w.facts: f.teaching_exam_id=None
    w.db.commit()
    assert not any("learning" in s["categories"] for s in summary(client,w)["students"])


def test_settings_persist_by_year_and_validate_and_support(client, attention_world):
    w=attention_world()
    cfg=dict(percentile_drop=5, grade_score_drop=3,target_top_percent=None,target_grade2=60,target_grade3=None)
    r=client.put("/api/v1/teaching/diagnosis/attention/settings",params=params(w),json=cfg)
    assert r.status_code==200
    d=summary(client,w)
    assert d["settings"]==cfg
    b=next(s for s in d["students"] if s["person_id"]==w.pids[1])
    assert [s["code"] for s in b["signals"]]==["support"]
    other=attention_world()
    assert summary(client,other)["settings"]["target_grade2"] is None
    cfg["grade_score_drop"]=0
    assert client.put("/api/v1/teaching/diagnosis/attention/settings",params=params(w),json=cfg).status_code==422


def test_statistics_exclusion_only_suppresses_homework(client, attention_world):
    from app.db.workspace_models import HomeworkStatsExclusion
    w=attention_world()
    w.db.add(HomeworkStatsExclusion(data_domain="teaching",class_ref_id=w.cls.id,identity_id=w.pids[0]))
    w.db.commit()
    a=next(s for s in summary(client,w)["students"] if s["person_id"]==w.pids[0])
    assert a["categories"]==["learning"]


def test_empty_class_and_out_of_scope_writes(client, attention_world):
    from app.db.workspace_models import TeachingClass
    w=attention_world()
    empty=TeachingClass(academic_year_id=w.ay.id,grade=2,subject="物理",label="合成空班")
    w.db.add(empty);w.db.commit()
    p={**params(w),"teaching_class_id":empty.id}
    assert client.get("/api/v1/teaching/diagnosis/attention",params=p).json()["students"]==[]
    req=dict(evidence_key="stale",measures="合成跟进",review_date=w.today.isoformat())
    assert client.post(f"/api/v1/teaching/diagnosis/attention/{w.pids[0]}/follow-ups",params=p,json=req).status_code==404
    assert client.get("/api/v1/teaching/diagnosis/attention",params={**params(w),"teaching_class_id":999999}).status_code==404


def test_followup_snapshot_due_improvement_close_recurrence_and_duplicate(client, attention_world):
    from app.db.workspace_models import WsStudentNote
    from app.diagnosis.attention import attention_summary
    w=attention_world()
    a=summary(client,w)["students"][0]
    req=dict(evidence_key=a["evidence_key"],measures="面谈核查合成错题",review_date=(w.today+timedelta(days=7)).isoformat())
    url=f"/api/v1/teaching/diagnosis/attention/{w.pids[0]}/follow-ups"
    r=client.post(url,params=params(w),json=req)
    assert r.status_code==200, r.text
    nid=r.json()["id"]
    original=json.loads(json.dumps(w.db.get(WsStudentNote,nid).baseline_value))
    assert client.post(url,params=params(w),json=req).status_code==409
    d=summary(client,w)
    a=next(s for s in d["students"] if s["person_id"]==w.pids[0])
    assert not a["active"] and a["followups"]
    # Improvement exits current risk but never removes the scheduled review.
    for f in w.facts[::3]: f.grade_score=95
    w.db.commit()
    d=attention_summary(w.db,replace(w.ctx,as_of=w.today+timedelta(days=7)))
    a=next(s for s in d["students"] if s["person_id"]==w.pids[0])
    assert any(s["category"]=="review" for s in a["signals"])
    assert a["followups"][0]["baseline"]==original["attention"]
    assert w.db.get(WsStudentNote,nid).baseline_value==original
    # New dated quality evidence is a different signal and resurfaces.
    w.db.get(WsStudentNote,nid).review_date=w.today
    w.db.commit()
    r=client.post(f"/api/v1/teaching/diagnosis/attention/follow-ups/{nid}/finish",params=params(w),json={"result":"已核对合成情况"})
    assert r.status_code==200,r.text
    w.db.expire_all()
    assert w.db.get(WsStudentNote,nid).status=="done"
    assert w.db.get(WsStudentNote,nid).baseline_value==original
    assert client.post(f"/api/v1/teaching/diagnosis/attention/follow-ups/{nid}/finish",params=params(w),json={"result":"重复"}).status_code==409


def test_stale_evidence_and_blank_measures_zero_write(client, attention_world):
    from app.db.workspace_models import WsStudentNote
    w=attention_world()
    before=w.db.query(WsStudentNote).count()
    req=dict(evidence_key="stale",measures="核对",review_date=w.today.isoformat())
    url=f"/api/v1/teaching/diagnosis/attention/{w.pids[0]}/follow-ups"
    assert client.post(url,params=params(w),json=req).status_code==409
    req["measures"]=" "
    assert client.post(url,params=params(w),json=req).status_code==422
    assert w.db.query(WsStudentNote).count()==before


def test_no_rate_or_correlation_needed_for_achievement_changes(client, attention_world):
    w=attention_world()
    for b in w.batches: b.expected_members_json="[]"
    w.db.commit()
    a=next(s for s in summary(client,w)["students"] if s["person_id"]==w.pids[0])
    assert "learning" in a["categories"]
    assert "correlations" not in a


def test_new_homework_event_reawakens_while_same_evidence_is_waiting(client, attention_world):
    from app.db import workspace_models as wm
    w=attention_world()
    a=summary(client,w)["students"][0]
    r=client.post(f"/api/v1/teaching/diagnosis/attention/{w.pids[0]}/follow-ups",
        params=params(w),json=dict(evidence_key=a["evidence_key"],measures="合成面谈",
        review_date=(w.today+timedelta(days=7)).isoformat()))
    assert r.status_code==200
    a=next(s for s in summary(client,w)["students"] if s["person_id"]==w.pids[0])
    assert not a["active"]
    batch=wm.HomeworkAssignment(data_domain="teaching",academic_year_id=w.ay.id,class_ref_id=w.cls.id,
        subject="物理",homework_type="合成新作业",assigned_date=w.today,batch_token=uuid4().hex,
        expected_members_json=json.dumps(w.pids))
    w.db.add(batch);w.db.flush()
    w.db.add(wm.HomeworkSubmission(assignment_id=batch.id,person_id=w.pids[0],submission_status="missing"))
    w.db.commit()
    a=next(s for s in summary(client,w)["students"] if s["person_id"]==w.pids[0])
    assert a["active"] and a["followups"]
    assert [s["code"] for s in a["signals"]]==["missing"]
    assert a["signals"][0]["severity"]==3


def test_shared_scores_authorization_and_private_notes_never_cross(client, attention_world):
    from app.db import workspace_models as wm
    w=attention_world()
    hc=wm.AdministrativeClass(academic_year_id=w.ay.id,grade=2,class_num=31,label="合成关联行政班")
    hp=wm.WsStudentIdentity(data_domain="homeroom",display_name="合成另一域身份")
    w.db.add_all([hc,hp]);w.db.flush()
    w.db.add(wm.Enrollment(admin_class_id=hc.id,identity_id=hp.id,valid_from=w.ay.start_date,status="active"))
    link=wm.HomeroomTeachingLink(admin_class_id=hc.id,teaching_class_id=w.cls.id,academic_year_id=w.ay.id,
        subject="物理",valid_from=w.ay.start_date,share_categories="roster,current_subject_score")
    w.db.add(link);w.db.flush()
    w.db.add(wm.LinkedStudent(link_id=link.id,homeroom_identity_id=hp.id,teaching_identity_id=w.pids[0],confirm_basis="synthetic"))
    # Remove native A rows. T may see only the explicitly linked subject.
    for f in w.facts[::3]:
        f.data_domain="homeroom";f.class_ref_id=hc.id;f.identity_id=hp.id;f.teaching_exam_id=None
    # Only the shared legacy exam remains; platform exams with the same day/name
    # are separate identities and deliberately cannot establish a trend.
    for i,f in enumerate(w.facts):
        if i % 3: w.db.delete(f)
    w.db.add(wm.WsStudentNote(data_domain="homeroom",person_id=hp.id,date=w.today,category="家访",
        content="合成私密档案",follow_up="合成私密待办",review_date=w.today,status="open"))
    w.db.commit()
    a=next(s for s in summary(client,w)["students"] if s["person_id"]==w.pids[0])
    # 2026-10 两域成绩不互通：hp 的 H 域成绩行即使已 link + current_subject_score
    # 授权也不再投影进 A 的教学时间线——"learning" 类风险只能来自本域事实
    assert "learning" not in a["categories"] and not a["followups"]
    assert "合成私密" not in json.dumps(summary(client,w),ensure_ascii=False)
    link.share_categories="roster"
    w.db.commit()
    a=next(s for s in summary(client,w)["students"] if s["person_id"]==w.pids[0])
    # 2026-10 两域成绩不互通：撤销成绩共享授权后教学侧同样读不到（隔离恒成立）
    assert "learning" not in a["categories"]


def test_get_is_readonly_future_exam_ignored_and_foreign_followup_rejected(client, attention_world):
    from app.db import workspace_models as wm
    w=attention_world()
    before=w.db.query(wm.WsStudentNote).count()
    for f in w.facts[-3:]:
        f.exam_date=w.today+timedelta(days=1)
        w.db.get(wm.TeachingExamScope,f.teaching_exam_id).exam_date=f.exam_date
    foreign=wm.WsStudentNote(data_domain="homeroom",person_id=w.pids[0],date=w.today,
        category="观察",content="合成其他域记录",status="open",review_date=w.today)
    w.db.add(foreign);w.db.commit()
    a=next(s for s in summary(client,w)["students"] if s["person_id"]==w.pids[0])
    assert len(a["timeline"])==2
    assert w.db.query(wm.WsStudentNote).count()==before+1
    r=client.post(f"/api/v1/teaching/diagnosis/attention/follow-ups/{foreign.id}/finish",
        params=params(w),json={"result":"不得关闭"})
    assert r.status_code==404
    w.db.expire_all()
    assert w.db.get(wm.WsStudentNote,foreign.id).status=="open"
