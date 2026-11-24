"""LangGraph workflow for interpreting, clarifying, and applying changes."""

import asyncio
import json
import logging
import re
import sqlite3
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from langgraph.graph import END, START, StateGraph

from app.agent.prompts import SYSTEM_PROMPT
from app.agent.state import AgentState
from app.agent.tools import AgentTools
from app.config import settings
from app.db import queries
from app.services.shift_change import time_to_minutes

logger = logging.getLogger(__name__)
INDIA_TZ = ZoneInfo("Asia/Kolkata")
TIME_PATTERN = re.compile(
    r"(?<!\d)(?P<hour>(?:[01]?\d|2[0-3]))"
    r"(?::(?P<minute>[0-5]\d))?\s*(?P<ampm>am|pm)?(?!\d)",
    re.IGNORECASE,
)


def _normalise_time(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    raw = value.strip().lower()
    match = re.fullmatch(r"(\d{1,2})(?::([0-5]\d))?\s*(am|pm)?", raw)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or "00")
    suffix = match.group(3)
    if suffix:
        if not 1 <= hour <= 12:
            return None
        hour = hour % 12 + (12 if suffix == "pm" else 0)
    elif not 0 <= hour <= 23:
        return None
    return f"{hour:02d}:{minute:02d}"


def _parse_time(text: str) -> str | None:
    without_dates = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)
    matches = list(TIME_PATTERN.finditer(without_dates))
    if not matches:
        return None
    match = matches[-1]
    raw = match.group(0).strip()
    normalized = _normalise_time(raw)
    if normalized and match.group("ampm") is None and match.group("minute") is None:
        # A bare hour in a drop request is commonly PM; retain its literal hour
        # rather than silently guessing a different time.
        return normalized
    return normalized


def _bare_hour(text: str) -> int | None:
    without_dates = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text.lower())
    if ":" in without_dates or re.search(r"\b(?:am|pm)\b", without_dates):
        return None
    match = re.search(
        r"\b(?:make it|set it to|at|to|by)\s+(1[0-2]|[1-9])\b",
        without_dates,
    )
    if match:
        return int(match.group(1))
    if re.fullmatch(r"\s*(1[0-2]|[1-9])\s*", without_dates):
        return int(without_dates.strip())
    return None


def _fallback_intent(message: str, pending: dict[str, Any]) -> dict[str, Any]:
    lowered = message.lower().strip()
    intent: dict[str, Any] = {
        "action": "change",
        "direction": None,
        "time": _parse_time(message),
        "date": None,
        "confirm": None,
    }
    if pending.get("awaiting") == "time_period" and lowered in {"am", "pm"}:
        hour = int(pending["ambiguous_hour"]) % 12
        intent["time"] = f"{hour + (12 if lowered == 'pm' else 0):02d}:00"

    if any(term in lowered for term in ("pickup", "pick up", "morning cab")):
        intent["direction"] = "pickup"
    elif any(term in lowered for term in ("drop", "drop-off", "drop off", "evening cab")):
        intent["direction"] = "drop"
    if lowered in {"yes", "yes please", "confirm", "go ahead", "i still want it"}:
        intent["confirm"] = True
    elif lowered in {"no", "no thanks", "cancel", "don't", "do not"}:
        intent["confirm"] = False

    if any(word in lowered for word in ("status", "my request", "approval", "approved")):
        intent["action"] = "status"
    elif not any(
        word in lowered
        for word in ("change", "move", "make it", "shift", "late", "early", "instead", "can i go", "want")
    ) and not intent["confirm"] and not pending:
        intent["action"] = "unclear"

    if "tomorrow" in lowered:
        intent["date"] = (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    elif "today" in lowered:
        intent["date"] = datetime.now(INDIA_TZ).date().isoformat()
    else:
        date_match = re.search(r"\b\d{4}-\d{2}-\d{2}\b", message)
        if date_match:
            try:
                intent["date"] = date.fromisoformat(date_match.group(0)).isoformat()
            except ValueError:
                pass

    if intent["direction"] is None:
        intent["direction"] = pending.get("direction")
    if intent["time"] is None:
        intent["time"] = pending.get("time")
    if intent["date"] is None:
        intent["date"] = pending.get("date")
    if pending and (
        intent["direction"] is not None
        or intent["time"] is not None
        or intent["confirm"] is not None
    ):
        intent["action"] = "change"
    return intent


def _parse_model_output(content: Any) -> dict[str, Any] | None:
    if isinstance(content, list):
        content = "".join(
            str(block.get("text", "")) if isinstance(block, dict) else str(block)
            for block in content
        )
    if not isinstance(content, str):
        return None
    cleaned = content.strip()
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", cleaned, flags=re.IGNORECASE)
    try:
        result = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    if not isinstance(result, dict):
        return None
    action = result.get("action")
    if action not in {"change", "status", "unclear"}:
        return None
    direction = result.get("direction")
    if direction not in {None, "pickup", "drop"}:
        return None
    confirm = result.get("confirm")
    if confirm is not None and not isinstance(confirm, bool):
        return None
    raw_date = result.get("date")
    parsed_date = None
    if raw_date is not None:
        if not isinstance(raw_date, str):
            return None
        try:
            parsed_date = date.fromisoformat(raw_date).isoformat()
        except ValueError:
            return None
    return {
        "action": action,
        "direction": direction,
        "time": _normalise_time(result.get("time")),
        "date": parsed_date,
        "confirm": confirm,
    }


class ShiftChangeAgent:
    """One LLM intent call at most; all tools and rules execute in application code."""

    def __init__(self, llm=None) -> None:
        self.llm = llm if llm is not None else self._build_llm()
        workflow = StateGraph(AgentState)
        workflow.add_node("interpret", self._interpret)
        workflow.add_node("execute", self._execute)
        workflow.add_edge(START, "interpret")
        workflow.add_edge("interpret", "execute")
        workflow.add_edge("execute", END)
        self.graph = workflow.compile()

    @staticmethod
    def _build_llm():
        if not settings.GEMINI_API_KEY:
            return None
        try:
            from langchain_google_genai import ChatGoogleGenerativeAI

            return ChatGoogleGenerativeAI(
                model=settings.LLM_MODEL,
                google_api_key=settings.GEMINI_API_KEY,
                temperature=0,
                max_retries=0,
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
        except (ImportError, ValueError) as exc:
            logger.error("Gemini client could not be initialized: %s", exc)
            return None

    async def _interpret(self, state: AgentState) -> dict[str, Any]:
        pending = state.get("conversation", {}).get("pending", {})
        if not self.llm:
            intent = _fallback_intent(state["user_message"], pending)
            return {"intent": intent, "llm_calls": 0}
        try:
            response = await asyncio.wait_for(
                self.llm.ainvoke(
                    [
                        ("system", SYSTEM_PROMPT),
                        (
                            "human",
                            json.dumps(
                                {
                                    "pending_context": {
                                        key: value
                                        for key, value in pending.items()
                                        if key in {"direction", "time", "date", "awaiting"}
                                    },
                                    "employee_message": state["user_message"],
                                }
                            ),
                        ),
                    ]
                ),
                timeout=settings.LLM_TIMEOUT_SECONDS,
            )
        except (asyncio.TimeoutError, TimeoutError) as exc:
            logger.warning("Intent model timed out: %s", exc)
            return {
                "intent": {"action": "model_error"},
                "llm_calls": 1,
                "status": "model_error",
            }
        except Exception as exc:
            logger.warning("Intent model call failed: %s", exc)
            return {
                "intent": {"action": "model_error"},
                "llm_calls": 1,
                "status": "model_error",
            }

        parsed = _parse_model_output(getattr(response, "content", response))
        if parsed is None:
            return {
                "intent": {"action": "invalid_model_output"},
                "llm_calls": 1,
                "status": "invalid_model_output",
            }
        parsed["direction"] = parsed["direction"] or pending.get("direction")
        parsed["time"] = parsed["time"] or pending.get("time")
        parsed["date"] = parsed["date"] or pending.get("date")
        if pending and (
            parsed["direction"] is not None
            or parsed["time"] is not None
            or parsed["confirm"] is not None
        ):
            parsed["action"] = "change"
        return {"intent": parsed, "llm_calls": 1}

    async def _execute(self, state: AgentState) -> dict[str, Any]:
        try:
            return await self._execute_inner(state)
        except (sqlite3.Error, TimeoutError):
            logger.exception(
                "Shift-change tool failed for employee %s",
                state.get("employee_id", "unknown"),
            )
            return {
                "status": "tool_error",
                "reply": (
                    "I couldn't check your roster or cab availability right now. "
                    "Your roster has not been changed. Please try again in a moment."
                ),
                "request_id": None,
                "conversation": state.get("conversation", {}),
                "llm_calls": state.get("llm_calls", 0),
            }

    async def _execute_inner(self, state: AgentState) -> dict[str, Any]:
        if state.get("status") in {"model_error", "invalid_model_output"}:
            return {
                "status": "temporarily_unavailable",
                "reply": (
                    "I'm having trouble understanding that request right now. "
                    "Please try again in a moment."
                ),
            }

        db = state["db"]
        employee_id = state["employee_id"]
        company_id = state["company_id"]
        conversation_id = state["conversation_id"]
        tools = AgentTools(
            db,
            employee_id,
            company_id,
            state.get("redis_client"),
        )
        intent = state["intent"]
        conversation = dict(state.get("conversation", {}))
        pending = dict(conversation.get("pending", {}))

        if intent["action"] == "status":
            requests = queries.list_employee_requests(db, employee_id, company_id)
            if not requests:
                return {
                    "status": "no_requests",
                    "reply": "You don't have any shift-change requests yet.",
                    "request_id": None,
                }
            latest = requests[0]
            reply = (
                f"Your latest {latest['trip_type']} change request is "
                f"{latest['status'].replace('_', ' ')}."
            )
            if latest["status"] == "pending_approval":
                reply += " It's waiting for your transport manager; check again later."
            elif latest["status"] == "cancelled":
                reply += " The cab or roster changed before it could be applied. Please choose another time."
            elif latest["manager_decision"]:
                reply += f" Manager decision: {latest['manager_decision']}."
            return {
                "status": latest["status"],
                "reply": reply,
                "request_id": latest["id"],
            }

        if intent["action"] == "unclear":
            return {
                "status": "clarification",
                "reply": "I can help change a pickup or drop time. Which one do you mean?",
                "request_id": None,
            }

        if pending.get("awaiting") == "approval_confirmation":
            if intent.get("confirm") is False:
                conversation.pop("pending", None)
                return {
                    "status": "cancelled",
                    "reply": "Okay, I haven't submitted the change. Your roster stays as it is.",
                    "request_id": None,
                    "conversation": conversation,
                }
            if intent.get("confirm") is True:
                return await self._submit_pending(
                    tools, pending, conversation_id, conversation, True
                )

        if pending.get("awaiting") == "alternative":
            chosen = _parse_time(state["user_message"])
            matching = next(
                (
                    option
                    for option in pending.get("options", [])
                    if option.get("time") == chosen
                ),
                None,
            )
            if matching:
                pending["cab_id"] = matching["cab_id"]
                pending["time"] = matching["time"]
                pending.pop("options", None)
                return await self._submit_pending(
                    tools, pending, conversation_id, conversation, False
                )
            options = pending.get("options", [])
            times = ", ".join(option["time"] for option in options)
            return {
                "status": "clarification",
                "reply": f"Please choose one of the available times: {times}.",
                "request_id": None,
            }

        direction = intent.get("direction")
        requested_time = intent.get("time")
        scheduled_date = intent.get("date") or (
            datetime.now(INDIA_TZ).date() + timedelta(days=1)
        ).isoformat()
        if direction not in {"pickup", "drop"}:
            pending = {"time": requested_time, "date": scheduled_date, "awaiting": "direction"}
            conversation["pending"] = pending
            return {
                "status": "clarification",
                "reply": "Is this for your pickup or your drop?",
                "request_id": None,
                "conversation": conversation,
            }
        ambiguous_hour = _bare_hour(state["user_message"])
        if direction == "drop" and ambiguous_hour is not None:
            conversation["pending"] = {
                "direction": direction,
                "date": scheduled_date,
                "ambiguous_hour": ambiguous_hour,
                "awaiting": "time_period",
            }
            return {
                "status": "clarification",
                "reply": (
                    f"Do you mean {ambiguous_hour:02d}:00 or "
                    f"{(ambiguous_hour % 12) + 12:02d}:00? Reply with AM/PM or the full time."
                ),
                "request_id": None,
                "conversation": conversation,
            }
        if requested_time is None:
            pending = {
                "direction": direction,
                "date": scheduled_date,
                "awaiting": "time",
            }
            conversation["pending"] = pending
            return {
                "status": "clarification",
                "reply": f"What time would you like your {direction}?",
                "request_id": None,
                "conversation": conversation,
            }
        try:
            time_to_minutes(requested_time)
            date.fromisoformat(scheduled_date)
        except ValueError:
            return {
                "status": "clarification",
                "reply": "Please provide a valid date and time, such as 18:30 tomorrow.",
                "request_id": None,
            }

        existing = queries.get_existing_employee_request(
            db,
            employee_id,
            company_id,
            scheduled_date,
            direction,
            requested_time,
        )
        if existing is not None:
            status = existing["status"]
            reply = {
                "applied": "This change was already applied to your roster.",
                "pending_approval": "This request is already waiting for your transport manager.",
                "rejected": "Your manager already declined this request.",
                "cancelled": "This request could not be applied. Please choose another time.",
            }.get(status, f"This request is already {status.replace('_', ' ')}.")
            conversation.pop("pending", None)
            return {
                "status": status,
                "reply": reply,
                "request_id": existing["id"],
                "conversation": conversation,
            }

        roster_entries = tools.get_my_roster(scheduled_date)
        roster = next(
            (entry for entry in roster_entries if entry["trip_type"] == direction),
            None,
        )
        if roster is None:
            conversation.pop("pending", None)
            return {
                "status": "no_roster",
                "reply": f"I couldn't find an active {direction} roster for {scheduled_date}. Please check the date with your transport desk.",
                "request_id": None,
                "conversation": conversation,
            }
        if requested_time == roster["scheduled_time"]:
            conversation.pop("pending", None)
            return {
                "status": "unchanged",
                "reply": f"Your {direction} is already scheduled for {requested_time}; your roster has not changed.",
                "request_id": None,
                "conversation": conversation,
            }
        candidates = tools.search_available_cabs(
            scheduled_date, direction, roster["route_id"]
        )
        if not candidates:
            return {
                "status": "no_cabs",
                "reply": f"There are no available {direction} cabs on your route for {scheduled_date}. Please contact your transport desk.",
                "request_id": None,
            }

        exact = next(
            (cab for cab in candidates if cab["scheduled_time"] == requested_time),
            None,
        )
        if exact is None:
            options = sorted(
                candidates,
                key=lambda cab: (
                    abs(time_to_minutes(cab["scheduled_time"]) - time_to_minutes(requested_time)),
                    cab["scheduled_time"],
                ),
            )[:3]
            conversation["pending"] = {
                "direction": direction,
                "date": scheduled_date,
                "time": requested_time,
                "roster_id": roster["id"],
                "awaiting": "alternative",
                "options": [
                    {"cab_id": cab["id"], "time": cab["scheduled_time"]}
                    for cab in options
                ],
            }
            times = ", ".join(cab["scheduled_time"] for cab in options)
            return {
                "status": "alternatives",
                "reply": (
                    f"I couldn't find an available cab at {requested_time}. "
                    f"The nearest available times are {times}. Reply with a time to choose one."
                ),
                "request_id": None,
                "conversation": conversation,
            }

        pending = {
            "direction": direction,
            "date": scheduled_date,
            "time": exact["scheduled_time"],
            "roster_id": roster["id"],
            "cab_id": exact["id"],
            "awaiting": "approval_confirmation",
        }
        return await self._submit_pending(
            tools, pending, conversation_id, conversation, False
        )

    async def _submit_pending(
        self,
        tools: AgentTools,
        pending: dict[str, Any],
        conversation_id: str,
        conversation: dict[str, Any],
        confirmed: bool,
    ) -> dict[str, Any]:
        result = tools.raise_shift_change_request(
            roster_id=pending["roster_id"],
            time=pending["time"],
            cab_id=pending["cab_id"],
            conversation_id=conversation_id,
            confirmed_for_approval=confirmed,
        )
        status = result["status"]
        reply = result["message"]
        if status == "approval_confirmation_required":
            pending["awaiting"] = "approval_confirmation"
            conversation["pending"] = pending
            reply += " Would you like me to send this to your transport manager for approval?"
        elif status == "pending_approval":
            conversation.pop("pending", None)
            reply += f" Request ID: {result['request_id']}."
        elif status == "no_seats":
            conversation.pop("pending", None)
            reply += " Please choose another available time and I can check again."
        else:
            conversation.pop("pending", None)
        return {
            "status": status,
            "reply": reply,
            "request_id": result.get("request_id"),
            "conversation": conversation,
        }

    async def ainvoke(
        self,
        *,
        db,
        employee_id: str,
        company_id: str,
        conversation_id: str,
        message: str,
        conversation: dict[str, Any],
        redis_client=None,
    ) -> dict[str, Any]:
        initial: AgentState = {
            "db": db,
            "employee_id": employee_id,
            "company_id": company_id,
            "conversation_id": conversation_id,
            "user_message": message,
            "conversation": conversation,
            "redis_client": redis_client,
            "llm_calls": 0,
        }
        result = await self.graph.ainvoke(initial)
        return {
            "status": result.get("status", "ok"),
            "reply": result.get("reply", "I couldn't complete that. Please try again."),
            "request_id": result.get("request_id"),
            "conversation": result.get("conversation", conversation),
            "llm_calls": min(result.get("llm_calls", 0), settings.MAX_LLM_CALLS),
        }
