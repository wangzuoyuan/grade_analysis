"""Exercise real upgrade/backfill/repeat/downgrade on a private database."""
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path


def test_preserve_legacy_notes_and_attention_snapshot(tmp_path):
    backend=Path(__file__).resolve().parents[2]
    env=dict(os.environ,EXAM_TRACKER_DIR=str(tmp_path),EXAM_TRACKER_BACKUP_DIR=str(tmp_path/'backup'))
    def run(*args):
        result=subprocess.run([sys.executable,'-m','alembic',*args],cwd=backend,env=env,capture_output=True,text=True)
        assert result.returncode==0,result.stderr
    run('upgrade','0021')
    db=sqlite3.connect(tmp_path/'db.sqlite')
    db.execute("INSERT INTO ws_student_identity(id,data_domain,display_name) VALUES(1,'homeroom','迁移合成学生')")
    baseline=json.dumps({'metric':'total:主三门','value':300,'unit':'rank','source':'auto'})
    snapshot=json.dumps({'source':'attention-v1','signals':[{'code':'quality'}]})
    for nid,metric,value,state in [(1,'total:主三门',baseline,'open'),(2,'total:主三门',baseline,'done'),(3,None,snapshot,'open'),(4,None,None,None)]:
        db.execute("INSERT INTO ws_student_note(id,data_domain,person_id,date,category,content,target_metric,baseline_value,status,follow_up_done) VALUES(?,'homeroom',1,'2026-10-01','谈话','迁移合成记录',?,?,?,?)",(nid,metric,value,state,int(state=='done')))
    db.commit()
    before=db.execute('SELECT * FROM ws_student_note ORDER BY id').fetchall()
    run('upgrade','head');run('upgrade','head')
    rows=db.execute('SELECT note_id,metric,baseline_value,status FROM ws_intervention_target ORDER BY note_id').fetchall()
    assert len(rows)==2
    assert json.loads(rows[0][2])==json.loads(baseline)
    assert [r[3] for r in rows]==['open','done']
    assert db.execute('SELECT * FROM ws_student_note ORDER BY id').fetchall()==before
    assert not db.execute('PRAGMA foreign_key_check').fetchall()
    run('downgrade','0021')
    assert db.execute('SELECT * FROM ws_student_note ORDER BY id').fetchall()==before
    db.close()
