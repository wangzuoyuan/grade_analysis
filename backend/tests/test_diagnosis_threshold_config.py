"""进退步全局阈值：默认边界、持久化、恢复及与长期名次分段隔离。"""

import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def clear_threshold_row():
    from app.db.models import DiagnosisThresholdConfig, SessionLocal

    def clear():
        with SessionLocal() as db:
            row = db.get(DiagnosisThresholdConfig, 1)
            if row is not None:
                db.delete(row)
                db.commit()

    clear()
    yield
    clear()


def _trend(*ranks, thresholds=None):
    from app.diagnosis.features import _trend_indicator

    exams = {
        f"第{i}次": {"totals": {"主三门": SimpleNamespace(score=250, xueji_rank=rank, grade_rank=None)}}
        for i, rank in enumerate(ranks, start=1)
    }
    return _trend_indicator(exams, list(exams), thresholds)


def test_default_boundaries_split_net_direction_and_each_streak():
    from app.diagnosis.features import _streak_of

    # 净变化恰好 80 可判进步；两次各 40 不构成连续进步。
    at_boundary = _trend(300, 260, 220)
    assert at_boundary["direction_recent"] == "进步"
    assert at_boundary["streak"] == {"kind": None, "count": 0}
    assert (at_boundary["direction_threshold"], at_boundary["streak_threshold"]) == (80, 50)
    assert _trend(300, 260, 221)["direction_recent"] == "持平"
    assert _trend(200, 240, 280)["direction_recent"] == "退步"
    assert _trend(200, 240, 279)["direction_recent"] == "持平"
    assert _trend(300, 250, 200)["streak"] == {"kind": "进步", "count": 2}
    assert _trend(200, 250, 300)["streak"] == {"kind": "退步", "count": 2}
    assert _streak_of([50, 49]) == (None, 0)


def test_api_saves_custom_values_and_restores_defaults():
    from app.db.models import AnalysisConfig, DiagnosisThresholdConfig, SessionLocal
    from app.diagnosis.thresholds import get_trend_thresholds

    with TestClient(app) as client:
        initial = client.get("/api/diagnosis-threshold-config")
        assert initial.status_code == 200
        assert initial.json() == {
            "direction_rank_change": 80,
            "streak_rank_change": 50,
            "defaults": {"direction_rank_change": 80, "streak_rank_change": 50},
            "is_default": True,
        }
        custom = {"direction_rank_change": 100, "streak_rank_change": 60}
        saved = client.put("/api/diagnosis-threshold-config", json=custom)
        assert saved.status_code == 200
        assert {key: saved.json()[key] for key in custom} == custom
        assert saved.json()["is_default"] is False
        with SessionLocal() as db:
            assert get_trend_thresholds(db)["direction_rank_change"] == 100
            assert db.get(DiagnosisThresholdConfig, 1) is not None
            assert db.get(AnalysisConfig, 1) is None
        assert _trend(300, 260, 220, thresholds=saved.json())["direction_recent"] == "持平"
        assert _trend(300, 240, 180, thresholds=saved.json())["streak"]["count"] == 2
        reset = client.delete("/api/diagnosis-threshold-config")
        assert reset.status_code == 200
        assert reset.json()["is_default"] is True
        with SessionLocal() as db:
            assert db.get(DiagnosisThresholdConfig, 1) is None
        assert client.put("/api/diagnosis-threshold-config", json=initial.json()["defaults"]).json()["is_default"] is True


@pytest.mark.parametrize("payload", [
    {"direction_rank_change": 0, "streak_rank_change": 50},
    {"direction_rank_change": 80, "streak_rank_change": 1001},
    {"direction_rank_change": 80.5, "streak_rank_change": 50},
])
def test_api_rejects_invalid_values_without_overwriting(payload):
    with TestClient(app) as client:
        response = client.put("/api/diagnosis-threshold-config", json=payload)
        assert response.status_code in (400, 422)
        assert client.get("/api/diagnosis-threshold-config").json()["is_default"] is True


def test_migration_0018_upgrade_and_downgrade_in_separate_database(tmp_path):
    backend_dir = Path(__file__).resolve().parents[1]
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    backup_dir = tmp_path / "backups"
    backup_dir.mkdir()
    env = {**os.environ, "EXAM_TRACKER_DIR": str(data_dir), "EXAM_TRACKER_BACKUP_DIR": str(backup_dir)}

    def migrate(*args):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args], cwd=backend_dir, env=env,
            capture_output=True, text=True,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    migrate("upgrade", "0017")
    with sqlite3.connect(data_dir / "db.sqlite") as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='diagnosis_threshold_config'").fetchone()
    migrate("upgrade", "0018")
    with sqlite3.connect(data_dir / "db.sqlite") as db:
        db.execute("INSERT INTO diagnosis_threshold_config (id,direction_rank_change,streak_rank_change) VALUES (1,80,50)")
        assert db.execute("SELECT direction_rank_change,streak_rank_change FROM diagnosis_threshold_config").fetchone() == (80, 50)
    migrate("downgrade", "0017")
    with sqlite3.connect(data_dir / "db.sqlite") as db:
        assert not db.execute("SELECT name FROM sqlite_master WHERE name='diagnosis_threshold_config'").fetchone()
