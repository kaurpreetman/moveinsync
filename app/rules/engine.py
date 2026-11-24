"""Deterministic enforcement of company shift-change policy."""

from dataclasses import dataclass
from datetime import date, datetime, time
from zoneinfo import ZoneInfo


INDIA_TZ = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class RuleVerdict:
    allowed: bool
    needs_approval: bool
    reason: str


class RulesEngine:
    """Business rules are evaluated in Python; model output is never a verdict."""

    @staticmethod
    def evaluate(
        policy: dict,
        roster: dict,
        new_time: str,
        target_cab: dict,
        now: datetime | None = None,
    ) -> RuleVerdict:
        try:
            trip_date = date.fromisoformat(roster["scheduled_date"])
            trip_time = time.fromisoformat(roster["scheduled_time"])
            requested_time = time.fromisoformat(new_time)
        except (KeyError, TypeError, ValueError):
            return RuleVerdict(False, False, "The roster or requested time is invalid.")

        if requested_time.second or requested_time.microsecond:
            return RuleVerdict(False, False, "Please use a time in HH:MM format.")
        current = now or datetime.now(INDIA_TZ)
        if current.tzinfo is None:
            current = current.replace(tzinfo=INDIA_TZ)
        else:
            current = current.astimezone(INDIA_TZ)
        if trip_date < current.date():
            return RuleVerdict(False, False, "This roster entry is in the past.")

        if (
            target_cab.get("company_id") != policy.get("id")
            or target_cab.get("scheduled_date") != trip_date.isoformat()
            or target_cab.get("direction") != roster.get("trip_type")
            or target_cab.get("scheduled_time") != new_time
        ):
            return RuleVerdict(False, False, "That cab does not match your roster.")
        if target_cab.get("total_seats", 0) > policy.get("max_seats_per_cab", 0):
            return RuleVerdict(False, False, "That cab exceeds your company's seat limit.")
        if target_cab.get("seats_available", 0) < 1:
            return RuleVerdict(False, False, "That cab has no seats available.")

        cutoff_key = (
            "cutoff_minutes_pickup"
            if roster["trip_type"] == "pickup"
            else "cutoff_minutes_drop"
        )
        cutoff_minutes = int(policy[cutoff_key])
        scheduled = datetime.combine(trip_date, trip_time, tzinfo=INDIA_TZ)
        remaining_minutes = (scheduled - current).total_seconds() / 60

        if remaining_minutes <= 0:
            return RuleVerdict(False, False, "The scheduled trip time has already passed.")
        if remaining_minutes < cutoff_minutes:
            return RuleVerdict(
                False,
                True,
                f"Your company's cut-off is {cutoff_minutes} minutes before the "
                f"scheduled {roster['trip_type']}. There are {int(remaining_minutes)} "
                "minutes left, so this needs manager approval.",
            )
        return RuleVerdict(True, False, "The change is within company policy.")
