"""Manager approval endpoint."""

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from app.api.dependencies import get_connection, get_redis_client
from app.auth.dependencies import require_manager
from app.auth.jwt_handler import TokenData
from app.services.shift_change import ShiftChangeService

router = APIRouter(prefix="/manager", tags=["manager"])


class ManagerDecision(BaseModel):
    approved: bool
    notes: str | None = Field(default=None, max_length=500)

    model_config = ConfigDict(extra="forbid")


@router.post("/approve/{request_id}")
def approve_request(
    request_id: str,
    decision: ManagerDecision,
    manager: TokenData = Depends(require_manager),
    db=Depends(get_connection),
    redis_client=Depends(get_redis_client),
) -> dict:
    return ShiftChangeService(db, redis_client).approve_request(
        request_id=request_id,
        company_id=manager.company_id,
        manager_id=manager.employee_id,
        approved=decision.approved,
        manager_notes=decision.notes,
    )
