"""Employee chat endpoints."""

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from app.agent.graph import ShiftChangeAgent
from app.api.dependencies import get_agent, get_connection, get_redis_client
from app.auth.dependencies import get_current_user
from app.auth.jwt_handler import TokenData
from app.db import queries

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str = Field(min_length=1, max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=100)

    model_config = ConfigDict(extra="forbid")


class ChatResponse(BaseModel):
    status: str
    reply: str
    conversation_id: str
    request_id: str | None = None
    llm_calls: int


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest,
    user: TokenData = Depends(get_current_user),
    db=Depends(get_connection),
    agent: ShiftChangeAgent = Depends(get_agent),
    redis_client=Depends(get_redis_client),
) -> ChatResponse:
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=422, detail="Message cannot be blank.")
    if queries.get_employee(db, user.employee_id, user.company_id) is None:
        raise HTTPException(status_code=403, detail="Token identity is not a known employee.")
    conversation_id = payload.conversation_id or user.employee_id
    conversation = queries.get_conversation_state(
        db, user.company_id, user.employee_id, conversation_id
    )
    try:
        result: dict[str, Any] = await agent.ainvoke(
            db=db,
            employee_id=user.employee_id,
            company_id=user.company_id,
            conversation_id=conversation_id,
            message=message,
            conversation=conversation,
            redis_client=redis_client,
        )
    except (sqlite3.Error, TimeoutError):
        logger.exception("Shift-change tool failed for conversation %s", conversation_id)
        result = {
            "status": "tool_error",
            "reply": (
                "I couldn't check your roster or cab availability right now. "
                "Your roster has not been changed. Please try again in a moment."
            ),
            "request_id": None,
            "conversation": conversation,
            "llm_calls": 0,
        }
    except Exception:
        logger.exception("Unexpected chat failure for conversation %s", conversation_id)
        result = {
            "status": "temporarily_unavailable",
            "reply": (
                "I couldn't process that request right now. Your roster has not been "
                "changed. Please try again shortly."
            ),
            "request_id": None,
            "conversation": conversation,
            "llm_calls": 0,
        }

    try:
        queries.save_conversation_turn(
            db,
            company_id=user.company_id,
            employee_id=user.employee_id,
            conversation_id=conversation_id,
            user_message=message,
            assistant_message=result["reply"],
            state=result.get("conversation", conversation),
        )
    except sqlite3.Error:
        logger.exception("Could not save chat turn for conversation %s", conversation_id)
        result["status"] = "temporarily_unavailable"
        result["reply"] = (
            "I couldn't save this conversation right now. Your roster has not been "
            "changed. Please try again shortly."
        )
    return ChatResponse(
        status=result["status"],
        reply=result["reply"],
        conversation_id=conversation_id,
        request_id=result.get("request_id"),
        llm_calls=result.get("llm_calls", 0),
    )


@router.get("/history")
def chat_history(
    conversation_id: str | None = None,
    user: TokenData = Depends(get_current_user),
    db=Depends(get_connection),
) -> dict[str, Any]:
    selected_id = conversation_id or user.employee_id
    messages = queries.get_conversation_history(
        db, user.company_id, user.employee_id, selected_id
    )
    return {"conversation_id": selected_id, "messages": messages}
