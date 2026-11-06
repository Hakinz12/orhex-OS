# ORHEX Autonomous Orchestration Engine v1

## Purpose
The orchestration engine is the coordination layer between the current deterministic anticipation engine and approval-gated task/action system. It turns current owner-scoped state into a ranked, explainable next-step plan and stores an audit record for each decision cycle.

## Decision cycle
1. Authenticate the owner (visitor credential or personal access token).
2. Refresh deterministic anticipation signals from active goals and incomplete commitments.
3. Prepare deduplicated task proposals from active insights. Proposals remain pending.
4. Read only that owner's active goals, incomplete commitments, open tasks and proposals.
5. Rank next steps: approval requests and overdue tasks first, then tasks due within 24 hours and active anticipation signals.
6. Persist an idempotent `orchestration_runs` audit record with counts, rationale, recommendations and safety outcomes.

## API
- `POST /api/v1/orchestration/run` — body: `{ "idempotency_key": "UUID", "trigger": "manual|dashboard|conversation|scheduled" }`.
- `GET /api/v1/orchestration/latest` — latest owner-scoped run.
- `GET /api/v1/orchestration/runs?limit=20` — owner-scoped run history (limit 1–100).

## Safety boundaries
- The engine does not call an LLM to invent facts or make hidden decisions.
- It does not send email/messages, create calendar events, submit blockchain transactions, sign transactions, or move funds.
- It may prepare task proposals, but existing approval endpoints remain the only route to approve their supported task-creation effect.
- A task recommendation is guidance, not an external action.
- Sui wallet context is not needed for this v1 decision cycle and is not used to initiate transactions.
- Memory retrieval or Walrus writes are not triggered implicitly. Existing explicit-consent memory rules remain unchanged.

## Idempotency and ownership
Each run is unique per `(user_id, idempotency_key)`. All source queries are filtered by authenticated `user_id`; the same key used by two owners creates two independent runs. Run summaries contain no raw memory contents, credentials, or wallet secrets.

## Current limitations / next phase
This is a request-triggered decision cycle, not a background agent. The `scheduled` trigger is metadata only; no scheduler or notification delivery is installed. v1 does not execute arbitrary tools or external connectors. Future adapters must define scopes, permission checks, explicit approval requirements, idempotency, retries, and auditable outcomes before they can be orchestrated.
