"""Roster service for MoveInSync shift change request agent."""

from typing import Any
from app.db import queries


class RosterService:
    """Service wrapping database queries for employee roster operations."""

    def __init__(self, db) -> None:
        self.db = db

    def get_employee_roster(
        self, employee_id: str, company_id: str, date: str
    ) -> list[dict[str, Any]]:
        """Fetch all roster entries for a specific employee on a specific date.

        Args:
            employee_id: Unique ID of the employee.
            company_id: Company identifier for tenant isolation.
            date: Scheduled date string (YYYY-MM-DD).

        Returns:
            List of roster entry dictionaries.
        """
        return queries.get_employee_roster(self.db, employee_id, company_id, date)

    def get_roster_entry(
        self, roster_id: str, employee_id: str, company_id: str
    ) -> dict[str, Any] | None:
        """Fetch a specific roster entry scoped by employee and company.

        Args:
            roster_id: Unique ID of the roster entry.
            employee_id: Unique ID of the employee.
            company_id: Company identifier for tenant isolation.

        Returns:
            Roster entry dictionary if found, None otherwise.
        """
        return queries.get_roster_entry(self.db, roster_id, employee_id, company_id)
