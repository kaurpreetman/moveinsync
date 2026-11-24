from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.auth.jwt_handler import create_token
from app.main import app

INDIA_TZ = ZoneInfo("Asia/Kolkata")


def test_approval_is_persisted_and_resumed_after_app_restart(database):
    now = datetime.now(INDIA_TZ).replace(second=0, microsecond=0)
    departure = target = None
    for offset in range(35, 90):
        candidate_departure = now + timedelta(minutes=offset)
        candidate_target = candidate_departure + timedelta(minutes=5)
        if candidate_departure.date() == candidate_target.date():
            departure, target = candidate_departure, candidate_target
            break
    assert departure is not None
    assert target is not None
    trip_date = departure.date().isoformat()
    original_time = departure.strftime("%H:%M")
    target_time = target.strftime("%H:%M")
    database.execute(
        "UPDATE roster SET scheduled_date = ?, scheduled_time = ? WHERE id = ?",
        (trip_date, original_time, "roster-acme-emp1-drop"),
    )
    database.execute(
        "UPDATE cabs SET scheduled_date = ? WHERE id = ?",
        (trip_date, "acme-cab5"),
    )
    database.execute(
        "UPDATE cabs SET scheduled_date = ?, scheduled_time = ? WHERE id = ?",
        (trip_date, target_time, "acme-cab6"),
    )
    database.commit()

    employee_token = create_token("acme-emp1", "acme")
    manager_token = create_token("acme-mgr1", "acme", role="manager")
    headers = {"Authorization": f"Bearer {employee_token}"}
    message = f"Change my drop to {target_time} on {trip_date}"

    with TestClient(app) as client:
        first = client.post(
            "/chat",
            headers=headers,
            json={"message": message, "conversation_id": "restart-test"},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "approval_confirmation_required"
        assert "manager approval" in first.json()["reply"].lower()

        confirmed = client.post(
            "/chat",
            headers=headers,
            json={"message": "Yes", "conversation_id": "restart-test"},
        )
        assert confirmed.status_code == 200
        assert confirmed.json()["status"] == "pending_approval"
        request_id = confirmed.json()["request_id"]

    # A new TestClient lifespan reconstructs the agent while retaining SQLite data.
    with TestClient(app) as restarted_client:
        pending = restarted_client.get(
            f"/requests/{request_id}", headers=headers
        )
        assert pending.status_code == 200
        assert pending.json()["status"] == "pending_approval"

        decision = restarted_client.post(
            f"/manager/approve/{request_id}",
            headers={"Authorization": f"Bearer {manager_token}"},
            json={"approved": True},
        )
        assert decision.status_code == 200
        assert decision.json()["status"] == "approved"

        resolved = restarted_client.get(
            f"/requests/{request_id}", headers=headers
        )
        assert resolved.status_code == 200
        assert resolved.json()["status"] == "applied"
        history = restarted_client.get(
            "/chat/history?conversation_id=restart-test",
            headers=headers,
        )
        assert history.status_code == 200
        assert any(
            "approved and applied" in entry["message"]
            for entry in history.json()["messages"]
        )


def test_wrong_manager_cannot_approve_another_company_request(database):
    from app.db import queries

    roster = queries.get_roster_entry(
        database, "roster-globex-emp1-drop", "globex-emp1", "globex"
    )
    cab = queries.get_cab(database, "globex-cab4", "globex")
    assert roster and cab
    pending = queries.create_pending_request(
        database,
        employee_id="globex-emp1",
        company_id="globex",
        roster=roster,
        requested_time=cab["scheduled_time"],
        target_cab=cab,
        reason="test approval",
        conversation_id="scope-test",
    )
    acme_manager = create_token("acme-mgr1", "acme", role="manager")
    with TestClient(app) as client:
        response = client.post(
            f"/manager/approve/{pending['request_id']}",
            headers={"Authorization": f"Bearer {acme_manager}"},
            json={"approved": True},
        )
    assert response.status_code == 200
    assert response.json()["status"] == "not_found"
