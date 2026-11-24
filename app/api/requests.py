"""Employee-scoped request status endpoints."""

from fastapi import APIRouter, Depends, HTTPException

from app.api.dependencies import get_connection
from app.auth.dependencies import get_current_user
from app.auth.jwt_handler import TokenData
from app.db import queries

router = APIRouter(prefix="/requests", tags=["requests"])


@router.get("")
def list_requests(
    user: TokenData = Depends(get_current_user),
    db=Depends(get_connection),
) -> dict:
    return {
        "requests": queries.list_employee_requests(
            db, user.employee_id, user.company_id
        )
    }


@router.get("/{request_id}")
def request_status(
    request_id: str,
    user: TokenData = Depends(get_current_user),
    db=Depends(get_connection),
) -> dict:
    request = queries.get_shift_change_request(
        db, request_id, user.company_id, user.employee_id
    )
    if request is None:
        raise HTTPException(status_code=404, detail="Request not found.")
    return request
