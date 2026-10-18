"""登录鉴权：设了密码就默认所有入口要求登录（安全默认）。

设计目标（2026-10 安全修复，独立代码审查问题 15）：鉴权决策只信服务端
可控信息，**绝不信任客户端可伪造的 `Host` 头或任意转发头**。

- 未设 `APP_PASSWORD` → 鉴权整体关闭（本地 Mac `run.py` 开发零负担）。
- 设了 `APP_PASSWORD` → 默认所有 `/api` 请求都要求登录会话（白名单端点见
  `app/main.py`）。本部署所有请求都经 caddy 反代、直连对端恒为 caddy，
  因此两个网络 env 均不配置（默认）时**全入口需登录**——安全默认。
- 可选的内网免登录必须显式配置网络边界（逗号分隔 IP/CIDR，默认空）：
  - `AUTH_BYPASS_NETWORKS`：**有效客户端 IP** 命中才免登录。
  - `AUTH_TRUSTED_PROXY_NETWORKS`：直连对端 `request.client.host` 命中时
    才信任 `X-Forwarded-For`，并从右往左跳过仍是可信代理的条目、取第一个
    非可信条目作为有效客户端 IP；未配置时转发头完全不看（伪造无效）。
    未配信任代理时，有效客户端 IP 就是直连对端 IP（局域网直连部署只配
    BYPASS 即可）。链条中出现非法 XFF 条目或链条全是可信代理 → 视为
    不可信，要求登录（fail closed）。
  - 兼容 IPv6（如 `::1`）；非法的 env 网络条目被忽略（匹配不到任何地址，
    不会放大攻击面）。
- `PUBLIC_HOST` 不再参与鉴权决策（env 保留容忍，仅为旧配置不报错）。

会话用 HMAC 签名的过期时间戳做无状态 token，存 httpOnly cookie，
不需要服务端会话存储。签名密钥优先取 `SESSION_SECRET`，否则在数据目录
落盘一份随机密钥，保证容器重启后已签发的会话不失效。
"""

import base64
import hashlib
import hmac
import ipaddress
import os
import secrets
import time

from app.paths import DATA_DIR

COOKIE_NAME = "exam_session"
TOKEN_TTL = 30 * 24 * 3600  # 30 天


def _password() -> str:
    return os.environ.get("APP_PASSWORD", "").strip()


def auth_enabled() -> bool:
    return bool(_password())


def _networks(env_name: str) -> list:
    """解析逗号分隔的 IP/CIDR 名单；非法条目忽略（fail closed，不放大攻击面）。"""
    nets = []
    for item in os.environ.get(env_name, "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            nets.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            continue
    return nets


def _parse_ip(value: str):
    """解析地址字符串为 IPv4Address/IPv6Address；兼容代理常见的 `[::1]` 写法。

    无法解析（含带端口、掩码碎片等）→ None。
    """
    value = (value or "").strip()
    if value.startswith("[") and value.endswith("]"):
        value = value[1:-1]
    try:
        return ipaddress.ip_address(value)
    except ValueError:
        return None


def _in_networks(ip, nets) -> bool:
    if ip is None:
        return False
    return any(ip in net for net in nets)


def _effective_client_ip(request, trusted):
    """按直连对端与可信代理链求「有效客户端 IP」；不可信/无法判定 → None。

    - 直连对端不在可信代理名单 → 就是直连 IP（X-Forwarded-For 完全忽略，
      伪造无效）；
    - 直连对端可信 → 从右往左解包 X-Forwarded-For（右侧条目由离我们最近的
      可信代理追加，客户端伪造的只能是左侧前缀、永远不会被选中），跳过仍是
      可信代理的条目，取第一个非可信条目；非法条目或链条全是可信代理 → None。
    """
    if request.client is None:
        return None
    peer = _parse_ip(request.client.host)
    if peer is None or not _in_networks(peer, trusted):
        return peer
    entries: list[str] = []
    for value in request.headers.getlist("x-forwarded-for"):
        entries.extend(value.split(","))
    if not entries:
        # 可信代理没带 XFF：有效客户端只能当成该代理自身。
        return peer
    for raw in reversed(entries):
        entry_ip = _parse_ip(raw)
        if entry_ip is None:
            return None  # 非法条目：链条不可信
        if not _in_networks(entry_ip, trusted):
            return entry_ip
    return None  # 整条链都是可信代理：客户端信息已丢失


def auth_required_for(request) -> bool:
    """该请求是否需要登录会话。

    设了 `APP_PASSWORD` 后默认全部要求登录；仅当有效客户端 IP 显式命中
    `AUTH_BYPASS_NETWORKS` 才免登录。绝不看 Host 头。
    """
    if not auth_enabled():
        return False
    ip = _effective_client_ip(request, _networks("AUTH_TRUSTED_PROXY_NETWORKS"))
    if ip is None:
        return True  # 拿不到可判定地址：fail closed
    return not _in_networks(ip, _networks("AUTH_BYPASS_NETWORKS"))


def _secret() -> bytes:
    s = os.environ.get("SESSION_SECRET", "").strip()
    if s:
        return s.encode()
    path = os.path.join(DATA_DIR, ".session_secret")
    try:
        if os.path.exists(path):
            with open(path, "rb") as f:
                return f.read()
        os.makedirs(DATA_DIR, exist_ok=True)
        val = secrets.token_bytes(32)
        with open(path, "wb") as f:
            f.write(val)
        os.chmod(path, 0o600)
        return val
    except OSError:
        # 退回进程级随机：重启会让旧会话失效，但功能仍可用。
        return secrets.token_bytes(32)


def make_token(ttl: int = TOKEN_TTL) -> str:
    exp = str(int(time.time()) + ttl).encode()
    sig = hmac.new(_secret(), exp, hashlib.sha256).digest()
    return (
        base64.urlsafe_b64encode(exp).decode()
        + "."
        + base64.urlsafe_b64encode(sig).decode()
    )


def verify_token(token: str) -> bool:
    if not token:
        return False
    try:
        exp_b64, sig_b64 = token.split(".", 1)
        exp = base64.urlsafe_b64decode(exp_b64)
        sig = base64.urlsafe_b64decode(sig_b64)
    except Exception:
        return False
    expected = hmac.new(_secret(), exp, hashlib.sha256).digest()
    if not hmac.compare_digest(sig, expected):
        return False
    try:
        return int(exp) >= int(time.time())
    except ValueError:
        return False


def check_password(candidate: str) -> bool:
    pw = _password()
    if not pw:
        return False
    return hmac.compare_digest((candidate or "").strip(), pw)
