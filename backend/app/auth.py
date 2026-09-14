"""按访问入口区分的轻量登录鉴权。

设计目标（用户要求）：**内网免登录，外网才登录**。
- 未设 `APP_PASSWORD` → 鉴权整体关闭（本地 Mac `run.py` 开发零负担）。
- 设了 `APP_PASSWORD` 且请求 Host 命中 `PUBLIC_HOST`（DDNS 域名）→ 要求会话。
- 内网用 NAS 局域网 IP 直连（Host 是 IP，不等于域名）→ 放行。

会话用 HMAC 签名的过期时间戳做无状态 token，存 httpOnly cookie，
不需要服务端会话存储。签名密钥优先取 `SESSION_SECRET`，否则在数据目录
落盘一份随机密钥，保证容器重启后已签发的会话不失效。
"""

import base64
import hashlib
import hmac
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


def _public_host() -> str:
    return os.environ.get("PUBLIC_HOST", "").strip().lower()


def _request_host(request) -> str:
    host = request.headers.get("host", "")
    return host.split(":")[0].strip().lower()


def auth_required_for(request) -> bool:
    """该请求是否需要登录会话。"""
    if not auth_enabled():
        return False
    ph = _public_host()
    if not ph:
        # 设了密码却没配 PUBLIC_HOST：保守起见，所有入口都要求登录。
        return True
    return _request_host(request) == ph


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
