"""P1 /api/v1 路由层。

由集成者在 ``app.main`` 挂载::

    from app.api import create_api_router
    app.include_router(create_api_router())

错误契约（docs/contracts/p1-api.md §0）：业务错误统一
``{"error": "<code>", "detail": ...}``；DomainError 由各路由函数经
``domain_endpoint`` 装饰器统一转 JSONResponse，FastAPI 不支持 router 级
exception_handler，故用装饰器封装（不能改 main.py 全局注册）。
"""

import functools
from typing import Callable, TypeVar

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from app.core.errors import DomainError

T = TypeVar("T")


def domain_endpoint(func: Callable[..., T]) -> Callable[..., T]:
    """把 DomainError 转成契约错误 JSON 的轻量装饰器。

    DomainError.to_http() 返回 ``(status_code, payload)``；payload 形如
    ``{"error": code, "detail": ...}``。路由函数内正常 return 的值仍走
    FastAPI 的 response_model 校验/过滤。
    """

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        try:
            return func(*args, **kwargs)
        except DomainError as exc:
            status_code, payload = exc.to_http()
            return JSONResponse(payload, status_code=status_code)

    return wrapper


def current_teacher_id(db: Session) -> int:
    """单教师应用：取（必要时延迟创建，与 /api/teacher 行为一致）首位教师。

    工作台是否"已配置"由教师绑定/教学班数据决定，由
    resolve_workspace_context 判定；这里只保证 teacher_id 存在。
    """
    from app.db.models import Teacher

    teacher = db.query(Teacher).first()
    if teacher is None:
        teacher = Teacher()
        db.add(teacher)
        db.commit()
        db.refresh(teacher)
    return teacher.id


def create_api_router() -> APIRouter:
    """构建 /api/v1 总路由（前缀唯一来源）。

    students_mgmt / homework 是集成者预建的 P4/P5 骨架（空路由），
    由对应子代理填充实现；挂载点统一在此维护，避免并行改本文件。
    """
    from app.api import analysis as analysis_module
    from app.api import chat as chat_module
    from app.api import homework as homework_module
    from app.api import imports as imports_module
    from app.api import canonical as canonical_module
    from app.api import scores, shared, students, students_mgmt
    from app.diagnosis import router as diagnosis_router_module
    from app.diagnosis import changes_router  # P1-B3 变化分解
    from app.diagnosis import types_router  # P1-B2 类型引擎（仅追加本行 include）
    from app.diagnosis import correlation_router  # P2-C1 作业×成绩相关性（仅追加本行 include）
    from app.diagnosis import action_router  # P2-C2 行动首页（仅追加本行 include）
    from app.diagnosis import report_router  # P2-C3 诊断版学生报告（仅追加本行 include）
    from app.diagnosis import review_router  # P2-C4 干预复查对照（仅追加本行 include）
    from app.diagnosis import research_router  # P3-D1 教研统计（仅追加本行 include）

    router = APIRouter(prefix="/api/v1")
    router.include_router(shared.router)
    router.include_router(students.router)
    router.include_router(scores.router)
    router.include_router(imports_module.router)
    router.include_router(canonical_module.router)
    router.include_router(analysis_module.router)
    router.include_router(students_mgmt.router)
    router.include_router(homework_module.router)
    router.include_router(chat_module.router)
    router.include_router(diagnosis_router_module.router)
    router.include_router(changes_router.router)
    router.include_router(types_router.router)
    router.include_router(correlation_router.router)
    router.include_router(action_router.router)
    router.include_router(report_router.router)
    router.include_router(review_router.router)
    router.include_router(research_router.router)
    return router
