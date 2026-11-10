# ORHEX Anticipation Engine (v1)

## Scope

The engine is deterministic and evidence-backed. It evaluates only the authenticated user's existing structured `Goal`, `Commitment`, and `Task` records. It does not use an LLM to invent deadlines, does not read another user's records, does not write to Walrus, and does not execute actions.

## Rules

- An incomplete commitment with a due date in the past produces a high-priority `commitment_overdue` insight.
- An incomplete commitment due within the next seven days produces a medium-priority `commitment_due_soon` insight.
- An active goal whose target date is in the past produces a high-priority `goal_overdue` insight.
- An active goal whose target date is within the next seven days produces a medium-priority `goal_due_soon` insight.
- An open task (`todo` or `in_progress`) due in the past produces a high-priority `task_overdue` insight.
- An open task due within the next seven days produces a medium-priority `task_due_soon` insight.
- Records without a due date are not guessed at.

Each insight includes a deterministic fingerprint, source record reference, rule evidence, explanation, priority, and due date. Repeated refreshes update the same active insight instead of creating duplicates. Insights no longer supported by current source data become `resolved`. Dismissed insights are not silently reactivated.

## API

All endpoints require an ORHEX bearer credential:

- `GET /api/v1/anticipation` — list active insights.
- `GET /api/v1/anticipation?include_inactive=true` — include dismissed and resolved insights.
- `POST /api/v1/anticipation/refresh` — evaluate current goals, commitments, and open tasks and return active insights.
- `PATCH /api/v1/anticipation/{insight_id}` with `{"status":"dismissed"}` or `{"status":"active"}` — dismiss or reactivate an insight. Ownership is enforced in the query.

## Operational notes and limitations

- This version is request-triggered; it does not run a scheduler or send push/email notifications.
- The current source data must be entered into the existing `goals` and `commitments` tables. Natural-language extraction and goal/commitment CRUD UX are separate follow-on work.
- No blockchain activity is treated as a personal commitment or goal. Verified wallet context remains a read-only source until explicit, tested rules are added.
- The engine is intentionally deterministic so every recommendation can be traced to a source record and rule.
