"""Scoped application tools used by the LangGraph execution node."""

from typing import Any

from app.db import queries
from app.services.cab import CabService
from app.services.roster import RosterService
from app.services.shift_change import ShiftChangeService


class AgentTools:
    """Tools close over identity from the verified JWT, never model arguments."""

    def __init__(
        self,
        db,
        employee_id: str,
        company_id: str,
        redis_client=None,
    ) -> None:
        self.db = db
        self.employee_id = employee_id
        self.company_id = company_id
        self.rosters = RosterService(db)
        self.cabs = CabService(db)
        self.changes = ShiftChangeService(db, redis_client)

    def get_my_roster(self, date: str) -> list[dict[str, Any]]:
        return self.rosters.get_employee_roster(
            self.employee_id, self.company_id, date
        )

    def search_available_cabs(
        self, date: str, direction: str, route_id: str
    ) -> list[dict[str, Any]]:
        policy = queries.get_company_policy(self.db, self.company_id)
        if policy is None:
            return []
        cabs = self.cabs.get_available_cabs(
            self.company_id, date, direction, route_id
        )
        return [
            cab
            for cab in cabs
            if cab["total_seats"] <= policy["max_seats_per_cab"]
        ]

    def raise_shift_change_request(
        self,
        roster_id: str,
        time: str,
        cab_id: str,
        conversation_id: str,
        confirmed_for_approval: bool,
    ) -> dict[str, Any]:
        return self.changes.submit_change(
            employee_id=self.employee_id,
            company_id=self.company_id,
            roster_id=roster_id,
            new_time=time,
            new_cab_id=cab_id,
            conversation_id=conversation_id,
            confirm_rule_break=confirmed_for_approval,
        )
