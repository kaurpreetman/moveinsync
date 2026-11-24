from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.config import settings
from app.db.database import get_db, init_db

INDIA_TZ = ZoneInfo("Asia/Kolkata")


@pytest.fixture
def database(tmp_path, monkeypatch):
    db_path = str(tmp_path / "moveinsync-test.db")
    monkeypatch.setattr(settings, "DB_PATH", db_path)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    init_db(db_path)
    db = get_db(db_path)
    try:
        yield db
    finally:
        db.close()


def future_trip_time(minutes_from_now: int) -> tuple[str, str]:
    value = (datetime.now(INDIA_TZ) + timedelta(minutes=minutes_from_now)).replace(
        second=0, microsecond=0
    )
    return value.date().isoformat(), value.strftime("%H:%M")
