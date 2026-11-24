from fastapi.testclient import TestClient

from app.db import queries
from app.main import app
from app.auth.jwt_handler import create_token
from tests.conftest import INDIA_TZ
from datetime import datetime, timedelta


def test_employee_cannot_read_other_company_roster(database):
    date = (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    assert queries.get_employee_roster(
        database, "globex-emp1", "acme", date
    ) == []


def test_employee_cannot_read_other_company_request(database):
    date = (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    roster = queries.get_roster_entry(
        database, "roster-globex-emp1-drop", "globex-emp1", "globex"
    )
    cab = queries.get_cab(database, "globex-cab4", "globex")
    assert roster is not None
    assert cab is not None
    pending = queries.create_pending_request(
        database,
        employee_id="globex-emp1",
        company_id="globex",
        roster=roster,
        requested_time=cab["scheduled_time"],
        target_cab=cab,
        reason="test pending request",
        conversation_id="globex-conversation",
    )

    token = create_token("acme-emp1", "acme")
    with TestClient(app) as client:
        response = client.get(
            f"/requests/{pending['request_id']}",
            headers={"Authorization": f"Bearer {token}"},
        )
    assert response.status_code == 404
    assert response.json() == {"detail": "Request not found."}


def test_chat_rejects_identity_fields_from_request_body(database):
    token = create_token("acme-emp1", "acme")
    with TestClient(app) as client:
        response = client.post(
            "/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "message": "show my roster",
                "employee_id": "globex-emp1",
                "company_id": "globex",
            },
        )
    assert response.status_code == 422


def test_demo_page_is_served(database):
    with TestClient(app) as client:
        response = client.get("/")
    assert response.status_code == 200
    assert "Shift-Change Assistant" in response.text


def test_tool_failure_returns_plain_recoverable_message(monkeypatch, database):
    from app.agent.tools import AgentTools

    def fail_roster_lookup(self, date):
        raise OSError("private internal details")

    monkeypatch.setattr(AgentTools, "get_my_roster", fail_roster_lookup)
    token = create_token("acme-emp1", "acme")
    with TestClient(app) as client:
        response = client.post(
            "/chat",
            headers={"Authorization": f"Bearer {token}"},
            json={"message": "change my drop to 19:00 tomorrow"},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "temporarily_unavailable"
    assert "try again" in response.json()["reply"].lower()
    assert "private internal details" not in response.text
