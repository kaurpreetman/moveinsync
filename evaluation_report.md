# Evaluation report

## Method

The suite uses pytest, temporary SQLite databases, seeded Acme/Globex data, a deterministic fake LLM, and real FastAPI routes through `TestClient`. No live LLM calls or paid services are required. Concurrency tests use separate SQLite connections and synchronize two workers before attempting the same seat allocation.

Run:

```powershell
python -m pytest -q
python scripts/run_evaluation.py
```

The evaluation runner writes one record per scenario to `evaluation_set.jsonl` with the required six fields.

## Scenarios

- Company-scoped roster and request access; identity fields are rejected from chat input.
- Different Acme/Globex cut-offs; a cut-off violation is not submitted until the employee confirms escalation.
- Ambiguous messages and follow-up context.
- Fake-LLM tool selection and malformed model output.
- Tool failure and model timeout return recoverable messages without exposing internal details.
- Pending manager approval remains available after app restart; only the assigned manager can apply it, and the decision is appended to the original employee conversation.
- Concurrent last-seat requests and double-submit idempotency.

## Results (2026-11-24)

- `python -m pytest -q`: **22 passed**. One upstream LangGraph pending-deprecation warning remains; no test failures.
- `python scripts/run_evaluation.py`: **7/7 passed**. The resulting records are in `evaluation_set.jsonl`.
- Python `compileall` passed. Docker Compose YAML parsed, but `docker compose config`/container startup could not be run because Docker is not installed in this environment.
- A browser run against the local FastAPI server completed the clarification flow and returned `applied`.
- Live Gemini p95 was **not measured**: no API key was available. `python scripts/benchmark_latency.py` correctly skipped. The configured 2.5-second model-call timeout and one-call cap are design bounds, not a substitute for a real-provider measurement.
- Redis was unavailable in the local environment. The API started and allocation tests passed through SQLite transaction fallback.

## Known limitations

- No Gemini API key is committed or required. A real-provider p95 measurement must be performed with the evaluator's own free-tier key and network.
- Docker startup is not verified on this machine; only the Compose YAML syntax was checked because the Docker CLI is absent.
- The service seeds one day of synthetic roster data and exposes a development-only token issuer. It does not integrate with any real transport or corporate identity systems.
- SQLite is suitable for the single-host case study, not a high-availability deployment. Redis improves seat-lock contention but SQLite remains the transaction authority.
- A user must explicitly confirm a cut-off exception before it enters the manager queue. Requests don't auto-expire; managers and employees can inspect the persistent request status.
