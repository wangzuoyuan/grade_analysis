"""教学单科导入与官方质量分析。所有确认写入同一个事务。"""
import hashlib
import json
import secrets
from datetime import date, datetime, timedelta

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from sqlalchemy import update
from sqlalchemy.orm import Session

from app.api import _queries as q, current_teacher_id, domain_endpoint
from app.core.errors import InvalidScopeParam, LinkVersionConflict, ResourceOutOfScope
from app.core.context import resolve_workspace_context
from app.db.models import get_db
from app.db.workspace_models import (AcademicYear, TeachingClass, TeachingClassMember,
    TeachingExamScope, TeachingComparisonRoster, TeachingClassQuality,
    ScoreFact, ImportBatch, WsStudentIdentity, WsStudentAlias)
from app.ingest.teaching_platform import parse, GRADE_LABELS

router = APIRouter(prefix="/teaching/platform", tags=["teaching-platform"])
SCORE_FIELDS = ("score", "grade_score", "xueji_rank", "grade_percentile")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def scope(db, year_id, grade, subject=None, target=None):
    ay = q.resolve_year(db, year_id)
    if grade not in (1,2,3):
        raise InvalidScopeParam("请明确选择高一、高二或高三")
    allowed = {r[0] for r in db.query(TeachingClass.subject).filter_by(academic_year_id=ay.id, status="active").all()}
    if not allowed:
        allowed = set(q.carryover_subjects(db, ay.id))
    subject = subject or (next(iter(allowed)) if len(allowed)==1 else None)
    if subject not in allowed:
        raise ResourceOutOfScope("学科不在本人任教范围")
    teacher_id = current_teacher_id(db)
    params,_,ids = q.build_teaching_params(db, ay.id, target, subject)
    params["grade"]=grade
    ctx = resolve_workspace_context(db, teacher_id, "teaching", params)
    tc = db.get(TeachingClass, target) if target else None
    if tc and (tc.grade not in (None,grade) or tc.academic_year_id != ay.id):
        raise InvalidScopeParam("目标教学班的年级或学年不匹配，请选择正确班级")
    return ay,subject,ctx,tc


def get_exam(db,s):
    return db.query(TeachingExamScope).filter_by(academic_year_id=s["academic_year_id"],
        grade=s["grade"],subject=s["subject"],exam_name=s["exam_name"]).one_or_none()


def get_roster(db,s):
    return db.query(TeachingComparisonRoster).filter_by(academic_year_id=s["academic_year_id"],
        grade=s["grade"],subject=s["subject"]).order_by(TeachingComparisonRoster.version.desc()).first()


def state(db,s,ctx):
    exam = get_exam(db,s)
    roster = get_roster(db,s)
    facts = db.query(ScoreFact).filter_by(data_domain="teaching",academic_year_id=s["academic_year_id"],
        subject=s["subject"],exam_name=s["exam_name"]).order_by(ScoreFact.id).all()
    aliases = db.query(WsStudentAlias).filter_by(data_domain="teaching").order_by(WsStudentAlias.id).all()
    from app.api.imports import _scope_snapshot
    return digest({"context":_scope_snapshot(ctx,s.get("teaching_class_id"),"platform"),
        "classes":[(t.id,t.grade,t.label,t.status) for t in db.query(TeachingClass).filter_by(academic_year_id=s["academic_year_id"],subject=s["subject"]).order_by(TeachingClass.id)],
        "members":[(m.id,m.identity_id,m.teaching_class_id,m.valid_from,m.valid_to) for m in db.query(TeachingClassMember).filter(TeachingClassMember.teaching_class_id.in_(list(ctx.class_ids))).order_by(TeachingClassMember.id)],
        "roster":(roster.version,roster.classes) if roster else None,
        "exam":(exam.id,exam.exam_date,exam.class_snapshot,exam.aggregates,exam.data_revision) if exam else None,
        "quality":[(v.id,v.metrics,v.data_revision) for v in db.query(TeachingClassQuality).filter_by(exam_id=exam.id).order_by(TeachingClassQuality.id)] if exam else [],
        "facts":[(f.id,f.identity_id,f.class_ref_id,f.teaching_exam_id,f.data_revision,*[getattr(f,k) for k in SCORE_FIELDS]) for f in facts],
        "aliases":[(a.id,a.identity_id,a.alias_value,a.academic_year_id,a.valid_from,a.valid_to) for a in aliases]})


def class_key(c):
    return c["class_kind"]+":"+c["class_label"]


def merged_files(s):
    students,classes,aggregates = {},{},[]
    errors=[]
    for f in s["files"]:
        if not f.get("parsed_ok"):
            errors.append({"file":f["filename"],"message":f.get("message")})
            continue
        for row in f["rows"]:
            row = {**row,"source":{"filename":f["filename"],"sheet":f["sheet"],"row":row["source_row"]}}
            row["field_sources"]={k:row["source"] for k in SCORE_FIELDS if row.get(k) is not None}
            old=students.get(row["alias"])
            if old:
                if old["name"]!=row["name"]:
                    errors.append({"alias":row["alias"],"message":"同学号不同姓名"})
                for k in SCORE_FIELDS:
                    if row.get(k) is not None and old.get(k) is not None and row[k]!=old[k]:
                        errors.append({"alias":row["alias"],"field":k,"existing":old[k],"incoming":row[k]})
                    elif row.get(k) is not None:
                        old[k]=row[k]
                        old["field_sources"][k]=row["source"]
            else:
                students[row["alias"]]=row
        for row in f["classes"]:
            key=class_key(row)
            old=classes.get(key)
            if old and any(old.get(k)!=row.get(k) for k in ("metrics","teacher_name","class_type")):
                errors.append({"class_label":row["class_label"],"message":"批内同班级统计不同"})
            classes[key]=row
        aggregates.extend(f["aggregates"])
    unique={digest({k:v for k,v in a.items() if k!="source"}):a for a in aggregates}
    return students,classes,list(unique.values()),errors


def identities(db,s,students,confirmations=None,new_aliases=(),enforce=False):
    from app.api.imports import _resolve_file_students
    mapping,new,conflicts,candidates = _resolve_file_students(db,"teaching",s["academic_year_id"],
        list(students.values()),date.fromisoformat(s["exam_date"]))
    if conflicts:
        raise LinkVersionConflict("学号姓名冲突，整批拒绝",details={"conflicts":conflicts})
    confirmations=confirmations or {}
    by_alias={}
    for c in candidates:
        by_alias.setdefault(c["alias"],[]).append(c)
    pending=[]
    for alias,choices in by_alias.items():
        if alias in new_aliases:
            new.append(students[alias])
        elif alias in confirmations:
            if confirmations[alias] not in {c["person_id"] for c in choices}:
                raise InvalidScopeParam("所选身份不属于该学号的候选")
            mapping[alias]=confirmations[alias]
        else:
            pending.extend(choices)
    if enforce and pending:
        raise LinkVersionConflict("请先逐项确认历史身份",details={"identity_candidates":pending})
    return mapping,new,pending


def plan(db,s,mapping):
    students,classes,aggregates,errors=merged_files(s)
    exam=get_exam(db,s)
    changes,conflicts,score_plans,quality_plans=[],[],[],[]
    if exam and str(exam.exam_date)!=s["exam_date"]:
        errors.append({"message":"同范围同名考试日期不同"})
    for alias,row in students.items():
        ident=mapping.get(alias)
        existing = db.query(ScoreFact).filter_by(data_domain="teaching",academic_year_id=s["academic_year_id"],
            exam_name=s["exam_name"],identity_id=ident,subject_key=s["subject"],total_key="").one_or_none() if ident else None
        if existing and (existing.class_ref_id!=s["teaching_class_id"] or existing.teaching_exam_id not in (None,exam.id if exam else None)):
            errors.append({"alias":alias,"message":"已有成绩属于其他班级/考试范围"})
            continue
        if existing and (existing.exam_date is None or str(existing.exam_date)!=s["exam_date"]):
            errors.append({"alias":alias,"field":"exam_date","existing":str(existing.exam_date) if existing.exam_date else None,
                "incoming":s["exam_date"],"message":"已有成绩的考试日期不同或未明确，请先核对；补录不改写日期"})
            continue
        supplement=s["operation"]=="supplement"
        if supplement and not existing:
            errors.append({"alias":alias,"message":"补录须先存在对应本域成绩；投影成绩请在原来源补录"})
            continue
        if supplement and row.get("score") is not None and row["score"]!=existing.score:
            errors.append({"alias":alias,"field":"score","existing":existing.score,"incoming":row["score"],"message":"补录不能修改原始分"})
        fields = ("xueji_rank","grade_percentile") if supplement and s["grade"]==1 else ("grade_score",) if supplement else SCORE_FIELDS
        updates={k:row[k] for k in fields if row.get(k) is not None and (not existing or getattr(existing,k)!=row[k])}
        for k,v in updates.items():
            before=getattr(existing,k) if existing else None
            change={"entity":f"{row['name']}（{alias}）","field":k,"existing":before,"incoming":v,"source":row["field_sources"][k]}
            changes.append(change)
            if before is not None:
                conflicts.append(change)
        score_plans.append((row,existing,updates))
    for key,row in classes.items():
        existing=db.query(TeachingClassQuality).filter_by(exam_id=exam.id,class_kind=row["class_kind"],class_label=row["class_label"]).one_or_none() if exam else None
        # 教师和班型也是考试事实的一部分，与指标一同核对。
        new_metrics={**row["metrics"],"teacher_name":row["teacher_name"],"class_type":row["class_type"]}
        for k,v in new_metrics.items():
            before=(existing.metrics or {}).get(k) if existing else None
            if v is not None and v!=before:
                change={"entity":key,"field":k,"existing":before,"incoming":v,"source":row["source"]}
                changes.append(change)
                if before is not None:
                    conflicts.append(change)
        quality_plans.append((row,existing,new_metrics))
    if exam:
        old_aggregates={(a["class_kind"],a["label"]):a for a in exam.aggregates or []}
        for a in aggregates:
            old=old_aggregates.get((a["class_kind"],a["label"]))
            if old and (old["metrics"]!=a["metrics"] or old["coverage"]!=a["coverage"]):
                change={"entity":a["label"],"field":"aggregate","existing":old["metrics"],"incoming":a["metrics"],"source":a["source"]}
                changes.append(change)
                conflicts.append(change)
    roster=get_roster(db,s)
    baseline = exam.class_snapshot if exam else roster.classes if roster else []
    wanted={class_key(c):dict(c) for c in baseline}
    roster_changes=[]
    provided_kinds={c["class_kind"] for c in classes.values()}
    if s.get("replace_roster"):
        for key,c in list(wanted.items()):
            if c["class_kind"] in provided_kinds and key not in classes:
                wanted.pop(key)
                roster_changes.append({"action":"remove","class_kind":c["class_kind"],"class_label":c["class_label"]})
    for key,c in classes.items():
        if key not in wanted:
            tc=db.query(TeachingClass).filter_by(academic_year_id=s["academic_year_id"],subject=s["subject"],label=c["class_label"]).first() if c["class_kind"]=="teaching" else None
            if tc and tc.grade!=s["grade"] and not (tc.grade is None and tc.id==s["teaching_class_id"]):
                tc=None
            wanted[key]={"class_kind":c["class_kind"],"class_label":c["class_label"],"class_type":c["class_type"],"teacher_name":c["teacher_name"],"managed_class_id":tc.id if tc else None}
            roster_changes.append({"action":"add","class_kind":c["class_kind"],"class_label":c["class_label"]})
        else:
            wanted[key]={**wanted[key],**{k:c[k] for k in ("class_type","teacher_name") if c[k] is not None}}
    if exam and exam.class_snapshot and roster_changes:
        change={"entity":s["exam_name"],"field":"class_snapshot","existing":exam.class_snapshot,"incoming":list(wanted.values())}
        changes.append(change)
        conflicts.append(change)
    return {"changes":changes,"conflicts":conflicts,"errors":errors,"roster_changes":roster_changes,
        "snapshot":list(wanted.values()),"score_plans":score_plans,"quality_plans":quality_plans,"aggregates":aggregates}


@router.post("/imports/preview")
@domain_endpoint
async def preview(request: Request, db: Session=Depends(get_db)):
    form=await request.form()
    try:
        year_id=int(form.get("academic_year_id", ""))
        grade=int(form.get("grade", ""))
        target=int(form["teaching_class_id"]) if form.get("teaching_class_id") else None
        exam_date=date.fromisoformat(str(form.get("exam_date", "")))
    except (ValueError,TypeError):
        raise InvalidScopeParam("请明确填写学年、年级、考试日期和有效班级") from None
    ay,subject,ctx,tc=scope(db,year_id,grade,form.get("subject"),target)
    name=str(form.get("exam_name", "")).strip()
    operation=str(form.get("operation", "initial"))
    if not name or len(name)>128 or operation not in ("initial","supplement"):
        raise InvalidScopeParam("考试名称必填，导入类型须为首次/补录")
    if not ay.start_date<=exam_date<=ay.end_date:
        raise InvalidScopeParam("考试日期不在目标学年内")
    files=[]
    for upload in form.getlist("files"):
        if not getattr(upload,"filename",None):
            continue
        data=await upload.read()
        try:
            f=parse(data,upload.filename,grade,subject)
            f["parsed_ok"]=True
        except Exception as exc:
            f={"filename":upload.filename,"parsed_ok":False,"kind":"unknown","message":str(exc),"rows":[],"classes":[],"aggregates":[]}
        f["content_digest"]=hashlib.sha256(data).hexdigest()
        files.append(f)
    if not files:
        raise InvalidScopeParam("请选择 .xlsx 文件")
    if any(f["rows"] for f in files) and tc is None:
        raise InvalidScopeParam("学生明细必须指定本人管理的教学班；纯质量表无需选择")
    s={"kind":"teaching_platform","academic_year_id":ay.id,"grade":grade,"subject":subject,
        "exam_name":name,"exam_date":exam_date.isoformat(),"teaching_class_id":target,"operation":operation,"files":files,
        "replace_roster":form.get("replace_roster")=="true"}
    students,_,_,_=merged_files(s)
    mapping,new,candidates=identities(db,s,students)
    p=plan(db,s,mapping)
    s["state_digest"]=state(db,s,ctx)
    token=secrets.token_hex(24)
    expires=datetime.utcnow()+timedelta(minutes=30)
    db.add(ImportBatch(token=token,data_domain="teaching",scope_json=json.dumps(s,ensure_ascii=False),
        content_digest=digest([f["content_digest"] for f in files]),expires_at=expires))
    db.commit()
    return {"token":token,"expires_at":expires.isoformat(),"scope":{k:s[k] for k in ("academic_year_id","grade","subject","exam_name","exam_date","operation")},
        "items":[{k:v for k,v in f.items() if k not in ("rows",)} | {"row_count":len(f["rows"])} for f in files],
        **{k:p[k] for k in ("changes","conflicts","errors","roster_changes")},
        "new_students":new,"identity_candidates":candidates}


class ConfirmRequest(BaseModel):
    token: str
    revise: bool=False
    accept_roster_changes: bool=False
    identity_confirmations: dict[str,int]=Field(default_factory=dict)
    identity_new_aliases: list[str]=Field(default_factory=list)


@router.post("/imports/confirm")
@domain_endpoint
def confirm(req: ConfirmRequest, db: Session=Depends(get_db)):
    from app.api.shared import _load_preview_batch
    from app.api.imports import _valid_member_row
    batch=_load_preview_batch(db,req.token)
    s=json.loads(batch.scope_json or "{}")
    if s.get("kind")!="teaching_platform":
        raise LinkVersionConflict("不是教学平台预览令牌")
    try:
        # 先取得 SQLite 写锁，再重读和校验预览版本，避免并发确认丢失修订。
        claimed=db.execute(update(ImportBatch).where(ImportBatch.id==batch.id,ImportBatch.status=="pending").values(status="applying"))
        if claimed.rowcount!=1:
            raise LinkVersionConflict("令牌已消费")
        ay,subject,ctx,tc=scope(db,s["academic_year_id"],s["grade"],s["subject"],s["teaching_class_id"])
        if state(db,s,ctx)!=s["state_digest"]:
            raise LinkVersionConflict("预览后的数据或范围已变化，请重新预览")
        students,_,_,_=merged_files(s)
        mapping,new,candidates=identities(db,s,students,req.identity_confirmations,req.identity_new_aliases,True)
        p=plan(db,s,mapping)
        if p["errors"]:
            raise LinkVersionConflict("整批校验失败，零写入",details={"conflicts":p["errors"]})
        if p["conflicts"] and not req.revise:
            raise LinkVersionConflict("存在不同值，请核对差异后确认修订",details={"conflicts":p["conflicts"]})
        if p["roster_changes"] and not req.accept_roster_changes:
            raise LinkVersionConflict("请确认新增对比参照；不会加入管理班或名册",details={"roster_changes":p["roster_changes"]})
        if s["operation"]=="supplement" and new:
            raise LinkVersionConflict("补录不创建学生")
        exam=get_exam(db,s)
        if not exam:
            exam=TeachingExamScope(academic_year_id=ay.id,grade=s["grade"],subject=subject,
                exam_name=s["exam_name"],exam_date=date.fromisoformat(s["exam_date"]),class_snapshot=[],aggregates=[])
            db.add(exam)
            db.flush()
        prior={"snapshot":exam.class_snapshot,"aggregates":exam.aggregates,"revision":exam.data_revision}
        changed_snapshot=exam.class_snapshot!=p["snapshot"]
        exam.class_snapshot=p["snapshot"]
        # 同一汇总覆盖/标签的修订替换本项；未上传的保留，不因补录丢失。
        aggregate_map={(a["class_kind"],a["label"]):a for a in exam.aggregates or []}
        for a in p["aggregates"]:
            key=(a["class_kind"],a["label"])
            old=aggregate_map.get(key)
            if not old or {k:v for k,v in old.items() if k!="source"}!={k:v for k,v in a.items() if k!="source"}:
                aggregate_map[key]=a
        next_aggregates=list(aggregate_map.values())
        if changed_snapshot or digest(exam.aggregates)!=digest(next_aggregates) or p["changes"]:
            exam.data_revision=(exam.data_revision or 1)+1
        exam.aggregates=next_aggregates
        roster=get_roster(db,s)
        if p["quality_plans"] and (roster is None or exam.exam_date>=roster.source_exam_date):
            current={class_key(c):c for c in roster.classes} if roster else {}
            incoming={class_key(c):c for c in p["snapshot"] if class_key(c) in {class_key(row) for row,_,_ in p["quality_plans"]}}
            if s.get("replace_roster"):
                kinds={c["class_kind"] for c in incoming.values()}
                current={k:c for k,c in current.items() if c["class_kind"] not in kinds or k in incoming}
            current.update(incoming)
            if not roster or digest(list(current.values()))!=digest(roster.classes) or exam.exam_date>roster.source_exam_date:
                db.add(TeachingComparisonRoster(academic_year_id=ay.id,grade=s["grade"],subject=subject,
                    version=roster.version+1 if roster else 1,source_exam_date=exam.exam_date,classes=list(current.values())))
        created=members=imported=revised=skipped=0
        start=max(ay.start_date,exam.exam_date)
        for item in new:
            ident=WsStudentIdentity(data_domain="teaching",display_name=item["name"])
            db.add(ident)
            db.flush()
            mapping[item["alias"]]=ident.id
            created+=1
        if tc and students and s["operation"]=="initial":
            tc.grade=s["grade"]
            for alias in students:
                pid=mapping[alias]
                if not db.query(WsStudentAlias).filter_by(identity_id=pid,data_domain="teaching",academic_year_id=ay.id,alias_value=alias).first():
                    db.add(WsStudentAlias(identity_id=pid,data_domain="teaching",academic_year_id=ay.id,
                        alias_scope=str(ay.id),alias_value=alias,source="platform_import",valid_from=start))
                if _valid_member_row(db,tc.id,pid,exam.exam_date) is None:
                    # 曾离班不能通过成绩导入静默重新招入。
                    if db.query(TeachingClassMember).filter_by(teaching_class_id=tc.id,identity_id=pid).first():
                        raise LinkVersionConflict("该学生考试时不在目标班，请先核对成员有效期")
                    db.add(TeachingClassMember(teaching_class_id=tc.id,identity_id=pid,valid_from=start,source="platform_import"))
                    members+=1
        for row,existing,updates in p["score_plans"]:
            if existing is None:
                existing=ScoreFact(data_domain="teaching",academic_year_id=ay.id,exam_name=exam.exam_name,
                    exam_date=exam.exam_date,subject=subject,identity_id=mapping[row["alias"]],class_ref_id=tc.id,
                    teaching_exam_id=exam.id,source="platform_import",score=None)
                db.add(existing)
                imported+=1
            elif updates:
                revised+=1
                existing.data_revision=(existing.data_revision or 1)+1
            else:
                skipped+=1
            existing.teaching_exam_id=exam.id
            for k,v in updates.items():
                setattr(existing,k,v)
            existing.field_sources={**(existing.field_sources or {}),**{k:row["field_sources"][k] for k in updates}}
        for row,existing,metrics in p["quality_plans"]:
            clean={k:v for k,v in metrics.items() if v is not None}
            if existing is None:
                db.add(TeachingClassQuality(exam_id=exam.id,class_kind=row["class_kind"],class_label=row["class_label"],metrics=metrics,source=row["source"]))
                imported+=1
            elif any(existing.metrics.get(k)!=v for k,v in clean.items()):
                existing.metrics={**existing.metrics,**clean}
                existing.source=row["source"]
                existing.data_revision+=1
                revised+=1
            else:
                skipped+=1
        s["receipt"]={"changes":p["changes"],"previous_exam":prior}
        batch.scope_json=json.dumps(s,ensure_ascii=False)
        batch.status="confirmed"
        db.commit()
        return {"exam_id":exam.id,"imported":imported,"revised":revised,"skipped":skipped,"students_created":created,"members_synced":members}
    except Exception:
        db.rollback()
        raise


def checked_exam(db,exam_id):
    exam=db.get(TeachingExamScope,exam_id)
    if not exam:
        raise ResourceOutOfScope("考试不存在")
    _,_,ctx,_=scope(db,exam.academic_year_id,exam.grade,exam.subject)
    return exam,ctx


@router.get("/exams")
@domain_endpoint
def list_exams(academic_year_id:int,grade:int,subject:str|None=None,db:Session=Depends(get_db)):
    ay,subject,_,_=scope(db,academic_year_id,grade,subject)
    return {"exams":[{"id":e.id,"exam_name":e.exam_name,"exam_date":e.exam_date.isoformat(),"grade":e.grade,"subject":e.subject}
        for e in db.query(TeachingExamScope).filter_by(academic_year_id=ay.id,grade=grade,subject=subject).order_by(TeachingExamScope.exam_date.desc(),TeachingExamScope.id.desc())]}


def student_rows(db,exam,ctx,target=None):
    from app.api.analysis import _rank_map
    ids=[c.id for c in db.query(TeachingClass).filter_by(academic_year_id=exam.academic_year_id,subject=exam.subject,grade=exam.grade) if c.id in ctx.class_ids]
    if target is not None:
        if target not in ids:
            raise ResourceOutOfScope("教学班不在该考试年级范围")
        ids=[target]
    if not ids:
        return []
    members=q.members_at(db,"teaching",ids,exam.exam_date)
    entries=q.readable_facts(db,ctx,exam.exam_name,member_ids=members,class_ids=ids,teaching_exam_id=exam.id)
    entries=[e for e in entries if e.fact.teaching_exam_id==exam.id or (e.source_domain=="homeroom" and e.fact.exam_date==exam.exam_date)]
    ranks=_rank_map([(e.person_id,e.fact.score) for e in entries])
    names=q.names_for(db,[e.person_id for e in entries])
    labels={c.id:c.label for c in db.query(TeachingClass).filter(TeachingClass.id.in_(ids))}
    return [{"person_id":e.person_id,"name":names.get(e.person_id),"class_id":e.class_ref_id,
        "class_label":labels.get(e.class_ref_id),"score":e.fact.score,"grade_score":e.fact.grade_score,
        "grade_label":GRADE_LABELS.get(e.fact.grade_score),"xueji_rank":e.fact.xueji_rank,
        "grade_percentile":e.fact.grade_percentile,"rank":ranks.get(e.person_id),"source_domain":e.source_domain} for e in entries]


@router.get("/exams/{exam_id}/students")
@domain_endpoint
def students(exam_id:int,teaching_class_id:int|None=None,db:Session=Depends(get_db)):
    exam,ctx=checked_exam(db,exam_id)
    rows=student_rows(db,exam,ctx,teaching_class_id)
    return {"students":sorted(rows,key=lambda r:(r["rank"] is None,r["rank"] or 0,r["person_id"])),
        "metadata":{"grade":exam.grade,"subject":exam.subject,"membership_basis":"exam","rank_basis":"selected_managed_classes"}}


def comparison(db,exam,ctx):
    quality={r.class_kind+":"+r.class_label:r for r in db.query(TeachingClassQuality).filter_by(exam_id=exam.id)}
    samples=student_rows(db,exam,ctx)
    classes=[]
    for c in exam.class_snapshot:
        row=quality.get(class_key(c))
        metrics=row.metrics if row else {}
        sample=[r for r in samples if r["class_id"]==c.get("managed_class_id") and r["score"] is not None]
        estimated=sum(r["score"] for r in sample)/len(sample) if sample else None
        classes.append({**c,"id":class_key(c),"metrics":metrics,"source":"official" if row else "estimated" if sample else "unavailable",
            "raw_avg":metrics.get("raw_avg") if row else estimated,"grade_avg":metrics.get("grade_avg"),
            "sample_count":len(sample),"provenance":row.source if row else None})
    overall={"raw":None,"grade":None}
    warnings=[]
    keys={c["id"] for c in classes}
    official=[c for c in classes if c["source"]=="official"]
    for basis in overall:
        field="raw_avg" if basis=="raw" else "grade_avg"
        if basis=="grade" and exam.grade==1:
            continue
        matches=[a for a in exam.aggregates if a.get("coverage_verified") and set(a["coverage"])==keys and a["metrics"].get(field) is not None]
        values={a["metrics"][field] for a in matches}
        if len(values)>1:
            warnings.append(f"同范围官方{('原始' if basis=='raw' else '等级')}均分汇总行不一致，总体与均差暂不可用")
        elif len(values)==1 and len({c["class_kind"] for c in classes})==1:
            overall[basis]={"value":next(iter(values)),"source":"official","method":"matching_aggregate","class_keys":sorted(keys)}
        elif classes and len(official)==len(classes) and {c["class_kind"] for c in classes}=={"administrative"}:
            valid=all(c.get(field) is not None and c["metrics"].get("actual_count") is not None for c in classes)
            if basis=="grade":
                valid=valid and all(all(b.get("count") is not None for b in c["metrics"].get("bands",[])) and sum(b.get("count",0) for b in c["metrics"].get("bands",[]))==c["metrics"]["actual_count"] for c in classes)
            count=sum(c["metrics"].get("actual_count") or 0 for c in classes)
            if valid and count:
                overall[basis]={"value":sum(c[field]*c["metrics"]["actual_count"] for c in classes)/count,
                    "source":"official","method":"weighted_classes","class_keys":sorted(keys)}
        for source in ("official","estimated"):
            rows=sorted([c for c in classes if c["source"]==source and c.get(field) is not None],key=lambda c:-c[field])
            prev=None
            rank=0
            for i,c in enumerate(rows,1):
                if c[field]!=prev:
                    rank=i
                c[basis+"_rank"]=rank
                prev=c[field]
                c[basis+"_diff"]=c[field]-overall[basis]["value"] if overall[basis] and source==overall[basis]["source"] else None
    if classes and overall["raw"] is None:
        warnings.append("总体均分不可合并：班级覆盖、来源或教学班与行政班的人群互斥性未明确；均差显示 —")
    if any(c["metrics"].get("stddev_basis")=="unspecified" for c in classes):
        warnings.append("官方标准差未注明原始分/等级分口径，按来源值展示")
    return {"exam":{"id":exam.id,"exam_name":exam.exam_name,"grade":exam.grade,"subject":exam.subject,"exam_date":str(exam.exam_date)},
        "metadata":{"mode":"teaching","membership_basis":"exam_snapshot","data_revision":exam.data_revision},
        "classes":classes,"overall":overall,"warnings":warnings}


@router.get("/exams/{exam_id}/compare")
@domain_endpoint
def compare(exam_id:int,db:Session=Depends(get_db)):
    exam,ctx=checked_exam(db,exam_id)
    return comparison(db,exam,ctx)
