"""Measure end-to-end /chat p95 with the configured live model."""

import math
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient

from app.auth.jwt_handler import create_token
from app.config import settings
from app.db.database import init_db
from app.main import app


def main() -> int:
    if not settings.GEMINI_API_KEY:
        print("Skipped: set GEMINI_API_KEY to measure live-model chat latency.")
        return 2

    db_path = Path("data/latency-benchmark.sqlite")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    settings.DB_PATH = str(db_path)
    init_db(str(db_path))
    token = create_token("acme-emp1", "acme")
    samples: list[float] = []
    with TestClient(app) as client:
        for index in range(20):
            started = time.perf_counter()
            response = client.post(
                "/chat",
                headers={"Authorization": f"Bearer {token}"},
                json={
                    "message": "Do I have any transport requests waiting?",
                    "conversation_id": f"latency-{index}",
                },
            )
            samples.append(time.perf_counter() - started)
            if response.status_code != 200:
                raise RuntimeError(f"Chat request failed with HTTP {response.status_code}.")
            if response.json()["llm_calls"] > 1:
                raise RuntimeError("The chat exceeded its one-call-per-message budget.")

    ordered = sorted(samples)
    p95 = ordered[math.ceil(0.95 * len(ordered)) - 1]
    print(f"Requests: {len(samples)}")
    print(f"Live-model end-to-end p95: {p95:.3f}s")
    print(f"Target (5.000s): {'PASS' if p95 <= 5 else 'FAIL'}")
    return 0 if p95 <= 5 else 1


if __name__ == "__main__":
    raise SystemExit(main())
