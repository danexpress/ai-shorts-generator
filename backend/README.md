# AI Shorts Generator backend

FastAPI implements the operations in [`../openapi.yaml`](../openapi.yaml). SQLAlchemy stores users, bearer tokens, projects, uploads, transcripts, analysis runs, jobs, idempotency keys, and usage entries. SQLite is the default database. Demo content is seeded once in an empty database.

## Run

From the repository root, run `make run`. It starts the API and the video processing worker together (the worker transcribes and analyzes uploads; without it projects would stay in *Transcribing*), and Ctrl-C stops both. Run `make worker` in a second terminal only for social publishing. Set `PROCESSING_MODE=demo` to use the simulated pipeline instead. To override the connection URL:

```sh
DATABASE_URL='sqlite+pysqlite:////absolute/path/to/ai-shorts.db' make run
```

The default file is `backend/ai_shorts.db`, independent of the current working directory. Relative SQLite paths resolve from `backend/` because the Makefile starts the server there. Open API docs at <http://127.0.0.1:8000/docs>; start the frontend in another terminal with `make frontend`.

`DATABASE_URL` accepts SQLAlchemy URLs. The engine, request-scoped sessions, ORM models, and queries use SQLAlchemy's cross-database APIs. SQLite connection settings are isolated in `app/database.py`. To use PostgreSQL later, install its DBAPI driver, configure a URL such as `postgresql+psycopg://user:password@host:5432/ai_shorts`, and apply schema migrations first. The default install includes SQLite's standard Python driver; a PostgreSQL driver is not included.

The first server start creates the schema and demo rows. Set `SEED_DEMO_DATA=false` to disable demo seeding for a new database. Seeding runs once and only if no users exist; restarting does not overwrite or duplicate data. Schema changes require migrations: `create_all` creates missing tables but does not migrate existing ones.

To enable YouTube URL import, set `YOUTUBE_IMPORT=true` before starting the server. The New Project screen accepts public YouTube watch and `youtu.be` links, requires the rights confirmation, downloads the video to `MEDIA_DIR/sources/`, and then lets the normal processing flow run. It enforces a 30-second minimum, 3-hour maximum, and 4-GiB download cap. Import is disabled by default. The downloader is yt-dlp; make sure your use complies with YouTube's terms and you have permission to download and reuse the video.

Uploaded source videos are stored by default under `backend/media/sources/` using opaque filenames. Set `MEDIA_DIR` to change the storage root. Browser playback streams byte ranges through short-lived signed URLs, allowing seeking without downloading the whole file. Select a suggestion and choose **Render Short** to create a vertical 1080p MP4 (or call `POST /v1/projects/{id}/render` with resolution `720` or `1080`). By default the render fills the 9:16 canvas with a center crop (`framing: "crop"`) and burns in karaoke captions (see [Karaoke captions](#karaoke-captions)); `framing: "fit"` instead keeps the entire source frame and its proportions on black padding, and `captions: false` renders without captions. The backend stores rendered files under `backend/media/renders/` by default, exposes preview/download links in the project downloads, and removes rendered files after seven days or when the project is deleted. The bundled `imageio-ffmpeg` build is used by default (it includes libass, which burns in captions), falling back to an `ffmpeg` on your PATH. Captioned renders skip any FFmpeg without the libass `ass` filter, which some package-manager builds such as Homebrew's omit. Set `FFMPEG_BIN` to force a specific binary; for captions it must include libass. Set `MEDIA_SIGNING_KEY` to a private random value outside local development.

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

**Account deletion**: Settings → *Delete account* (or `DELETE /v1/me` with `{"confirmEmail": "<the account email>"}`) permanently deletes the user and everything it owns in one transaction (`app/account_deletion.py`). That covers projects and their source/render/transcript files (processing and queued posts are cancelled first), captions, sessions and tokens, social connections with their encrypted platform tokens, publishing history, usage and events. Rows are found by following foreign keys from the user, so new tables are covered automatically. After the commit each platform is asked to revoke its access token; failures are listed in `notRevokedPlatforms` and shown to the user, but the stored tokens are deleted either way. Posts already published on platforms are not deleted. Cookie sessions need the CSRF token, and the session cookies are cleared.

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

Uploaded videos are transcribed locally by the processing worker (MLX Whisper on Apple-silicon GPUs, faster-whisper elsewhere) (see [Transcription](#transcription)). Clip suggestions are the most engaging 20–40 s moments found by Claude in that transcript (see [Highlight detection](#highlight-detection)). Selected intervals are actually rendered with FFmpeg, with burned-in word-highlighted captions; face-aware framing and object storage are not implemented. With `PROCESSING_MODE=demo`, transcripts and suggestions are canned demo content and processing advances when API requests arrive; `HIGHLIGHT_MODEL=demo` keeps real transcription but uses the demo analyzer (no API key needed). Platform-specific social copy is available through the optional configured copy model; creators confirm the transcript before generating. YouTube URLs are actually downloaded when the opt-in feature flag is enabled.

It enforces ownership, invitations, one active job per creator, source limits, usage allowance, retries, cancellation, idempotency, and retention. Transcript JSON and clip output use portable SQLAlchemy JSON columns; ownership, job state, and usage use relational columns and constraints.

## Transcription

`make run` starts `app.jobs.worker` alongside the API. The worker claims processing jobs with a renewable lease, transcribes the source with [faster-whisper](https://github.com/SYSTRAN/faster-whisper) (word timestamps, VAD filtering) outside any database transaction, and then runs analysis. Progress shown in the app is the real fraction of audio decoded. Canceling or deleting a project while it transcribes makes the worker discard its result.

- **Backend:** `WHISPER_BACKEND=auto` (default) uses [MLX Whisper](https://github.com/ml-explore/mlx-examples/tree/main/whisper) on the Apple-silicon GPU when available, and faster-whisper otherwise. MLX is installed automatically on Apple-silicon Macs. Set `WHISPER_BACKEND=faster-whisper` to force the CPU/CUDA path.
- **Speed:** measured on an M4 Pro with a 5-minute slice of a real 69-minute podcast, with word timestamps:

  | Backend / model | 5 min of audio | 69-min video (projected) |
  | --- | --- | --- |
  | MLX `small` (GPU, default on Macs) | 8 s | ~2 min |
  | MLX `large-v3-turbo` (GPU) | 14 s | ~3.5 min |
  | faster-whisper `small` (CPU int8) | 42 s | ~10 min |

  Through the app (decoding included), MLX `small` ran 10 minutes of that video in 17 s. Forcing faster-whisper onto all CPU cores was 5× slower than its default, so thread counts are left alone.
- **Model:** `WHISPER_MODEL` (default `small`; `tiny`/`base` are faster; `turbo` (`large-v3-turbo`), `medium` and `large-v3` are more accurate). On MLX the names map to `mlx-community` conversions, or give a Hugging Face repo directly. Models download on first use (`small` about 0.5 GB, `turbo` about 1.6 GB).
- **Device (faster-whisper):** `WHISPER_DEVICE=auto` uses an NVIDIA GPU (CUDA, float16) when present, otherwise CPU int8.
- **Long videos on MLX:** audio is decoded once and transcribed in about 5-minute chunks cut at the quietest moment near each boundary, so no word is split. The language is detected on the first chunk and kept, so set `WHISPER_LANGUAGE=en` if videos open with music. Progress updates after every chunk.
- **Tighter word timing:** `uv sync --extra align` then `WHISPER_ALIGN=whisperx` re-aligns words with WhisperX (wav2vec2 forced alignment). Words it cannot align keep their Whisper timing. The extra pulls in PyTorch and is not installed by default.
- **Transcribed once:** results are cached at `MEDIA_DIR/transcripts/<sha256 of source>.<model>.json` in the format `segments → words → {text, start, end, probability}`. Retries and identical uploads reuse the file. The cache file is deleted with the last project transcript that refers to it, so it follows the analysis retention policy.
- **Stored transcript:** the project transcript keeps the existing API shape (`segments[].words[] = {w, s, e}`); transcript edits, captions and analysis all read from it.
- **No speech:** symbol-only output (`♪`), Whisper's no-speech heuristic, and repetitive decoding loops are filtered. If nothing remains (silence, music only), the project fails with `NO_SPEECH`, which explains the problem and cannot be retried.
- **Logs:** the worker logs the transcription backend at startup, plus audio duration, transcription time and realtime factor, and cache hits. It warns at startup when no `ANTHROPIC_API_KEY` is set, because highlight detection (the next stage) needs one, or `HIGHLIGHT_MODEL=demo`.

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
6. **Logs**: each request logs model, latency, input/output/cached tokens, stop reason and request ID; each job logs the total calls, tokens and estimated cost (prices in `app/llm.py`).

Requests use `client.beta.messages.create` with effort `HIGHLIGHT_EFFORT` (default `high`), the transcript as a cached prefix (retries and re-ranking reuse it), and, on models that support it, the server-side refusal fallback (`fallbacks: "default"`; `HIGHLIGHT_FALLBACKS=off` disables it). Credentials come from `ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN` or an `ant auth login` profile.

### Changing the model or prompt

- **Model**: set `HIGHLIGHT_MODEL` (default `claude-opus-5-5`; e.g. `claude-sonnet-5-5` is cheaper, `claude-haiku-5-5` cheapest) and restart `make run`. The model name is saved with every analysis run. Add a new model's prices to `PRICES` in `app/llm.py` for cost logging (unknown models log tokens only), and to `FALLBACK_MODELS` only if it accepts server-side fallbacks. Tune `HIGHLIGHT_EFFORT` alongside the model: lower effort is faster and cheaper.
- **Prompt**: edit `SYSTEM`, `GUIDANCE`, `RETRY` or `RERANK_SYSTEM` in `app/highlights/prompt.py`, keeping the `{min_seconds}`, `{max_seconds}` and `{count}` placeholders and the output schema in sync with `parse_candidates`. Bump `PROMPT_VERSION`; it is stored with each run (shown as *prompt* in the suggestions footer) so results can be traced to the prompt that produced them.
- **Clip length / count**: `highlight_min_seconds` / `highlight_max_seconds` in `app/config.py` and `HIGHLIGHT_CANDIDATES`; the prompt and validation both read them.
- Run `make test` afterwards: `tests/test_highlights.py` covers validation, snapping, retries, windowing, signals and the request shape with a mocked LLM.

## Story Mode

Story Mode turns a topic ("a lighthouse keeper who finds a message in a bottle") or a pasted story into a narrated Short. The backend pipeline is complete: script → voice → render. The Story tab in the web app comes next.

1. **Request** (`POST /v1/stories`): either a topic (3–300 characters) or a pasted story (40–6,000 characters), plus a genre, a target length of 30, 45 or 60 seconds, and an optional tone (each genre has a default tone).
2. **Prompt** (`app/story/prompt.py`): the rules are:
   - a hook of at most 7 words (under 3 seconds spoken), which also opens the first scene;
   - short spoken-style sentences;
   - a word budget of 130–150 words per minute of the target length;
   - a clear ending or twist;
   - no real people's names and no copyrighted characters or brands.

   A pasted story is adapted and tightened but never given new events, and a short one is never padded. The topic or story is sent as a cached block marked as data, followed by the length, word budget, scene count and tone. The answer is structured JSON: `{"title", "hook", "scenes": [{"id", "narration", "visual_description", "mood"}], "estimated_seconds"}`.
3. **Validation** (`app/story/script.py`):
   - Every field is checked: it must be present and non-empty, within its length limit, with 1–12 scenes and at most 400 words.
   - Scene ids are renumbered, and `estimated_seconds` is recomputed at 140 words per minute plus a 0.25 s pause between scenes, so the model's own estimate is never trusted.
   - **Structural problems** (or invalid JSON, a refusal, a truncated answer) are retried once with the problems listed; a second failure returns `SCRIPT_FAILED`.
   - **Timing problems** (hook too long or not first, outside the word budget, over 60 s) are also retried once. If the second answer still has them, it is accepted and the problems are shown to the creator as warnings.
4. **Timing:** at 140 wpm the top of the 150-wpm budget would run past 60 s once pauses are added, so the 60-second budget is capped at 120–135 words. The real length is measured on the generated audio in the voiceover step.
5. **Editing:**
   - `PUT /v1/stories/{id}/script` saves the creator's edits. Structure is enforced (`INVALID_SCRIPT` explains what's wrong), but timing only produces warnings.
   - `POST …/script/regenerate` writes a new version; with `shorten: true` it rewrites the current script shorter.
   - Both take the story's `revision`, so an edit made in another tab is never overwritten (`STALE_REVISION`).
6. **Project file:** each story is a `story_projects` row whose JSON `document` holds:
   - the request, script, source (generated or edited), model, prompt version and usage;
   - empty `voice`, `timings`, `background`, `music` and `style` slots for the later steps.

   Deleting the account deletes its stories.
7. **Cost and caching:**
   - Scripts are cached by a content hash of the prompt version, model, effort and request (`MEDIA_DIR/story-cache/scripts/`), so asking again for the same script is free. Regenerating uses a new variant key.
   - Each call logs latency, tokens and cost, and the usage is saved in the document.
   - A script typically takes 10–20 s and about $0.03–0.04 with `claude-opus-5-5` at medium effort. Generation is limited to 6 per creator per minute.

Settings: `STORY_MODEL` (default `claude-opus-5-5`; `demo` writes a canned script offline), `STORY_EFFORT` (default `medium`) and `STORY_FALLBACKS`. They use the same Claude credentials and shared client (`app/llm.py`) as highlight detection.

### Voiceover

`POST /v1/stories/{id}/voice` with `{revision, provider, voice, speed}` queues the voice; the processing worker started by `make run` generates it in the background (it also runs with `PROCESSING_MODE=demo`). Poll `GET /v1/stories/{id}`: the status goes `voicing` → `voiced` (or `voice_failed`, with a readable `voiceJob.error`), and `voiceJob.progress` counts finished scenes. `POST …/voice/playback` returns a 30-minute signed link to the WAV for the preview player.

1. **Providers** (`app/story/tts/`; `GET /v1/story-voices` lists them with their voices, speed range and default speed):
   - **Kokoro**, the default (`STORY_TTS_PROVIDER=kokoro`). It's free, runs locally, and is installed by `make install`/`make run` through the `kokoro` extra (`EXTRAS=` skips it). The first voice downloads the model from Hugging Face (about 330 MB). Twelve English voices are offered. It returns word timestamps and needs about 9 s for a 35 s voiceover on Apple silicon.
   - **ElevenLabs** (hosted). Set `ELEVENLABS_API_KEY`; optionally `ELEVENLABS_MODEL` (default `eleven_multilingual_v2`) and `ELEVENLABS_VOICES="voice_id:Label,..."` (by default a few premade voices, which depend on your account). It uses the character-timestamp endpoint, sends neighbouring scenes as `previous_text`/`next_text` so intonation flows, and is billed per character: set `ELEVENLABS_COST_PER_1K_CHARS` to your plan's price for the cost logs.
   - **Demo** produces tones instead of a voice, for tests and offline use.
2. **Speed:** each provider has a range and a default. At 1.0 Kokoro reads about 180–200 words per minute, while scripts are planned at 130–150, so its default is 0.8. The real length is measured on the audio, never assumed.
3. **Per scene:** each scene is spoken separately, decoded to 48 kHz mono, and trimmed of edge silence below −45 dBFS, keeping a 40 ms lead and an 80 ms tail.
4. **Joining:** scenes are joined with exactly 0.25 s of silence between them.
5. **Loudness:** gain, then a peak limiter, corrected from the measured result until within 0.5 LU of **−14 LUFS**, with true peaks around −1.5 dBTP. Plain `loudnorm` came out 1–2 LU quiet on speech. Measured on a real Kokoro voiceover: −14.3 LUFS, −1.2 dBTP.
6. **Word timings** are always the script's own words (`app/story/timing.py`). The timing source is chosen in this order:
   1. the provider's timestamps;
   2. WhisperX forced alignment of the known text (`WHISPER_ALIGN=whisperx`);
   3. Whisper transcription of the audio;
   4. an even spread by word length.

   The source's words are matched to the script's, so "3" spoken as "three" keeps the caption "3"; unmatched words are spread over the gaps. Every word has its scene id, and each scene has its start and end in `voice.scenes`, which is also saved as the story's `timings`.
7. **Cache and cost:**
   - Each scene's audio and timings are cached by a hash of the provider, model, voice, speed and text (`MEDIA_DIR/story-cache/tts/`). Editing one scene re-speaks only that scene, and asking for the same voice again is free.
   - Logs and the story's usage record the time, characters, billed (uncached) characters and cost. Kokoro's output varies by a few hundred milliseconds between runs, and the cache keeps a story's voice stable.
8. **Too long:** if the voiceover runs over 60 s, `voice.tooLong` is set and a warning asks the creator to shorten the script (`POST …/script/regenerate` with `shorten: true`). The audio is never cut.
9. **Staleness and safety:**
   - Changing a scene's narration marks the voice `stale`; title or visual edits don't.
   - The script can't be edited while the voice is generating (`STORY_BUSY`), and a newer request, or deleting the story, discards an in-flight result.
   - Unavailable providers (network, rate limit, 5xx) retry up to 3 times with backoff; rejected text fails at once.
   - Deleting a story or the account deletes its voice files (`MEDIA_DIR/stories/<id>/`).

### Background, captions, music and render

`POST /v1/stories/{id}/render` takes `{revision, background, music, captionPreset, captions, titleCard}` and queues the render, which the processing worker turns into a finished Short. It needs a current voice: `VOICE_REQUIRED` if there is none, `VOICE_STALE` if the narration changed since. `GET /v1/story-assets` lists what can be picked; `GET /v1/stories/{id}/renders` lists the results with playback and download links.

1. **Backgrounds** (`app/story/visuals.py`, `GET /v1/story-assets`):
   - **Bundled loops:** five original loops generated by FFmpeg on first use (slow drifting gradients with film grain, played forward then backward so they loop seamlessly). They are about 1–2 MB each, cached under `MEDIA_DIR/story-cache/backgrounds/`, with no stock footage involved. Each genre preselects one (`default_background` in its template).
   - **Your folder:** video files in `STORY_BACKGROUNDS_DIR` (default `MEDIA_DIR/story-library/backgrounds/`). Add royalty-free or self-made loops there.
   - **Your library:** the creator's own uploaded videos, while their source is retained.

   Clips are scaled and center-cropped to 9:16 by the same filter as clip renders (`renderer.fill_filter`), then looped or trimmed to length. Only your own clips are slightly darkened (`eq` brightness, gamma and saturation) so white captions stay readable; the bundled loops are dark by design.
   **v2 (AI images per scene):** add a `VisualSource` whose `filter()` turns one image per scene into slow pan/zoom shots. It receives the scene timeline (`start`, `end`, `visual_description`, `mood`); nothing else changes.
2. **Captions:**
   - The voiceover's words go through the existing karaoke caption module unchanged: `chunk_words` (1–3 words per chunk), `apply_min_duration`, then `build_ass` with the chosen preset (active-word highlight, safe-zone fitting).
   - Fillers are kept, since a scripted "hmm" is spoken on purpose.
   - The caption snapshot is saved with the render, so SRT/ASS exports work as for clips.
3. **Title card** (`app/story/titlecard.py`): the title (`titleCard: "title"`, the default), the hook (`"hook"`) or nothing (`"none"`).
   - It shows for the first 2 seconds in the upper third, above the captions, on one translucent box.
   - It's in the preset's font at the largest size (124 down to 80) that fits in three lines inside the safe zone; a longer title is cut with an ellipsis.
4. **Music** (`app/story/music.py`, optional): tracks are files in `STORY_MUSIC_DIR` (default `MEDIA_DIR/story-library/music/`); none are bundled.
   - Each track is measured, then set **18 dB under the narration** (−32 LUFS under the −14 LUFS voice).
   - After the last word it swells back up by 8 dB and fades out, and it loops if it's shorter than the Short.
   - Ducking follows the voice timeline instead of a sidechain compressor: scenes are only 0.25 s apart, and a compressor would pump in every pause.
5. **Render** (`app/story/render.py`): one FFmpeg pass, with background, voice, music and burned-in captions through the same caption working directory as clip renders.
   - Output: 1080×1920, 30 fps, H.264 (yuv420p, CRF 21) and 48 kHz stereo AAC at 192 kb/s, with `+faststart`.
   - Length: the voiceover plus a 0.8 s outro, kept at or under 60 s whenever the voice is.
   - A 45 s Short renders in about 3 s on Apple silicon.
6. **Where renders live:** each render is an ordinary `RenderRecord` on a hidden project (`sourceType: "story"`, left out of `GET /v1/projects`). Downloads, SRT/ASS exports, render retention (`render_retention_days`) and **Review & publish** therefore work unchanged.
   - The publishing-copy context is the narration itself, marked as verified, so creators can generate platform copy without a transcript review.
   - Deleting the story deletes its renders and cancels queued posts, as deleting a project does.
7. **Re-rendering:** the render settings are saved in the story document (`background`, `music`, `style`) and returned as `renderSettings`, so the next render starts from them. Changing only the background, music or style re-renders without re-writing the script or re-speaking the voice; both come from the cache.

Measured end to end with no mocks (a topic → `claude-opus-5-5` → Kokoro `bm_fable` → the midnight loop with music):
- **Script:** 22 s.
- **Voice:** 11 s, for 44.3 s of audio at −14.6 LUFS.
- **Render:** 3.9 s, for a 45.1 s, 7 MB Short.
- **Re-render:** 2.5 s, with the script and voice served from the cache.

### Adding a voice provider

Subclass `TTSProvider` (`app/story/tts/base.py`) and register it in `PROVIDERS` (`app/story/tts/__init__.py`):

```python
class MyProvider(TTSProvider):
    id, label = "mytts", "My TTS"
    speed_range, default_speed = (0.8, 1.2), 1.0

    @property
    def cost_per_character(self):  # USD, for cost logs; 0 for local engines
        return 0.00015

    def unavailable_reason(self):  # shown in the voice picker; None when usable
        return None if self.settings.my_tts_key else "Set MY_TTS_KEY to use My TTS."

    def voices(self):
        return [Voice("narrator", "Narrator", "Calm · male")]

    def synthesize(self, text, voice, speed, destination, context):
        destination.write_bytes(call_my_api(text, voice, speed))  # any format FFmpeg reads
        return Speech(words=None, characters=len(text))  # None: aligned automatically
```

Raise `TTSUnavailable` for failures worth retrying (network, quota, outage) and `TTSFailed` when the text or voice is rejected. Return word timestamps (`[{"text", "start", "end"}]`, seconds from the file's start) when the API has them. Otherwise return `words=None` and the timing fallbacks above apply. Trimming, pauses, loudness, caching and cost logging are handled for every provider. Bump the class's `version` when its output for the same input changes, so cached audio isn't reused. `tests/test_story_voice.py` shows how to test an HTTP provider against a mocked API.

### Adding a genre template

Add a file to `app/story/genres/`, for example `scifi.py`:

```python
from . import Genre

GENRE = Genre(
    id="scifi",
    label="Sci-fi",
    guidance="One strange technology, one human cost, and a final reveal that changes its meaning.",
    default_tone="wonder with an edge",
    moods=("curious", "uneasy", "awed", "haunting"),
    order=60,
)
```

It's discovered automatically and appears in `GET /v1/story-genres`, ordered by `order`. `guidance` is sent to the model with every script in that genre. Bump `PROMPT_VERSION` in `app/story/prompt.py` if existing scripts in that genre should not be served from the cache. Run `make test`: `tests/test_story_script.py` checks every genre's template and that demo scripts fit each length.

## Modules and checks

- `app/database.py`: configurable engine and request transactions; SQLite options only.
- `app/db_models.py`: SQLAlchemy tables and constraints.
- `app/store.py`: database operations, seed content, simulated jobs, usage, retention.
- `app/routers/`: auth, upload, project, media, render, transcript, analysis, and usage APIs.
- `app/renderer.py`: FFmpeg vertical MP4 rendering.
- `app/transcription/`: faster-whisper transcriber, optional WhisperX alignment, transcript cache.
- `app/jobs/worker.py`: leased processing worker for real transcription and analysis.
- `app/llm.py`: shared Claude client (structured JSON output, prompt caching, error mapping, token and cost logging) and the retry-once helper.
- `app/story/`: Story Mode genre templates, script prompt, validation and timing, content-hash cache, story service; `tts/` voice providers, `audio.py` scene joining and loudness, `timing.py` word timings, `voice.py` the voiceover pipeline, `visuals.py` backgrounds, `titlecard.py` title card and captions, `music.py` music bed, `render.py` the single-pass render, `worker.py` background voice and render jobs.
- `app/highlights/`: Claude prompt, candidate validation and sentence snapping, transcript windows, optional loudness/scene signals.
- `app/captions/`: chunking, presets, glyph measurement, ASS/SRT writers and caption-track service; `app/routers/captions.py` exposes presets and per-clip tracks.
- `app/youtube.py`: supported YouTube URL validation and bounded video download.
- `app/auth.py`, `app/models.py`, `app/config.py`, `app/main.py`: authentication, schemas, settings, and app lifecycle.

Run `make test` and `make lint` from the repository root. Worker and transcription tests use a fake transcriber and highlight tests a mocked LLM; neither Whisper nor the Claude API runs in the test suite. Tests cover the existing and social routes, source and rendered video range streaming, render storage and cleanup, restart persistence, multi-instance access, concurrency, transaction rollback, foreign keys, idempotency, usage, and PostgreSQL DDL compilation. `make test-frontend` also runs an isolated live HTTP integration test.

## Social publishing

Connect accounts under **Settings → Social Accounts**, then open a rendered Short and choose **Review & publish**. YouTube, TikTok, X and Instagram use separate official API adapters, encrypted user OAuth tokens and independent persistent publishing jobs, posted now or at a scheduled time. Start `make worker` in a second terminal using the same environment, database and media directory as `make run`. Platforms default disabled until configured.

See [social publishing setup](../docs/social-publishing.md) for each platform's developer app (redirect URIs, scopes, environment variables), what each platform's review requires, and security and the [environment template](.env.social.example). The OpenAPI contract includes the new endpoints, including the asynchronous 202 publish response. No live platform credentials or copy-model key are included.
