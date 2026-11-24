from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

from app.db.database import get_db
from app.db import queries
from app.services.shift_change import ShiftChangeService
import redis


def _race_change(db_path, barrier, employee_id, roster_id):
    db = get_db(db_path)
    try:
        roster = queries.get_roster_entry(db, roster_id, employee_id, "acme")
        target = queries.get_cab(db, "acme-cab3", "acme")
        assert roster and target
        barrier.wait(timeout=5)
        return queries.apply_immediate_change(
            db,
            employee_id=employee_id,
            company_id="acme",
            roster=roster,
            target_cab=target,
            conversation_id="race",
        )
    finally:
        db.close()


def test_two_employees_racing_for_last_seat_only_one_change_applies(
    database, tmp_path
):
    database.execute(
        "UPDATE cabs SET seats_available = 1 WHERE id = 'acme-cab3'"
    )
    database.commit()
    barrier = Barrier(2)
    db_path = str(tmp_path / "moveinsync-test.db")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda args: _race_change(db_path, barrier, *args),
                [
                    ("acme-emp1", "roster-acme-emp1-pickup"),
                    ("acme-emp2", "roster-acme-emp2-pickup"),
                ],
            )
        )
    assert sorted(result["status"] for result in results) == ["applied", "no_seats"]
    assert database.execute(
        "SELECT seats_available FROM cabs WHERE id = 'acme-cab3'"
    ).fetchone()[0] == 0
    assert database.execute(
        "SELECT COUNT(*) FROM shift_change_requests"
    ).fetchone()[0] == 1


def test_double_submit_creates_one_request_and_reserves_one_seat(database, tmp_path):
    database.execute(
        "UPDATE cabs SET seats_available = 1 WHERE id = 'acme-cab3'"
    )
    database.commit()
    barrier = Barrier(2)
    db_path = str(tmp_path / "moveinsync-test.db")
    args = ("acme-emp1", "roster-acme-emp1-pickup")
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(
            pool.map(
                lambda _: _race_change(db_path, barrier, *args),
                range(2),
            )
        )
    assert [result["status"] for result in results] == ["applied", "applied"]
    assert sum(not result["duplicate"] for result in results) == 1
    assert database.execute(
        "SELECT seats_available FROM cabs WHERE id = 'acme-cab3'"
    ).fetchone()[0] == 0
    assert database.execute(
        "SELECT COUNT(*) FROM shift_change_requests"
    ).fetchone()[0] == 1


def test_redis_failure_falls_back_to_atomic_sqlite(database):
    class UnavailableRedis:
        def lock(self, *_args, **_kwargs):
            raise redis.ConnectionError("simulated redis outage")

    result = ShiftChangeService(database, UnavailableRedis()).submit_change(
        employee_id="acme-emp1",
        company_id="acme",
        roster_id="roster-acme-emp1-drop",
        new_time="19:00",
        new_cab_id="acme-cab6",
        conversation_id="redis-fallback",
    )
    assert result["status"] == "applied"
    assert database.execute(
        "SELECT COUNT(*) FROM shift_change_requests"
    ).fetchone()[0] == 1
