# LifeOS Dashboard & Daily Briefing

## Scope in this increment

The workspace home page now includes a daily briefing panel backed by the authenticated anticipation API. On load, it asks the backend to refresh deterministic deadline signals and displays up to four highest-priority active signals. Users can manually refresh the briefing and dismiss individual signals.

The Insights page lists deterministic, owner-scoped deadline signals for active goals, incomplete commitments, and open tasks. Each signal includes its source, due date, explanation, priority, and dismissal action.

The Timeline page combines actual current records from the authenticated task, memory, anticipation, action-proposal, and latest-orchestration APIs. It displays task creation/completion and due dates, confirmed memory saves, active deadline signals, and recorded ORHEX proposals/decision cycles. It loads only the latest 50 confirmed active memories. This is a recent activity view, not a complete audit log: prior task edits, conversation turns, calendar events, wallet activity, and dismissed proposals are not available as event history.

## Data and trust boundaries

- Data comes from authenticated owner-scoped task, memory, anticipation, proposal, and orchestration routes.
- The Next.js BFF reads the HttpOnly session cookie and forwards the bearer credential server-side.
- State-changing BFF requests enforce same-origin checks.
- The backend remains responsible for owner scoping and deterministic rule evaluation.
- The dashboard does not fabricate dates, events, goals, or recommendations.
- Daily briefing refresh is request-driven when the dashboard loads or the user presses Refresh. It is not a scheduled background job or push notification system.

## BFF routes

| Method | BFF route | Backend route |
|---|---|---|
| GET | `/api/bff/anticipation` | `GET /api/v1/anticipation` |
| POST | `/api/bff/anticipation/refresh` | `POST /api/v1/anticipation/refresh` |
| PATCH | `/api/bff/anticipation/:insightId` | `PATCH /api/v1/anticipation/:insightId` |

## Remaining work

- Add a dedicated daily briefing response with summary counts and a stable time window.
- Add durable task-change and proposal-dismissal history if those past transitions need to appear in Timeline.
- Add calendar, conversation, or wallet activity only after those sources expose reliable owner-scoped event history.
- Add user-configurable briefing cadence and notification preferences.
- Add background evaluation only after deploying a durable scheduler/worker and idempotent job execution.
- Expand insight types only when supported by persisted evidence and tests.
