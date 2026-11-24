# MoveInSync Shift-Change Agent

A small FastAPI + LangGraph service that handles employee roster-change requests for two sample companies. SQLite stores the roster, company policies, requests, and conversation state. Redis provides a short-lived seat lock; SQLite transactions remain the final authority for seat allocation.

## Run it

From this directory, run:

```powershell
docker compose up --build
```

The demo chat UI is at `http://localhost:8000`; the API is documented at `http://localhost:8000/docs`. Docker Compose starts the app and Redis, and the app seeds Acme and Globex sample data on first startup. Persisted SQLite and Redis data are stored in Compose volumes.

An API key is optional for local use. Without `GEMINI_API_KEY`, the app uses a deliberately small deterministic intent parser so that the demo remains runnable. To enable Gemini intent extraction, set `GEMINI_API_KEY` in the environment before starting Compose. Never put a real key in source control.

### Try a request

The development token issuer only issues tokens for seeded employees and derives the role from the database:

```powershell
curl.exe -X POST http://localhost:8000/auth/token `
  -H "Content-Type: application/json" `
  -d "{\"company_id\":\"acme\",\"employee_id\":\"acme-emp1\"}"
```

Use the returned `access_token` as a bearer token in `/chat`:

```json
{
  "message": "Change my drop to 19:00 tomorrow",
  "conversation_id": "alice-evening"
}
```

The same `conversation_id` carries clarification context across messages. Managers use their own token to call `POST /manager/approve/{request_id}` with `{"approved": true}` or `false`. Employees can check `GET /requests/{request_id}` or `GET /requests`; both are scoped to the token identity.

The root demo page can connect as a seeded employee or manager without displaying the bearer token. The dev UI and `/auth/token` endpoint are for local evaluation only.

Sample employee IDs are `acme-emp1` through `acme-emp5` and `globex-emp1` through `globex-emp4`. Managers are `acme-mgr1` and `globex-mgr1`.

> `/auth/token` is a demo-only issuer. Set `DEV_AUTH_ENABLED=false` outside the local evaluation environment and supply tokens from a trusted issuer. Replace the development `JWT_SECRET` before any non-local deployment.

## Design

- **Identity and isolation:** company and employee IDs come exclusively from a verified HS256 JWT. Request models reject extra identity fields. Every roster, cab, request, and conversation lookup is scoped by both IDs; tools close over identity from the token rather than accepting identity as model arguments. Manager authorization additionally checks the employee's assigned manager in SQLite.
- **LLM boundary:** Gemini (when configured) is used for one task only: extracting `{action, direction, time, date, confirm}`. The output is parsed and allow-listed. The model cannot select a company, employee, roster ID, cab ID, policy verdict, or manager decision. If output is malformed, the app asks the user to retry; it never applies a model-generated confirmation.
- **LangGraph and tools:** a two-node graph interprets one message and runs scoped roster/cab/change tools. Clarification and option-choice state is serialized in SQLite between graph invocations, so the graph does not need to remain alive between messages.
- **Policy:** `RulesEngine` applies per-company cut-offs, trip-time validity, company/cab matching, and maximum seats. A cut-off violation creates no request unless the employee explicitly confirms escalation; confirmation creates a pending request and does not change the roster. A manager can approve only their assigned employee's pending request.
- **Durable pause/resume:** pending requests, manager decisions, roster updates, and chat context live in SQLite. An approval action applies or rejects the stored request and appends the result to that employee's original conversation in one transaction. Recreating the FastAPI process does not lose the pending request or its conversation; employees see the result through chat history or request status.
- **Seat correctness:** Redis locks reduce contention across app instances. `BEGIN IMMEDIATE`, an atomic `seats_available > 0` update, idempotency lookup, and a database uniqueness constraint provide the correctness guarantee even if Redis is unavailable.
- **Errors:** model timeout/invalid output, missing roster, full cabs, DB contention, Redis lock contention, and stale approval each return a user-facing next step. Internal exceptions are logged server-side and never returned as stack traces.

### LLM call and latency budget

There is **at most one LLM call per employee message** (intent extraction); no response-generation call is made. There are no model retries. Gemini calls have a 2.5-second timeout and `max_retries=0`, leaving the remainder of the 5-second target for local tools and response formatting. No-key runs make zero model calls. Automated tests use no provider and cannot spend quota. Run `python scripts/run_evaluation.py` to regenerate the deterministic evaluation set; it records call counts in the tool-selection case. To measure live end-to-end p95 with your key, run `python scripts/benchmark_latency.py`.

## Tests and evaluation

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python scripts/run_evaluation.py
```

`evaluation_set.jsonl` is regenerated with one JSON object per scenario and the required `id`, `input`, `expected`, `actual`, `pass`, and `notes` fields. `evaluation_report.md` records the test run, evaluation outcomes, latency observations, and limitations.

## Trade-offs and limitations

- SQLite was chosen for a one-command sample deployment and transactional seat correctness. Redis is used for short seat locks, but no Redis-backed graph checkpoint is needed: the graph is stateless between calls, while approval and conversation data are durable SQLite records. This is simpler than serializing in-flight graph objects and makes a restart easy to reason about. SQLite is not a substitute for a production multi-region database.
- The development parser intentionally supports common time phrasing rather than unrestricted language. If the model is absent or returns untrusted output, the assistant asks for clarification instead of guessing.
- The sample roster is generated for the next local calendar day and covers a small set of employees/routes. This is a behavior prototype, not a production dispatch or identity system.
- Approval is an explicit policy exception path: an employee confirms escalation, then the assigned manager may approve it. The cut-off request itself never mutates the roster; a full cab at approval cancels the request and tells the manager to ask the employee to choose again.
- The benchmark/evaluation suite uses fake or deterministic intent results. The actual Gemini network p95 depends on provider/network conditions; the configured hard timeout bounds the model portion, but this repository cannot certify a real-provider p95 without a valid API key and a run on the evaluator's machine.
