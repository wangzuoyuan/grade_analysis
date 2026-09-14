"""登录鉴权：内网免登录、外网（命中 PUBLIC_HOST）才要求会话。

用真实 app + TestClient，靠 monkeypatch 切换环境变量；鉴权函数在每次请求时
读 env，因此无需重建 app。
"""

import pytest
from fastapi.testclient import TestClient

from app.main import app

PROTECTED = "/api/exams"   # 受保护的只读端点
PUBLIC = "nas.example.com"  # 假设的 DDNS 域名


@pytest.fixture
def client():
    return TestClient(app)


def test_no_password_means_open(client, monkeypatch):
    """未设 APP_PASSWORD → 鉴权关闭，任何入口都放行。"""
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    r = client.get(PROTECTED)
    assert r.status_code != 401


def test_external_requires_login(client, monkeypatch):
    """设了密码 + Host 命中 PUBLIC_HOST + 无 cookie → 401。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("PUBLIC_HOST", PUBLIC)
    r = client.get(PROTECTED, headers={"host": PUBLIC})
    assert r.status_code == 401


def test_internal_ip_bypasses_login(client, monkeypatch):
    """即使设了密码，内网 IP 入口（Host 不等于域名）也放行。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("PUBLIC_HOST", PUBLIC)
    r = client.get(PROTECTED, headers={"host": "192.168.1.50:8080"})
    assert r.status_code != 401


def test_login_then_access(client, monkeypatch):
    """外网入口：登录拿 cookie 后即可访问受保护端点。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("PUBLIC_HOST", PUBLIC)

    # 错误密码 → 401
    bad = client.post("/api/login", json={"password": "wrong"}, headers={"host": PUBLIC})
    assert bad.status_code == 401

    # 正确密码 → 200 并种 cookie
    ok = client.post("/api/login", json={"password": "s3cret"}, headers={"host": PUBLIC})
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    # 带 cookie 再访问 → 放行
    r = client.get(PROTECTED, headers={"host": PUBLIC})
    assert r.status_code != 401


def test_auth_status_reflects_entry(client, monkeypatch):
    """status 按入口返回 required：内网 false、外网 true。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("PUBLIC_HOST", PUBLIC)

    ext = client.get("/api/auth/status", headers={"host": PUBLIC}).json()
    assert ext["required"] is True

    intl = client.get("/api/auth/status", headers={"host": "10.0.0.2:8080"}).json()
    assert intl["required"] is False
