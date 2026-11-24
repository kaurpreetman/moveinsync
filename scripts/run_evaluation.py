"""Run deterministic end-to-end evaluation cases and write JSONL results."""

import asyncio
import json
import sqlite3
import tempfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from pathlib import Path
import sys
from threading import Barrier
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from app.agent.graph import ShiftChangeAgent
from app.agent.tools import AgentTools
from app.auth.jwt_handler import create_token
from app.config import settings
from app.db import queries
from app.db.database import get_db, init_db
from app.main import app
from app.services.shift_change import ShiftChangeService

INDIA_TZ = ZoneInfo("Asia/Kolkata")
OUTPUT_PATH = Path(__file__).resolve().parents[1] / "evaluation_set.jsonl"


class FakeLLM:
    def __init__(self, value: dict):
        self.value = value
        self.calls = 0

    async def ainvoke(self, _messages):
        self.calls += 1
        return SimpleNamespace(content=json.dumps(self.value))


def _record(case_id: str, user_input: str, expected: str, actual: str, notes: str) -> dict:
    return {
        "id": case_id,
        "input": user_input,
        "expected": expected,
        "actual": actual,
        "pass": actual == expected,
        "notes": notes,
    }


def _new_database(path: Path):
    init_db(str(path))
    return get_db(str(path))


async def _run_agent(agent, db, employee, company, conversation_id, message, context):
    return await agent.ainvoke(
        db=db,
        employee_id=employee,
        company_id=company,
        conversation_id=conversation_id,
        message=message,
        conversation=context,
    )


def _tool_selection(path: Path) -> dict:
    db = _new_database(path)
    date = (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    llm = FakeLLM(
        {
            "action": "change",
            "direction": "drop",
            "time": "19:00",
            "date": date,
            "confirm": None,
        }
    )
    result = asyncio.run(
        _run_agent(
            ShiftChangeAgent(llm),
            db,
            "acme-emp1",
            "acme",
            "evaluation-tools",
            "Change my drop to 19:00 tomorrow",
            {},
        )
    )
    db.close()
    actual = (
        f"{result['status']} with {result['llm_calls']} fake LLM call"
        if result["status"] == "applied" and llm.calls == 1
        else f"{result['status']} with {llm.calls} fake LLM calls"
    )
    expected = "applied with 1 fake LLM call"
    return _record(
        "tool-selection",
        "Change my drop to 19:00 tomorrow",
        expected,
        actual,
        "Fake model extracts intent; authenticated context selects roster and cab tools.",
    )


def _clarification(path: Path) -> dict:
    db = _new_database(path)
    agent = ShiftChangeAgent(llm=None)
    context = {}
    for message in ("Can I go a bit late?", "drop", "19:00"):
        result = asyncio.run(
            _run_agent(
                agent, db, "acme-emp1", "acme", "evaluation-clarify", message, context
            )
        )
        context = result["conversation"]
    db.close()
    actual = (
        "applied after direction and time follow-ups"
        if result["status"] == "applied"
        else result["status"]
    )
    expected = "applied after direction and time follow-ups"
    return _record(
        "clarification-context",
        "Can I go a bit late? / drop / 19:00",
        expected,
        actual,
        "Conversation state is passed between graph invocations without another LLM call.",
    )


def _cutoff(path: Path) -> dict:
    db = _new_database(path)
    now = datetime.now(INDIA_TZ)
    departure = None
    target = None
    for minutes in range(35, 90):
        candidate = now + timedelta(minutes=minutes)
        candidate_target = candidate + timedelta(minutes=5)
        if candidate.date() == candidate_target.date():
            departure, target = candidate, candidate_target
            break
    if departure is None or target is None:
        raise RuntimeError("Could not prepare a same-day cut-off evaluation case.")
    trip_date = departure.date().isoformat()
    new_time = target.strftime("%H:%M")
    db.execute(
        "UPDATE roster SET scheduled_date = ?, scheduled_time = ? WHERE id = ?",
        (trip_date, departure.strftime("%H:%M"), "roster-acme-emp1-drop"),
    )
    db.execute(
        "UPDATE cabs SET scheduled_date = ?, scheduled_time = ? WHERE id = ?",
        (trip_date, new_time, "acme-cab6"),
    )
    db.commit()
    result = ShiftChangeService(db).submit_change(
        employee_id="acme-emp1",
        company_id="acme",
        roster_id="roster-acme-emp1-drop",
        new_time=new_time,
        new_cab_id="acme-cab6",
        conversation_id="evaluation-cutoff",
    )
    count = db.execute("SELECT COUNT(*) FROM shift_change_requests").fetchone()[0]
    db.close()
    actual = (
        "approval_confirmation_required; no request created"
        if result["status"] == "approval_confirmation_required" and count == 0
        else f"{result['status']}; {count} request(s)"
    )
    expected = "approval_confirmation_required; no request created"
    return _record(
        "cutoff-gate",
        f"Change my drop to {new_time} within Acme's 90-minute cut-off",
        expected,
        actual,
        "The roster is unchanged until the employee confirms manager escalation.",
    )


def _tenant_isolation(path: Path) -> dict:
    db = _new_database(path)
    roster = queries.get_roster_entry(
        db, "roster-globex-emp1-drop", "globex-emp1", "globex"
    )
    cab = queries.get_cab(db, "globex-cab4", "globex")
    if roster is None or cab is None:
        raise RuntimeError("Sample Globex request data is missing.")
    pending = queries.create_pending_request(
        db,
        employee_id="globex-emp1",
        company_id="globex",
        roster=roster,
        requested_time=cab["scheduled_time"],
        target_cab=cab,
        reason="evaluation-only pending record",
        conversation_id="evaluation",
    )
    db.close()
    settings.DB_PATH = str(path)
    token = create_token("acme-emp1", "acme")
    with TestClient(app) as client:
        response = client.get(
            f"/requests/{pending['request_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
    actual = "404 request not found" if response.status_code == 404 else str(response.status_code)
    return _record(
        "cross-company-isolation",
        "Acme employee asks for a Globex request by ID",
        "404 request not found",
        actual,
        "The endpoint scopes request lookup by both JWT company and employee.",
    )


def _approval_restart(path: Path) -> dict:
    db = _new_database(path)
    roster = queries.get_roster_entry(
        db, "roster-acme-emp1-drop", "acme-emp1", "acme"
    )
    cab = queries.get_cab(db, "acme-cab6", "acme")
    if roster is None or cab is None:
        raise RuntimeError("Sample Acme approval data is missing.")
    pending = queries.create_pending_request(
        db,
        employee_id="acme-emp1",
        company_id="acme",
        roster=roster,
        requested_time=cab["scheduled_time"],
        target_cab=cab,
        reason="evaluation approval",
        conversation_id="evaluation-restart",
    )
    db.close()

    # Re-open after initialization to model process recreation against durable storage.
    init_db(str(path))
    restarted_db = get_db(str(path))
    result = ShiftChangeService(restarted_db).approve_request(
        request_id=pending["request_id"],
        company_id="acme",
        manager_id="acme-mgr1",
        approved=True,
    )
    request = queries.get_shift_change_request(
        restarted_db, pending["request_id"], "acme", "acme-emp1"
    )
    history = queries.get_conversation_history(
        restarted_db, "acme", "acme-emp1", "evaluation-restart"
    )
    restarted_db.close()
    actual = (
        "applied and conversation resumed after database reopen"
        if result["status"] == "approved"
        and request
        and request["status"] == "applied"
        and any("approved and applied" in entry["message"] for entry in history)
        else result["status"]
    )
    expected = "applied and conversation resumed after database reopen"
    return _record(
        "approval-after-restart",
        "Manager approves a pending change after app restart",
        expected,
        actual,
        "The request, roster update, manager decision, and employee conversation message survive database reopen.",
    )


def _tool_failure(path: Path) -> dict:
    db = _new_database(path)
    db.close()
    settings.DB_PATH = str(path)

    def fail_roster_lookup(_self, _date):
        raise sqlite3.OperationalError("simulated private database failure")

    token = create_token("acme-emp1", "acme")
    with patch.object(AgentTools, "get_my_roster", fail_roster_lookup):
        with TestClient(app) as client:
            response = client.post(
                "/chat",
                headers={"Authorization": f"Bearer {token}"},
                json={"message": "change my drop to 19:00 tomorrow"},
            )
    body = response.json()
    actual = (
        "friendly retry message; no internal details"
        if response.status_code == 200
        and "try again" in body["reply"].lower()
        and "private database failure" not in response.text
        else body.get("status", str(response.status_code))
    )
    expected = "friendly retry message; no internal details"
    return _record(
        "tool-failure",
        "Roster tool raises a database error during a chat request",
        expected,
        actual,
        "The API logs the exception and gives the employee a clear retry instruction.",
    )


def _race_worker(db_path: str, barrier: Barrier, employee_id: str, roster_id: str):
    db = get_db(db_path)
    try:
        roster = queries.get_roster_entry(db, roster_id, employee_id, "acme")
        target = queries.get_cab(db, "acme-cab3", "acme")
        if roster is None or target is None:
            raise RuntimeError("Sample seat-race data is missing.")
        barrier.wait(timeout=5)
        return queries.apply_immediate_change(
            db,
            employee_id=employee_id,
            company_id="acme",
            roster=roster,
            target_cab=target,
            conversation_id="evaluation-race",
        )
    finally:
        db.close()


def _last_seat_race(path: Path) -> dict:
    db = _new_database(path)
    db.execute("UPDATE cabs SET seats_available = 1 WHERE id = 'acme-cab3'")
    db.commit()
    db.close()
    barrier = Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: _race_worker(str(path), barrier, *args),
                [
                    ("acme-emp1", "roster-acme-emp1-pickup"),
                    ("acme-emp2", "roster-acme-emp2-pickup"),
                ],
            )
        )
    check = get_db(str(path))
    count = check.execute("SELECT COUNT(*) FROM shift_change_requests").fetchone()[0]
    seats = check.execute(
        "SELECT seats_available FROM cabs WHERE id = 'acme-cab3'"
    ).fetchone()[0]
    check.close()
    actual = (
        "one applied; one rejected; one request; zero seats left"
        if sorted(result["status"] for result in results) == ["applied", "no_seats"]
        and count == 1
        and seats == 0
        else f"{[result['status'] for result in results]}; {count} requests; {seats} seats"
    )
    expected = "one applied; one rejected; one request; zero seats left"
    return _record(
        "last-seat-race",
        "Two employees request the last seat at the same time",
        expected,
        actual,
        "SQLite BEGIN IMMEDIATE and the conditional seat decrement serialize allocation.",
    )


def run() -> list[dict]:
    records = []
    original_db_path = settings.DB_PATH
    original_api_key = settings.GEMINI_API_KEY
    settings.GEMINI_API_KEY = ""
    try:
        with tempfile.TemporaryDirectory(prefix="moveinsync-eval-") as directory:
            root = Path(directory)
            cases = (
                _tool_selection,
                _clarification,
                _cutoff,
                _tenant_isolation,
                _approval_restart,
                _tool_failure,
                _last_seat_race,
            )
            for case in cases:
                path = root / f"{case.__name__}.sqlite"
                records.append(case(path))
    finally:
        settings.DB_PATH = original_db_path
        settings.GEMINI_API_KEY = original_api_key
    OUTPUT_PATH.write_text(
        "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
        encoding="utf-8",
    )
    return records


if __name__ == "__main__":
    results = run()
    passed = sum(result["pass"] for result in results)
    print(f"Evaluation: {passed}/{len(results)} passed")
    for result in results:
        print(f"{'PASS' if result['pass'] else 'FAIL'} {result['id']}: {result['actual']}")
    if passed != len(results):
        raise SystemExit(1)
