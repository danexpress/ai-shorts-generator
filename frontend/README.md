# Frontend

From the repository root, use two terminals:

```sh
make run       # FastAPI on http://127.0.0.1:8000
```

```sh
make frontend  # Frontend on http://127.0.0.1:3000
```

Open <http://127.0.0.1:3000> and sign in with `maya@example.com` / `DemoPass123!` to see seeded projects. Serve the page over HTTP rather than opening it as a local file.

The app uses `createApi({ mode: 'http' })`. `config.js` sets the API base URL; edit it if the backend runs on a different port. The backend allows frontend origins on port 3000 by default. For a different frontend port, also update the backend CORS configuration.

Password sign-in returns a bearer token. The client keeps it in memory, automatically adds it to requests, and clears it on logout or authentication failure. Refreshing the page requires sign-in again. YouTube availability comes from the backend's `features.youtubeImport` flag.

The backend stores uploaded source videos under `backend/media/sources/` by default (override with `MEDIA_DIR`). Clip cards and the preview dialog stream the source video using short-lived signed URLs and play the suggested time range; seeded demo projects have no source video attached. Processing still uses demo transcripts and suggestions, and final Short rendering is not implemented, so no rendered clips are saved yet.

Run `make test-frontend` for the service suite, authentication/page-logic tests, and an integration test against a temporary FastAPI process. Node.js and `make install` are required. The in-app Tests panel runs the isolated service suite, not live backend integration tests.
