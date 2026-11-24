"""Request-scoped database and agent dependencies."""

import logging
from collections.abc import Generator

import redis
from fastapi import Request

from app.db.database import get_db

logger = logging.getLogger(__name__)


def get_connection() -> Generator:
    db = get_db()
    try:
        yield db
    finally:
        db.close()


def get_agent(request: Request):
    return request.app.state.agent


def get_redis_client(request: Request) -> redis.Redis | None:
    return getattr(request.app.state, "redis", None)
