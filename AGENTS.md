# AGENTS.md

## Layout

- `apps/web` — Next.js 15 / React 19 / TypeScript frontend.
- `services/api` — FastAPI backend (`app.main:app`), SQLAlchemy async, pytest suite.
- `scripts` — operator diagnostics and demo seeding. `docs` — design/API notes.
- No root package manager workspace. Run backend commands from `services/api`, frontend commands from `apps/web`.

## Commands

Backend (from `services/api`; Windows venv path shown, use `.venv/bin/python` on POSIX):

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe -m pytest tests/test_memory_auth_api.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_memory_auth_api.py::test_name -q
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
.\.venv\Scripts\python.exe -m app.cli provision-user
.\.venv\Scripts\python.exe -m app.cli revoke-user-tokens --user-id <id>
```

`app` imports require the working directory to be `services/api` (or `PYTHONPATH` set accordingly). Run from the repo root for live integration checks:

```powershell
services\api\.venv\Scripts\python.exe scripts\verify_integrations.py
```

Frontend (from `apps/web`):

```powershell
npm install
npm run dev
npm run lint
npm run test:bff
npm run build
```

There is no separate typecheck script; `npm run build` performs TypeScript checking. `npm run test:bff` uses Node's built-in test runner and works without `npm install`, but lint/build require installed dependencies.

## Current implementation state

- Only the `health`, `auth`, and `memory` routers are mounted in `services/api/app/main.py`.
- `docs/API_CONTRACT.md` and `docs/ARCHITECTURE.md` describe planned chat, briefing, anticipation, and Sui-wallet endpoints that are not implemented. Do not assume those routes or service modules exist; verify against `app/api/v1/` and `app/main.py`.
- Backend tests use fake adapters and in-memory SQLite, so they do not need real API keys or network access.

## Security and auth invariants

- Memory endpoints require `Authorization: Bearer orh_...`. Tokens are created only through `python -m app.cli provision-user`; there is no public signup, login, or token-issuance route.
- Ownership and the Walrus namespace are derived from the bearer token's database user. Request bodies use `extra="forbid"` and must not accept `user_id` or `namespace` from clients.
- The browser talks only to same-origin BFF routes (`/api/session`, `/api/bff/memory/*`). The bearer token is stored in an HttpOnly cookie server-side; never expose it to client components, `localStorage`, or `NEXT_PUBLIC_*`.
- State-changing BFF routes enforce same-origin requests.
- Production requires HTTPS `API_PUBLIC_URL`, explicit HTTPS `CORS_ORIGINS`, and an HTTPS `ORHEX_API_BASE_URL` for the frontend.

## Data and integrations

- SQLAlchemy async with SQLite by default. `init_db()` runs `create_all` plus hand-written in-place migrations in `app/core/database.py`; there is no Alembic. Add a migration helper and call it from `init_db()` when changing schema.
- Memory writes are committed locally as `pending` before the Walrus call, then become `confirmed`, `failed`, or `reconciliation_required`. Never automatically retry an uncertain write; use the reconcile/job-status path.
- `memwal==0.2.1` stores text and namespace only and exposes no blob-status or permanent-delete API. Permanent delete returns `501`; Trash is a local soft delete and does not touch Walrus.
- Tatum uses Sui GraphQL at `/graphql` with an `x-api-key` header, not legacy JSON-RPC. Gemini sends its key via `x-goog-api-key`, never in a URL.
- Walrus relayer URL is `https://relayer.memory.walrus.xyz`; do not use `https://memory.wal.app` (404).
- Backend config loads `.env` from the workspace root, `services/api`, and the current directory. Frontend server config lives in `apps/web/.env.local` as `ORHEX_API_BASE_URL` (legacy `ORHREX_API_BASE_URL` is still accepted).

## Testing quirks

- Run backend pytest from `services/api`; its `pyproject.toml` supplies `testpaths` and `pythonpath`, while the root `pytest.ini` also enables asyncio auto mode.
- Persistence tests depend on SQLite foreign-key listeners and triggers; do not casually swap the test engine.
- `scripts/seed_10_memories.py` writes directly through the adapter to a hard-coded `user_default` namespace, bypassing authenticated per-user ownership. Do not treat it as the production memory flow.

## Repository hygiene

- `.env`, `.env.local`, and `*.db` are gitignored. Never commit secrets or local databases.
- The working tree contains both root and `services/api` copies of `orhrex.db`; leave them untracked.
