# MoveInSync Shift-Change Request Agent

## Case-study submission

This repository contains my implementation of the **AI Developer, campus case study A**: a chat agent for handling employee transport shift-change requests. It demonstrates how an assistant can interpret a request, check a roster and available cabs, apply company-specific rules, and route policy exceptions to a manager.

This is a self-contained prototype built for the case study. It uses generated sample data and mock services; it does not connect to MoveInSync production systems or use real employee information.

## Run the application

The application starts with one command from this directory:

```powershell
docker compose up --build
```

Open the chat UI at <http://localhost:8000> or the API documentation at <http://localhost:8000/docs>. On first startup, the app seeds sample roster and policy data for Acme and Globex. SQLite and Redis data persist in Docker volumes.

The app runs without an LLM API key using a small deterministic intent parser. To enable Gemini intent extraction, set `GEMINI_API_KEY` in your environment before starting Compose. Never commit a real key.

### Try the API

For local evaluation, the development token issuer creates a token for a seeded employee. The role is read from the sample database:

```powershell
curl.exe -X POST http://localhost:8000/auth/token `
  -H "Content-Type: application/json" `
  -d "{\"company_id\":\"acme\",\"employee_id\":\"acme-emp1\"}"
```

Use the returned `access_token` as a bearer token when calling `/chat`, with a request body such as:

```json
{
  "message": "Change my drop to 19:00 tomorrow",
  "conversation_id": "alice-evening"
}
```

Reuse `conversation_id` to continue a conversation after a clarification. Managers use their own token to call `POST /manager/approve/{request_id}` with `{"approved": true}` or `false`. Employees can check their requests through `GET /requests` or `GET /requests/{request_id}`.

The root demo page can connect as a seeded employee or manager. Sample employee IDs are `acme-emp1` through `acme-emp5` and `globex-emp1` through `globex-emp4`; manager IDs are `acme-mgr1` and `globex-mgr1`.

> **Local demo authentication only:** `/auth/token` and the UI's seeded identities are for evaluation. Before any non-local deployment, disable the development issuer with `DEV_AUTH_ENABLED=false`, use a trusted JWT issuer, and replace the development `JWT_SECRET`.

## What the agent handles

- Reads only the roster and requests belonging to the company and employee in the verified JWT.
- Asks a clarifying question instead of guessing when a message is ambiguous, and carries that context into the next message.
- Checks company cut-offs, trip validity, cab eligibility, and seat availability in application code.
- Offers another available time when the requested cab is unavailable.
- Requires explicit employee confirmation before escalating a cut-off exception to the employee's assigned transport manager.
- Persists pending approvals and conversations so the manager can decide after the service restarts.
- Returns a plain next step when a tool, model call, approval, or seat reservation fails.
- Includes concurrency protection for competing seat requests and duplicate submissions.

## Design and safety decisions

- **Identity is not model input.** Company and employee identity come only from the verified HS256 JWT. API request models reject extra identity fields; agent tools are scoped from the token. Manager authorization checks the employee's assigned manager in the database.
- **The model interprets; code decides.** When configured, Gemini extracts only an allow-listed intent (`action`, `direction`, `time`, `date`, and `confirm`). Model output cannot choose a tenant, employee, cab, policy outcome, or manager decision. Malformed output leads to a retry prompt, not a change.
- **Rules are deterministic.** `RulesEngine` checks policy and cab constraints before any mutation. A cutoff violation never directly changes the roster. A confirmed exception creates a pending manager request; only an authorized manager can resolve it.
- **Approval state survives restart.** SQLite stores requests, decisions, roster changes, and conversation context. The graph itself is stateless between messages, so there is no in-memory approval workflow to lose.
- **Seat allocation is atomic.** Redis locks reduce contention, while SQLite transactions, a conditional seat decrement, idempotency checks, and a uniqueness constraint enforce correctness. SQLite remains authoritative if Redis is unavailable.
- **Failures are surfaced safely.** User-facing messages explain what happened and what to do next; internal exception details are not returned to the chat.

## LLM call and latency budget

The agent makes **at most one LLM call per employee message** for intent extraction. It does not make a separate response-generation call and configures no provider retries. The Gemini request timeout is capped at 2.5 seconds; local runs without a key make zero model calls. Automated tests use deterministic/fake behavior and do not require a provider or consume quota.

Run `python scripts/benchmark_latency.py` with a valid `GEMINI_API_KEY` to measure end-to-end live-provider latency on your machine. Provider/network p95 was not measured as part of this submission, so the five-second p95 target is not claimed as verified.

## Tests and evaluation

Install dependencies and run the test suite and evaluation set:

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
python scripts/run_evaluation.py
```

The tests cover tenant isolation, company-specific cut-offs, clarification/tool behavior, failures, manager approval after restart, seat contention, and duplicate submissions. The evaluation runner writes one JSONL record per case to [`evaluation_set.jsonl`](evaluation_set.jsonl), with the required `id`, `input`, `expected`, `actual`, `pass`, and `notes` fields. See [`evaluation_report.md`](evaluation_report.md) for the recorded scenarios, results, trade-offs, and known limitations.

Last recorded local validation: **22 tests passed** and **7/7 evaluation cases passed**. Docker Compose startup and live Gemini latency should be verified in the submission/evaluation environment.

## Trade-offs and limitations

- SQLite keeps the sample easy to run and provides transactional seat correctness. It is not a production multi-region datastore; Redis is an optional coordination layer, not the source of truth.
- The no-key parser intentionally handles common request phrasing rather than unrestricted language. When it cannot safely interpret a request, it asks for clarification.
- Sample roster data is generated for a nearby local date and a small set of employees and routes. The token issuer, UI, and mock services are evaluation scaffolding, not production identity or dispatch systems.
- The model timeout bounds the configured Gemini call, but does not by itself prove the overall five-second p95 goal. Actual performance depends on the environment and provider.
