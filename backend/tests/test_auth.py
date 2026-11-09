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


# ---------- 2026-11-09 P1/P2 修复：token 密码指纹 / docs 关闭 / 登录锁定 ----------


def test_password_fingerprint_rotates_tokens_on_change(monkeypatch):
    """改 APP_PASSWORD → 旧 token 立即失效（修复前只签 exp，改密码不踢旧会话）。"""
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.setenv("APP_PASSWORD", "first-secret")
    from app import auth

    token = auth.make_token(ttl=3600)
    assert auth.verify_token(token) is True
    # 改密码：签名消息混入的密码指纹变化 → 旧 token 全量失效
    monkeypatch.setenv("APP_PASSWORD", "second-secret")
    assert auth.verify_token(token) is False
    fresh = auth.make_token(ttl=3600)
    assert auth.verify_token(fresh) is True


def test_token_format_without_password_unchanged(monkeypatch):
    """免登模式（未设 APP_PASSWORD）签名消息保持旧格式，token 语义不受影响。"""
    monkeypatch.delenv("SESSION_SECRET", raising=False)
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    from app import auth

    token = auth.make_token(ttl=3600)
    assert auth.verify_token(token) is True


def test_interactive_docs_disabled(client, monkeypatch):
    """docs/redoc/openapi.json 端点已关闭：公网未登录不再暴露 API 面。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/openapi.json").status_code == 404


def test_login_lockout_after_five_failures(client, monkeypatch):
    """连续 5 次失败 → 锁定 10 分钟：锁内正确密码也 429（不泄露猜中信号）。"""
    from app import auth_router

    auth_router.reset_login_lock()
    monkeypatch.setattr(auth_router.time, "sleep", lambda _s: None)  # 免真实等待
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    try:
        for i in range(5):
            r = client.post("/api/login", json={"password": "wrong"})
            assert r.status_code == 401, i
        # 第 5 次失败已触发锁定：正确密码也被拒
        locked = client.post("/api/login", json={"password": "s3cret"})
        assert locked.status_code == 429
        assert "过多" in locked.json()["error"]
    finally:
        auth_router.reset_login_lock()


def test_login_lockout_expires_and_success_resets(client, monkeypatch):
    """锁定窗口过期后恢复登录；成功登录清零失败计数。"""
    from app import auth_router

    auth_router.reset_login_lock()
    monkeypatch.setattr(auth_router.time, "sleep", lambda _s: None)
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    fake = {"t": 1000.0}
    monkeypatch.setattr(auth_router, "_now", lambda: fake["t"])
    try:
        for _ in range(5):
            assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"password": "s3cret"}).status_code == 429
        # 时间越过锁定窗口 → 正确密码恢复可用，且成功清零计数
        fake["t"] += auth_router.LOGIN_LOCK_SECONDS + 1
        ok = client.post("/api/login", json={"password": "s3cret"})
        assert ok.status_code == 200
        assert client.post("/api/login", json={"password": "wrong"}).status_code == 401
        assert client.post("/api/login", json={"password": "s3cret"}).status_code == 200
    finally:
        auth_router.reset_login_lock()
