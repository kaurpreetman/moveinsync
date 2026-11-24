"""Business logic service for shift-change requests."""

import logging
from datetime import datetime
from typing import Any

import redis as redis_lib

from app.db import queries
from app.rules.engine import RulesEngine

logger = logging.getLogger(__name__)


def time_to_minutes(time_str: str) -> int:
    """Convert a validated HH:MM time to minutes since midnight."""
    parsed = datetime.strptime(time_str, "%H:%M")
    return parsed.hour * 60 + parsed.minute


def find_best_cab(cabs: list[dict[str, Any]], target_time: str) -> dict[str, Any] | None:
    """Return the available same-route option closest to a requested time."""
    if not cabs:
        return None
    target_minutes = time_to_minutes(target_time)
    return min(
        cabs,
        key=lambda cab: (
            abs(time_to_minutes(cab["scheduled_time"]) - target_minutes),
            cab["scheduled_time"],
            cab["id"],
        ),
    )


class ShiftChangeService:
    """Core policy, seat-allocation, and manager-approval operations."""

    def __init__(
        self, db, redis_client: redis_lib.Redis | None = None
    ) -> None:
        self.db = db
        self.redis = redis_client

    def submit_change(
        self,
        employee_id: str,
        company_id: str,
        roster_id: str,
        new_time: str,
        new_cab_id: str,
        conversation_id: str,
        confirm_rule_break: bool = False,
    ) -> dict[str, Any]:
        existing = queries.get_existing_request_for_roster(
            self.db, roster_id, employee_id, company_id, new_time
        )
        if existing is not None:
            if existing["status"] == "applied":
                return {
                    "status": "applied",
                    "request_id": existing["id"],
                    "duplicate": True,
                    "message": "This request was already applied to your roster.",
                }
            if existing["status"] == "pending_approval":
                return {
                    "status": "pending_approval",
                    "request_id": existing["id"],
                    "duplicate": True,
                    "message": "This request is already waiting for your transport manager.",
                }
            return {
                "status": existing["status"],
                "request_id": existing["id"],
                "duplicate": True,
                "message": f"This request is already {existing['status'].replace('_', ' ')}.",
            }

        roster = queries.get_roster_entry(
            self.db, roster_id, employee_id, company_id
        )
        if roster is None:
            return {"status": "not_found", "message": "I couldn't find that roster entry."}

        policy = queries.get_company_policy(self.db, company_id)
        target_cab = queries.get_cab(self.db, new_cab_id, company_id)
        if policy is None or target_cab is None:
            return {
                "status": "not_found",
                "message": "I couldn't verify the company policy or selected cab.",
            }

        if target_cab["route_id"] != roster["route_id"]:
            return {
                "status": "invalid_cab",
                "message": "That cab is not on your roster route.",
            }

        verdict = RulesEngine.evaluate(policy, roster, new_time, target_cab)
        if verdict.needs_approval and not confirm_rule_break:
            return {
                "status": "approval_confirmation_required",
                "message": verdict.reason,
            }
        if not verdict.allowed and not verdict.needs_approval:
            return {"status": "denied", "message": verdict.reason}

        if verdict.needs_approval:
            try:
                pending = queries.create_pending_request(
                    self.db,
                    employee_id=employee_id,
                    company_id=company_id,
                    roster=roster,
                    requested_time=new_time,
                    target_cab=target_cab,
                    reason=verdict.reason,
                    conversation_id=conversation_id,
                )
            except Exception:
                self.db.rollback()
                raise
            return {
                "status": "pending_approval",
                "message": "Your request is waiting for your transport manager's decision.",
                "request_id": pending["request_id"],
                "duplicate": pending["duplicate"],
            }

        lock = None
        lock_acquired = False
        if self.redis is not None:
            try:
                lock = self.redis.lock(
                    f"seat-lock:{company_id}:{new_cab_id}:{roster['scheduled_date']}",
                    timeout=5,
                )
                lock_acquired = bool(lock.acquire(blocking_timeout=0.5))
            except redis_lib.RedisError as exc:
                logger.warning("Redis seat lock unavailable; SQLite will serialize: %s", exc)
                lock = None
            if lock is not None and not lock_acquired:
                return {
                    "status": "temporarily_unavailable",
                    "message": "I couldn't secure the seat just now. Please try again.",
                }

        try:
            result = queries.apply_immediate_change(
                self.db,
                employee_id=employee_id,
                company_id=company_id,
                roster=roster,
                target_cab=target_cab,
                conversation_id=conversation_id,
            )
        finally:
            if lock is not None and lock_acquired:
                try:
                    lock.release()
                except redis_lib.RedisError as exc:
                    logger.warning("Could not release Redis seat lock: %s", exc)

        if result["status"] == "applied":
            already = " This request was already applied." if result.get("duplicate") else ""
            return {
                "status": "applied",
                "request_id": result["request_id"],
                "message": (
                    f"Confirmed: your {roster['trip_type']} is now at "
                    f"{target_cab['scheduled_time']} on your updated roster.{already}"
                ),
            }
        if result["status"] == "no_seats":
            return {
                "status": "no_seats",
                "message": "That cab filled up while we were processing your request.",
            }
        if result["status"] == "stale_roster":
            return {
                "status": "stale_roster",
                "message": "Your roster changed while I was processing this. Please check it and try again.",
            }
        if result["status"] in {"pending_approval", "approved", "rejected"}:
            return {
                "status": result["status"],
                "request_id": result.get("request_id"),
                "message": f"This request is already {result['status'].replace('_', ' ')}.",
            }
        return {"status": "error", "message": "I couldn't apply that change. Please try again."}

    def approve_request(
        self,
        request_id: str,
        company_id: str,
        manager_id: str,
        approved: bool,
        manager_notes: str | None = None,
    ) -> dict[str, Any]:
        result = queries.resolve_manager_request(
            self.db,
            request_id=request_id,
            company_id=company_id,
            manager_id=manager_id,
            approved=approved,
            notes=manager_notes,
        )
        messages = {
            "approved": "Request approved and applied to the roster.",
            "rejected": "Request rejected. The employee can see the decision in request status.",
            "not_found": "Request not found.",
            "forbidden": "This request is not assigned to you.",
            "stale": "The roster changed before approval, so the request could not be applied.",
            "no_seats": "The cab filled up before approval could be applied. Ask the employee to choose another time.",
        }
        if result["status"] == "not_pending":
            return {
                "status": "not_pending",
                "message": f"This request is already {result['current_status']}.",
            }
        return {
            "status": result["status"],
            "request_id": result.get("request_id", request_id),
            "message": messages.get(result["status"], "Could not resolve this request."),
        }
