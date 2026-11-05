# AI Shorts Generator

Turns long videos into vertical Shorts. It transcribes uploads locally with Whisper, has Claude pick the most engaging moments, and renders 1080×1920 clips with karaoke captions. **Story Mode** turns a topic or a pasted story into a narrated Short: a Claude-written script, a Kokoro or ElevenLabs voiceover, an AI picture per scene (local FLUX or OpenAI) with slow pan and zoom, or a background loop, captions and optional music. Creators sign in with Google, then publish to YouTube, TikTok, X and Instagram now or on a schedule.

## Quick start

```sh
make install          # backend dependencies (uv); the frontend needs no build
make run              # API + video processing worker
make frontend         # web app on http://127.0.0.1:3000, in a second terminal
make worker           # social publishing worker, only if publishing is configured
```

Set `ANTHROPIC_API_KEY` for highlight detection, or `HIGHLIGHT_MODEL=demo` to try it without one. Run `make help` for every target. Check changes with `make lint`, `make test` and `make test-frontend`.

## Documentation

| Topic | Where |
| --- | --- |
| Running the backend, Google Sign-In, account deletion, transcription, captions, highlight detection, Story Mode (adding voices and genres) | [backend/README.md](backend/README.md) |
| Frontend structure and browser tests | [frontend/README.md](frontend/README.md) |
| Social publishing: developer app setup for each platform (redirect URIs, scopes, env vars), platform reviews, scheduling, quotas, security | [docs/social-publishing.md](docs/social-publishing.md) |
| Environment templates | [backend/.env.example](backend/.env.example), [backend/.env.social.example](backend/.env.social.example) |
| HTTP API | [openapi.yaml](openapi.yaml) |
| Design decisions | [docs/decisions/](docs/decisions/) |
| Original product spec | [docs/spec.md](docs/spec.md) |
