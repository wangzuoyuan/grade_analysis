"""P6 AI 会话端点的请求/响应模型（契约 docs/contracts/p6-ai-mcp.md §1）。

关键语义：
- 响应中的 scope 是服务端解析并冻结的快照的【公开投影】：含 cohort_size，
  不含 member_person_ids 明单——成员明细按需经 search_students 等工具查询，
  接口层绝不泄露名单（契约 §1 GET"不泄露成员外的数据"）。
- 请求只携带 mode 与资源 ID（class_id/teaching_class_id/subject），服务端
  重新解析，绝不信任客户端提交的成员/学科（契约 §0 红线）。
"""

from typing import List, Optional

from pydantic import BaseModel


class ChatSessionCreateRequest(BaseModel):
    """POST /chat/sessions 请求体：mode 必填，其余可选（服务端解析）。"""

    mode: str
    academic_year_id: Optional[int] = None
    class_id: Optional[int] = None  # homeroom 模式：行政班
    teaching_class_id: Optional[int] = None  # teaching 模式：教学班（缺省全部所教班）
    subject: Optional[str] = None  # teaching 模式：任教学科（多学科时必填）


class ChatScopeInfo(BaseModel):
    """会话作用域的对外快照视图（不含成员明单）。links 为冻结的可见关联
    集合（Q02：仅 link_id/version/status/linked_pairs，不含成员映射明细）。"""

    mode: str
    data_domain: str
    academic_year_id: Optional[int] = None
    class_ids: List[int] = []
    subject: Optional[str] = None
    link_id: Optional[int] = None
    link_version: Optional[int] = None
    links: List[dict] = []
    as_of: str
    cohort_size: int


class ChatSessionResponse(BaseModel):
    session_id: int
    scope: ChatScopeInfo


class ChatCloseResponse(BaseModel):
    session_id: int
    status: str


class ChatMessageRequest(BaseModel):
    """POST /chat/sessions/{id}/messages 请求体：单条用户消息（服务端
    持久化会话历史并组装"历史 + 本次输入"请求模型，Q03）。"""

    content: str


class ChatHistoryMessage(BaseModel):
    """GET /chat/sessions/{id}/messages 的单条历史（刷新恢复语义：前端
    以此重建消息列表，不依赖浏览器本地状态）。"""

    id: int
    role: str  # user | assistant
    content: str
    created_at: str


class ChatHistoryResponse(BaseModel):
    """会话历史只读投影（Q03）：open 与 closed/漂移失效会话都可读取，
    范围失效只禁止继续作答，不删除历史。"""

    messages: List[ChatHistoryMessage] = []
