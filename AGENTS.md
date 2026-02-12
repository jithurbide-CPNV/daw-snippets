# AGENTS Guide for `simppetssrv`
This document equips agentic coders to work effectively on the Snippet Vault codebase. Read it once end-to-end, then reference sections as needed.

## 1. Mission and Scope
- Serve a web UI and JSON API for managing code snippets with public/private visibility.
- Support authenticated users, owner-controlled edits, and admin approval of access requests.
- Prioritise clarity, deterministic behaviour, and fast feedback (lint/tests under seconds).
- No audio feeds or RSS remain—this is purely a snippet management app backed by SQLite.
- Keep dependencies minimal (FastAPI, httpx, sqlite3 helpers); we intentionally dropped SQLAlchemy/Passlib/ItsDangerous.

## 2. Quick Start Commands
- Python 3.10+ required (CI uses Python 3.13.7).
- Install dev stack: `python3 -m pip install -e '.[dev]'`.
- Launch server: `python -m simppetssrv` (defaults to `0.0.0.0:8000`).
- Override DB location: `SIMPPETSSRV_DB_URL=sqlite+pysqlite:////tmp/snippets.db python -m simppetssrv`.
- Bootstrap admin: set `SIMPPETSSRV_ADMIN_EMAIL` and `SIMPPETSSRV_ADMIN_PASSWORD` before launch.
- Force HTTPS redirects: `SIMPPETSSRV_FORCE_HTTPS=true python -m simppetssrv`.
- Health check: `curl http://127.0.0.1:8000/healthz` → `{"status": "ok"}`.

## 3. Tests and Lint
- Run all tests: `pytest`.
- Async tests use `pytest-asyncio` strict mode; keep new async code compatible (`await` only inside `pytest.mark.asyncio`).
- Single test: `pytest tests/test_app.py::test_admin_can_approve_request`.
- Lint: `ruff check .`.
- Format: `ruff format .`.
- Pre-flight loop prior to PR/handoff: `ruff check . && ruff format . && pytest`.

## 4. Project Layout
- `src/simppetssrv/app.py`: FastAPI wiring, HTML rendering helpers, route definitions.
- `src/simppetssrv/members.py`: lightweight SQLite data access for users, email/password tokens, cookie signing.
- `src/simppetssrv/snippets.py`: SQLite helpers for snippets and access requests.
- `src/simppetssrv/__main__.py`: CLI entry, reads `SIMPPETSSRV_*` env vars, starts uvicorn.
- `tests/`: async tests for core flows plus CLI helper coverage; `conftest.py` injects `src/` into `PYTHONPATH`.
- `README.md` / `AGENTS.md`: keep these in sync with behaviour/tooling changes.

## 5. Database Schema (SQLite)
- Tables: `users`, `snippets`, `snippet_access_requests`, `email_tokens`, `password_reset_tokens`.
- Tables are created lazily via `CREATE TABLE IF NOT EXISTS`; no migrations yet. When expanding schema, update helper functions to keep compatibility and mention upgrade steps here.
- Datetimes stored as ISO 8601 strings (UTC). Handle conversions carefully—always attach tzinfo before comparisons.

## 6. Auth Model
- Users authenticate with email + password; hashing uses PBKDF2 via `hashlib.pbkdf2_hmac` (see `members.py`).
- Session cookie format: `user_id|timestamp|signature` with HMAC-SHA256. Keep this stable; refer to `_build_member_cookie` and `_parse_member_cookie` before editing.
- No email delivery by default. `_send_*` helpers log to stdout; integrate actual SMTP by replacing these functions and documenting required env vars.

## 7. Snippet Access Rules
- `public`: visible to everyone.
- `private`: visible only to owner and users with an `approved` access request.
- Users submit requests from the detail page. Owners cannot request their own snippet.
- Admins view `/admin/requests` and approve/deny; approval grants read access.
- Keep `SNIPPET_VISIBILITIES` / `REQUEST_STATUSES` constants centralised; update tests when adding new statuses.

## 8. HTML Rendering Guidelines
- Rendering functions in `app.py` return full HTML strings; they use a shared `_BASE_STYLES` block for consistent aesthetics.
- Stick to ASCII in templates; avoid inline JavaScript. Small progressive enhancements (e.g., data attributes) are acceptable but consult product direction if you plan major UI overhauls.
- When adding forms, keep them self-contained (server handles validation + re-render with errors).

## 9. Coding Style Essentials
- Import order: stdlib → third-party → first-party (`ruff` enforces `I` rule).
- Always enable `from __future__ import annotations` in new modules.
- Type annotate public functions; prefer builtin collection types (`dict[str, Any]`) over `typing.Dict`.
- Avoid inline comments unless clarifying non-obvious logic; rely on descriptive naming.
- Keep functions small; move repeated SQL to helper utilities in `members.py` / `snippets.py`.

## 10. SQLite Helper Conventions
- Helpers open fresh connections (`sqlite3.connect(..., check_same_thread=False)`); transactions rely on context managers.
- Always call `_ensure_tables` before executing queries that rely on schema.
- Use parameterised queries; never concatenate untrusted strings into SQL.
- Return dictionaries (converted from `sqlite3.Row`) for consumption in `app.py`.

## 11. Error Handling Expectations
- Raise `HTTPException` with clear, user-friendly messages. Avoid leaking tracebacks or raw SQL errors.
- When rejecting access (403), still render a helpful HTML page indicating how to proceed (login, request access, etc.).
- Use guard clauses to keep flows readable; avoid nested `if` pyramids.

## 12. Testing Strategy
- Integration tests spin up `create_app` with temporary SQLite file, interact via `httpx.AsyncClient`.
- When adding behaviours, mimic existing tests: log in, capture cookies, follow redirects intentionally.
- Tests run fast (sub-second). Keep new tests focused; prefer explicit assertions on HTML strings or status codes.

## 13. Admin Workflows
- Admin routes require logged-in user with `role == "admin"`.
- `_bootstrap_admin_user` refreshes password + verifies email if the admin exists—safe to call repeatedly.
- If you add admin features, put HTML renderers near the bottom of `app.py` and reuse shared styles.

## 14. CLI Notes
- `python -m simppetssrv` reads env vars, prints accessible URL, and starts uvicorn.
- Host/port defaults: `SIMPPETSSRV_HOST`/`SIMPPETSSRV_PORT`; CLI still uses uvicorn’s reload option via `SIMPPETSSRV_RELOAD=true`.
- Extend CLI with caution; keep `_display_host` tests green.

## 15. Dependencies
- Runtime: FastAPI, uvicorn, python-multipart (form uploads), sqlite3 (stdlib), hashlib/hmac for security.
- Dev: pytest, pytest-asyncio, httpx, ruff. Any new dependency must be justified; prefer stdlib.

## 16. Cursor / Copilot Rules
- No `.cursor/rules/` or `.cursorrules` files.
- No `.github/copilot-instructions.md`.
- If rules appear later, summarise them here immediately so agents stay aligned.

## 17. Pre-Commit Checklist
- `ruff check .` → clean.
- `ruff format .` → no diff.
- `pytest` → all passing.
- Review snippet/request logic for race conditions (double inserts, approvals).
- Update documentation if behaviour or configuration changes.

## 18. Common Issues
- **`ModuleNotFoundError: itsdangerous`**: no longer used. If you see this, remove stale imports.
- **Multiple access requests**: helper enforces unique `(snippet_id, requester_id)`; ensure UI messaging handles "pending" state.
- **Expired cookies**: `_parse_member_cookie` returns `None`; treat as anonymous user.
- **SQLite locked**: uncommon; ensure connections are short-lived and operations atomic.

## 19. Agent Workflow Tips
- Log the commands you run in task summaries (lint/tests) for hand-off clarity.
- Keep tasks focused—update tests alongside code to maintain fast feedback.
- When touching auth/session code, double-check cookie signing + password hashing logic.
- For UI tweaks, test both desktop and mobile widths (CSS includes responsive breakpoints).

## 20. Glossary
- **Snippet**: code entry with title, language, description, body, visibility.
- **Access request**: user-submitted request to view a private snippet, transitions `pending → approved/denied`.
- **Admin**: user with `role='admin'`, can approve requests and manage snippets like any user.
- **Session cookie**: `snippet_member`, signed via HMAC, contains user id and expiry.
- **Data directory**: `data/snippets.db` by default; override via `SNIPPETSRV_DB_URL`.

## 21. Extending the API
- Use JSON responses sparingly; HTML pages are the primary UX but nothing prevents adding JSON endpoints.
- When introducing new API routes, mirror behaviour in tests using `AsyncClient`.
- Validate request payloads via Pydantic models only if complexity warrants; otherwise rely on form parsing.
- Keep response times low by avoiding heavy joins; load related entities via dedicated helpers.

## 22. Local Development Workflow
- Use `sqlite3 data/snippets.db '.tables'` to inspect schema during debugging.
- Reset the database by removing the file; tables will be recreated automatically.
- Record manual testing steps (login, create snippet, request/approve) before handing changes to reviewers.
- Note known credentials (admin email/password) in local `.env` files but avoid committing them.
- Run the app with `uvicorn` directly only when you need custom reload behaviour (e.g., `uvicorn simppetssrv.app:create_app --reload`).

## 23. Security Notes
- Cookie signing uses HMAC-SHA256; updating the format requires bumping clients to clear old sessions.
- Password hashing relies on PBKDF2 with 200k iterations; adjust cost carefully and document rationale.
- Email and password reset tokens are single-use; they expire automatically via timestamp checks.
- When integrating real email delivery, prefer transactional providers and store configuration secrets outside the repo.
- Audit new dependencies for licence/compliance before adding them to `pyproject.toml`.

Happy coding—ship confidently!
