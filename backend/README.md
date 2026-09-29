# AI Shorts Generator backend

FastAPI implements the operations in [`../openapi.yaml`](../openapi.yaml). SQLAlchemy stores users, bearer tokens, projects, uploads, transcripts, analysis runs, jobs, idempotency keys, and usage entries. SQLite is the default database. Demo content is seeded once in an empty database.

## Run

From the repository root, run `make run`. To override the connection URL:

```sh
DATABASE_URL='sqlite+pysqlite:////absolute/path/to/ai-shorts.db' make run
```

The default file is `backend/ai_shorts.db`, independent of the current working directory. Relative SQLite paths resolve from `backend/` because the Makefile starts the server there. Open API docs at <http://127.0.0.1:8000/docs>; start the frontend in another terminal with `make frontend`.

`DATABASE_URL` accepts SQLAlchemy URLs. The engine, request-scoped sessions, ORM models, and queries use SQLAlchemy's cross-database APIs. SQLite connection settings are isolated in `app/database.py`. To use PostgreSQL later, install its DBAPI driver, configure a URL such as `postgresql+psycopg://user:password@host:5432/ai_shorts`, and apply schema migrations first. The default install includes SQLite's standard Python driver; a PostgreSQL driver is not included.

The first server start creates the schema and demo rows. Set `SEED_DEMO_DATA=false` to disable demo seeding for a new database. Seeding runs once and only if no users exist; restarting does not overwrite or duplicate data. Schema changes require migrations: `create_all` creates missing tables but does not migrate existing ones.

To enable YouTube URL import, set `YOUTUBE_IMPORT=true` before starting the server. The New Project screen accepts public YouTube watch and `youtu.be` links, requires the rights confirmation, downloads the video to `MEDIA_DIR/sources/`, and then lets the normal processing flow run. It enforces a 30-second minimum, 60-minute maximum, and 4-GiB download cap. Import is disabled by default. The downloader is yt-dlp; make sure your use complies with YouTube's terms and you have permission to download and reuse the video.

Uploaded source videos are stored by default under `backend/media/sources/` using opaque filenames. Set `MEDIA_DIR` to change the storage root. Browser playback streams byte ranges through short-lived signed URLs, allowing seeking without downloading the whole file. Select a suggestion and choose **Render Short** to create a vertical 1080p MP4 (or call `POST /v1/projects/{id}/render` with resolution `720` or `1080`). Rendering preserves the entire source frame and its proportions, centering it on the vertical canvas with black padding when needed. Existing cropped exports must be rendered again to use this framing. The backend stores rendered files under `backend/media/renders/` by default, exposes preview/download links in the project downloads, and removes rendered files after seven days or when the project is deleted. `imageio-ffmpeg` supplies FFmpeg; set `FFMPEG_BIN` to use a system FFmpeg binary instead. Set `MEDIA_SIGNING_KEY` to a private random value outside local development.

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

Transcript and clip results are demo content, and processing advances when API requests arrive. Selected intervals are actually rendered with FFmpeg; automatic transcription, clip analysis, face-aware framing, captions, metadata generation, and object storage are not implemented. YouTube URLs are actually downloaded when the opt-in feature flag is enabled.

It enforces ownership, invitations, one active job per creator, source limits, usage allowance, retries, cancellation, idempotency, and retention. Transcript JSON and clip output use portable SQLAlchemy JSON columns; ownership, job state, and usage use relational columns and constraints.

## Modules and checks

- `app/database.py`: configurable engine and request transactions; SQLite options only.
- `app/db_models.py`: SQLAlchemy tables and constraints.
- `app/store.py`: database operations, seed content, simulated jobs, usage, retention.
- `app/routers/`: auth, upload, project, media, render, transcript, analysis, and usage APIs.
- `app/renderer.py`: FFmpeg vertical MP4 rendering.
- `app/youtube.py`: supported YouTube URL validation and bounded video download.
- `app/auth.py`, `app/models.py`, `app/config.py`, `app/main.py`: authentication, schemas, settings, and app lifecycle.

Run `make test` and `make lint` from the repository root. Tests cover all 24 routes, source and rendered video range streaming, render storage and cleanup, restart persistence, multi-instance access, concurrency, transaction rollback, foreign keys, idempotency, usage, and PostgreSQL DDL compilation. `make test-frontend` also runs an isolated live HTTP integration test.
