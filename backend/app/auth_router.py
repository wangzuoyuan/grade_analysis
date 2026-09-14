"""登录 / 登出 / 状态查询（挂在 /api 前缀下）。"""

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

    内网入口拿到 `required: false`，前端据此直接放行、毫无感知。
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
    if not check_password(body.password):
        time.sleep(0.5)  # 固定延时，削弱在线暴力破解
        return JSONResponse({"ok": False, "error": "密码错误"}, status_code=401)
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
