# AI Shorts Generator backend

FastAPI implements the 19 operations in [`../openapi.yaml`](../openapi.yaml). SQLAlchemy stores users, bearer tokens, projects, uploads, transcripts, analysis runs, jobs, idempotency keys, and usage entries. SQLite is the default database. Demo content is seeded once in an empty database.

## Run

From the repository root, run `make run`. To override the connection URL:

```sh
DATABASE_URL='sqlite+pysqlite:////absolute/path/to/ai-shorts.db' make run
```

The default file is `backend/ai_shorts.db`, independent of the current working directory. Relative SQLite paths resolve from `backend/` because the Makefile starts the server there. Open API docs at <http://127.0.0.1:8000/docs>; start the frontend in another terminal with `make frontend`.

`DATABASE_URL` accepts SQLAlchemy URLs. The engine, request-scoped sessions, ORM models, and queries use SQLAlchemy's cross-database APIs. SQLite connection settings are isolated in `app/database.py`. To use PostgreSQL later, install its DBAPI driver, configure a URL such as `postgresql+psycopg://user:password@host:5432/ai_shorts`, and apply schema migrations first. The default install includes SQLite's standard Python driver; a PostgreSQL driver is not included.

The first server start creates the schema and demo rows. Set `SEED_DEMO_DATA=false` to disable demo seeding for a new database. Seeding runs once and only if no users exist; restarting does not overwrite or duplicate data. Schema changes require migrations: `create_all` creates missing tables but does not migrate existing ones.

Use one application process with SQLite for local development. SQLite serializes writes, and demo jobs advance as API requests arrive. PostgreSQL can be used for a multi-process deployment after adding its driver and migrations.

## Demo accounts

All seeded accounts use `DemoPass123!`. Passwords are individually hashed with Argon2id.

| Email | Access / seeded content |
| --- | --- |
| `maya@example.com` | Creator with ready, failed, and expired projects |
| `jon@example.com` | Creator with a separate private project |
| `ops@example.com` | Operator; cannot access other creators' content |
| `sam@example.com` | Revoked invitation; sign-in denied |
| `disabled@example.com` | Disabled account; sign-in denied |

These credentials are for local development only.

## Authentication

`POST /v1/auth/google` keeps its historical path but accepts email and password. It does not perform Google OAuth. Login returns the session details plus `access_token`, `token_type`, and `expires_in`.

```sh
curl -X POST http://127.0.0.1:8000/v1/auth/google \
  -H 'Content-Type: application/json' \
  -d '{"email":"maya@example.com","password":"DemoPass123!"}'
```

Send `Authorization: Bearer <access_token>` on protected requests. Random tokens are stored as SHA-256 digests, expire after one hour, and can be revoked using `POST /v1/auth/logout`. Token records and project access survive server restarts.

The frontend already uses password sign-in and adds the bearer token automatically. Run `make run` and `make frontend` in separate repository-root terminals, then open <http://127.0.0.1:3000>. The frontend holds the token in memory; after a page refresh, sign in again. Edit `frontend/config.js` if the API uses a different port. CORS defaults to ports 3000 on localhost.

## Backend behavior

The API validates upload metadata, but transfers no video bytes. Upload URLs use `mock://`. Transcript and clip results are demo content, and processing advances when API requests arrive. This backend has no real media probing, transcription, LLM, render, or object-storage services. Optional YouTube import only simulates creation and never fetches the supplied URL.

It enforces ownership, invitations, one active job per creator, source limits, usage allowance, retries, cancellation, idempotency, and retention. Transcript JSON and clip output use portable SQLAlchemy JSON columns; ownership, job state, and usage use relational columns and constraints.

## Modules and checks

- `app/database.py`: configurable engine and request transactions; SQLite options only.
- `app/db_models.py`: SQLAlchemy tables and constraints.
- `app/store.py`: database operations, seed content, simulated jobs, usage, retention.
- `app/routers/`: auth, upload, project, transcript, analysis, and usage APIs.
- `app/auth.py`, `app/models.py`, `app/config.py`, `app/main.py`: authentication, schemas, settings, and app lifecycle.

Run `make test` and `make lint` from the repository root. Tests cover all 19 routes, restart persistence, multi-instance access, concurrency, transaction rollback, foreign keys, idempotency, usage, and PostgreSQL DDL compilation. `make test-frontend` also runs an isolated live HTTP integration test.
