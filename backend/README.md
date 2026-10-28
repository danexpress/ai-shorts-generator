# AI Shorts Generator backend

FastAPI implements the operations in [`../openapi.yaml`](../openapi.yaml). SQLAlchemy stores users, bearer tokens, projects, uploads, transcripts, analysis runs, jobs, idempotency keys, and usage entries. SQLite is the default database. Demo content is seeded once in an empty database.

## Run

From the repository root, run `make run` and, in a second terminal, `make worker` (it transcribes and analyzes uploaded videos; without it projects stay in *Transcribing*). Set `PROCESSING_MODE=demo` on both to use the simulated pipeline instead. To override the connection URL:

```sh
DATABASE_URL='sqlite+pysqlite:////absolute/path/to/ai-shorts.db' make run
```

The default file is `backend/ai_shorts.db`, independent of the current working directory. Relative SQLite paths resolve from `backend/` because the Makefile starts the server there. Open API docs at <http://127.0.0.1:8000/docs>; start the frontend in another terminal with `make frontend`.

`DATABASE_URL` accepts SQLAlchemy URLs. The engine, request-scoped sessions, ORM models, and queries use SQLAlchemy's cross-database APIs. SQLite connection settings are isolated in `app/database.py`. To use PostgreSQL later, install its DBAPI driver, configure a URL such as `postgresql+psycopg://user:password@host:5432/ai_shorts`, and apply schema migrations first. The default install includes SQLite's standard Python driver; a PostgreSQL driver is not included.

The first server start creates the schema and demo rows. Set `SEED_DEMO_DATA=false` to disable demo seeding for a new database. Seeding runs once and only if no users exist; restarting does not overwrite or duplicate data. Schema changes require migrations: `create_all` creates missing tables but does not migrate existing ones.

To enable YouTube URL import, set `YOUTUBE_IMPORT=true` before starting the server. The New Project screen accepts public YouTube watch and `youtu.be` links, requires the rights confirmation, downloads the video to `MEDIA_DIR/sources/`, and then lets the normal processing flow run. It enforces a 30-second minimum, 3-hour maximum, and 4-GiB download cap. Import is disabled by default. The downloader is yt-dlp; make sure your use complies with YouTube's terms and you have permission to download and reuse the video.

Uploaded source videos are stored by default under `backend/media/sources/` using opaque filenames. Set `MEDIA_DIR` to change the storage root. Browser playback streams byte ranges through short-lived signed URLs, allowing seeking without downloading the whole file. Select a suggestion and choose **Render Short** to create a vertical 1080p MP4 (or call `POST /v1/projects/{id}/render` with resolution `720` or `1080`). By default the render fills the 9:16 canvas with a center crop (`framing: "crop"`) and burns in karaoke captions (see [Karaoke captions](#karaoke-captions)); `framing: "fit"` instead keeps the entire source frame and its proportions on black padding, and `captions: false` renders without captions. The backend stores rendered files under `backend/media/renders/` by default, exposes preview/download links in the project downloads, and removes rendered files after seven days or when the project is deleted. `imageio-ffmpeg` supplies FFmpeg; set `FFMPEG_BIN` to use a system FFmpeg binary instead. Set `MEDIA_SIGNING_KEY` to a private random value outside local development.

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

Browsers sign in with **Google** (when configured) or, while `ALLOW_PASSWORD_LOGIN=true` (the default, for the demo accounts and local development), with email and password. Turn password login off in production.

**Google Sign-In** is the OpenID Connect authorization-code flow with PKCE (`app/google_auth.py`):

1. `GET /v1/auth/google/start` stores a single-use state, nonce and PKCE verifier, binds them to the browser with a short-lived httpOnly cookie, and redirects to Google requesting only `openid email profile`. YouTube upload access is requested later, only when the creator connects YouTube (incremental authorization).
2. `GET /v1/auth/google/callback` consumes the state, exchanges the code, and verifies the ID token server-side: RS256 signature against Google's published keys (cached, refetched when Google rotates them), issuer, audience (our client ID), expiry, nonce, and a verified email. Nothing the browser sends about the user is trusted.
3. Users are found by Google `sub`. The first Google sign-in of an existing invited account links it by verified email; afterwards only `sub` is used, so email changes don't matter, and a different Google account can't take over a linked email. New accounts are created only for emails in `BETA_INVITE_EMAILS` (or anyone with `OPEN_SIGNUP=true`). Name and avatar are stored.
4. The browser returns to `FRONTEND_PUBLIC_URL`, with `?auth_error=<code>` on failure. Tokens never appear in URLs.

**Sessions** (`app/sessions.py`): a 15-minute access cookie (`shorts_session`) and a 30-day refresh cookie (`shorts_refresh`, sent only to `/v1/auth`), both httpOnly, `SameSite=Lax`, and `Secure` everywhere except a plain-HTTP localhost backend. `POST /v1/auth/refresh` rotates both. Each refresh token works once, and replaying a used one revokes the whole session; a second tab refreshing within 10 s gets `409 REFRESH_IN_PROGRESS` instead. Tokens are stored as SHA-256 digests. `POST /v1/auth/logout` revokes the session and clears the cookies.

**CSRF**: cookie-authenticated POST/PATCH/DELETE requests must send the session's `csrfToken` (returned by sign-in and `GET /v1/me`) in `X-CSRF-Token` and come from an allowed Origin (`CORS_ORIGINS` or `BACKEND_PUBLIC_URL`). The frontend keeps the token in memory only and stores nothing in browser storage.

**Bearer tokens**: password login also returns an `access_token` (1 hour, revocable with logout) for scripts and API clients; bearer requests don't use cookies or CSRF.

```sh
curl -X POST http://127.0.0.1:8000/v1/auth/google \
  -H 'Content-Type: application/json' \
  -d '{"email":"maya@example.com","password":"DemoPass123!"}'
```

### Setting up Google Sign-In

1. In Google Cloud Console → **APIs & Services → Credentials**, create an **OAuth client ID** of type **Web application**.
2. Authorized redirect URI: `BACKEND_PUBLIC_URL/v1/auth/google/callback`, for example `http://localhost:8000/v1/auth/google/callback` locally and `https://api.example.com/v1/auth/google/callback` in production. Google accepts plain HTTP only for `localhost`.
3. OAuth consent screen: app name, support email, privacy policy and terms URLs, scopes `openid`, `email`, `profile` (non-sensitive, so no verification review is needed for sign-in alone), and test users while in *Testing* mode. YouTube's `youtube.upload` scope is sensitive and needs Google's verification before public use; see [social publishing](../docs/social-publishing.md).
4. Set `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `BACKEND_PUBLIC_URL`, `FRONTEND_PUBLIC_URL` and `CORS_ORIGINS` (see `.env.example`), and add invited emails to `BETA_INVITE_EMAILS`.
5. Serve the frontend and API from the same host so the browser sends the `SameSite=Lax` cookies. For example, use `localhost` for both locally (not `localhost` for one and `127.0.0.1` for the other), or `app.example.com` and `api.example.com` in production. When `apiBaseUrl` in `frontend/config.js` and `FRONTEND_PUBLIC_URL` use `localhost`, open the app at `http://localhost:3000`.

## Backend behavior

Uploaded videos are transcribed locally with faster-whisper by the processing worker (see [Transcription](#transcription)). Clip suggestions are the most engaging 20–40 s moments found by Claude in that transcript (see [Highlight detection](#highlight-detection)). Selected intervals are actually rendered with FFmpeg, with burned-in word-highlighted captions; face-aware framing and object storage are not implemented. With `PROCESSING_MODE=demo`, transcripts and suggestions are canned demo content and processing advances when API requests arrive; `HIGHLIGHT_MODEL=demo` keeps real transcription but uses the demo analyzer (no API key needed). Platform-specific social copy is available through the optional configured copy model; it requires review of the demo transcript. YouTube URLs are actually downloaded when the opt-in feature flag is enabled.

It enforces ownership, invitations, one active job per creator, source limits, usage allowance, retries, cancellation, idempotency, and retention. Transcript JSON and clip output use portable SQLAlchemy JSON columns; ownership, job state, and usage use relational columns and constraints.

## Transcription

`make worker` runs `app.jobs.worker`, which claims processing jobs with a renewable lease, transcribes the source with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (word timestamps, VAD filtering) outside any database transaction, and then runs analysis. Progress shown in the app is the real fraction of audio decoded. Canceling or deleting a project while it transcribes makes the worker discard its result.

- **Model and device:** `WHISPER_MODEL` (default `small`; `tiny`/`base` are faster, `medium`/`large-v3` more accurate). `WHISPER_DEVICE=auto` uses an NVIDIA GPU (CUDA, float16) when present, otherwise CPU int8. CTranslate2 has no Apple-silicon GPU backend, so Macs run on CPU; `small` runs at roughly 0.4× realtime on an M4 Pro. The model downloads from Hugging Face on first use.
- **Tighter word timing:** `uv sync --extra align` then `WHISPER_ALIGN=whisperx` re-aligns words with WhisperX (wav2vec2 forced alignment). Words it cannot align keep their Whisper timing. The extra pulls in PyTorch and is not installed by default.
- **Transcribed once:** results are cached at `MEDIA_DIR/transcripts/<sha256 of source>.<model>.json` in the format `segments → words → {text, start, end, probability}`. Retries and identical uploads reuse the file. The cache file is deleted with the last project transcript that refers to it, so it follows the analysis retention policy.
- **Stored transcript:** the project transcript keeps the existing API shape (`segments[].words[] = {w, s, e}`); transcript edits, captions and analysis all read from it.
- **No speech:** symbol-only output (`♪`), Whisper's no-speech heuristic, and repetitive decoding loops are filtered. If nothing remains (silence, music only), the project fails with `NO_SPEECH`, which explains the problem and cannot be retried.
- **Logs:** model load time, audio duration, transcription time and realtime factor, and cache hits are logged by the worker.

## Karaoke captions

Captions are built from the stored transcript's word timestamps; they never trigger a new transcription.

1. **Chunking** (`app/captions/chunking.py`): the clip's words (those whose midpoint lies in the clip) are grouped into chunks of 1–3 words that fit at most 2 lines of about 15 characters. A chunk ends after punctuation (`. , ! ? ; :`), before a pause of 0.35 s or more, or when the next word would not fit; words are never split, and a single long word may take a line alone. Each chunk runs from its first word's start to its last word's end, extended to at least 0.3 s into neighbouring silence without overlapping. Filler words (`um`, `uh`, `er`, `hmm`…) are dropped by default. All of these rules are `ChunkOptions` defaults.
2. **Editing**: `GET /v1/projects/{id}/clips/{clipId}/captions` returns the clip's caption track, derived on the fly until it is first saved. `PATCH` the same path to change the style (`preset`), toggle `keepFillers`, replace the words (`chunks` — fix typos, nudge timing), or `reset` to the transcript. Edited words must stay in order, non-overlapping and inside the clip; chunk timing is recomputed from them. If the text of a chunk changes word count, the editor spreads the new words over the chunk's original time span and marks them `estimated`. Correcting the transcript afterwards marks a saved track `stale`; reset rebuilds it. Changing filler handling on an edited track requires a reset, because it re-chunks the words.
3. **Rendering** (`app/captions/formats.py`): an ASS file on a 1080×1920 canvas has one event per word. The whole chunk is shown and the word being spoken gets the active colour (and optional pop scale), so exactly one word is highlighted at a time. The renderer burns it in with libass, using only the bundled fonts in `app/captions/fonts/` (Montserrat, SIL Open Font License). Lines are measured with the font's glyph widths and shrunk if they would leave the safe zone. Each render stores a snapshot of its captions; the project downloads then include matching `.srt` (plain cues, one per chunk) and `.ass` files for that render.
4. **Preview**: the editor overlays the same chunks on the source video in the browser, following the video clock each animation frame, with the preset's colours and sizes.

### Caption presets

Presets live in `app/captions/presets.py`: Clean Bold (default: white Montserrat ExtraBold, thick black outline, spoken word yellow), Creator Pop (uppercase Black, spoken word green and 115%) and Minimal Highlight (SemiBold, upcoming words dimmed). Every visual value of a style is a `CaptionPreset` field; chunking rules are shared by all presets, so switching style never re-chunks or loses edits.

To add a preset:

1. If it needs a new font, add the `.ttf` to `app/captions/fonts/` (and `frontend/fonts/` plus an `@font-face` in `frontend/app.css` for the preview). Use a font whose licence allows embedding, and keep its licence file alongside it.
2. Add a `CaptionPreset(...)` entry to `PRESETS` with a unique `id`, the `font_file`, the font's full name as `font_name` (check it with fontTools: name ID 4), colours as `#RRGGBB`, and sizes in pixels on the 1080×1920 canvas. `position` is the vertical centre of the caption block as a fraction of the height (0.67 sits above the platforms' bottom overlays).
3. Run `make test`: `test_every_preset_fits_the_safe_zone` checks the font file exists and a worst-case two-line caption stays inside `SAFE_ZONE`. The new preset appears in `GET /v1/caption-presets` and in the editor automatically.

## Highlight detection

The worker's analysis stage sends the stored transcript to the Claude API and stores the answer as the project's suggestions, so the existing **Best moments → Preview → Render** flow is how a creator picks one: the chosen window is cut, center-cropped to 9:16 and captioned (Karaoke captions above). Nothing is re-transcribed.

1. **Prompt** (`app/highlights/prompt.py`): the transcript is sent as sentence lines `[start-end] text`, wrapped in a `<transcript>` block marked as data, followed by the creator's goal, note and topics to avoid. The model returns up to `HIGHLIGHT_CANDIDATES` (3) moments ranked best first, as structured JSON (`output_config.format` with a JSON schema): `{"candidates": [{"start", "end", "hook", "reason", "score"}]}`, score 0–10.
2. **Validation** (`app/highlights/validate.py`): every candidate is type-checked; timestamps must fall inside the video and the score in 0–10. Start and end are then snapped to sentence boundaries (sentences end at `. ! ?` or a pause of 1 s); length is brought into 20–40 s by adding or dropping whole sentences, falling back to word boundaries only for run-on speech, and a little padding is taken from the surrounding silence. Clips therefore never start or end mid-word. Overlapping candidates keep the higher score. If nothing valid comes back (bad JSON, a refusal, truncation, or only invalid candidates), the request is retried once with the problems listed; a second failure ends the job as `ANALYSIS_FAILED` (retryable).
3. **Long videos** (`app/highlights/transcript.py`): a transcript estimated above `HIGHLIGHT_WINDOW_TOKENS` is split into windows that overlap by 45 s (longer than any clip, so a moment on a window edge appears whole somewhere). Each window yields candidates; a final request compares all of them side by side and re-ranks them.
4. **Optional signals** (`HIGHLIGHT_SIGNALS=true`, off by default; `app/highlights/signals.py`): FFmpeg's `ebur128` measures momentary loudness and `scdet` finds scene changes. Each candidate's score moves by at most ±1.25 points for being louder or more visually dynamic than the rest of the video, then candidates are re-sorted. The adjustment is shown on the suggestion card.
5. **Results and failures**: suggestions show one *Engagement* score (score × 10). A video with too little speech, or where the model finds no usable moment, ends as `NO_HIGHLIGHTS` with an explanation and no retry. Missing credentials, rate limits and outages end as retryable `ANALYSIS_FAILED`; retrying re-runs only analysis.
6. **Logs**: each request logs model, latency, input/output/cached tokens, stop reason and request ID; each job logs the total calls, tokens and estimated cost (prices in `app/highlights/llm.py`).

Requests use `client.beta.messages.create` with effort `HIGHLIGHT_EFFORT` (default `high`), the transcript as a cached prefix (retries and re-ranking reuse it), and, on models that support it, the server-side refusal fallback (`fallbacks: "default"`; `HIGHLIGHT_FALLBACKS=off` disables it). Credentials come from `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or an `ant auth login` profile.

### Changing the model or prompt

- **Model**: set `HIGHLIGHT_MODEL` (default `claude-opus-5-5`; e.g. `claude-sonnet-5-5` is cheaper, `claude-haiku-5-5` cheapest) and restart `make worker`. The model name is saved with every analysis run. Add a new model's prices to `PRICES` in `app/highlights/llm.py` for cost logging (unknown models log tokens only), and to `FALLBACK_MODELS` only if it accepts server-side fallbacks. Tune `HIGHLIGHT_EFFORT` alongside the model: lower effort is faster and cheaper.
- **Prompt**: edit `SYSTEM`, `GUIDANCE`, `RETRY` or `RERANK_SYSTEM` in `app/highlights/prompt.py`, keeping the `{min_seconds}`, `{max_seconds}` and `{count}` placeholders and the output schema in sync with `parse_candidates`. Bump `PROMPT_VERSION`; it is stored with each run (shown as *prompt* in the suggestions footer) so results can be traced to the prompt that produced them.
- **Clip length / count**: `highlight_min_seconds` / `highlight_max_seconds` in `app/config.py` and `HIGHLIGHT_CANDIDATES`; the prompt and validation both read them.
- Run `make test` afterwards: `tests/test_highlights.py` covers validation, snapping, retries, windowing, signals and the request shape with a mocked LLM.

## Modules and checks

- `app/database.py`: configurable engine and request transactions; SQLite options only.
- `app/db_models.py`: SQLAlchemy tables and constraints.
- `app/store.py`: database operations, seed content, simulated jobs, usage, retention.
- `app/routers/`: auth, upload, project, media, render, transcript, analysis, and usage APIs.
- `app/renderer.py`: FFmpeg vertical MP4 rendering.
- `app/transcription/`: faster-whisper transcriber, optional WhisperX alignment, transcript cache.
- `app/jobs/worker.py`: leased processing worker for real transcription and analysis.
- `app/highlights/`: Claude prompt and client, candidate validation and sentence snapping, transcript windows, optional loudness/scene signals.
- `app/captions/`: chunking, presets, glyph measurement, ASS/SRT writers and caption-track service; `app/routers/captions.py` exposes presets and per-clip tracks.
- `app/youtube.py`: supported YouTube URL validation and bounded video download.
- `app/auth.py`, `app/models.py`, `app/config.py`, `app/main.py`: authentication, schemas, settings, and app lifecycle.

Run `make test` and `make lint` from the repository root. Worker and transcription tests use a fake transcriber and highlight tests a mocked LLM; neither Whisper nor the Claude API runs in the test suite. Tests cover the existing and social routes, source and rendered video range streaming, render storage and cleanup, restart persistence, multi-instance access, concurrency, transaction rollback, foreign keys, idempotency, usage, and PostgreSQL DDL compilation. `make test-frontend` also runs an isolated live HTTP integration test.

## Social publishing

Connect accounts under **Settings → Social Accounts**, then open a rendered Short and choose **Review & publish**. YouTube, TikTok and X use separate official API adapters, encrypted user OAuth tokens and independent persistent publishing jobs. Start `make worker` in a third terminal using the same environment, database and media directory as `make run`. Platforms default disabled until configured.

See [social publishing setup, callbacks, scopes, approvals and security](../docs/social-publishing.md) and the [environment template](.env.social.example). The OpenAPI contract includes the new endpoints, including the asynchronous 202 publish response. No live platform credentials or copy-model key are included.
