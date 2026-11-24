"""State passed between deterministic LangGraph nodes."""

from typing import Any, TypedDict


class AgentState(TypedDict, total=False):
    db: Any
    redis_client: Any
    employee_id: str
    company_id: str
    conversation_id: str
    user_message: str
    conversation: dict[str, Any]
    llm_calls: int
    intent: dict[str, Any]
    reply: str
    status: str
    request_id: str | None
