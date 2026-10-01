# Social publishing implementation

This module adds social accounts and publishing to stored renders without replacing upload, analysis, rendering, passwords, or bearer authentication. The repository currently simulates transcription/analysis and does not have a live copy model or cover/subtitle rendering; publishing must not claim those capabilities exist.

## Architecture and phases

1. Account foundations: additive SQLAlchemy tables, user/platform-bound Fernet token encryption, one-use state with browser binding, social-account settings UI.
2. YouTube: offline user OAuth, resumable uploads and processing confirmation.
3. TikTok: web OAuth, live creator settings, direct file transfer and asynchronous status.
4. X: user OAuth with PKCE, v2 chunked media and post creation.
5. Structured platform copy, render-context snapshots and cached generation.
6. Durable database-backed batches and worker jobs.
7. Per-platform safe retries, capped backoff and duplicate prevention.
8. Copy review, explicit approval, progress and retained history.
9. Security, mocked provider integration and browser regressions.

New tables: social_accounts, social_oauth_states, render_social_context, social_copy, publish_batches, publish_jobs, social_events, social_rate_limits, social_refresh_leases. History retains render IDs as identifiers so normal render expiration does not erase publication records. OAuth secrets and upload capabilities are encrypted; public response schemas use explicit allowlists.

## Developer portal configuration

All flags default off. Register callbacks exactly as `BACKEND_PUBLIC_URL/v1/social-accounts/{youtube|tiktok|x}/callback`. Use HTTPS for deployed backend and frontend URLs. TikTok web OAuth requires HTTPS, including testing; use an HTTPS tunnel/domain. The optional local HTTP setting is only for YouTube/X loopback development.

- Google Cloud: enable YouTube Data API; create a Web OAuth client and configure consent screen, privacy policy, test users and authorized redirect URI. Scopes: `https://www.googleapis.com/auth/youtube.upload`, `https://www.googleapis.com/auth/youtube.readonly`. Offline access permits refresh. API upload verification/audit and OAuth consent verification are separate. Private-only mode stays enabled unless deployment explicitly declares verification.
- TikTok for Developers: register a web app, Login Kit and Content Posting API with `user.info.basic` and `video.publish`, and register the HTTPS redirect. Complete app review/Direct Post audit before public beta. Unreviewed clients are limited to private accounts and SELF_ONLY, with provider user/post caps. Query creator info before showing and submitting publishing settings. This must be a creator-facing product, not an internal uploader or arbitrary cross-platform copying utility.
- X Developer Console: configure a confidential Web App with user-context OAuth 2/PKCE. Request `users.read`, `tweet.read`, `tweet.write`, `media.write`, `offline.access`. Register the callback and confirm media/post API access and billing in the portal. Do not substitute an app-only token.

No developer credentials or platform approvals are present in the repository. Mock integration tests cannot prove app approval or live posting.

## Official references checked September 30, 2026

- [YouTube uploads and project restrictions](https://developers.google.com/youtube/v3/docs/videos/insert)
- [YouTube resumable protocol](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol)
- [TikTok login and token refresh](https://developers.tiktok.com/docs/en/oauth-user-access-token-management)
- [TikTok required UX and audit](https://developers.tiktok.com/doc/content-sharing-guidelines)
- [TikTok direct post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post)
- [TikTok transfer limits](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide)
- [TikTok status, including private posts without public IDs](https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status)
- [X user OAuth](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code)
- [X v2 media initialization](https://docs.x.com/x-api/media/initialize-media-upload)
- [X official media/OAuth example](https://github.com/xdevplatform/samples/blob/main/python/media/media_upload_v2.py)
- [X video best practices and account limits](https://docs.x.com/x-api/media/quickstart/best-practices)

## Running and configuring

Use [`backend/.env.social.example`](../backend/.env.social.example) as a template for `backend/.env` (ignored by Git). Values are server-side only. The app reads exported environment variables; it does not silently load this file. From the repository root, in **both** the API and worker terminals:

```sh
set -a
source backend/.env
set +a
make run     # API terminal
# make worker # separate worker terminal, with the same exported environment
```

Keep `DATABASE_URL`, `MEDIA_DIR`, the encryption keys, client credentials, rollout flags and public URLs identical in both processes. Workers require shared access to the actual rendered files, not just the database. Start the API once to initialize new tables before starting workers. Existing databases receive additive social tables via `create_all`; manage schema changes with deployment migrations thereafter. All ORM tables compile for PostgreSQL; SQLite-specific settings remain in the existing database module.

Generate a persistent Fernet key once (from `backend/`):

```sh
uv run --locked python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Store it in your secret manager, never commit it. `SOCIAL_TOKEN_ENCRYPTION_KEYS` supports comma-separated keys: first encrypts new values, all decrypt older values. Keep old keys until existing account tokens and checkpoints have been re-encrypted or accounts reconnected. Losing the keys requires reconnecting accounts; replacing them at every restart breaks saved connections.

Set each `ENABLE_*_PUBLISHING=true` only after its corresponding app credentials and registered callback are ready. `YOUTUBE_CLIENT_ID` is the Google Web client ID. `TIKTOK_CLIENT_ID` is TikTok's **client key**. `X_CLIENT_ID` is the confidential OAuth 2 client ID. Verification/audit flags describe actual portal approval; setting them does not grant provider approval. Private visibility remains the default for YouTube; TikTok requires an explicit selection from live creator options and separate posting consent.

Configure `SOCIAL_COPY_API_URL`, `SOCIAL_COPY_API_KEY`, and `SOCIAL_COPY_MODEL` for a model supporting strict JSON-schema Chat Completions. No model or paid account is assumed. The clip transcript and context are sent to that configured provider only when generating/regenerating copy. No video bytes are sent to the copy model. Generation has typed validation, refusal handling, length checks and caches separate per-platform drafts. Model services can still produce factual errors: creators must review their copy.

**Current transcript limitation:** the original app simulates transcription and analysis. The publishing screen shows the saved clip transcript and requires creator confirmation that it matches the actual video before the backend accepts AI generation. Correct the transcript in the existing editor and re-render to update a render's snapshot, or write manual copy. This feature does not make the original transcript simulator production-ready.

## Workflow and API

Settings → Social Accounts connects/disconnects each platform. Open an existing rendered Short → **Review & publish**. No platform is preselected. Review/edit native copy, choose visibility and required settings, then explicitly click **Publish Everywhere**. Rendering never sends a post automatically. The optional YouTube cover uses the approved video's first frame; custom-cover rejection leaves the video successfully published and shows a clear notice. It does not add cover downloads to the original pipeline. [YouTube's official custom thumbnail API](https://developers.google.com/youtube/v3/docs/thumbnails/set) remains subject to account permissions; Shorts surfaces may not display the cover.

[`openapi.yaml`](../openapi.yaml) documents all existing and social endpoints. Social calls require the existing bearer session, except the state/browser-bound OAuth callback. The callback returns static HTML, never tokens, and consumes state before provider exchange. Disconnect can cancel an exchange in flight. `POST /v1/renders/{id}/publish` requires `approved:true` and an `Idempotency-Key`, returns **202** with a durable batch immediately after validation, and never uploads video inside that request. Each target references a social account owned by that creator. Reuse the key for the identical request after a lost response; different payloads require a new key.

The database permits one job per render/platform/external account. Further publishing requests for that target point the creator to history rather than producing another post. Batches are independent: admission can record a rejected target while queueing valid targets. `GET /v1/publish-batches/{id}` and rendered-Short history expose real states only. A failed-only retry endpoint requeues exactly one eligible job; successful platforms are never retried. Reconnecting must use the same external account to resume a failed job.

## Recovery, security and deployment

Workers claim short database leases, save encrypted upload checkpoints between chunks, refresh expiring tokens behind a separate connection lease, and recover jobs after restarts. Temporary failures use capped exponential backoff (four attempts, starting at 15 seconds). Authentication, invalid metadata, policy/media rejection and missing scopes are not automatically retried. Provider processing remains `processing` until a success state is confirmed; after 24 hours unresolved jobs require checking the provider.

Exactly-once delivery cannot be guaranteed where a provider lacks post idempotency. In particular, X post creation persists a `post_request_started` marker **before** sending the non-idempotent request. A timeout, ambiguous server response or crash after that marker produces `PUBLISH_UNCERTAIN` and disables automatic/manual blind retry. The creator must check the external account. YouTube reconciles resumable-upload completion; TikTok reconciles transfer/status with its publish ID. Private TikTok success can legitimately have no public post ID/link; the UI reports confirmed publication without inventing a URL.

Deployment must terminate HTTPS, use matching frontend/backend origins and an explicit `CORS_ORIGINS` allowlist, protect the database/key backups, and use same-site API routing for OAuth browser cookies. `SOCIAL_ALLOW_INSECURE_LOCALHOST` is a development exception for YouTube/X only. Social callbacks redact code/state query strings from app access logs; configure your reverse proxy/APM to redact callback queries, authorization headers and request bodies too. HTTP debug logs are suppressed for provider transport. Never enable payload logging around OAuth or copy APIs.

Tokens are encrypted and bound to their creator/platform; serializers expose profile fields only. Disconnect deletes saved credentials even when provider revocation fails. Upload URLs are provider-host allowlisted, redirects are disabled, stored render paths are server-owned, and ownership/active-user checks run at admission and worker execution. Publishing, retries, connecting and copy generation have creator-specific database rate limits. Inputs reject control characters and invalid/overlong platform metadata. Project deletion cancels social jobs and purges saved private copy/checkpoints; it deliberately does not delete already-created external posts. History survives normal render expiration. Completed jobs discard upload capabilities.

`social_events` stores the requested analytics events with event name, creator ID, platform, resource ID and time only. No tokens, full captions, transcripts or video content are included. Posting metadata/drafts are normal private application records, not analytics.

## Verification and remaining live setup

Run `make test`, `make test-frontend`, and `make lint`. Backend tests exercise OAuth state/replay/browser binding, encryption/refresh/disconnect races, official adapter request shapes, structured copy/refusals/cache/regeneration/review, one/two/three targets, partial rejection, restarts/leases, safe retries/caps, uncertain X posts, duplicate prevention, expiry/history, project deletion, permissions and rate limits. The frontend controller tests verify explicit approval, selected-only submission, idempotency reuse, isolated regeneration/retries and logout races.

The optional `frontend/browser-smoke.cjs` runs the original real upload/render/download workflow, then `social-browser-smoke.cjs` exercises review/editing, TikTok privacy/consent, processing, partial failure, failed-only retry, success links/history and mobile layout using mocked provider responses. It never creates external posts. See the frontend README for isolated test-server setup.

Before a live beta, configure the three developer apps, required scopes/callbacks, HTTPS public endpoints, encryption key and copy model, run the worker, and finish the applicable approvals. Perform live private posting/refresh/revocation smoke tests with consenting test accounts. These credentials and audits cannot be created or verified by mocked tests.

Additional primary references: [OpenAI structured JSON output](https://developers.openai.com/api/docs/guides/structured-outputs), [X append](https://docs.x.com/x-api/media/append-media-upload), [X finalize](https://docs.x.com/x-api/media/finalize-media-upload), [X processing status](https://docs.x.com/x-api/media/get-media-upload-status).
