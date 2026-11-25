# autopost

Uploads your website's new clips to your YouTube channel without manual steps. It writes the metadata with Claude, uploads the clip and records the result.

```
inbox/clip.mp4 ─▶ sha256 ─▶ ffprobe ─▶ metadata ─▶ videos.insert ─▶ SQLite ─▶ posted/
                  (skip if    (reject     (sidecar, or     (resumable,               (or failed/ after
                   posted)     empty or    Claude, or       8 MB chunks,              3 attempts)
                               corrupt)    file name)       backoff)
```

**Only post clips you own or are licensed to use.** autopost has no downloading or scraping, and none should be added. Reposting other people's clips leads to copyright claims and "reused content" demonetization.

## Files

| File | Purpose |
|---|---|
| `autopost.py` | The whole tool: `auth`, `run`, `enqueue`, `status` |
| `requirements.txt` | Python dependencies (Python 3.11+) |
| `autopost.service` | systemd unit for `run --loop` |
| `test_autopost.py` | Dry-run and upload-path tests using a generated 3-second clip |
| `.env.example` | Environment variables; copy to `.env`, which git ignores |

## Install

```bash
cd autopost
uv venv && uv pip install -r requirements.txt      # or: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && chmod 600 .env            # add ANTHROPIC_API_KEY
```

You also need `ffmpeg` and `ffprobe` on the PATH (`brew install ffmpeg` or `apt install ffmpeg`).

## Google Cloud setup

1. **Create a project.** Go to <https://console.cloud.google.com/>, open the project picker and choose **New project**.
2. **Enable the API.** Open **APIs & Services → Library**, search for **YouTube Data API v3** and click **Enable**.
3. **Configure the OAuth consent screen.** Open **Google Auth Platform** (formerly **APIs & Services → OAuth consent screen**).
   - Branding: enter an app name (it may not contain "YouTube"), a support email and a developer contact.
   - Audience: choose **External**. Under **Test users**, add the Google account that owns your channel.
   - Data Access: add the scope `https://www.googleapis.com/auth/youtube.upload`.
   - While the app's publishing status is **Testing**, Google expires the refresh token **7 days** after you sign in, and autopost would stop with an auth error each week. For a tool only you use, click **Publish app** to move it to **In production**. You'll see an "unverified app" warning when you sign in. Click **Advanced → Go to (app)**. Without verification, sign-in is limited to 100 users, which is enough for one channel.
4. **Create a Desktop client.** Open **Clients** (or **Credentials → Create credentials → OAuth client ID**) and choose application type **Desktop app**. Download the JSON and save it as `autopost/client_secret.json`, or point `YOUTUBE_CLIENT_SECRETS` at it. Git ignores this file.
5. **Authorize.**

   ```bash
   .venv/bin/python autopost.py auth
   ```

   Sign in with the channel's account. autopost saves `token.json` with mode 600, and git ignores it.

   **On a server with no browser:**

   ```bash
   # on the server
   .venv/bin/python autopost.py auth --no-browser --port 8765
   # on your laptop, in a second terminal
   ssh -L 8765:localhost:8765 you@server
   ```

   Open the printed URL in your laptop's browser. Google redirects to `localhost:8765`, and the tunnel carries that redirect to the server. Alternatively, run `auth` on your laptop and copy `token.json` to the server.

6. **Check your quota.** Open **APIs & Services → YouTube Data API v3 → Quotas** to see your project's actual limits (see [Quota and audit](#quota-and-audit)).

## Feeding clips in

Put clips in `inbox/` as `.mp4`, `.mov` or `.webm`. Use one of two methods. Either way, a half-written file is never uploaded.

- **`enqueue` command** (recommended):

  ```bash
  python autopost.py enqueue /path/to/clip.mp4 --sidecar /path/to/meta.json
  ```

  It copies each file to a hidden `.name.partial` file and then renames it. Sidecars go in first and the clip last. Sidecars that sit next to the source clip with the same name are picked up automatically.
- **Your backend writes the files directly.** Write each file to a name starting with `.` in `inbox/`, then `rename()` it to its final name. Write the sidecars before the clip. As a fallback, autopost also skips any clip modified in the last 10 seconds (`--settle-seconds`).

### Sidecars (all optional)

| File | Use |
|---|---|
| `<clip>.json` | `title`, `description`, `tags`, `context`, `sources`, plus optional `privacy` and `publish_at` |
| `<clip>.srt` | Transcript, used as context |
| `<clip>.narration.txt` or `<clip>.txt` | Narration script, used as context. `enqueue` renames a plain `narration.txt` to this. |
| `<clip>.jpg` (or `.png`) | Thumbnail |

```json
{
  "context": "What happens in the clip, who is in it, where and when.",
  "sources": ["https://example.com/original-article"],
  "tags": ["optional", "extra tags"],
  "publish_at": "2026-12-01T17:00:00-08:00"
}
```

A sidecar with a `title` is used as written; autopost only sanitizes it. Otherwise autopost sends the context, transcript and narration to Claude. The model comes from `AUTOPOST_MODEL` (default `claude-opus-5-5`), and the key comes from `ANTHROPIC_API_KEY`. The prompt tells Claude to use only facts in the context and to credit the sources. Claude returns strict JSON (`title` of at most 70 characters, a 2–4 paragraph `description` and 8–12 `tags`) through structured outputs. autopost then removes `<` and `>`, caps the title at 100 characters, caps the description at 5,000 bytes, keeps the tags within 500 characters as YouTube counts them, and appends any source Claude left out. If there's no key, Claude refuses, or any API error occurs, the title falls back to the file name. Generated metadata is stored, so a retry doesn't call Claude again.

Clips of 60 seconds or less get `#Shorts` appended to the description. YouTube now treats vertical clips of up to 3 minutes as Shorts on its own; the tag only helps.

## Running

```bash
python autopost.py run --once --dry-run      # prints the metadata; uploads and moves nothing
python autopost.py run --once                # one pass, for cron
python autopost.py run --loop --interval 300 # service mode
python autopost.py status                    # limits, queue, recent posts and errors
```

| Option | Default | Meaning |
|---|---|---|
| `--privacy private\|unlisted\|public` | `private` | Privacy at upload. A sidecar's `privacy` overrides it. |
| `--publish-at ISO` / `--publish-delay-hours N` | none | Scheduled publishing. Uploads as private with `publishAt`, and YouTube makes the video public at that time. A sidecar's `publish_at` overrides both. |
| `--no-synthetic` | off | Sends `containsSyntheticMedia=false`. By default it is `true`, declaring altered or synthetic content. |
| `--max-per-day` | 3 | Uploads per Pacific-time day. Env var: `AUTOPOST_MAX_PER_DAY`. |
| `--min-gap-hours` | 4 | Minimum time between uploads. Env var: `AUTOPOST_MIN_GAP_HOURS`. |
| `--category` | 22 | YouTube category ID (People & Blogs) |
| `--home` | current dir | Holds `inbox/`, `posted/`, `failed/`, `duplicates/`, `autopost.db`, `autopost.log` and the tokens. Env var: `AUTOPOST_HOME`. |

`selfDeclaredMadeForKids` is always `false`.

Cron example:

```cron
*/5 * * * * cd /opt/autopost && .venv/bin/python autopost.py run --once >> /dev/null 2>&1
```

A lock file stops a cron pass and the service from overlapping.

### systemd

```bash
sudo useradd --system --home /opt/autopost autopost
sudo mkdir -p /opt/autopost && sudo cp autopost.py requirements.txt /opt/autopost/
cd /opt/autopost && sudo uv venv .venv && sudo uv pip install --python .venv -r requirements.txt
# copy client_secret.json, token.json and .env into /opt/autopost, then:
sudo chown -R autopost:autopost /opt/autopost && sudo chmod 600 /opt/autopost/{.env,token.json,client_secret.json}
sudo cp autopost.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now autopost
journalctl -u autopost -f
```

## What happens when things go wrong

| Situation | Behaviour |
|---|---|
| Same file bytes already posted | Moved to `duplicates/`; not uploaded again |
| Empty, corrupt or no video stream | Recorded as `rejected` and moved to `failed/` |
| HTTP 5xx or a dropped connection during upload | The resumable upload continues from where it stopped, with exponential backoff and jitter (up to 10 retries) |
| Any other upload error | Recorded. The clip is retried on the next pass and moved to `failed/` after 3 attempts. |
| `quotaExceeded`, `uploadLimitExceeded` or `dailyLimitExceeded` | No attempt is counted. All uploads pause until 00:05 Pacific the next day. |
| Thumbnail rejected (custom thumbnails need a verified channel) | Logged; the post still succeeds |
| Token expired or revoked | The pass stops with an auth error; run `auth` again |
| YouTube returns a different privacy than requested | Logged as a warning |

Every attempt goes to the `events` table. The `clips` table holds the video ID, title, status, attempts and last error:

```bash
sqlite3 autopost.db "select posted_at, video_id, title from clips where status='posted'"
```

## Quota and audit

Your brief assumed a 10,000-unit daily quota, 1,600 units per upload (about 6 uploads a day), and that uploads from an unaudited project are forced to private. **Google changed both rules.** Our research on 2026-11-10, recorded in [`docs/youtube-api-limits.md`](../docs/youtube-api-limits.md), found:

- Upload cost dropped to about 100 units in December 2025. Since June 2026, `videos.insert` has its own bucket with a default of 100 calls a day. Other calls still share 10,000 units.
- Since 2026-11-02, uploads from unverified projects **are no longer forced to private**. The compliance audit is now the way to get more quota.

autopost is safe under either the old or the new rules:

- The default of 3 uploads a day fits even the old 1,600-unit cost. autopost warns if `--max-per-day` × 1,600 would exceed 10,000.
- A quota error pauses uploads until the next day instead of retrying.
- autopost always sends `privacyStatus` explicitly and defaults to `private`. If YouTube changes the privacy anyway, autopost logs a warning, so an old-style forced-private upload shows up in the log.

Check the **Quotas** page in your Cloud project for your real limits, because Google can change them.

## Tests

```bash
.venv/bin/python -m unittest test_autopost -v
```

The tests generate a 3-second 1080×1920 clip with ffmpeg and cover:

- the `enqueue` → `run --once --dry-run` flow, which must upload, move and record nothing
- sidecar sanitizing and source credits
- rejection of empty and corrupt files
- the daily limit and minimum gap
- the upload path against a fake YouTube client: retry after a 503 or dropped connection, a quota pause, failure after 3 attempts, and duplicate skipping

The tests never contact YouTube or Anthropic.
