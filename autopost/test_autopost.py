"""Dry-run tests. They need ffmpeg and never call YouTube or Anthropic.

    python -m unittest test_autopost -v
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import autopost  # noqa: E402


def make_clip(path: Path, seconds: int = 3) -> None:
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", f"testsrc=size=1080x1920:rate=30:duration={seconds}",
         "-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}", "-shortest",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac", str(path)],
        check=True,
    )


def run_cli(home: Path, *args: str) -> subprocess.CompletedProcess:
    env = {k: v for k, v in os.environ.items() if k not in {"ANTHROPIC_API_KEY", "AUTOPOST_HOME"}}
    return subprocess.run(
        [sys.executable, str(HERE / "autopost.py"), "--home", str(home), *args],
        capture_output=True, text=True, env=env, timeout=120,
    )


def dry_run_records(stdout: str) -> list[dict]:
    return [json.loads(line[len("DRY-RUN "):]) for line in stdout.splitlines() if line.startswith("DRY-RUN ")]


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "ffmpeg is required")
class DryRunTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autopost-test-"))
        self.home = self.tmp / "home"
        self.src = self.tmp / "src"
        self.src.mkdir()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_generated_clip_falls_back_to_filename_and_uploads_nothing(self):
        clip = self.src / "sunset_timelapse-final.mp4"
        make_clip(clip)
        res = run_cli(self.home, "enqueue", str(clip))
        self.assertEqual(res.returncode, 0, res.stderr)
        queued = self.home / "inbox" / clip.name
        self.assertTrue(queued.is_file())
        self.assertEqual([p.name for p in (self.home / "inbox").iterdir() if p.name.startswith(".")], [])

        res = run_cli(self.home, "run", "--once", "--dry-run", "--settle-seconds", "0")
        self.assertEqual(res.returncode, 0, res.stdout + res.stderr)
        [rec] = dry_run_records(res.stdout)
        self.assertEqual(rec["title"], "Sunset timelapse final")
        self.assertEqual(rec["source"], "filename")
        self.assertEqual(rec["resolution"], "1080x1920")
        self.assertAlmostEqual(rec["duration"], 3, delta=0.2)
        self.assertTrue(rec["description"].endswith("#Shorts"))
        self.assertEqual(rec["privacy"], "private")
        self.assertTrue(rec["contains_synthetic_media"])

        # Nothing moved, nothing recorded.
        self.assertTrue(queued.is_file())
        self.assertEqual(list((self.home / "posted").iterdir()), [])
        db = autopost.DB(self.home / "autopost.db")
        self.assertEqual(db.conn.execute("SELECT COUNT(*) FROM clips").fetchone()[0], 0)

    def test_sidecar_metadata_is_sanitized_and_sources_credited(self):
        clip = self.src / "talk.mp4"
        make_clip(clip)
        (self.src / "talk.json").write_text(json.dumps({
            "title": "<b>My   talk</b> " + "x" * 200,
            "description": "What I covered <script>.",
            "tags": ["one", "two words", "one", "#three", "bad,tag"] + [f"tag{i:03d}" for i in range(200)],
            "sources": ["https://example.com/paper"],
        }))
        (self.src / "talk.jpg").write_bytes(b"\xff\xd8\xff")
        self.assertEqual(run_cli(self.home, "enqueue", str(clip)).returncode, 0)
        self.assertTrue((self.home / "inbox" / "talk.json").is_file())

        res = run_cli(self.home, "run", "--once", "--dry-run", "--settle-seconds", "0",
                      "--publish-delay-hours", "24", "--privacy", "public", "--no-synthetic")
        [rec] = dry_run_records(res.stdout)
        self.assertEqual(rec["source"], "sidecar")
        self.assertLessEqual(len(rec["title"]), 100)
        self.assertTrue(rec["title"].startswith("bMy talk/b"))
        for text in (rec["title"], rec["description"], *rec["tags"]):
            self.assertNotIn("<", text)
            self.assertNotIn(">", text)
        self.assertIn("Sources:\nhttps://example.com/paper", rec["description"])
        self.assertEqual(rec["tags"][:4], ["one", "two words", "three", "bad tag"])
        self.assertLessEqual(sum(autopost.tag_cost(t) for t in rec["tags"]) + len(rec["tags"]) - 1, 500)
        self.assertEqual(rec["privacy"], "private")  # scheduled uploads are private until publishAt
        self.assertIsNotNone(rec["publish_at"])
        self.assertFalse(rec["contains_synthetic_media"])
        self.assertEqual(rec["thumbnail"], "talk.jpg")

    def test_empty_and_corrupt_files_are_rejected(self):
        inbox = self.home / "inbox"
        inbox.mkdir(parents=True)
        (inbox / "empty.mp4").write_bytes(b"")
        (inbox / "junk.mov").write_bytes(b"not a video at all" * 100)
        res = run_cli(self.home, "run", "--once", "--dry-run", "--settle-seconds", "0")
        self.assertEqual(res.returncode, 0)
        self.assertEqual(dry_run_records(res.stdout), [])
        self.assertIn("empty.mp4: rejected: file is empty", res.stdout)
        self.assertIn("junk.mov: rejected", res.stdout)


class LimitsTest(unittest.TestCase):
    def test_daily_limit_and_gap(self):
        tmp = Path(tempfile.mkdtemp())
        try:
            cfg = autopost.Config(home=tmp, max_per_day=2, min_gap_hours=4)
            db = autopost.DB(tmp / "a.db")
            now = autopost.utcnow()
            self.assertIsNone(autopost.upload_blocked(cfg, db, now))
            db.upsert("a" * 64, "a.mp4", status="posted", posted_at=autopost.iso(now))
            self.assertIn("minimum gap", autopost.upload_blocked(cfg, db, now))
            db.set_state("paused_until", autopost.iso(autopost.next_quota_reset(now)))
            self.assertIn("paused", autopost.upload_blocked(cfg, db, now))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class FakeResp(dict):
    def __init__(self, status):
        super().__init__(status=str(status))
        self.status = status
        self.reason = "error"


def http_error(status: int, reason: str):
    from googleapiclient.errors import HttpError

    body = json.dumps({"error": {"errors": [{"reason": reason}], "code": status}}).encode()
    return HttpError(FakeResp(status), body)


class FakeYouTube:
    """Stands in for the API client; `script` lists what each next_chunk call does."""

    def __init__(self, script):
        self.script = list(script)
        self.bodies = []
        self.thumbnails_set = []

    def videos(self):
        return self

    def thumbnails(self):
        outer = self

        class T:
            def set(self, videoId, media_body):
                outer.thumbnails_set.append(videoId)

                class X:
                    def execute(self_inner):
                        raise http_error(403, "forbidden")  # must not fail the post
                return X()
        return T()

    def insert(self, part, body, media_body):
        self.bodies.append(body)
        outer = self

        class Req:
            def next_chunk(self):
                step = outer.script.pop(0)
                if isinstance(step, Exception):
                    raise step
                return None, step
        return Req()


@unittest.skipUnless(shutil.which("ffmpeg"), "ffmpeg is required")
class UploadPathTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="autopost-up-"))
        self.cfg = autopost.Config(home=self.tmp, settle_seconds=0)
        self.cfg.inbox.mkdir(parents=True)
        make_clip(self.cfg.inbox / "clip.mp4")
        (self.cfg.inbox / "clip.json").write_text(json.dumps({"title": "Hello", "tags": ["a"]}))
        (self.cfg.inbox / "clip.jpg").write_bytes(b"\xff\xd8\xff")
        self._sleep = autopost.time.sleep
        autopost.time.sleep = lambda s: None
        self._client = autopost.youtube_client

    def tearDown(self):
        autopost.time.sleep = self._sleep
        autopost.youtube_client = self._client
        shutil.rmtree(self.tmp, ignore_errors=True)

    def use(self, fake):
        autopost.youtube_client = lambda cfg: fake

    def test_retry_then_post_records_and_moves(self):
        fake = FakeYouTube([http_error(503, "backendError"), ConnectionError("reset"),
                            {"id": "vid123", "status": {"privacyStatus": "private"}}])
        self.use(fake)
        self.assertEqual(autopost.run_once(self.cfg), 0)
        body = fake.bodies[0]
        self.assertEqual(body["status"]["privacyStatus"], "private")
        self.assertFalse(body["status"]["selfDeclaredMadeForKids"])
        self.assertTrue(body["status"]["containsSyntheticMedia"])
        self.assertTrue(body["snippet"]["description"].endswith("#Shorts"))
        self.assertEqual(fake.thumbnails_set, ["vid123"])
        self.assertEqual(sorted(p.name for p in self.cfg.posted.iterdir()), ["clip.jpg", "clip.json", "clip.mp4"])
        self.assertEqual(list(self.cfg.inbox.iterdir()), [])
        row = autopost.DB(self.cfg.db_path).conn.execute("SELECT * FROM clips").fetchone()
        self.assertEqual((row["status"], row["video_id"], row["title"]), ("posted", "vid123", "Hello"))

        # The same bytes again are set aside as a duplicate, not uploaded twice.
        self.cfg.min_gap_hours = 0
        shutil.copy(self.cfg.posted / "clip.mp4", self.cfg.inbox / "again.mp4")
        self.assertEqual(autopost.run_once(self.cfg), 0)
        self.assertEqual(len(fake.bodies), 1)
        self.assertTrue((self.cfg.duplicates / "again.mp4").exists())

    def test_quota_error_pauses_without_counting_an_attempt(self):
        self.use(FakeYouTube([http_error(403, "quotaExceeded")]))
        autopost.run_once(self.cfg)
        db = autopost.DB(self.cfg.db_path)
        self.assertIsNotNone(db.get_state("paused_until"))
        self.assertEqual(db.conn.execute("SELECT attempts FROM clips").fetchone()[0], 0)
        self.assertTrue((self.cfg.inbox / "clip.mp4").exists())

    def test_three_failures_move_to_failed(self):
        self.use(FakeYouTube([http_error(400, "invalidTitle")] * 3))
        for _ in range(3):
            autopost.run_once(self.cfg)
        self.assertTrue((self.cfg.failed / "clip.mp4").exists())
        row = autopost.DB(self.cfg.db_path).conn.execute("SELECT * FROM clips").fetchone()
        self.assertEqual((row["status"], row["attempts"]), ("failed", 3))
        self.assertIn("invalidTitle", row["last_error"])


if __name__ == "__main__":
    unittest.main()
