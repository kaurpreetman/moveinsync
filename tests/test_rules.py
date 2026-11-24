from datetime import datetime, timedelta

from app.db import queries
from app.rules.engine import INDIA_TZ, RulesEngine
from app.agent.tools import AgentTools


def test_cutoff_uses_each_company_policy(database):
    now = datetime.now(INDIA_TZ)
    # Both roster times are one hour away: Acme requires 90 minutes,
    # while Globex requires 60.
    departure = now + timedelta(minutes=70)
    date = departure.date().isoformat()
    scheduled = departure.strftime("%H:%M")
    for company, employee, roster_id, target_id in (
        ("acme", "acme-emp1", "roster-acme-emp1-drop", "acme-cab6"),
        ("globex", "globex-emp1", "roster-globex-emp1-drop", "globex-cab4"),
    ):
        database.execute(
            "UPDATE roster SET scheduled_date = ?, scheduled_time = ? WHERE id = ?",
            (date, scheduled, roster_id),
        )
        database.execute(
            "UPDATE cabs SET scheduled_date = ? WHERE id = ?",
            (date, target_id),
        )
    database.commit()

    acme_roster = queries.get_roster_entry(
        database, "roster-acme-emp1-drop", "acme-emp1", "acme"
    )
    acme_policy = queries.get_company_policy(database, "acme")
    acme_target = queries.get_cab(database, "acme-cab6", "acme")
    globex_roster = queries.get_roster_entry(
        database, "roster-globex-emp1-drop", "globex-emp1", "globex"
    )
    globex_policy = queries.get_company_policy(database, "globex")
    globex_target = queries.get_cab(database, "globex-cab4", "globex")

    assert acme_roster and acme_policy and acme_target
    assert globex_roster and globex_policy and globex_target
    acme = RulesEngine.evaluate(
        acme_policy, acme_roster, acme_target["scheduled_time"], acme_target, now
    )
    globex = RulesEngine.evaluate(
        globex_policy,
        globex_roster,
        globex_target["scheduled_time"],
        globex_target,
        now,
    )
    assert acme.needs_approval
    assert not acme.allowed
    assert globex.allowed


def test_cutoff_request_is_not_created_without_employee_confirmation(database):
    from app.services.shift_change import ShiftChangeService

    departure = datetime.now(INDIA_TZ) + timedelta(minutes=30)
    target = departure + timedelta(minutes=10)
    trip_date, trip_time = departure.strftime("%Y-%m-%d"), departure.strftime("%H:%M")
    new_time = target.strftime("%H:%M")
    database.execute(
        "UPDATE roster SET scheduled_date = ?, scheduled_time = ? WHERE id = ?",
        (trip_date, trip_time, "roster-acme-emp1-drop"),
    )
    database.execute(
        "UPDATE cabs SET scheduled_date = ?, scheduled_time = ? WHERE id = ?",
        (trip_date, new_time, "acme-cab6"),
    )
    database.commit()

    result = ShiftChangeService(database).submit_change(
        employee_id="acme-emp1",
        company_id="acme",
        roster_id="roster-acme-emp1-drop",
        new_time=new_time,
        new_cab_id="acme-cab6",
        conversation_id="test",
        confirm_rule_break=False,
    )
    count = database.execute(
        "SELECT COUNT(*) FROM shift_change_requests"
    ).fetchone()[0]
    assert result["status"] == "approval_confirmation_required"
    assert count == 0


def test_company_capacity_filters_out_oversized_cabs(database):
    date = (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()
    database.execute(
        "UPDATE cabs SET total_seats = 6 WHERE id = 'acme-cab3'"
    )
    database.commit()
    cabs = AgentTools(database, "acme-emp1", "acme").search_available_cabs(
        date, "pickup", "acme-r1"
    )
    assert "acme-cab3" not in {cab["id"] for cab in cabs}
