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

New tables: social_accounts, social_oauth_states, render_social_context, social_copy, publish_batches, publish_jobs, social_events, social_rate_limits. History retains render IDs as identifiers so normal render expiration does not erase publication records. OAuth secrets and upload capabilities are encrypted; public response schemas use explicit allowlists.

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
