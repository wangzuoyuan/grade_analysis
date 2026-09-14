"""领域错误体系（P1）。

供 ``app/core/context.py`` 及后续 API 层使用：路由层捕获 ``DomainError``
后调用 ``to_http()`` 得到 (status_code, JSON body)，统一错误外显格式
``{"error": <机器码>, "detail": <人读信息>, ...details}``。

错误码与 HTTP 映射（与 03-architecture.md §2 一致）：
- 无效参数（格式/类型/缺失）→ 422 ``invalid_scope_param``
- 格式合法但不在本工作台允许范围内的资源 ID → 404 ``resource_out_of_scope``
  （含“请求了非绑定班”这类域边界违规 DomainViolation）
- 工作台未配置（无教师绑定/无任教学科/无学年等）→ 409 ``workspace_not_configured``
- 关联版本乐观锁冲突 → 409 ``link_version_conflict``
"""

from typing import Any


class DomainError(Exception):
    """领域错误基类。子类通过 status_code / code 声明 HTTP 语义。"""

    status_code = 400
    code = "domain_error"

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None):
        self.message = message or self.code
        self.details: dict[str, Any] = dict(details or {})
        super().__init__(self.message)

    def to_http(self) -> tuple[int, dict[str, Any]]:
        """返回 (HTTP 状态码, 响应 JSON 体)。"""
        body: dict[str, Any] = {"error": self.code, "detail": self.message}
        body.update(self.details)
        return self.status_code, body


class WorkspaceNotConfigured(DomainError):
    """工作台尚未完成配置（教师未绑定班级、教学侧未声明任教学科、
    学年/班级等基础数据未建立）。"""

    status_code = 409
    code = "workspace_not_configured"


class InvalidScopeParam(DomainError):
    """作用域参数无效：缺失、类型错误、取值非法。"""

    status_code = 422
    code = "invalid_scope_param"


class ResourceOutOfScope(DomainError):
    """资源 ID 格式合法，但不在当前工作台的允许范围内
    （例如请求了非本人绑定的班级；即域边界违规 DomainViolation）。
    用 404 而非 403，避免向客户端探测性请求泄露资源存在性。"""

    status_code = 404
    code = "resource_out_of_scope"


class LinkVersionConflict(DomainError):
    """HomeroomTeachingLink 版本乐观锁冲突：请求携带的 link_version
    与当前 active 关联的 version 不一致（关联已被修改或撤销）。"""

    status_code = 409
    code = "link_version_conflict"
