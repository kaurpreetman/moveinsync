"""FastAPI application entry point."""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

import redis
from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field
from fastapi.responses import FileResponse

from app.agent.graph import ShiftChangeAgent
from app.api import chat, manager, requests
from app.api.dependencies import get_connection
from app.auth.dependencies import get_current_user
from app.auth.jwt_handler import TokenData, create_token
from app.config import settings
from app.db.database import get_db, init_db
from app.db.queries import get_employee

logger = logging.getLogger(__name__)


class TokenRequest(BaseModel):
    employee_id: str = Field(min_length=1, max_length=100)
    company_id: str = Field(min_length=1, max_length=100)

    model_config = ConfigDict(extra="forbid")


def create_app() -> FastAPI:
    @asynccontextmanager
    async def lifespan(application: FastAPI):
        init_db()
        application.state.agent = ShiftChangeAgent()
        client = redis.Redis.from_url(
            settings.REDIS_URL,
            decode_responses=True,
            socket_connect_timeout=0.5,
            socket_timeout=0.5,
        )
        try:
            client.ping()
            application.state.redis = client
            logger.info("Connected to Redis for distributed seat locks.")
        except redis.RedisError as exc:
            application.state.redis = None
            logger.warning(
                "Redis is unavailable; SQLite transactions still protect seat allocation: %s",
                exc,
            )
        try:
            yield
        finally:
            if getattr(application.state, "redis", None) is not None:
                application.state.redis.close()

    application = FastAPI(
        title="MoveInSync Shift-Change Agent",
        version="1.0.0",
        description="JWT-authenticated, tenant-scoped employee roster change assistant.",
        lifespan=lifespan,
    )
    application.include_router(chat.router)
    application.include_router(manager.router)
    application.include_router(requests.router)

    @application.get("/", include_in_schema=False)
    def demo_ui() -> FileResponse:
        return FileResponse(Path(__file__).parent / "static" / "index.html")

    @application.get("/health")
    def health(request: Request) -> dict:
        db = get_db()
        try:
            db.execute("SELECT 1").fetchone()
        finally:
            db.close()
        return {
            "status": "ok",
            "redis": "connected" if getattr(request.app.state, "redis", None) else "unavailable",
        }

    @application.post("/auth/token")
    def issue_dev_token(payload: TokenRequest, db=Depends(get_connection)) -> dict:
        if not settings.DEV_AUTH_ENABLED:
            raise HTTPException(status_code=404, detail="Not found.")
        employee = get_employee(db, payload.employee_id, payload.company_id)
        if employee is None:
            raise HTTPException(status_code=404, detail="Employee not found.")
        role = "manager" if employee["is_manager"] else "employee"
        token = create_token(
            employee_id=employee["id"],
            company_id=employee["company_id"],
            role=role,
            name=employee["name"],
        )
        return {"access_token": token, "token_type": "bearer", "role": role}

    @application.get("/me")
    def current_identity(
        user: TokenData = Depends(get_current_user),
        db=Depends(get_connection),
    ) -> dict:
        employee = get_employee(db, user.employee_id, user.company_id)
        if employee is None:
            raise HTTPException(status_code=403, detail="Token identity is not a known employee.")
        return {
            "employee_id": employee["id"],
            "company_id": employee["company_id"],
            "name": employee["name"],
            "role": "manager" if employee["is_manager"] else "employee",
        }

    return application


app = create_app()
