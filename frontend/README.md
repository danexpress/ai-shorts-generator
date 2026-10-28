# Shorts Studio frontend

The existing static HTML application uses its React-backed DC runtime, with no build step. API calls and workflow state remain in `AI Shorts Generator.dc.html`; `services.js` provides the HTTP client and mock test service. `support.js` is the existing generated runtime.

Run `make run` and `make frontend` from the repository root in separate terminals. Open http://127.0.0.1:3000. Configure the API origin in `config.js`. Demo credentials and backend feature flags are documented in `../backend/README.md`.

Sign-in is **Continue with Google** (when the backend has Google configured) and/or the email/password form (when `ALLOW_PASSWORD_LOGIN` is on); the login screen asks `/v1/auth/options` which to show. Sessions are httpOnly cookies set by the API, so the page never sees or stores a token: nothing is written to browser storage. The HTTP client sends requests with credentials, keeps the session's CSRF token in memory (from sign-in or `/v1/me`) and sends it as `X-CSRF-Token` on writes, and on a 401 performs one shared `/v1/auth/refresh` and retries. Login survives page refresh through the cookies; logout and authentication errors end the session. A failed Google sign-in returns with `?auth_error=<code>`, which is shown once and removed from the address bar. Serve the frontend and API from the same host (e.g. both `localhost`) so the browser sends the SameSite=Lax cookies.

## Presentation architecture

- `app.css`: centralized light/dark tokens, 8px spacing scale, shared controls, screen layouts, breakpoints, focus states, reduced-motion behavior.
- `components.js`: reusable React presentation components and screen composition. `ShortsUI.App` receives view data and callbacks from the page controller using the runtime's existing `x-import` mechanism.
- Shared components include AppSidebar, PageHeader, ProjectTable, ClipCard, ScoreIndicator, VideoPreview, ProcessingStatus, UploadDropzone, CaptionPresetCard, UsageIndicator, EmptyState, ErrorState, and StatusBadge.
- `industry.css` and the `_ds/` artifacts remain for the existing standalone service diagnostics page.

The main workflow is Dashboard → New Project → Processing → Best moments → Preview → Rendered Short. Projects, Usage, Settings, transcript editing, regeneration, cancellation, retry, and existing downloads remain accessible. Appearance is a browser preference, not an account setting. Mobile navigation collapses below 900px; editing panels stack below 640px.

Best moments shows Claude's highlight candidates with one engagement score (and, with signals enabled, the loudness/scene adjustment). The editor preview shows the chosen framing (center crop by default, or the full source frame) with a live karaoke caption overlay in the selected style; caption text and timing are edited per line and saved before rendering. Clip previews stop at the selected interval. Render quality supports the backend's 1080p and 720p options. Processing shows actual stage changes; uploads use an indeterminate transfer state because the fetch client does not expose reliable byte progress. Project thumbnails use real source playback links, with placeholders when source media is unavailable.

## Backend capability boundaries

The API renders MP4 videos with burned-in karaoke captions in three caption styles, and exports TXT/JSON transcripts plus per-render SRT/ASS subtitle files. Burned-in hook text, watermark rendering and cover downloads are not implemented. Optional platform-specific social copy is now provided by the configured social copy service. Their requested UI surfaces are clearly marked unavailable. Unavailable branding controls sit in a compact disclosure so the primary workflow stays clear. The suggested hook can be copied for publishing; the UI does not fabricate platform-specific metadata or imply that disabled settings affect the exported video.

Source retention is 24 hours; rendered files are retained for 7 days; analysis for 30 days. The upload contract accepts MP4/MOV/WebM, 30 seconds–3 hours, up to 4 GB. The YouTube URL option is always shown; it can start a project only when the backend enables import (`YOUTUBE_IMPORT=true`), and otherwise explains that import is turned off.

## Verification

`make test-frontend` runs the service contract tests and Node tests, including a real isolated FastAPI integration. No browser dependency is required for those checks. `make test` runs the backend suite, including FFmpeg export verification.

`browser-smoke.cjs` is an optional real Chrome/Playwright test. It checks login restoration after refresh, logout, project search, URL consent, file upload, status transitions, transcript corrections, regeneration, video playback, 720p rendering, downloading, themes, responsive layouts, keyboard navigation, project deletion, action placement, and empty/loading/error states. It requires an **isolated** seeded backend with YouTube import enabled and a 30–60 second test MP4. It creates a test project and deletes it on successful completion; failed runs may leave a test project in that isolated database.

Example (install test tools outside the app):

```sh
npm install --prefix /tmp/shorts-ui-tools playwright
# In a separate terminal, from backend/:
DATABASE_URL=sqlite+pysqlite:////tmp/shorts-ui-test.db \
MEDIA_DIR=/tmp/shorts-ui-media YOUTUBE_IMPORT=true \
uv run --locked uvicorn app.main:app --host 127.0.0.1 --port 8011
# From the repository root, with make frontend running on port 3000:
PLAYWRIGHT_MODULE=/tmp/shorts-ui-tools/node_modules/playwright \
BACKEND_TEST_URL=http://127.0.0.1:8011 \
VIDEO_TEST_FILE=/absolute/path/to/test-video.mp4 \
node frontend/browser-smoke.cjs
```

`CHROME_PATH` overrides the default macOS Chrome executable. `FRONTEND_TEST_URL` and `SCREENSHOT_DIR` can override the frontend URL and screenshot directory. Screenshots and test media should stay outside the repository.

## Social publishing

`social-ui.js` contains Social Accounts, the review/editor screen and shared publishing history. The page controller and HTTP client handle OAuth popup validation, cached/edited copy, explicit approval, real batch polling and failed-only retries. Settings → Social Accounts manages connections; rendered Shorts link to **Review & publish**. Native YouTube, TikTok and X drafts are separate. No platform is selected automatically. TikTok settings come from live creator info, with no initial privacy choice and separate required consent. AI generation requires review of the saved transcript because the existing transcription engine is a simulator.

Configure platform credentials/flags, a copy model and the worker as described in [social publishing setup](../docs/social-publishing.md). `social-browser-smoke.cjs` adds mocked provider publishing checks to the optional browser regression; no external content is posted.
