"""Tenant-scoped SQLite queries and atomic roster mutations."""

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any


def _dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(row) if row is not None else None


def get_employee_roster(
    db: sqlite3.Connection, employee_id: str, company_id: str, date: str
) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT r.*, c.route_id, c.scheduled_time AS cab_time, rt.name AS route_name
        FROM roster r
        JOIN cabs c ON c.id = r.cab_id AND c.company_id = r.company_id
        JOIN routes rt ON rt.id = c.route_id AND rt.company_id = c.company_id
        WHERE r.employee_id = ? AND r.company_id = ? AND r.scheduled_date = ?
          AND r.status = 'active'
        ORDER BY r.trip_type
        """,
        (employee_id, company_id, date),
    ).fetchall()
    return [dict(row) for row in rows]


def get_roster_entry(
    db: sqlite3.Connection, roster_id: str, employee_id: str, company_id: str
) -> dict[str, Any] | None:
    return _dict(
        db.execute(
            """
            SELECT r.*, c.route_id, rt.name AS route_name
            FROM roster r
            JOIN cabs c ON c.id = r.cab_id AND c.company_id = r.company_id
            JOIN routes rt ON rt.id = c.route_id AND rt.company_id = c.company_id
            WHERE r.id = ? AND r.employee_id = ? AND r.company_id = ?
              AND r.status = 'active'
            """,
            (roster_id, employee_id, company_id),
        ).fetchone()
    )


def get_company_policy(
    db: sqlite3.Connection, company_id: str
) -> dict[str, Any] | None:
    return _dict(
        db.execute(
            "SELECT * FROM companies WHERE id = ?", (company_id,)
        ).fetchone()
    )


def get_available_cabs(
    db: sqlite3.Connection,
    company_id: str,
    date: str,
    direction: str,
    route_id: str | None = None,
) -> list[dict[str, Any]]:
    sql = """
        SELECT c.*, rt.name AS route_name
        FROM cabs c
        JOIN routes rt ON rt.id = c.route_id AND rt.company_id = c.company_id
        WHERE c.company_id = ? AND c.scheduled_date = ? AND c.direction = ?
          AND c.seats_available > 0
    """
    params: list[Any] = [company_id, date, direction]
    if route_id is not None:
        sql += " AND c.route_id = ?"
        params.append(route_id)
    sql += " ORDER BY c.scheduled_time, c.id"
    return [dict(row) for row in db.execute(sql, params).fetchall()]


def get_cab(
    db: sqlite3.Connection, cab_id: str, company_id: str
) -> dict[str, Any] | None:
    return _dict(
        db.execute(
            """
            SELECT c.*, rt.name AS route_name
            FROM cabs c
            JOIN routes rt ON rt.id = c.route_id AND rt.company_id = c.company_id
            WHERE c.id = ? AND c.company_id = ?
            """,
            (cab_id, company_id),
        ).fetchone()
    )


def get_employee(
    db: sqlite3.Connection, employee_id: str, company_id: str
) -> dict[str, Any] | None:
    return _dict(
        db.execute(
            "SELECT * FROM employees WHERE id = ? AND company_id = ?",
            (employee_id, company_id),
        ).fetchone()
    )


def create_pending_request(
    db: sqlite3.Connection,
    *,
    employee_id: str,
    company_id: str,
    roster: dict[str, Any],
    requested_time: str,
    target_cab: dict[str, Any],
    reason: str,
    conversation_id: str,
) -> dict[str, Any]:
    request_id = str(uuid.uuid4())
    try:
        db.execute(
            """
            INSERT INTO shift_change_requests (
                id, employee_id, company_id, original_roster_id, original_time,
                requested_time, original_cab_id, new_cab_id, trip_type,
                scheduled_date, status, reason, conversation_id
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending_approval', ?, ?)
            """,
            (
                request_id,
                employee_id,
                company_id,
                roster["id"],
                roster["scheduled_time"],
                requested_time,
                roster["cab_id"],
                target_cab["id"],
                roster["trip_type"],
                roster["scheduled_date"],
                reason,
                conversation_id,
            ),
        )
        db.commit()
        return {"request_id": request_id, "duplicate": False}
    except sqlite3.IntegrityError:
        db.rollback()
        existing = db.execute(
            """
            SELECT id, status FROM shift_change_requests
            WHERE employee_id = ? AND company_id = ? AND scheduled_date = ?
              AND requested_time = ? AND trip_type = ?
            """,
            (
                employee_id,
                company_id,
                roster["scheduled_date"],
                requested_time,
                roster["trip_type"],
            ),
        ).fetchone()
        if existing is None:
            raise
        return {
            "request_id": existing["id"],
            "status": existing["status"],
            "duplicate": True,
        }


def apply_immediate_change(
    db: sqlite3.Connection,
    *,
    employee_id: str,
    company_id: str,
    roster: dict[str, Any],
    target_cab: dict[str, Any],
    conversation_id: str,
) -> dict[str, Any]:
    """Create the request and reserve/release seats as one serialized transaction."""
    requested_time = target_cab["scheduled_time"]
    db.execute("BEGIN IMMEDIATE")
    try:
        existing = db.execute(
            """
            SELECT id, status FROM shift_change_requests
            WHERE employee_id = ? AND company_id = ? AND scheduled_date = ?
              AND requested_time = ? AND trip_type = ?
            """,
            (
                employee_id,
                company_id,
                roster["scheduled_date"],
                requested_time,
                roster["trip_type"],
            ),
        ).fetchone()
        if existing:
            db.commit()
            return {
                "status": existing["status"],
                "request_id": existing["id"],
                "duplicate": True,
            }

        current = db.execute(
            """
            SELECT id, cab_id, scheduled_time FROM roster
            WHERE id = ? AND employee_id = ? AND company_id = ? AND status = 'active'
            """,
            (roster["id"], employee_id, company_id),
        ).fetchone()
        if current is None:
            db.rollback()
            return {"status": "stale_roster"}

        if target_cab["id"] != current["cab_id"]:
            reserved = db.execute(
                """
                UPDATE cabs SET seats_available = seats_available - 1
                WHERE id = ? AND company_id = ? AND scheduled_date = ?
                  AND direction = ? AND seats_available > 0
                """,
                (
                    target_cab["id"],
                    company_id,
                    roster["scheduled_date"],
                    roster["trip_type"],
                ),
            )
            if reserved.rowcount != 1:
                db.rollback()
                return {"status": "no_seats"}

            released = db.execute(
                """
                UPDATE cabs SET seats_available = seats_available + 1
                WHERE id = ? AND company_id = ?
                """,
                (current["cab_id"], company_id),
            )
            if released.rowcount != 1:
                db.rollback()
                return {"status": "stale_roster"}

        request_id = str(uuid.uuid4())
        db.execute(
            """
            INSERT INTO shift_change_requests (
                id, employee_id, company_id, original_roster_id, original_time,
                requested_time, original_cab_id, new_cab_id, trip_type,
                scheduled_date, status, reason, conversation_id, resolved_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'applied', ?, ?, ?)
            """,
            (
                request_id,
                employee_id,
                company_id,
                roster["id"],
                current["scheduled_time"],
                requested_time,
                current["cab_id"],
                target_cab["id"],
                roster["trip_type"],
                roster["scheduled_date"],
                "Automatic approval - within policy",
                conversation_id,
                datetime.now(timezone.utc).isoformat(),
            ),
        )
        changed = db.execute(
            """
            UPDATE roster SET cab_id = ?, scheduled_time = ?, status = 'changed'
            WHERE id = ? AND employee_id = ? AND company_id = ? AND status = 'active'
            """,
            (
                target_cab["id"],
                requested_time,
                roster["id"],
                employee_id,
                company_id,
            ),
        )
        if changed.rowcount != 1:
            db.rollback()
            return {"status": "stale_roster"}
        db.commit()
        return {"status": "applied", "request_id": request_id, "duplicate": False}
    except Exception:
        db.rollback()
        raise


def resolve_manager_request(
    db: sqlite3.Connection,
    *,
    request_id: str,
    company_id: str,
    manager_id: str,
    approved: bool,
    notes: str | None,
) -> dict[str, Any]:
    """Authorize the assigned manager and atomically resolve a pending request."""
    db.execute("BEGIN IMMEDIATE")
    try:
        request = db.execute(
            """
            SELECT q.*, e.manager_id AS assigned_manager_id
            FROM shift_change_requests q
            JOIN employees e
              ON e.id = q.employee_id AND e.company_id = q.company_id
            WHERE q.id = ? AND q.company_id = ?
            """,
            (request_id, company_id),
        ).fetchone()
        manager = db.execute(
            """
            SELECT id FROM employees
            WHERE id = ? AND company_id = ? AND is_manager = 1
            """,
            (manager_id, company_id),
        ).fetchone()
        if request is None:
            db.rollback()
            return {"status": "not_found"}
        if manager is None or request["assigned_manager_id"] != manager_id:
            db.rollback()
            return {"status": "forbidden"}
        if request["status"] != "pending_approval":
            db.rollback()
            return {"status": "not_pending", "current_status": request["status"]}

        now = datetime.now(timezone.utc).isoformat()

        def append_employee_message(message: str) -> None:
            if request["conversation_id"]:
                db.execute(
                    """
                    INSERT INTO conversation_messages
                        (company_id, employee_id, conversation_id, role, message)
                    VALUES (?, ?, ?, 'assistant', ?)
                    """,
                    (
                        request["company_id"],
                        request["employee_id"],
                        request["conversation_id"],
                        message,
                    ),
                )

        if not approved:
            db.execute(
                """
                UPDATE shift_change_requests
                SET status = 'rejected', manager_decision = ?, resolved_at = ?
                WHERE id = ? AND status = 'pending_approval'
                """,
                (notes or "Rejected by manager", now, request_id),
            )
            append_employee_message(
                "Your shift-change request was declined by your transport manager. "
                "Check request status for the recorded decision."
            )
            db.commit()
            return {"status": "rejected", "request_id": request_id}

        roster = db.execute(
            """
            SELECT r.id, r.cab_id, r.scheduled_time, c.route_id
            FROM roster r
            JOIN cabs c ON c.id = r.cab_id AND c.company_id = r.company_id
            WHERE r.id = ? AND r.employee_id = ? AND r.company_id = ?
              AND r.scheduled_date = ? AND r.trip_type = ? AND r.status = 'active'
            """,
            (
                request["original_roster_id"],
                request["employee_id"],
                company_id,
                request["scheduled_date"],
                request["trip_type"],
            ),
        ).fetchone()
        target = db.execute(
            """
            SELECT id, scheduled_time, route_id, total_seats FROM cabs
            WHERE id = ? AND company_id = ? AND scheduled_date = ? AND direction = ?
            """,
            (
                request["new_cab_id"],
                company_id,
                request["scheduled_date"],
                request["trip_type"],
            ),
        ).fetchone()
        policy = db.execute(
            "SELECT max_seats_per_cab FROM companies WHERE id = ?",
            (company_id,),
        ).fetchone()
        if (
            roster is None
            or target is None
            or policy is None
            or roster["cab_id"] != request["original_cab_id"]
            or target["route_id"] != roster["route_id"]
            or target["total_seats"] > policy["max_seats_per_cab"]
            or target["scheduled_time"] != request["requested_time"]
        ):
            db.execute(
                """
                UPDATE shift_change_requests
                SET status = 'cancelled', manager_decision = ?,
                    resolved_at = ?
                WHERE id = ?
                """,
                ("Could not apply: roster or cab changed", now, request_id),
            )
            append_employee_message(
                "Your manager approved the request, but the roster changed before "
                "it could be applied. Please contact the transport desk."
            )
            db.commit()
            return {"status": "stale"}

        if target["id"] != roster["cab_id"]:
            reserved = db.execute(
                """
                UPDATE cabs SET seats_available = seats_available - 1
                WHERE id = ? AND company_id = ? AND seats_available > 0
                """,
                (target["id"], company_id),
            )
            if reserved.rowcount != 1:
                db.execute(
                    """
                    UPDATE shift_change_requests
                    SET status = 'cancelled', manager_decision = ?,
                        resolved_at = ?
                    WHERE id = ?
                    """,
                    ("Could not apply: cab has filled up", now, request_id),
                )
                append_employee_message(
                    "Your manager approved the request, but the cab filled up before "
                    "the change could be applied. Please choose another time."
                )
                db.commit()
                return {"status": "no_seats"}
            db.execute(
                "UPDATE cabs SET seats_available = seats_available + 1 WHERE id = ? AND company_id = ?",
                (roster["cab_id"], company_id),
            )

        db.execute(
            """
            UPDATE roster SET cab_id = ?, scheduled_time = ?, status = 'changed'
            WHERE id = ? AND employee_id = ? AND company_id = ? AND status = 'active'
            """,
            (
                target["id"],
                target["scheduled_time"],
                roster["id"],
                request["employee_id"],
                company_id,
            ),
        )
        db.execute(
            """
            UPDATE shift_change_requests
            SET status = 'applied', manager_decision = ?, resolved_at = ?
            WHERE id = ? AND status = 'pending_approval'
            """,
            (notes or "Approved by assigned manager", now, request_id),
        )
        append_employee_message(
            "Your shift-change request was approved and applied to your roster."
        )
        db.commit()
        return {"status": "approved", "request_id": request_id}
    except Exception:
        db.rollback()
        raise


def get_shift_change_request(
    db: sqlite3.Connection,
    request_id: str,
    company_id: str,
    employee_id: str | None = None,
) -> dict[str, Any] | None:
    sql = "SELECT * FROM shift_change_requests WHERE id = ? AND company_id = ?"
    params: list[Any] = [request_id, company_id]
    if employee_id is not None:
        sql += " AND employee_id = ?"
        params.append(employee_id)
    return _dict(db.execute(sql, params).fetchone())


def get_existing_request_for_roster(
    db: sqlite3.Connection,
    roster_id: str,
    employee_id: str,
    company_id: str,
    requested_time: str,
) -> dict[str, Any] | None:
    return _dict(
        db.execute(
            """
            SELECT id, status FROM shift_change_requests
            WHERE original_roster_id = ? AND employee_id = ? AND company_id = ?
              AND requested_time = ?
            """,
            (roster_id, employee_id, company_id, requested_time),
        ).fetchone()
    )


def get_existing_employee_request(
    db: sqlite3.Connection,
    employee_id: str,
    company_id: str,
    scheduled_date: str,
    trip_type: str,
    requested_time: str,
) -> dict[str, Any] | None:
    return _dict(
        db.execute(
            """
            SELECT id, status FROM shift_change_requests
            WHERE employee_id = ? AND company_id = ? AND scheduled_date = ?
              AND trip_type = ? AND requested_time = ?
            """,
            (
                employee_id,
                company_id,
                scheduled_date,
                trip_type,
                requested_time,
            ),
        ).fetchone()
    )


def list_employee_requests(
    db: sqlite3.Connection, employee_id: str, company_id: str, limit: int = 20
) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT id, status, original_time, requested_time, trip_type,
               scheduled_date, reason, manager_decision, created_at, resolved_at
        FROM shift_change_requests
        WHERE employee_id = ? AND company_id = ?
        ORDER BY created_at DESC LIMIT ?
        """,
        (employee_id, company_id, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def get_conversation_state(
    db: sqlite3.Connection, company_id: str, employee_id: str, conversation_id: str
) -> dict[str, Any]:
    row = db.execute(
        """
        SELECT state_json FROM conversations
        WHERE company_id = ? AND employee_id = ? AND conversation_id = ?
        """,
        (company_id, employee_id, conversation_id),
    ).fetchone()
    if row is None:
        return {}
    try:
        value = json.loads(row["state_json"])
        return value if isinstance(value, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_conversation_turn(
    db: sqlite3.Connection,
    *,
    company_id: str,
    employee_id: str,
    conversation_id: str,
    user_message: str,
    assistant_message: str,
    state: dict[str, Any],
) -> None:
    db.execute(
        """
        INSERT INTO conversations (company_id, employee_id, conversation_id, state_json)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(company_id, employee_id, conversation_id)
        DO UPDATE SET state_json = excluded.state_json,
                      updated_at = datetime('now')
        """,
        (company_id, employee_id, conversation_id, json.dumps(state)),
    )
    db.executemany(
        """
        INSERT INTO conversation_messages
            (company_id, employee_id, conversation_id, role, message)
        VALUES (?, ?, ?, ?, ?)
        """,
        [
            (company_id, employee_id, conversation_id, "employee", user_message),
            (company_id, employee_id, conversation_id, "assistant", assistant_message),
        ],
    )
    db.commit()


def get_conversation_history(
    db: sqlite3.Connection,
    company_id: str,
    employee_id: str,
    conversation_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    rows = db.execute(
        """
        SELECT role, message, created_at FROM conversation_messages
        WHERE company_id = ? AND employee_id = ? AND conversation_id = ?
        ORDER BY id DESC LIMIT ?
        """,
        (company_id, employee_id, conversation_id, limit),
    ).fetchall()
    return [dict(row) for row in reversed(rows)]
