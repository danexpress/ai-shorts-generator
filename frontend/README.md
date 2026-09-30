# Shorts Studio frontend

The existing static HTML application uses its React-backed DC runtime, with no build step. API calls and workflow state remain in `AI Shorts Generator.dc.html`; `services.js` provides the HTTP client and mock test service. `support.js` is the existing generated runtime.

Run `make run` and `make frontend` from the repository root in separate terminals. Open http://127.0.0.1:3000. Configure the API origin in `config.js`. Demo credentials and backend feature flags are documented in `../backend/README.md`.

Login survives page refresh in the same tab. The HTTP client saves only the bearer token and its expiration in browser `sessionStorage`, scoped to the API URL; passwords and account data are not stored. On startup, the app validates the saved token with `/v1/me` before opening the dashboard. Sessions still expire after one hour. Logout and authentication errors clear the saved token; closing the tab ends browser session storage. A connection failure offers a retry without discarding a still-valid token. When browser storage is blocked, login works in memory for the current page.

## Presentation architecture

- `app.css`: centralized light/dark tokens, 8px spacing scale, shared controls, screen layouts, breakpoints, focus states, reduced-motion behavior.
- `components.js`: reusable React presentation components and screen composition. `ShortsUI.App` receives view data and callbacks from the page controller using the runtime's existing `x-import` mechanism.
- Shared components include AppSidebar, PageHeader, ProjectTable, ClipCard, ScoreIndicator, VideoPreview, ProcessingStatus, UploadDropzone, CaptionPresetCard, UsageIndicator, EmptyState, ErrorState, and StatusBadge.
- `industry.css` and the `_ds/` artifacts remain for the existing standalone service diagnostics page.

The main workflow is Dashboard → New Project → Processing → Best moments → Preview → Rendered Short. Projects, Usage, Settings, transcript editing, regeneration, cancellation, retry, and existing downloads remain accessible. Appearance is a browser preference, not an account setting. Mobile navigation collapses below 900px; editing panels stack below 640px.

Video previews preserve the full source frame. Clip previews stop at the selected interval. Render quality supports the backend's 1080p and 720p options. Processing shows actual stage changes; uploads use an indeterminate transfer state because the fetch client does not expose reliable byte progress. Project thumbnails use real source playback links, with placeholders when source media is unavailable.

## Backend capability boundaries

The existing API renders MP4 videos and exports TXT/JSON transcripts. Caption styles, burned-in hook text, watermark rendering, cover/SRT/ASS downloads, and generated platform metadata are not implemented by the API. Their requested UI surfaces are clearly marked unavailable. Unavailable caption/branding controls and additional downloads sit in compact disclosures so the primary workflow stays clear. The suggested hook can be copied for publishing; the UI does not fabricate platform-specific metadata or imply that disabled settings affect the exported video.

Source retention is 24 hours; rendered files are retained for 7 days; analysis for 30 days. The upload contract accepts MP4/MOV/WebM, 30 seconds–3 hours, up to 4 GB. YouTube import appears only when the backend enables it (`YOUTUBE_IMPORT=true`).

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
