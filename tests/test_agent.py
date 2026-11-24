import json
import asyncio
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from app.agent.graph import ShiftChangeAgent
from app.agent.tools import AgentTools
from app.db import queries
from tests.conftest import INDIA_TZ


class FakeLLM:
    def __init__(self, content):
        self.content = content
        self.calls = 0

    async def ainvoke(self, messages):
        self.calls += 1
        return SimpleNamespace(content=self.content)


@pytest.mark.asyncio
async def test_langgraph_selects_scoped_roster_and_cab_tools(database):
    tomorrow = (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    fake = FakeLLM(
        json.dumps(
            {
                "action": "change",
                "direction": "drop",
                "time": "19:00",
                "date": tomorrow,
                "confirm": None,
            }
        )
    )
    result = await ShiftChangeAgent(fake).ainvoke(
        db=database,
        employee_id="acme-emp1",
        company_id="acme",
        conversation_id="tool-test",
        message="Please change my drop to 19:00 tomorrow",
        conversation={},
    )
    assert result["status"] == "applied"
    assert result["llm_calls"] == 1
    assert fake.calls == 1
    assert "19:00" in result["reply"]
    assert queries.get_roster_entry(
        database, "roster-acme-emp1-drop", "acme-emp1", "acme"
    ) is None


@pytest.mark.asyncio
async def test_chat_double_submit_is_idempotent_after_roster_change(database):
    tomorrow = (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    fake = FakeLLM(
        json.dumps(
            {
                "action": "change",
                "direction": "drop",
                "time": "19:00",
                "date": tomorrow,
                "confirm": None,
            }
        )
    )
    agent = ShiftChangeAgent(fake)
    args = {
        "db": database,
        "employee_id": "acme-emp1",
        "company_id": "acme",
        "conversation_id": "double-submit",
        "message": "Change my drop to 19:00 tomorrow",
        "conversation": {},
    }
    first = await agent.ainvoke(**args)
    second = await agent.ainvoke(**args)
    count = database.execute(
        "SELECT COUNT(*) FROM shift_change_requests"
    ).fetchone()[0]
    assert first["status"] == "applied"
    assert second["status"] == "applied"
    assert "already applied" in second["reply"]
    assert count == 1
    assert fake.calls == 2


@pytest.mark.asyncio
async def test_ambiguous_request_carries_context_across_messages(database):
    agent = ShiftChangeAgent(llm=None)
    common = {
        "db": database,
        "employee_id": "acme-emp1",
        "company_id": "acme",
        "conversation_id": "follow-up",
    }
    first = await agent.ainvoke(
        **common, message="Can I go a bit late?", conversation={}
    )
    second = await agent.ainvoke(
        **common, message="drop", conversation=first["conversation"]
    )
    third = await agent.ainvoke(
        **common, message="19:00", conversation=second["conversation"]
    )
    assert first["status"] == "clarification"
    assert "pickup or your drop" in first["reply"]
    assert "What time" in second["reply"]
    assert third["status"] == "applied"


@pytest.mark.asyncio
async def test_nearest_available_slot_is_offered_and_requires_employee_choice(database):
    agent = ShiftChangeAgent(llm=None)
    date = (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    first = await agent.ainvoke(
        db=database,
        employee_id="acme-emp1",
        company_id="acme",
        conversation_id="alternative-test",
        message=f"Change my drop to 18:30 on {date}",
        conversation={},
    )
    assert first["status"] == "alternatives"
    assert "18:00" in first["reply"]
    assert "19:00" in first["reply"]

    chosen = await agent.ainvoke(
        db=database,
        employee_id="acme-emp1",
        company_id="acme",
        conversation_id="alternative-test",
        message="19:00",
        conversation=first["conversation"],
    )
    assert chosen["status"] == "applied"
    assert "19:00" in chosen["reply"]


@pytest.mark.asyncio
async def test_bare_drop_hour_is_clarified_instead_of_guessed(database):
    result = await ShiftChangeAgent(llm=None).ainvoke(
        db=database,
        employee_id="acme-emp1",
        company_id="acme",
        conversation_id="ambiguous-hour",
        message="Make it 8 instead",
        conversation={"pending": {"direction": "drop"}},
    )
    assert result["status"] == "clarification"
    assert "08:00 or 20:00" in result["reply"]
    assert database.execute(
        "SELECT COUNT(*) FROM shift_change_requests"
    ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_invalid_model_output_is_safe_and_does_not_apply(database):
    fake = FakeLLM("I will approve and change the roster.")
    result = await ShiftChangeAgent(fake).ainvoke(
        db=database,
        employee_id="acme-emp1",
        company_id="acme",
        conversation_id="bad-model",
        message="change my drop",
        conversation={},
    )
    assert result["status"] == "temporarily_unavailable"
    assert "try again" in result["reply"]
    assert database.execute(
        "SELECT COUNT(*) FROM shift_change_requests"
    ).fetchone()[0] == 0


@pytest.mark.asyncio
async def test_model_timeout_returns_a_friendly_response(monkeypatch, database):
    from app.config import settings

    class SlowLLM:
        async def ainvoke(self, messages):
            await asyncio.sleep(0.1)
            return SimpleNamespace(
                content='{"action":"change","direction":"drop","time":"19:00"}'
            )

    monkeypatch.setattr(settings, "LLM_TIMEOUT_SECONDS", 0.01)
    result = await ShiftChangeAgent(SlowLLM()).ainvoke(
        db=database,
        employee_id="acme-emp1",
        company_id="acme",
        conversation_id="timeout",
        message="change my drop to 19:00",
        conversation={},
    )
    assert result["status"] == "temporarily_unavailable"
    assert "try again" in result["reply"]
    assert result["llm_calls"] == 1


@pytest.mark.asyncio
async def test_tool_error_preserves_model_call_count_and_returns_plain_message(
    monkeypatch, database
):
    import sqlite3

    def fail_roster_lookup(self, date):
        raise sqlite3.OperationalError("internal database detail")

    monkeypatch.setattr(AgentTools, "get_my_roster", fail_roster_lookup)
    date = (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    fake = FakeLLM(
        json.dumps(
            {
                "action": "change",
                "direction": "drop",
                "time": "19:00",
                "date": date,
                "confirm": None,
            }
        )
    )
    result = await ShiftChangeAgent(fake).ainvoke(
        db=database,
        employee_id="acme-emp1",
        company_id="acme",
        conversation_id="tool-error",
        message="change my drop to 19:00 tomorrow",
        conversation={},
    )
    assert result["status"] == "tool_error"
    assert result["llm_calls"] == 1
    assert "try again" in result["reply"].lower()
    assert "internal database detail" not in result["reply"]


def test_tools_bind_to_authenticated_company_and_employee(database):
    tool = AgentTools(database, "acme-emp1", "acme")
    roster = tool.get_my_roster(
        (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    )
    assert roster
    assert {row["company_id"] for row in roster} == {"acme"}
    assert {row["employee_id"] for row in roster} == {"acme-emp1"}
