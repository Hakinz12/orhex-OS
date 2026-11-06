# ORHEX Task & Action Engine

## Scope
The engine adds owner-scoped tasks and an approval-gated action proposal workflow. Tasks can be captured, listed, updated and completed. The first proposal adapter turns active anticipation insights into proposed task-creation actions.

## Safety model
- Every task and proposal is scoped to the authenticated user on every query.
- Preparing suggestions is non-destructive and deduplicated by source insight.
- A proposal remains `proposed` until the user explicitly approves or dismisses it.
- Approval currently supports only `create_task`; the task is created in the same database transaction as the proposal status change.
- No email, calendar, payment, wallet signing, chain transaction, memory write, or arbitrary external side effect is executed.
- Repeated approval is rejected with HTTP 409.

## API
- `GET/POST /api/v1/tasks`
- `PATCH /api/v1/tasks/{task_id}`
- `GET /api/v1/tasks/actions`
- `POST /api/v1/tasks/actions/prepare`
- `POST /api/v1/tasks/actions/{action_id}/approve` with `{ "status": "approved" | "dismissed" }`

The Next.js `/tasks` page uses HttpOnly session cookies through same-origin BFF routes.

## Current limitations
No scheduler, background agent, notifications, recurring tasks, subtasks, dependencies, or external action connectors are included. The initial suggestion generator only transforms active anticipation insights into task proposals.

## Anticipation-triggered preparation
Refreshing anticipation also prepares any new action proposals for the authenticated owner. This is request-driven and deterministic: it runs when the user refreshes the daily briefing/anticipation endpoint, not on a background schedule. Approval remains manual.
