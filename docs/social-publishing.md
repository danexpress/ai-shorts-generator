# Social publishing: setup and operation

Creators connect their YouTube, TikTok, X and Instagram accounts. They review AI-written or their own copy for each platform, and publish a rendered Short now or at a scheduled time. Publishing runs in a background worker, one durable job per platform, with safe retries and a status and link for every post.

This guide covers:
1. [What you need first](#1-what-you-need-first)
2. [Setting up each platform's developer app](#2-setting-up-each-platforms-developer-app)
3. [Configuration reference](#3-configuration-reference)
4. [Using it](#4-using-it)
5. [How publishing works](#5-how-publishing-works)
6. [Security](#6-security)
7. [Adding or replacing a platform adapter](#7-adding-or-replacing-a-platform-adapter)
8. [Testing and what mocks can't prove](#8-testing-and-what-mocks-cant-prove)

Sign-in itself (Google Sign-In, sessions, CSRF) is documented in the [backend README](../backend/README.md#authentication). No developer credentials or platform approvals are included in this repository.

## 1. What you need first

- **Public HTTPS URLs.** Platforms redirect to `BACKEND_PUBLIC_URL/v1/social-accounts/<platform>/callback` after a creator approves access.
  - TikTok and Instagram reject HTTP redirects even for local testing, so use an HTTPS tunnel or domain. YouTube and X accept `http://localhost` only with `SOCIAL_ALLOW_INSECURE_LOCALHOST=true`.
  - Instagram also downloads the video from `BACKEND_PUBLIC_URL`, so that address must be reachable from the internet.
- **An encryption key** for stored platform tokens. Generate one once from `backend/` with `uv run --locked python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`, keep it in a secret manager, and set it as `SOCIAL_TOKEN_ENCRYPTION_KEYS`. AWS KMS is an option; see [Security](#6-security).
- **The publishing worker.** Run `make worker` in a second terminal next to `make run`, with the same environment, database and media directory. `make run` already starts the video processing worker; `make worker` adds the social publisher.
- **An environment file.** Copy [`backend/.env.social.example`](../backend/.env.social.example) to `backend/.env`, which Git ignores. The app doesn't load it automatically, so before `make run` and `make worker` run `set -a; source backend/.env; set +a`.

Each platform stays switched off until you set `ENABLE_<PLATFORM>_PUBLISHING=true` and its credentials. Until then, Settings → Social Accounts shows it as not yet enabled.

## 2. Setting up each platform's developer app

| Platform | Developer portal | Redirect URI to register | Scopes | Review needed before public use |
| --- | --- | --- | --- | --- |
| YouTube | [Google Cloud console](https://console.cloud.google.com/) | `…/v1/social-accounts/youtube/callback` | `youtube.upload`, `youtube.readonly` | OAuth consent verification, and a YouTube API audit for public videos |
| TikTok | [TikTok for Developers](https://developers.tiktok.com/) | `…/v1/social-accounts/tiktok/callback` (HTTPS) | `user.info.basic`, `video.publish` | App review and the Direct Post audit |
| X | [X Developer Console](https://developer.x.com/) | `…/v1/social-accounts/x/callback` | `users.read`, `tweet.read`, `tweet.write`, `media.write`, `offline.access` | No review; a paid API plan with media upload and posting |
| Instagram | [Meta for Developers](https://developers.facebook.com/) | `…/v1/social-accounts/instagram/callback` (HTTPS) | `instagram_business_basic`, `instagram_business_content_publish` | Meta App Review (Advanced Access) and business verification |

`…` stands for your `BACKEND_PUBLIC_URL`. Register the URI exactly, character for character.

The "approved" flags (`YOUTUBE_PUBLISHING_VERIFIED`, `TIKTOK_PUBLISHING_AUDITED`, `INSTAGRAM_PUBLISHING_APPROVED`) tell the app that a platform's review has passed. Setting them doesn't grant approval: set them only after the platform has approved your app. Until then the app restricts posting and shows creators a notice instead of failing.

### YouTube

1. In the Google Cloud console, create (or pick) a project and enable the **YouTube Data API v3**.
2. Configure the **OAuth consent screen**: app name, support email, privacy policy and terms URLs, the two scopes below, and test users while the app is in *Testing*.
3. Create an **OAuth client ID** of type **Web application**, and add the redirect URI under *Authorized redirect URIs*.
4. Set `YOUTUBE_CLIENT_ID` and `YOUTUBE_CLIENT_SECRET` to the Web client's ID and secret, then `ENABLE_YOUTUBE_PUBLISHING=true`.

- **Scopes:** `https://www.googleapis.com/auth/youtube.upload` to upload, and `https://www.googleapis.com/auth/youtube.readonly` to read the connected channel and check processing. The app requests offline access, so it gets a refresh token. If the creator signed in with Google, `include_granted_scopes` adds YouTube on top of that sign-in (incremental authorization).
- **Review:**
  - `youtube.upload` is a *sensitive* scope. Until Google verifies your OAuth consent screen, only test users can connect, and they see an "unverified app" warning.
  - Separately, videos uploaded by API projects that haven't passed the YouTube API Services audit are locked to private. The app therefore forces private uploads and shows a notice until you set `YOUTUBE_PUBLISHING_VERIFIED=true`.
- **Quota:** shared by all creators of the project. By default you get 100 uploads a day, plus 10,000 units a day for other calls, reset at midnight Pacific; see [How publishing works](#5-how-publishing-works). Request more through Google's quota extension form, then raise `YOUTUBE_DAILY_UPLOADS` and `YOUTUBE_DAILY_UNITS`.
- **Shorts:** uploads must be vertical and at most 3 minutes, and the app adds `#Shorts`.

### TikTok

1. In TikTok for Developers, register a **web** app and add the **Login Kit** and **Content Posting API** products, with **Direct Post** enabled.
2. Register the redirect URI. TikTok requires HTTPS here, even while testing.
3. Request the scopes `user.info.basic` and `video.publish`.
4. Set `TIKTOK_CLIENT_ID` to the app's **client key** (TikTok's name for the client ID) and `TIKTOK_CLIENT_SECRET`, then `ENABLE_TIKTOK_PUBLISHING=true`.

- **Review:**
  - Until app review and the Direct Post audit pass, TikTok only lets your app post to private accounts with *Only me* (`SELF_ONLY`) visibility, and limits how many users and posts it allows.
  - The app offers only `SELF_ONLY` and shows a notice until you set `TIKTOK_PUBLISHING_AUDITED=true`.
  - The audit checks TikTok's required posting experience, which this app implements: creator info is fetched before showing settings, there's no default privacy, explicit consent and commercial-content disclosure are required, and AI-generated content can be labelled. It must be a creator-facing product, not an internal uploader.
- **Limits:** privacy options and maximum duration come live from each creator's account and are checked at admission and again when the post is sent. A private post can succeed without a public link; the app reports it as published without inventing a URL.

### X

1. In the X Developer Console, create a project and app. Enable **OAuth 2.0** user authentication as a **Web App** (confidential client) with **Read and write** permissions.
2. Register the callback URL and a website URL.
3. Make sure your plan includes **media upload and post creation**, and set up billing in the portal.
4. Set `X_CLIENT_ID` and `X_CLIENT_SECRET` to the OAuth 2.0 client ID and secret, then `ENABLE_X_PUBLISHING=true`.

- **Scopes:** `users.read`, `tweet.read`, `tweet.write`, `media.write`, and `offline.access` (for refresh tokens). The app uses PKCE and never an app-only token.
- **Review:** none, but access depends on your paid plan. Set `X_COST_PER_POST` to what your plan charges per post, so the job logs show the real cost.
- **Duplicate safety:** creating a post on X isn't idempotent. The app records that it is about to post before calling X. If the outcome is unclear (a timeout or crash), the job is marked `PUBLISH_UNCERTAIN` and never retried blindly; the creator checks X instead.

### Instagram

1. In Meta for Developers, create an app of type **Business**, add the **Instagram** product, and choose **API setup with Instagram login**.
2. Under *Business login settings*, register the HTTPS redirect URI.
3. Add the Instagram accounts you'll test with as **Instagram testers** (app roles).
4. Set `INSTAGRAM_CLIENT_ID` and `INSTAGRAM_CLIENT_SECRET` to the **Instagram app ID and secret** shown on that screen (not the Facebook app's), then `ENABLE_INSTAGRAM_PUBLISHING=true`.

- **Scopes:** `instagram_business_basic` to read the account, and `instagram_business_content_publish` to publish Reels.
- **Review:** until **App Review** grants Advanced Access to both permissions (which requires **business verification**), only accounts with a role on your app can connect. The app shows a notice until you set `INSTAGRAM_PUBLISHING_APPROVED=true`.
- **Accounts:** only Business and Creator accounts can publish; personal accounts are refused when connecting.
- **Video delivery:** Instagram downloads the video itself. For each post the worker gives it a signed link to the render that expires after one hour and is never logged, so `BACKEND_PUBLIC_URL` must be public HTTPS.
- **Limits checked before upload:** MP4/MOV, H.264 or HEVC with AAC, 23–60 fps, 3 s to 15 min, at most 300 MB and 1,920 pixels wide. Captions are limited to 2,200 characters, 30 hashtags and 20 @mentions. Posting is capped at 100 API posts per account per rolling 24 hours, checked before each post; a post over the limit waits.
- **Tokens:** long-lived tokens last 60 days and renew when used in their final week, so an account unused for 60 days must reconnect. Instagram Login has no revocation endpoint: disconnecting deletes the stored tokens, and the creator removes the app under Instagram *Settings → Apps and websites*.

## 3. Configuration reference

All values are server-side environment variables. The platform credentials and flags follow the patterns `<PLATFORM>_CLIENT_ID`, `<PLATFORM>_CLIENT_SECRET` and `ENABLE_<PLATFORM>_PUBLISHING`, for `YOUTUBE`, `TIKTOK`, `X` and `INSTAGRAM`.

| Variable | Default | Purpose |
| --- | --- | --- |
| `BACKEND_PUBLIC_URL` / `FRONTEND_PUBLIC_URL` | `http://127.0.0.1:8000` / `:3000` | Public addresses: callbacks, media links, redirects back to the app |
| `SOCIAL_TOKEN_ENCRYPTION_KEYS` | none | Comma-separated Fernet keys; the first encrypts, all decrypt |
| `TOKEN_KMS_KEY_ID`, `TOKEN_KMS_REGION` | none | Optional AWS KMS envelope encryption (`uv sync --extra kms`) |
| `ENABLE_<PLATFORM>_PUBLISHING` | `false` | Turn a platform on once its app is configured |
| `<PLATFORM>_CLIENT_ID`, `<PLATFORM>_CLIENT_SECRET` | none | Developer app credentials (see each platform above) |
| `YOUTUBE_PUBLISHING_VERIFIED`, `TIKTOK_PUBLISHING_AUDITED`, `INSTAGRAM_PUBLISHING_APPROVED` | `false` | Set only after that platform's review passed; lifts private-only or tester-only mode |
| `YOUTUBE_DAILY_UPLOADS`, `YOUTUBE_DAILY_UNITS` | `100`, `10000` | Your project's YouTube quota |
| `X_COST_PER_POST` | `0` | USD per X post, for cost logs |
| `SOCIAL_ADAPTER_<PLATFORM>` | built-in | Replace a platform's adapter (`package.module:Class`) |
| `SOCIAL_COPY_API_URL`, `SOCIAL_COPY_API_KEY`, `SOCIAL_COPY_MODEL` | none | Optional AI copy model (strict JSON-schema Chat Completions) |
| `SOCIAL_ALLOW_INSECURE_LOCALHOST` | `false` | Allow `http://localhost` callbacks for YouTube and X in development |

Keep `DATABASE_URL`, `MEDIA_DIR`, the encryption keys, credentials, flags and public URLs identical for the API and both workers. The workers need the actual rendered files, not just the database.

## 4. Using it

1. **Connect:** Settings → Social Accounts → *Connect* for each platform. Disconnecting deletes the stored tokens and asks the platform to revoke them; for Instagram the creator removes the app instead.
2. **Compose:** open a rendered Short → *Review & publish*. No platform is preselected.
   - *Generate* writes platform-native copy from the clip's transcript; creators confirm the transcript first. Each draft can be edited and regenerated on its own.
   - Then set visibility and the required settings: YouTube visibility and cover, TikTok privacy and consent, Instagram *Also show in profile feed*.
3. **Publish or schedule:** choose *Post now* or *Schedule* (a date and time in your timezone, 1 minute to 30 days ahead), then click *Publish Everywhere* or *Schedule Posts*. Nothing is ever posted without that click.
4. **Follow progress:** each platform shows *Scheduled → Waiting → Uploading → Processing → Creating post → Published* (with a link) or *Failed* (with a reason).
   - Failed jobs that are safe to retry get a *Retry* button.
   - Scheduled posts can be rescheduled or cancelled until they start.
   - History stays after the render expires.

The API is documented in [`openapi.yaml`](../openapi.yaml). `POST /v1/renders/{id}/publish` needs `approved: true` and an `Idempotency-Key`. It returns **202** with a batch at once and never uploads inside the request. Sending the same key again returns the same batch; changed content needs a new key.

## 5. How publishing works

- **Jobs:** one job per render, platform and account, so a Short can't be posted twice to the same account. Each job advances one step at a time and saves an encrypted checkpoint between steps (for example after each upload chunk), so a restarted worker carries on where it left off.
- **Leases:** workers claim jobs with short leases, and token refreshes are serialized per account.
- **Retries:** temporary failures (network errors, 5xx, rate limits) retry with exponential backoff, starting at 15 seconds and capped at 15 minutes, for up to 4 attempts. Permanent failures (rejected media, invalid captions, missing permissions, revoked access) fail at once with a clear reason.
- **Never posting twice:** platforms without idempotent post creation (X's post, Instagram's `media_publish`) are marked `PUBLISH_UNCERTAIN` when the outcome is unclear, and never retried blindly. YouTube and TikTok uploads reconcile with the platform instead. A job unresolved 24 hours after it was due is marked uncertain.
- **Validation:** each platform's video and caption limits are checked when you click publish and errors are shown immediately. They're checked again before uploading.
- **Scheduling:** scheduled jobs wait as `scheduled` and run as normal at their time. The render is kept until a day after the post time. Cancelling deletes the never-started jobs, so nothing reaches the platform and the Short can be posted again.
- **Quotas:** adapters declare daily quota buckets and per-call costs, and the worker reserves a call's cost before making it. When a bucket is full, or the platform reports a quota error, the job stays queued until just after the reset, with a note, and doesn't use up a retry.
  - YouTube: 100 `videos.insert` calls a day, plus 10,000 units a day (status checks 1, cover images 50), reset at midnight Pacific.
  - Instagram: checks its rolling 100-post limit before each post.
- **`#Shorts`:** added to YouTube descriptions, and to the title when it fits within 100 characters, unless already present.
- **Logs:**
  - one line per step: `publish job=… platform=x step=publish status=processing attempt=1 took=0.8s`;
  - a summary when a job finishes: `result=published attempts=1 total=42.0s api_cost_usd=0.1500`;
  - or a warning when it fails: `result=failed error=MEDIA_REJECTED`.

  Lines contain only IDs, platforms, statuses, error codes, durations and costs.

## 6. Security

- **Tokens at rest:** encrypted with Fernet and bound to their creator and platform, so a value copied to another account won't decrypt.
  - Rotating keys: put the new key first in `SOCIAL_TOKEN_ENCRYPTION_KEYS` and keep old keys until tokens have refreshed. Losing all keys means accounts must reconnect.
- **AWS KMS (optional):** set `TOKEN_KMS_KEY_ID` after `uv sync --extra kms`.
  - New values are encrypted with a KMS data key (AES-256, encryption context `purpose=ai-shorts-social-tokens`) and stored as `kms1:<wrapped key>.<token>`.
  - Each process reuses a data key for up to 5 minutes or 10,000 values, so uploads don't call KMS for every checkpoint.
  - If KMS is unavailable, operations fail closed and nothing is stored in plaintext.
  - Existing Fernet values keep working, so keep `SOCIAL_TOKEN_ENCRYPTION_KEYS` until they've been refreshed.
  - Grant `kms:GenerateDataKey` and `kms:Decrypt` on the key, ideally with `"Condition": {"StringEquals": {"kms:EncryptionContext:purpose": "ai-shorts-social-tokens"}}`.
  - This path is tested against a fake KMS and its API names checked against boto3's KMS model; it hasn't run against a live AWS account.
- **OAuth:**
  - The `state` is single-use, expires, and is bound to the browser that started the connection.
  - Callbacks return static HTML and never tokens.
  - Code and state values are removed from the app's access logs. Configure your reverse proxy and APM to redact callback queries, authorization headers and request bodies too.
- **Requests:** social endpoints accept the browser's cookie session (writes need the CSRF token and an allowed Origin) or a bearer token. Every request and worker step re-checks ownership and that the user is active.
  - Publishing, retries, connecting and copy generation are limited to 6 requests per minute per creator and action.
- **Data handling:**
  - Provider hosts are allowlisted and redirects disabled. Logs never contain tokens, captions or media links.
  - Deleting a project cancels its posts and removes private drafts and checkpoints. Deleting an account removes everything, including tokens, and asks each platform to revoke access.
  - Posts already published on platforms are never deleted.

## 7. Adding or replacing a platform adapter

Every platform is an adapter implementing `SocialPublisher` (`backend/app/social/adapters/base.py`). Routes, jobs, the worker and the UI only call this interface:

| Method | Purpose |
| --- | --- |
| `connect(state, verifier)` → URL | Start linking an account (OAuth with PKCE by default) |
| `exchange(code, verifier)` → `Tokens`, `identity(access)` → `Identity` | Finish linking; who was linked |
| `refresh_token(refresh)` → `Tokens` | Renew tokens before they expire (`refresh_ahead_seconds` sets how early) |
| `validate(video, metadata, creator)` | Check format, duration, size and caption limits; raise `ProviderError` |
| `publish(token, video, info, metadata, checkpoint, media_url)` → `PublishStep` | Advance one step and return the checkpoint to save; the final step carries `post_id` and `post_url` |
| `get_status(token, checkpoint)` → `PublishStep` | Poll processing |
| `disconnect(access)` | Revoke access at the platform |

Optional hooks with safe defaults:
- `needs_creator_info` / `creator_info()`: live per-account options.
- `upload_complete(checkpoint)`: media fully transferred.
- `create_post()`: called when a step returns `publishing`, behind the never-repeat safeguard.
- `supports_thumbnail` / `set_thumbnail()`.
- `needs_media_url`: the worker passes a one-hour signed public link.
- `quota_limits()` / `quota_costs()` / `quota_timezone`.
- `cost_per_post_usd`.
- `requires_client_credentials`.

Raise `ProviderError(code, retryable=True)` for transient failures, and `uncertain=True` when a post may already exist.

To replace a built-in adapter, for example with a unified posting API serving several platforms, set `SOCIAL_ADAPTER_<PLATFORM>=package.module:ClassName` for each platform it serves. One class may serve several platforms (each instance knows its platform), and it must subclass `SocialPublisher`; a wrong path fails at startup. Set `requires_client_credentials = False` if it uses its own API key. `backend/tests/test_social_registry.py` runs a full connect → publish → published flow through such an adapter.

Adding a new platform also needs its name in `PLATFORMS` (`app/social/config.py`), its copy model in `COPY_MODELS` and post model on `SocialPublishTarget` (`app/social/models.py`), its built-in adapter in `BUILTIN` (`app/social/adapters/__init__.py`), and its composer fields in `frontend/social-ui.js`.

## 8. Testing and what mocks can't prove

`make test`, `make test-frontend` and `make lint` cover:
- OAuth state, replay and browser binding;
- token encryption, KMS, refresh and disconnect races;
- each adapter's request shapes against fake platform APIs;
- copy generation, caching and review;
- partial rejection, restarts and leases, retries and caps, uncertain posts and duplicate prevention;
- scheduling, quotas, logging, project and account deletion, permissions and rate limits.

`frontend/browser-smoke.cjs` and `social-browser-smoke.cjs` drive a real browser against mocked platform responses and never post.

Mocks can't prove that your developer apps are approved or that live posting works. Before a public launch:
- configure all four apps;
- run live private-post, refresh and revoke checks with consenting test accounts;
- complete each platform's review.

## References

Checked September 30, 2026 (YouTube, TikTok, X) and November 3, 2026 (YouTube quota, Instagram):
- YouTube: [videos.insert](https://developers.google.com/youtube/v3/docs/videos/insert), [resumable uploads](https://developers.google.com/youtube/v3/guides/using_resumable_upload_protocol), [quota costs](https://developers.google.com/youtube/v3/determine_quota_cost), [thumbnails.set](https://developers.google.com/youtube/v3/docs/thumbnails/set).
- TikTok: [token management](https://developers.tiktok.com/docs/en/oauth-user-access-token-management), [content sharing guidelines](https://developers.tiktok.com/doc/content-sharing-guidelines), [Direct Post](https://developers.tiktok.com/doc/content-posting-api-reference-direct-post), [media transfer](https://developers.tiktok.com/doc/content-posting-api-media-transfer-guide), [post status](https://developers.tiktok.com/doc/content-posting-api-reference-get-video-status).
- X: [OAuth 2.0 authorization code](https://docs.x.com/fundamentals/authentication/oauth-2-0/authorization-code), media [initialize](https://docs.x.com/x-api/media/initialize-media-upload) / [append](https://docs.x.com/x-api/media/append-media-upload) / [finalize](https://docs.x.com/x-api/media/finalize-media-upload) / [status](https://docs.x.com/x-api/media/get-media-upload-status), [video best practices](https://docs.x.com/x-api/media/quickstart/best-practices).
- Instagram (Graph API v26.0): [business login](https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login), [content publishing](https://developers.facebook.com/docs/instagram-platform/content-publishing), [media and Reels specs](https://developers.facebook.com/docs/instagram-platform/instagram-graph-api/reference/ig-user/media).
