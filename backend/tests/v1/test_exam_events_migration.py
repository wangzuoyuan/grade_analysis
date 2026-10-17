"""0019 -> 0020 keeps raw values, references and historical domain separation."""
import os
from pathlib import Path
import subprocess
import sys


def test_event_upgrade_preserves_every_original_column_and_foreign_key(tmp_path):
    root=Path(__file__).resolve().parents[2]
    (tmp_path/'data').mkdir()
    (tmp_path/'backups').mkdir()
    env={**os.environ,'EXAM_TRACKER_DIR':str(tmp_path/'data'),
         'EXAM_TRACKER_BACKUP_DIR':str(tmp_path/'backups'),'PYTHONPATH':str(root)}
    script=r'''
from sqlalchemy import text
from app.db.schema import ensure_app_schema,_alembic_config
from app.db.models import engine
from alembic import command
ensure_app_schema()
cfg=_alembic_config()
command.downgrade(cfg,'0019')
with engine.begin() as c:
 c.execute(text("INSERT INTO academic_year(id,name,start_date,end_date) VALUES(1,'合成升级学年','2030-09-01','2031-08-31')"))
 c.execute(text("INSERT INTO administrative_class(id,academic_year_id,grade,class_num,label) VALUES(1,1,2,6,'合成行政班')"))
 c.execute(text("INSERT INTO teaching_class(id,academic_year_id,grade,subject,label,sort_order) VALUES(1,1,2,'物理','合成教学班',0)"))
 for i,domain in [(1,'homeroom'),(2,'teaching')]:
  c.execute(text("INSERT INTO ws_student_identity(id,data_domain,display_name) VALUES(:id,:domain,'合成升级学生')"),dict(id=i,domain=domain))
 c.execute(text("INSERT INTO teaching_exam_scope(id,academic_year_id,grade,subject,exam_name,exam_date,class_snapshot,aggregates,data_revision) VALUES(1,1,2,'物理','合成同场','2030-11-01','[]','[]',1)"))
 c.execute(text("INSERT INTO teaching_class_quality(exam_id,class_kind,class_label,metrics,source,data_revision) VALUES(1,'teaching','合成教学班','{}','{}',1)"))
 for domain,pid,subject,total,score,grade,exam in [('homeroom',1,'物理','',0,None,None),('homeroom',1,'数学','',80,None,None),('homeroom',1,None,'主三门',None,None,None),('teaching',2,'物理','',0,65,1)]:
  c.execute(text("INSERT INTO score_fact(data_domain,academic_year_id,exam_name,exam_date,class_ref_id,identity_id,subject,total_type,subject_key,total_key,score,grade_score,teaching_exam_id,source,data_revision,exam_date_precision,field_sources) VALUES(:domain,1,'合成同场','2030-11-01',1,:pid,:subject,:total,:sk,:tk,:score,:grade,:exam,'synthetic',3,'day','{}')"),dict(domain=domain,pid=pid,subject=subject,total=total or None,sk=subject or '',tk=total,score=score,grade=grade,exam=exam))
 c.execute(text("INSERT INTO workspace_class_average(data_domain,academic_year_id,exam_name,exam_date,grade,class_num,subject_averages,total_averages,source,data_revision) VALUES('homeroom',1,'合成同场','2030-11-01',2,6,'{}','{}','synthetic',1)"))
 tables=['score_fact','teaching_exam_scope','teaching_class_quality','workspace_class_average']
 columns={t:[r[1] for r in c.execute(text('PRAGMA table_info('+t+')'))] for t in tables}
 before={t:c.execute(text('SELECT '+','.join(columns[t])+' FROM '+t+' ORDER BY id')).fetchall() for t in tables}
command.upgrade(cfg,'head')
with engine.connect() as c:
 for t in tables:
  assert c.execute(text('SELECT '+','.join(columns[t])+' FROM '+t+' ORDER BY id')).fetchall()==before[t],t
 assert c.execute(text('PRAGMA foreign_key_check')).fetchall()==[]
 assert c.execute(text('PRAGMA integrity_check')).scalar()=='ok'
 assert c.execute(text('PRAGMA foreign_keys')).scalar()==1
 assert c.execute(text('SELECT COUNT(*) FROM workspace_exam_event')).scalar()==2
 assert c.execute(text('SELECT COUNT(DISTINCT exam_event_id) FROM score_fact')).scalar()==2
 assert c.execute(text('SELECT COUNT(*) FROM score_fact WHERE exam_event_id IS NULL')).scalar()==0
 assert c.execute(text('SELECT COUNT(*) FROM workspace_exam_event WHERE merged_into_id IS NOT NULL')).scalar()==0
 assert c.execute(text("SELECT exam_event_id FROM workspace_class_average")).scalar()==c.execute(text("SELECT exam_event_id FROM score_fact WHERE data_domain='homeroom' LIMIT 1")).scalar()
 assert c.execute(text('SELECT version_num FROM alembic_version')).scalar()=='0020'
command.downgrade(cfg,'0019')
with engine.connect() as c:
 assert c.execute(text('PRAGMA foreign_key_check')).fetchall()==[]
 assert c.execute(text('PRAGMA foreign_keys')).scalar()==1
 for t in tables:
  assert c.execute(text('SELECT '+','.join(columns[t])+' FROM '+t+' ORDER BY id')).fetchall()==before[t],t
command.upgrade(cfg,'head')
'''
    result=subprocess.run([sys.executable,'-c',script],cwd=root,env=env,capture_output=True,text=True)
    assert result.returncode==0,result.stdout+result.stderr
