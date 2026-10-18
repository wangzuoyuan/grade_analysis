"""登录鉴权：设了 APP_PASSWORD 后默认全入口要求登录（不看 Host 头）。

2026-10 安全修复（独立代码审查问题 15）后的语义：
- 未设 APP_PASSWORD → 鉴权关闭，任何入口都放行；
- 设了 → 默认全部要求登录；伪造任意 Host 不能绕过；
- 可选内网免登录：AUTH_BYPASS_NETWORKS（有效客户端 IP 命中免登录）+
  AUTH_TRUSTED_PROXY_NETWORKS（直连对端命中才信任 X-Forwarded-For，
  从右往左解包）；未配信任代理时伪造 XFF 完全无效。

用真实 app + TestClient 做端到端断言（TestClient 的 client= 参数可指定
直连对端 IP），另对 auth_required_for 构造 starlette Request 做单元级验证；
鉴权函数在每次请求时读 env，因此无需重建 app。
"""

import importlib
import sys

import pytest
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient
from starlette.requests import Request

from app import main as main_module
from app.auth import auth_required_for
from app.main import app

PROTECTED = "/api/exams"   # 受保护的只读端点
PUBLIC = "nas.example.com"  # 假设的 DDNS 域名（修复后仅作为兼容 env，不参与鉴权）

LAN_CLIENT = "192.168.1.23"      # 合成内网客户端 IP
PROXY = "10.0.0.2"               # 合成可信反代（caddy）IP
WAN_CLIENT = "203.0.113.9"       # 合成公网客户端 IP（TEST-NET-3，非真实地址）


@pytest.fixture
def client():
    return TestClient(app)


def _request(client_ip="203.0.113.9", port=55555, headers=None):
    """构造带 client / headers 的最小 starlette Request（单元级验证用）。"""
    scope = {
        "type": "http",
        "http_version": "1.1",
        "method": "GET",
        "scheme": "http",
        "path": "/api/x",
        "raw_path": b"/api/x",
        "query_string": b"",
        "root_path": "",
        "server": ("testserver", 80),
        "client": None if client_ip is None else (client_ip, port),
        "headers": [
            (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
        ],
    }
    return Request(scope)


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
    """status.required 与中间件同源：默认恒 True；命中 BYPASS 网络才 False。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.delenv("AUTH_BYPASS_NETWORKS", raising=False)
    monkeypatch.delenv("AUTH_TRUSTED_PROXY_NETWORKS", raising=False)

    for host in (PUBLIC, "localhost", "192.168.1.50:8080"):
        s = client.get("/api/auth/status", headers={"host": host}).json()
        assert s["required"] is True, host

    lan = TestClient(app, client=(LAN_CLIENT, 5555))
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "192.168.1.0/24")
    s = lan.get("/api/auth/status").json()
    assert s["required"] is False
    monkeypatch.delenv("AUTH_BYPASS_NETWORKS", raising=False)


# ---------- 端到端：网络边界免登录（BYPASS + TRUSTED_PROXY） ----------


def test_lan_direct_client_bypasses_via_bypass_networks(monkeypatch):
    """未配信任代理：直连对端 IP 命中 BYPASS 即免登录（局域网直连场景）。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "192.168.1.0/24")
    monkeypatch.delenv("AUTH_TRUSTED_PROXY_NETWORKS", raising=False)
    c = TestClient(app, client=(LAN_CLIENT, 5555))
    assert c.get(PROTECTED).status_code != 401


def test_proxy_forwarded_lan_client_bypasses(monkeypatch):
    """直连可信代理 + XFF=内网 IP → 免登录（本部署 caddy 反代场景）。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "192.168.1.0/24")
    monkeypatch.setenv("AUTH_TRUSTED_PROXY_NETWORKS", PROXY)
    c = TestClient(app, client=(PROXY, 5555))
    r = c.get(
        PROTECTED,
        headers={"x-forwarded-for": f"{WAN_CLIENT}, {LAN_CLIENT}"},
    )
    assert r.status_code != 401


def test_proxy_forwarded_wan_client_requires_login(monkeypatch):
    """直连可信代理 + XFF=公网 IP → 仍要求登录。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "192.168.1.0/24")
    monkeypatch.setenv("AUTH_TRUSTED_PROXY_NETWORKS", PROXY)
    c = TestClient(app, client=(PROXY, 5555))
    r = c.get(PROTECTED, headers={"x-forwarded-for": WAN_CLIENT})
    assert r.status_code == 401


def test_untrusted_direct_client_cannot_forge_xff(monkeypatch):
    """直连对端不是可信代理 → 伪造 XFF=内网 IP 完全无效 → 401。"""
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "192.168.1.0/24")
    monkeypatch.setenv("AUTH_TRUSTED_PROXY_NETWORKS", PROXY)
    c = TestClient(app, client=(WAN_CLIENT, 5555))
    r = c.get(PROTECTED, headers={"x-forwarded-for": LAN_CLIENT})
    assert r.status_code == 401


LAN_TOKEN = "l" * 64  # 合成内网免登密钥


def test_lan_bypass_token_header_bypasses(monkeypatch):
    """内网入口注入的正确密钥头 → 免登录（caddy :8090 场景）。

    TestClient 默认对端不可解析为 IP（fail closed 路径），密钥头独立生效。"""
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


# ---------- 单元级：auth_required_for 全语义矩阵 ----------


def test_unit_defaults_fail_closed(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.delenv("AUTH_BYPASS_NETWORKS", raising=False)
    monkeypatch.delenv("AUTH_TRUSTED_PROXY_NETWORKS", raising=False)
    # 无网络豁免：公网、内网、命中 PUBLIC_HOST 的 Host 头一律要求登录
    assert auth_required_for(_request(WAN_CLIENT)) is True
    assert auth_required_for(_request(LAN_CLIENT)) is True
    assert auth_required_for(_request(WAN_CLIENT, headers={"host": PUBLIC})) is True


def test_unit_no_password_disables_auth(monkeypatch):
    monkeypatch.delenv("APP_PASSWORD", raising=False)
    assert auth_required_for(_request(WAN_CLIENT)) is False


def test_unit_bypass_networks(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.delenv("AUTH_TRUSTED_PROXY_NETWORKS", raising=False)
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "192.168.1.0/24, 10.0.0.7, ::1")
    assert auth_required_for(_request(LAN_CLIENT)) is False      # CIDR 命中
    assert auth_required_for(_request("10.0.0.7")) is False      # 单 IP 命中
    assert auth_required_for(_request("::1")) is False           # IPv6 兼容
    assert auth_required_for(_request(WAN_CLIENT)) is True
    assert auth_required_for(_request("192.168.2.23")) is True   # 同段不同网不误放
    # 非法 env 条目被忽略，不放大攻击面
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "not-a-cidr, 192.168.1.0/24")
    assert auth_required_for(_request("not-a-cidr")) is True
    assert auth_required_for(_request(LAN_CLIENT)) is False


def test_unit_untrusted_proxy_ignores_xff(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "192.168.1.0/24")
    monkeypatch.setenv("AUTH_TRUSTED_PROXY_NETWORKS", PROXY)
    # 直连不可信 + 伪造 XFF（含伪造可信代理链）→ 一律按直连 IP 判定
    assert (
        auth_required_for(
            _request(WAN_CLIENT, headers={"x-forwarded-for": LAN_CLIENT})
        )
        is True
    )
    assert (
        auth_required_for(
            _request(WAN_CLIENT, headers={"x-forwarded-for": f"{PROXY}, {LAN_CLIENT}"})
        )
        is True
    )


def test_unit_trusted_proxy_xff_unwrap(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "192.168.1.0/24")
    monkeypatch.setenv("AUTH_TRUSTED_PROXY_NETWORKS", f"{PROXY}, 10.0.0.3")

    xff = lambda v: {"x-forwarded-for": v}  # noqa: E731

    # 单跳代理：XFF 即客户端
    assert auth_required_for(_request(PROXY, headers=xff(LAN_CLIENT))) is False
    assert auth_required_for(_request(PROXY, headers=xff(WAN_CLIENT))) is True
    # 多跳可信代理：从右往左跳过可信代理条目
    assert (
        auth_required_for(
            _request("10.0.0.3", headers=xff(f"{LAN_CLIENT}, {PROXY}"))
        )
        is False
    )
    assert (
        auth_required_for(
            _request("10.0.0.3", headers=xff(f"{WAN_CLIENT}, {PROXY}"))
        )
        is True
    )
    # 客户端伪造的只能是左侧前缀：右侧（代理追加的真实客户端）永远获胜
    assert (
        auth_required_for(
            _request(PROXY, headers=xff(f"{LAN_CLIENT}, {WAN_CLIENT}"))
        )
        is True
    )
    # 非法 XFF 条目：解包途中遇到（右侧）→ 链条不可信；
    # 客户端伪造的左侧前缀则永远不会被选中、不影响判定
    assert auth_required_for(_request(PROXY, headers=xff("not-an-ip"))) is True
    assert (
        auth_required_for(_request(PROXY, headers=xff(f"{LAN_CLIENT}, garbage")))
        is True
    )
    assert (
        auth_required_for(_request(PROXY, headers=xff(f"garbage, {LAN_CLIENT}")))
        is False
    )
    # 整条链都是可信代理 → 客户端信息已丢失 → fail closed
    assert (
        auth_required_for(
            _request(PROXY, headers=xff(f"{PROXY}, 10.0.0.3"))
        )
        is True
    )
    # 可信代理没带 XFF：有效客户端当成代理自身
    assert auth_required_for(_request(PROXY)) is True
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", f"192.168.1.0/24, {PROXY}")
    assert auth_required_for(_request(PROXY)) is False


def test_unit_missing_client_scope(monkeypatch):
    monkeypatch.setenv("APP_PASSWORD", "s3cret")
    monkeypatch.setenv("AUTH_BYPASS_NETWORKS", "192.168.1.0/24")
    # 拿不到对端地址（client=None）或对端值非法 → fail closed
    assert auth_required_for(_request(None)) is True
    assert auth_required_for(_request("testclient")) is True


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
