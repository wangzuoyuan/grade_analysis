"""登录 / 登出 / 状态查询（挂在 /api 前缀下）。"""

import threading
import time

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from app.auth import (
    COOKIE_NAME,
    TOKEN_TTL,
    auth_enabled,
    auth_required_for,
    check_password,
    make_token,
    verify_token,
)

router = APIRouter(tags=["auth"])

# 2026-11-09 P2 修复：登录失败锁定（进程内，单用户部署语义足够）。
# 连续 LOGIN_MAX_FAILURES 次失败 → 锁 LOGIN_LOCK_SECONDS（锁内一律 429，
# 含正确密码，防爆破侧信道）；成功登录清零。0.5s 固定延时保留。
# 同步端点跑在线程池，计数与锁定窗口的操作都持锁；时钟走 _now()
# （time.monotonic，不受系统时间跳变影响）便于测试 monkeypatch。
LOGIN_MAX_FAILURES = 5
LOGIN_LOCK_SECONDS = 10 * 60

_login_state_lock = threading.Lock()
_login_failures = 0
_login_locked_until = 0.0


def _now() -> float:
    """可注入时钟（测试 monkeypatch 用）。"""
    return time.monotonic()


def reset_login_lock() -> None:
    """清零登录失败状态（测试隔离用；生产路径无需调用）。"""
    global _login_failures, _login_locked_until
    with _login_state_lock:
        _login_failures = 0
        _login_locked_until = 0.0


def _login_locked() -> bool:
    with _login_state_lock:
        return _now() < _login_locked_until


def _record_login_failure() -> None:
    global _login_failures, _login_locked_until
    with _login_state_lock:
        _login_failures += 1
        if _login_failures >= LOGIN_MAX_FAILURES:
            _login_locked_until = _now() + LOGIN_LOCK_SECONDS


def _record_login_success() -> None:
    global _login_failures, _login_locked_until
    with _login_state_lock:
        _login_failures = 0
        _login_locked_until = 0.0


class LoginBody(BaseModel):
    password: str = ""


def _is_secure(request: Request) -> bool:
    proto = request.headers.get("x-forwarded-proto", "").lower()
    if proto:
        return proto == "https"
    return request.url.scheme == "https"


@router.get("/auth/status")
def auth_status(request: Request):
    """按当前请求入口返回是否需要登录、以及是否已登录。

    未设密码或内网入口凭据匹配时 `required: false`，
    前端据此直接放行、毫无感知。判定与 main.py 的鉴权中间件同源
    （auth_required_for），不看 Host 头。
    """
    required = auth_required_for(request)
    authed = True
    if required:
        authed = verify_token(request.cookies.get(COOKIE_NAME, ""))
    return {"required": required, "authed": authed}


@router.post("/login")
def login(body: LoginBody, request: Request):
    if not auth_enabled():
        return {"ok": True, "required": False}
    if _login_locked():
        # 锁定窗口内不区分密码对错（一律 429），不泄露"密码已猜对"信号
        return JSONResponse(
            {"ok": False, "error": "登录尝试次数过多，请 10 分钟后重试"},
            status_code=429,
        )
    if not check_password(body.password):
        time.sleep(0.5)  # 固定延时，削弱在线暴力破解
        _record_login_failure()
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=401)
    _record_login_success()
    resp = JSONResponse({"ok": True})
    resp.set_cookie(
        COOKIE_NAME,
        make_token(),
        max_age=TOKEN_TTL,
        httponly=True,
        samesite="lax",
        secure=_is_secure(request),
        path="/",
    )
    return resp


@router.post("/logout")
def logout():
    resp = JSONResponse({"ok": True})
    resp.delete_cookie(COOKIE_NAME, path="/")
    return resp
