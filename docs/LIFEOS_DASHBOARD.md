# LifeOS Dashboard & Daily Briefing

## Scope in this increment

The workspace home page now includes a daily briefing panel backed by the authenticated anticipation API. On load, it asks the backend to refresh deterministic deadline signals and displays up to four highest-priority active signals. Users can manually refresh the briefing and dismiss individual signals.

The Insights page lists active anticipation signals with their priority, source type, due date, explanation, and dismissal action. The Timeline page currently presents active anticipation signals ordered by creation time. It is intentionally not described as a complete activity history: conversation, task, and wallet event streams are not yet unified.

## Data and trust boundaries

- All data comes from the authenticated `/api/v1/anticipation` backend routes.
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
- Add a unified event model for memory, conversation, task, and wallet activity before claiming a complete personal timeline.
- Add user-configurable briefing cadence and notification preferences.
- Add background evaluation only after deploying a durable scheduler/worker and idempotent job execution.
- Expand insight types only when supported by persisted evidence and tests.
