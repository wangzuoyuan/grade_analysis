"""登录与内网免登录使用一条判定路径。

未设 APP_PASSWORD 时沿用本地开发免登录；设置后必须有有效登录会话，
或携带内网入口注入的 AUTH_LAN_BYPASS_TOKEN。公网入口删除该头，
内网入口覆盖注入服务端密钥（deploy/Caddyfile）。内网端口必须限制可达范围。
Host、来源 IP、转发头与旧网段配置不参与鉴权。会话仍使用持久密钥签名。
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


def auth_required_for(request) -> bool:
    """内网入口凭据匹配则免登录，其余请求交由会话校验。"""
    if not auth_enabled():
        return False
    token = os.environ.get("AUTH_LAN_BYPASS_TOKEN", "").strip()
    values = request.headers.getlist("x-lan-bypass-token")
    if not token or len(values) != 1:
        return True
    # bytes 比较兼容任意请求头字符；错误/重复凭据不能变成豁免或 500。
    return not hmac.compare_digest(values[0].strip().encode("utf-8"), token.encode("utf-8"))


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
