"""
FastAPI dependencies for JWT-based authentication.

Every protected endpoint uses these to extract and validate identity
from the Authorization header.  Identity never comes from the request
body or from anything the LLM produces.
"""

from fastapi import Depends, Header, HTTPException

from app.auth.jwt_handler import TokenData, verify_token


async def get_current_user(
    authorization: str = Header(..., alias="Authorization"),
) -> TokenData:
    """Extract and validate the Bearer token from the Authorization header.

    Returns:
        TokenData with employee_id, company_id, and role.

    Raises:
        HTTPException 401: If the header is missing, malformed, or the token is invalid.
    """
    if not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail="Invalid authorization header. Expected 'Bearer <token>'.",
        )
    token = authorization[7:]
    if not token:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")
    try:
        return verify_token(token)
    except ValueError:
        raise HTTPException(status_code=401, detail="Invalid or expired token.")


async def require_manager(
    user: TokenData = Depends(get_current_user),
) -> TokenData:
    """Restrict access to manager-role tokens only.

    Raises:
        HTTPException 403: If the authenticated user is not a manager.
    """
    if user.role != "manager":
        raise HTTPException(status_code=403, detail="Manager role required.")
    return user
