"""Single LAN credential policy: legacy network settings never grant access."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(autouse=True)
def auth_env(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "synthetic-password")
    monkeypatch.setenv("AUTH_LAN_BYPASS_TOKEN", "synthetic-lan-" + "a" * 48)
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "0.0.0.0/0,::/0")
    monkeypatch.setenv("AUTH_TRUSTED_PROXY_NETWORKS", "0.0.0.0/0,::/0")


@pytest.mark.parametrize("peer", ["192.168.1.23", "127.0.0.1", "::1", "203.0.113.9"])
def test_legacy_network_settings_never_bypass_login(peer):
    with TestClient(app, client=(peer, 5000)) as client:
        response = client.get("/api/auth/status", headers={
            "Host": "localhost",
            "X-Forwarded-For": "192.168.1.23",
            "X-Real-IP": "192.168.1.23",
            "Forwarded": 'for=192.168.1.23;host=localhost;proto=https',
        })
        assert response.json() == {"required": True, "authed": False}
        assert client.get("/api/v1/shared/config").status_code == 401


def test_duplicate_lan_credentials_are_rejected():
    token = "synthetic-lan-" + "a" * 48
    with TestClient(app) as client:
        for values in ((token, "wrong"), ("wrong", token), (token, token)):
            response = client.get("/api/v1/shared/config", headers=[
                ("X-Lan-Bypass-Token", value) for value in values
            ])
            assert response.status_code == 401


def test_non_ascii_lan_credential_requires_login_instead_of_500():
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/api/v1/shared/config", headers=[
            (b"x-lan-bypass-token", b"\xff"),
        ])
        assert response.status_code == 401
