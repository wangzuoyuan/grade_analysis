"""Two-step upload isolation and token safety."""

from pathlib import Path
import importlib

import pytest
from fastapi.testclient import TestClient

from app.main import app
ingest_router = importlib.import_module("app.ingest.router")


@pytest.fixture
def client(tmp_path, monkeypatch):
    raw = tmp_path / "raw"
    monkeypatch.setattr(ingest_router, "RAW_DIR", str(raw))
    return TestClient(app)


def test_preview_same_filename_gets_unique_tokens_and_keeps_contents(client):
    response = client.post(
        "/api/uploads/preview",
        files=[
            ("files", ("同名.xlsx", b"first", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
            ("files", ("同名.xlsx", b"second", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")),
        ],
    )
    assert response.status_code == 200, response.text
    items = response.json()["files"]
    assert [item["filename"] for item in items] == ["同名.xlsx", "同名.xlsx"]
    assert items[0]["token"] != items[1]["token"]
    assert Path(ingest_router.resolve_token_path(items[0]["token"])).read_bytes() == b"first"
    assert Path(ingest_router.resolve_token_path(items[1]["token"])).read_bytes() == b"second"


@pytest.mark.parametrize("token", ["../secret.xlsx", "/tmp/secret.xlsx", "not-a-token.xlsx", ""])
def test_commit_rejects_non_opaque_or_traversal_token(client, token):
    response = client.post(
        "/api/uploads/commit",
        json={
            "items": [{
                "token": token,
                "filename": "原始.xlsx",
                "grade": 1,
                "semester": "上",
                "exam_type": "月考",
                "year": 2026,
                "month": 9,
            }]
        },
    )
    assert response.status_code == 200
    result = response.json()["results"][0]
    assert result["parsed_ok"] is False
    assert result["filename"] == "原始.xlsx"
    assert "令牌" in result["message"]
