"""
SQLite database setup: schema creation, connection management.

We use SQLite for its simplicity (single docker compose up, no Postgres
container) and because the workload is read-heavy with low concurrency.
WAL mode gives us concurrent readers without blocking.
"""

import os
import sqlite3

from app.config import settings

# ── Schema ──────────────────────────────────────────────────────────────────

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies (
    id   TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    cutoff_minutes_pickup INTEGER NOT NULL DEFAULT 120,
    cutoff_minutes_drop   INTEGER NOT NULL DEFAULT 90,
    max_seats_per_cab     INTEGER NOT NULL DEFAULT 4,
    allow_cross_route     INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS employees (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id),
    name       TEXT NOT NULL,
    email      TEXT NOT NULL,
    is_manager INTEGER NOT NULL DEFAULT 0,
    manager_id TEXT REFERENCES employees(id)
);

CREATE TABLE IF NOT EXISTS routes (
    id         TEXT PRIMARY KEY,
    company_id TEXT NOT NULL REFERENCES companies(id),
    name       TEXT NOT NULL,
    area       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS cabs (
    id              TEXT PRIMARY KEY,
    company_id      TEXT NOT NULL REFERENCES companies(id),
    route_id        TEXT NOT NULL REFERENCES routes(id),
    scheduled_date  TEXT NOT NULL,
    scheduled_time  TEXT NOT NULL,
    direction       TEXT NOT NULL CHECK(direction IN ('pickup', 'drop')),
    total_seats     INTEGER NOT NULL,
    seats_available INTEGER NOT NULL,
    driver_name     TEXT NOT NULL,
    vehicle_number  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS roster (
    id             TEXT PRIMARY KEY,
    employee_id    TEXT NOT NULL REFERENCES employees(id),
    company_id     TEXT NOT NULL REFERENCES companies(id),
    cab_id         TEXT NOT NULL REFERENCES cabs(id),
    scheduled_date TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    trip_type      TEXT NOT NULL CHECK(trip_type IN ('pickup', 'drop')),
    status         TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active', 'changed', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS shift_change_requests (
    id                TEXT PRIMARY KEY,
    employee_id       TEXT NOT NULL REFERENCES employees(id),
    company_id        TEXT NOT NULL REFERENCES companies(id),
    original_roster_id TEXT NOT NULL REFERENCES roster(id),
    original_time     TEXT NOT NULL,
    requested_time    TEXT NOT NULL,
    original_cab_id   TEXT NOT NULL,
    new_cab_id        TEXT,
    trip_type         TEXT NOT NULL,
    scheduled_date    TEXT NOT NULL,
    status            TEXT NOT NULL DEFAULT 'pending'
                          CHECK(status IN (
                              'pending','pending_approval','approved',
                              'rejected','applied','cancelled'
                          )),
    reason            TEXT,
    manager_decision  TEXT,
    conversation_id   TEXT,
    created_at        TEXT NOT NULL DEFAULT (datetime('now')),
    resolved_at       TEXT,
    UNIQUE(employee_id, scheduled_date, requested_time, trip_type)
);

CREATE TABLE IF NOT EXISTS conversations (
    company_id      TEXT NOT NULL,
    employee_id     TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    state_json      TEXT NOT NULL DEFAULT '{}',
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (company_id, employee_id, conversation_id)
);

CREATE TABLE IF NOT EXISTS conversation_messages (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    company_id      TEXT NOT NULL,
    employee_id     TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    role            TEXT NOT NULL CHECK(role IN ('employee', 'assistant')),
    message         TEXT NOT NULL,
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_requests_employee
    ON shift_change_requests(company_id, employee_id, created_at);
CREATE INDEX IF NOT EXISTS idx_messages_conversation
    ON conversation_messages(company_id, employee_id, conversation_id, id);
"""


# ── Connection helper ───────────────────────────────────────────────────────

def get_db(db_path: str | None = None) -> sqlite3.Connection:
    """Return a SQLite connection with sensible defaults.

    - row_factory = sqlite3.Row  → dict-like access
    - WAL journal mode           → concurrent reads
    - foreign_keys ON            → referential integrity
    """
    path = db_path or settings.DB_PATH
    os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
    conn = sqlite3.connect(
        path,
        timeout=settings.DB_BUSY_TIMEOUT_SECONDS,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ── Bootstrap ───────────────────────────────────────────────────────────────

def init_db(db_path: str | None = None) -> None:
    """Create tables (idempotent) and seed sample data if the DB is empty."""
    db = get_db(db_path)
    db.executescript(SCHEMA)

    # Only seed if companies table is empty (first run)
    row = db.execute("SELECT COUNT(*) AS cnt FROM companies").fetchone()
    if row["cnt"] == 0:
        from app.db.seed import seed_data
        seed_data(db)
        print("Database seeded with sample data.")
    else:
        print("Database already contains data — skipping seed.")

    db.close()
