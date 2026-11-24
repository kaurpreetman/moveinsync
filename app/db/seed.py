"""
Seed data for two companies with different transport policies.

Acme Corp  — strict:  120 min pickup cut-off, 90 min drop cut-off, 4 seats
Globex Inc — relaxed: 60 min pickup cut-off,  60 min drop cut-off, 6 seats

All cabs and roster entries are created for TOMORROW so the agent always
has valid future data to work with regardless of when the demo is run.
"""

import sqlite3
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

INDIA_TZ = ZoneInfo("Asia/Kolkata")


def _tomorrow() -> str:
    """Return tomorrow's date as YYYY-MM-DD."""
    return (datetime.now(INDIA_TZ).date() + timedelta(days=1)).isoformat()


def seed_data(db: sqlite3.Connection) -> None:
    """Insert sample data for two companies.  Idempotent guard is in init_db."""
    tomorrow = _tomorrow()

    # ── Companies ───────────────────────────────────────────────────────
    db.executemany(
        "INSERT INTO companies VALUES (?,?,?,?,?,?)",
        [
            ("acme",   "Acme Corp",  120, 90, 4, 0),
            ("globex", "Globex Inc",  60, 60, 6, 0),
        ],
    )

    # ── Routes ──────────────────────────────────────────────────────────
    db.executemany(
        "INSERT INTO routes VALUES (?,?,?,?)",
        [
            ("acme-r1",   "acme",   "Whitefield Route",      "Whitefield"),
            ("acme-r2",   "acme",   "Koramangala Route",     "Koramangala"),
            ("globex-r1", "globex", "Electronic City Route",  "Electronic City"),
            ("globex-r2", "globex", "Indiranagar Route",      "Indiranagar"),
        ],
    )

    # ── Employees ───────────────────────────────────────────────────────
    # Acme — 5 employees + 1 manager
    db.executemany(
        "INSERT INTO employees VALUES (?,?,?,?,?,?)",
        [
            ("acme-mgr1", "acme", "Frank Manager", "frank@acme.com",   1, None),
            ("acme-emp1", "acme", "Alice Johnson", "alice@acme.com",   0, "acme-mgr1"),
            ("acme-emp2", "acme", "Bob Smith",     "bob@acme.com",     0, "acme-mgr1"),
            ("acme-emp3", "acme", "Charlie Brown", "charlie@acme.com", 0, "acme-mgr1"),
            ("acme-emp4", "acme", "Diana Ross",    "diana@acme.com",   0, "acme-mgr1"),
            ("acme-emp5", "acme", "Eve Wilson",    "eve@acme.com",     0, "acme-mgr1"),
        ],
    )
    # Globex — 4 employees + 1 manager
    db.executemany(
        "INSERT INTO employees VALUES (?,?,?,?,?,?)",
        [
            ("globex-mgr1", "globex", "Karen Manager", "karen@globex.com", 1, None),
            ("globex-emp1", "globex", "Grace Lee",     "grace@globex.com", 0, "globex-mgr1"),
            ("globex-emp2", "globex", "Henry Wang",    "henry@globex.com", 0, "globex-mgr1"),
            ("globex-emp3", "globex", "Ivy Chen",      "ivy@globex.com",   0, "globex-mgr1"),
            ("globex-emp4", "globex", "Jack Davis",    "jack@globex.com",  0, "globex-mgr1"),
        ],
    )

    # ── Cabs ────────────────────────────────────────────────────────────
    # (id, company_id, route_id, date, time, direction, total, available, driver, vehicle)
    cabs = [
        # Acme – Whitefield pickups
        ("acme-cab1", "acme", "acme-r1", tomorrow, "08:00", "pickup", 4, 2, "Ravi Kumar",   "KA01AB1234"),
        ("acme-cab2", "acme", "acme-r1", tomorrow, "09:00", "pickup", 4, 1, "Suresh Reddy", "KA01CD5678"),
        ("acme-cab3", "acme", "acme-r1", tomorrow, "09:30", "pickup", 4, 3, "Mohan Das",    "KA01EF9012"),
        # Acme – Whitefield drops
        ("acme-cab4", "acme", "acme-r1", tomorrow, "17:00", "drop", 4, 2, "Ravi Kumar",   "KA01AB1234"),
        ("acme-cab5", "acme", "acme-r1", tomorrow, "18:00", "drop", 4, 1, "Suresh Reddy", "KA01CD5678"),
        ("acme-cab6", "acme", "acme-r1", tomorrow, "19:00", "drop", 4, 4, "Mohan Das",    "KA01EF9012"),
        # Acme – Koramangala
        ("acme-cab7", "acme", "acme-r2", tomorrow, "08:30", "pickup", 4, 2, "Anil Sharma", "KA01GH3456"),
        ("acme-cab8", "acme", "acme-r2", tomorrow, "17:30", "drop",   4, 3, "Anil Sharma", "KA01GH3456"),
        # Globex – Electronic City pickups
        ("globex-cab1", "globex", "globex-r1", tomorrow, "09:00", "pickup", 6, 3, "Prakash Rao",  "KA02AB1234"),
        ("globex-cab2", "globex", "globex-r1", tomorrow, "10:00", "pickup", 6, 5, "Venkat Iyer",  "KA02CD5678"),
        # Globex – Electronic City drops
        ("globex-cab3", "globex", "globex-r1", tomorrow, "18:00", "drop", 6, 2, "Prakash Rao",  "KA02AB1234"),
        ("globex-cab4", "globex", "globex-r1", tomorrow, "19:00", "drop", 6, 4, "Venkat Iyer",  "KA02CD5678"),
        # Globex – Indiranagar
        ("globex-cab5", "globex", "globex-r2", tomorrow, "09:30", "pickup", 6, 4, "Ramesh Gowda", "KA02EF9012"),
        ("globex-cab6", "globex", "globex-r2", tomorrow, "18:30", "drop",   6, 5, "Ramesh Gowda", "KA02EF9012"),
    ]
    db.executemany(
        "INSERT INTO cabs VALUES (?,?,?,?,?,?,?,?,?,?)", cabs
    )

    # ── Roster ──────────────────────────────────────────────────────────
    roster = [
        # Acme employees
        ("roster-acme-emp1-pickup", "acme-emp1", "acme", "acme-cab2", tomorrow, "09:00", "pickup", "active"),
        ("roster-acme-emp1-drop",   "acme-emp1", "acme", "acme-cab5", tomorrow, "18:00", "drop",   "active"),
        ("roster-acme-emp2-pickup", "acme-emp2", "acme", "acme-cab1", tomorrow, "08:00", "pickup", "active"),
        ("roster-acme-emp2-drop",   "acme-emp2", "acme", "acme-cab4", tomorrow, "17:00", "drop",   "active"),
        ("roster-acme-emp3-pickup", "acme-emp3", "acme", "acme-cab3", tomorrow, "09:30", "pickup", "active"),
        ("roster-acme-emp3-drop",   "acme-emp3", "acme", "acme-cab6", tomorrow, "19:00", "drop",   "active"),
        ("roster-acme-emp4-pickup", "acme-emp4", "acme", "acme-cab7", tomorrow, "08:30", "pickup", "active"),
        ("roster-acme-emp4-drop",   "acme-emp4", "acme", "acme-cab8", tomorrow, "17:30", "drop",   "active"),
        ("roster-acme-emp5-pickup", "acme-emp5", "acme", "acme-cab2", tomorrow, "09:00", "pickup", "active"),
        ("roster-acme-emp5-drop",   "acme-emp5", "acme", "acme-cab5", tomorrow, "18:00", "drop",   "active"),
        # Globex employees
        ("roster-globex-emp1-pickup", "globex-emp1", "globex", "globex-cab1", tomorrow, "09:00", "pickup", "active"),
        ("roster-globex-emp1-drop",   "globex-emp1", "globex", "globex-cab3", tomorrow, "18:00", "drop",   "active"),
        ("roster-globex-emp2-pickup", "globex-emp2", "globex", "globex-cab2", tomorrow, "10:00", "pickup", "active"),
        ("roster-globex-emp2-drop",   "globex-emp2", "globex", "globex-cab4", tomorrow, "19:00", "drop",   "active"),
        ("roster-globex-emp3-pickup", "globex-emp3", "globex", "globex-cab5", tomorrow, "09:30", "pickup", "active"),
        ("roster-globex-emp3-drop",   "globex-emp3", "globex", "globex-cab6", tomorrow, "18:30", "drop",   "active"),
        ("roster-globex-emp4-pickup", "globex-emp4", "globex", "globex-cab1", tomorrow, "09:00", "pickup", "active"),
        ("roster-globex-emp4-drop",   "globex-emp4", "globex", "globex-cab3", tomorrow, "18:00", "drop",   "active"),
    ]
    db.executemany(
        "INSERT INTO roster VALUES (?,?,?,?,?,?,?,?)", roster
    )

    db.commit()
    print(f"Seeded data for date: {tomorrow}")
