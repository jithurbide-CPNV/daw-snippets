# Snippet Vault

FastAPI application for managing code snippets with public and private visibility. Users can create accounts, store snippets, and request access to private entries. Admin users review access requests and approve or deny them from a queue. Accounts sont actifs immédiatement, aucune vérification email n’est nécessaire.

## Quick Start

```bash
python3 -m pip install -e '.[dev]'
python -m simppetssrv
```

The server listens on `0.0.0.0:8000` by default. Visit `http://127.0.0.1:8000/` to see the snippet catalog.

### Creating an Admin User at Launch

Provide credentials via environment variables to bootstrap an admin account:

```bash
export SIMPPETSSRV_ADMIN_EMAIL=admin@example.com
export SIMPPETSSRV_ADMIN_PASSWORD='super-secret'
python -m simppetssrv
```

The account is created (or refreshed) with the `admin` role. Log in through `/login` to approve requests at `/admin/requests`.

### Database Location

By default, data is stored in `data/snippets.db`. Override the location with a SQLite URL:

```bash
export SIMPPETSSRV_DB_URL=sqlite+pysqlite:////tmp/snippet-demo.db
python -m simppetssrv
```

The application creates tables on demand; no migrations are required.

## Accounts and Access

1. Users sign up at `/signup` et sont connectés automatiquement.
2. Depuis cet état connecté, ils créent des snippets via `/snippets/new`.
3. Public snippets appear for everyone; private snippets are visible only to the owner and approved users.
4. Non-owners can request access from the snippet detail page. Admins approve or deny requests.
5. Approved users gain read-only access to the snippet.

## HTTPS and Reverse Proxies

Force HTTPS redirects by setting `SIMPPETSSRV_FORCE_HTTPS=true`. When serving behind a proxy, ensure `X-Forwarded-*` headers are forwarded so the application can build correct absolute URLs.

## Environment Variables

| Variable | Description |
| --- | --- |
| `SIMPPETSSRV_DB_URL` | SQLite URL or filesystem path for the database. |
| `SIMPPETSSRV_SESSION_SECRET` | Secret used to sign session cookies; auto-generated if omitted. |
| `SIMPPETSSRV_ADMIN_EMAIL` / `SIMPPETSSRV_ADMIN_PASSWORD` | Optional admin bootstrap credentials. |
| `SIMPPETSSRV_FORCE_HTTPS` | `true`/`false` toggle for redirecting HTTP traffic to HTTPS. |
| `SIMPPETSSRV_HOST` / `SIMPPETSSRV_PORT` | Override bind host and port (used by the CLI entry point). |

## Tests and Tooling

- Lint: `ruff check .`
- Format: `ruff format .`
- Test suite: `pytest`
- Focused test: `pytest tests/test_app.py::test_authenticated_user_can_create_snippet`

## CLI Entry Point

Run `python -m simppetssrv` to start the server. The script prints a helpful URL with your LAN IP when binding to `0.0.0.0`.

⚠️ Password reset par email n’est pas pris en charge dans cette version. Prévoir une procédure interne (ex. admin CLI) si vous devez réinitialiser des comptes.

## Data Storage

All application data lives in the SQLite database. Back up `snippets.db` to preserve users, snippets, and request history. No audio files or other assets are required.
