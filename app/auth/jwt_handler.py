"""
JWT token creation and verification.

Tokens carry employee_id, company_id, and role.
Identity is NEVER taken from user input or the LLM — only from the JWT.
"""

from datetime import datetime, timedelta, timezone
from jose import jwt, JWTError
from pydantic import BaseModel, ConfigDict
from app.config import settings


class TokenData(BaseModel):
    """Decoded JWT payload. Injected into every request handler."""
    employee_id: str
    company_id: str
    role: str = "employee"      # "employee" or "manager"
    name: str = ""

    model_config = ConfigDict(extra="forbid")


def create_token(
    employee_id: str,
    company_id: str,
    role: str = "employee",
    name: str = "",
) -> str:
    """Create a signed JWT for an employee or manager.

    Args:
        employee_id: Unique employee identifier within the company.
        company_id: Company tenant identifier.
        role: 'employee' or 'manager'.
        name: Display name (convenience, not used for auth decisions).

    Returns:
        Encoded JWT string.
    """
    now = datetime.now(timezone.utc)
    payload = {
        "sub": employee_id,
        "company_id": company_id,
        "role": role,
        "name": name,
        "exp": now + timedelta(minutes=settings.JWT_EXPIRY_MINUTES),
        "iat": now,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def verify_token(token: str) -> TokenData:
    """Decode and validate a JWT.

    Args:
        token: Raw JWT string (without 'Bearer ' prefix).

    Returns:
        TokenData with employee identity.

    Raises:
        ValueError: If the token is invalid, expired, or malformed.
    """
    try:
        payload = jwt.decode(
            token,
            settings.JWT_SECRET,
            algorithms=[settings.JWT_ALGORITHM],
        )
        token_data = TokenData(
            employee_id=payload["sub"],
            company_id=payload["company_id"],
            role=payload.get("role", "employee"),
            name=payload.get("name", ""),
        )
        if token_data.role not in {"employee", "manager"}:
            raise ValueError("Invalid token role.")
        if not token_data.employee_id or not token_data.company_id:
            raise ValueError("Token identity fields cannot be empty.")
        return token_data
    except JWTError as exc:
        raise ValueError(f"Invalid token: {exc}") from exc
    except KeyError as exc:
        raise ValueError(f"Token missing required field: {exc}") from exc
