"""登录会话、内网凭据和显式 CORS 名单的行为验证。"""

import importlib
import sys

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

from app.main import app

PROTECTED = "/api/exams"   # 受保护的只读端点
PUBLIC = "nas.example.com"  # 假设的 DDNS 域名（修复后仅作为兼容 env，不参与鉴权）

@pytest.fixture
def client():
    return TestClient(app)


# ---------- 端到端：密码开关与 Host 伪造 ----------


def test_no_password_means_open(client, monkeypatch):
    """未设 APP_PASSWORD → 鉴权关闭，任何入口都放行。"""
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    r = client.get(PROTECTED)
    assert r.status_code != 401


def test_password_set_requires_login_by_default(client, monkeypatch):
    """设了密码（无论配没配 PUBLIC_HOST）→ 默认 401。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("PUBLIC_HOST", PUBLIC)
    r = client.get(PROTECTED, headers={"host": PUBLIC})
    assert r.status_code == 401


def test_forged_host_cannot_bypass(client, monkeypatch):
    """伪造任意 Host（原绕过手法：Host: localhost 等）→ 一律 401。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("PUBLIC_HOST", PUBLIC)
    for forged in ("localhost", "localhost:8000", "127.0.0.1", "192.168.1.50:8080"):
        r = client.get(PROTECTED, headers={"host": forged})
        assert r.status_code == 401, forged
    # 没配 PUBLIC_HOST 时同样全入口要求登录
    monkeypatch.delenv("PUBLIC_HOST", raising=False)
    r = client.get(PROTECTED, headers={"host": "localhost"})
    assert r.status_code == 401


def test_login_then_access(client, monkeypatch):
    """登录拿 cookie 后即可访问受保护端点。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")

    # 错误密码 → 401
    bad = client.post("/api/login", json={"password": "wrong"})
    assert bad.status_code == 401

    # 正确密码 → 200 并种 cookie
    ok = client.post("/api/login", json={"password": "s3cret"})
    assert ok.status_code == 200
    assert ok.json()["ok"] is True

    # 带 cookie 再访问 → 放行
    r = client.get(PROTECTED, headers={"host": "localhost"})
    assert r.status_code != 401


def test_whitelist_endpoints_open_without_login(client, monkeypatch):
    """白名单端点免登录：/api/login /api/logout /api/auth/status /api/health。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert client.get("/api/health").status_code == 200
    assert client.get("/api/auth/status").status_code == 200
    assert client.post("/api/logout").status_code == 200
    assert client.post("/api/login", json={"password": ""}).status_code == 401


def test_auth_status_reflects_required(client, monkeypatch):
    """页面状态与受保护 API 共用内网凭据判定。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTH_LAN_BYPASS_TOKEN", "synthetic-lan-token")
    for host in (PUBLIC, "localhost", "192.168.1.50:8080"):
        assert client.get("/api/auth/status", headers={"host": host}).json() == {
            "required": True, "authed": False,
        }
    assert client.get("/api/auth/status", headers={
        "X-Lan-Bypass-Token": "synthetic-lan-token",
    }).json() == {"required": False, "authed": True}


def test_logout_revokes_browser_cookie(client, monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    assert client.get("/api/auth/status").json()["authed"] is True
    assert client.post("/api/logout").status_code == 200
    assert client.get(PROTECTED).status_code == 401


LAN_TOKEN = "l" * 64  # 合成内网免登密钥


def test_lan_bypass_token_header_bypasses(monkeypatch):
    """内网入口注入的正确密钥头 → 免登录（caddy :8090 场景）。

    不依赖 TestClient 对端地址，密钥头独立生效。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTH_LAN_BYPASS_TOKEN", LAN_TOKEN)
    monkeypatch.delenv("AUTH_BYPASS_NETWORKS", raising=False)
    monkeypatch.delenv("AUTH_TRUSTED_PROXY_NETWORKS", raising=False)
    r = TestClient(app).get(PROTECTED, headers={"x-lan-bypass-token": LAN_TOKEN})
    assert r.status_code != 401
    status = TestClient(app).get("/api/auth/status", headers={"x-lan-bypass-token": LAN_TOKEN})
    assert status.json()["required"] is False


def test_lan_bypass_token_wrong_or_absent_still_401(monkeypatch):
    """密钥不对/没带/未配置密钥 → 一律要求登录；公网伪造头拿不到值即无法命中。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTH_LAN_BYPASS_TOKEN", LAN_TOKEN)
    monkeypatch.delenv("AUTH_BYPASS_NETWORKS", raising=False)
    monkeypatch.delenv("AUTH_TRUSTED_PROXY_NETWORKS", raising=False)
    c = TestClient(app)
    assert c.get(PROTECTED, headers={"x-lan-bypass-token": "x" * 64}).status_code == 401
    assert c.get(PROTECTED, headers={"x-lan-bypass-token": LAN_TOKEN[:63]}).status_code == 401
    assert c.get(PROTECTED).status_code == 401
    # 空白头（如 caddy 未配 env 时注入空值）绝不能命中
    assert c.get(PROTECTED, headers={"x-lan-bypass-token": ""}).status_code == 401


def test_lan_bypass_token_ignored_when_unset(monkeypatch):
    """未配置 AUTH_LAN_BYPASS_TOKEN：同名头（任意值）不产生任何豁免。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.delenv("AUTH_LAN_BYPASS_TOKEN", raising=False)
    monkeypatch.delenv("AUTH_BYPASS_NETWORKS", raising=False)
    monkeypatch.delenv("AUTH_TRUSTED_PROXY_NETWORKS", raising=False)
    r = TestClient(app).get(PROTECTED, headers={"x-lan-bypass-token": LAN_TOKEN})
    assert r.status_code == 401


# ---------- CORS：显式名单，无宽泛正则 ----------


def _cors_middleware(fastapi_app):
    found = [m for m in fastapi_app.user_middleware if m.cls is CORSMiddleware]
    assert len(found) == 1
    return found[0]


def test_cors_explicit_allowlist_no_regex():
    """默认名单只有本地 dev 来源；无 allow_origin_regex；凭据仅对显式名单开放。"""
    mw = _cors_middleware(app)
    assert mw.kwargs["allow_origins"] == [
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]
    assert not mw.kwargs.get("allow_origin_regex")  # 宽泛正则已删除
    assert mw.kwargs["allow_credentials"] is True


def _fresh_main():
    """重新导入 app.main 拿按当前 env 构建的新实例。

    v1 套件的隔离 fixture 会把 app.* 从 sys.modules 清除，此时
    importlib.reload 会因模块不在 sys.modules 而 ImportError；
    弹出后 import_module 对两种状态都成立。"""
    sys.modules.pop("app.main", None)
    return importlib.import_module("app.main")


def test_cors_env_origins_appended(monkeypatch):
    """CORS_ORIGINS（逗号分隔）在默认名单之后追加生效。"""
    monkeypatch.setenv("CORS_ORIGINS", "https://grade.example.com,http://127.0.0.1:5173")
    try:
        reloaded = _fresh_main()
        mw = _cors_middleware(reloaded.app)
        assert mw.kwargs["allow_origins"] == [
            "http://localhost:3000",
            "http://127.0.0.1:3000",
            "https://grade.example.com",
            "http://127.0.0.1:5173",
        ]
        assert not mw.kwargs.get("allow_origin_regex")
    finally:
        monkeypatch.delenv("CORS_ORIGINS", raising=False)
        _fresh_main()
