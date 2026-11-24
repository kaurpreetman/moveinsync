"""Cab service for MoveInSync shift change request agent."""

from typing import Any
from app.db import queries


class CabService:
    """Service wrapping database queries for cab operations."""

    def __init__(self, db) -> None:
        self.db = db

    def get_available_cabs(
        self,
        company_id: str,
        date: str,
        direction: str,
        route_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Fetch available cabs matching company, date, direction and optional route.

        Args:
            company_id: Company identifier for tenant isolation.
            date: Scheduled date string (YYYY-MM-DD).
            direction: Trip direction ('pickup' or 'drop').
            route_id: Optional route identifier.

        Returns:
            List of cab dictionaries.
        """
        return queries.get_available_cabs(self.db, company_id, date, direction, route_id)

    def get_cab(self, cab_id: str, company_id: str) -> dict[str, Any] | None:
        """Fetch cab details by cab ID scoped to company.

        Args:
            cab_id: Unique identifier for the cab.
            company_id: Company identifier for tenant isolation.

        Returns:
            Cab dictionary if found, None otherwise.
        """
        return queries.get_cab(self.db, cab_id, company_id)
