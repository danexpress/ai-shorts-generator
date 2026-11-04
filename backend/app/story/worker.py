"""Background voice generation for Story Mode, run inside the processing worker.

A story waiting with status "voicing" is claimed with a lease (so a crashed worker's
job is picked up again), spoken scene by scene, and saved only if the request is
still current: a newer voice request or a deleted story discards the result.
"""

import logging
import shutil
import time
from pathlib import Path

from sqlalchemy import or_, select

from ..db_models import StoryRecord, UserRecord
from ..renderer import RendererUnavailable, ffmpeg_path
from ..store import oid
from .audio import AudioFailed
from .tts import TTSFailed, TTSUnavailable, build_providers
from .voice import VoiceRequest, generate_voice

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
LEASE_SECONDS = 300
RETRY_SECONDS = 30  # times the attempt number, when the provider is unavailable


class Abandoned(Exception):
    """The story stopped belonging to this worker (new request, deleted, lease lost)."""


def story_dir(media_dir: Path, story_id: str) -> Path:
    return (Path(media_dir) / "stories" / story_id).resolve()


def remove_story_files(media_dir: Path, story_id: str):
    root = (Path(media_dir) / "stories").resolve()
    path = story_dir(media_dir, story_id)
    if path.parent == root and path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


class StoryWorker:
    def __init__(self, database, settings, clock=time.time, providers=None, transcriber=None):
        self.database, self.settings, self.clock = database, settings, clock
        self._providers = providers
        self._transcriber = transcriber
        self.owner = oid("sworker")[:40]

    @property
    def providers(self):
        if self._providers is None:
            self._providers = build_providers(self.settings)
        return self._providers

    def transcriber(self):
        """Whisper for providers without timestamps, loaded only if ever needed."""
        if self._transcriber is None:
            from ..transcription import build_transcriber

            self._transcriber = build_transcriber(self.settings)
        return self._transcriber

    def now(self) -> int:
        return int(self.clock() * 1000)

    def claim(self):
        now = self.now()
        with self.database.transaction() as db:
            record = db.scalar(
                select(StoryRecord)
                .where(
                    StoryRecord.status == "voicing",
                    or_(StoryRecord.leaseUntil.is_(None), StoryRecord.leaseUntil <= now),
                )
                .order_by(StoryRecord.updatedAt, StoryRecord.id)
                .limit(1)
            )
            if record is None:
                return None
            record.leaseOwner = self.owner
            record.leaseUntil = now + LEASE_SECONDS * 1000
            record.attempts = (record.attempts or 0) + 1
            return record.id, record.document["voiceRequest"]["id"], record.attempts

    def owned(self, db, story_id: str, request_id: str) -> StoryRecord:
        record = db.get(StoryRecord, story_id)
        user = db.get(UserRecord, record.userId) if record else None
        if (
            record is None
            or record.status != "voicing"
            or record.leaseOwner != self.owner
            or record.document.get("voiceRequest", {}).get("id") != request_id
            or user is None
            or not user.active
        ):
            raise Abandoned
        return record

    def run_once(self) -> bool:
        claimed = self.claim()
        if not claimed:
            return False
        story_id, request_id, attempt = claimed
        try:
            self.voice(story_id, request_id, attempt)
        except Abandoned:
            logger.info(
                "Story %s voice request was replaced or deleted; result discarded", story_id
            )
        except Exception:
            logger.exception("Unexpected error voicing story %s", story_id)
            try:
                self.finish(
                    story_id,
                    request_id,
                    error=("VOICE_FAILED", "The voice could not be generated. Try again."),
                )
            except Abandoned:
                pass
        return True

    def progress(self, story_id: str, request_id: str, value: float):
        with self.database.transaction() as db:
            record = self.owned(db, story_id, request_id)
            record.progress = round(value, 3)
            record.leaseUntil = self.now() + LEASE_SECONDS * 1000

    def voice(self, story_id: str, request_id: str, attempt: int):
        with self.database.sessions() as db:
            record = self.owned(db, story_id, request_id)
            document = record.document
        voice_request = document["voiceRequest"]
        if attempt > MAX_ATTEMPTS:
            return self.finish(
                story_id,
                request_id,
                error=("VOICE_UNAVAILABLE", "The voice service kept failing. Try again later."),
            )
        provider = self.providers.get(voice_request["provider"])
        request = VoiceRequest(
            voice_request["provider"], voice_request["voice"], voice_request["speed"]
        )
        destination = story_dir(self.settings.media_dir, story_id) / f"voice-{request_id}.wav"
        try:
            if provider is None:
                raise TTSFailed(f"Unknown voice provider {voice_request['provider']!r}.")
            ffmpeg = ffmpeg_path(self.settings.ffmpeg_binary)
            result = generate_voice(
                document["script"],
                request,
                provider,
                self.settings,
                ffmpeg,
                destination,
                on_progress=lambda value: self.progress(story_id, request_id, value),
                transcriber_factory=self.transcriber,
            )
        except TTSUnavailable as exc:
            logger.warning("Story %s voice attempt %d: %s", story_id, attempt, exc)
            if attempt >= MAX_ATTEMPTS:
                return self.finish(story_id, request_id, error=("VOICE_UNAVAILABLE", str(exc)))
            return self.retry_later(story_id, request_id, attempt)
        except (TTSFailed, AudioFailed, RendererUnavailable) as exc:
            logger.warning("Story %s voice failed: %s", story_id, exc)
            destination.unlink(missing_ok=True)
            return self.finish(story_id, request_id, error=("VOICE_FAILED", str(exc)))
        result["file"] = destination.name
        result["requestId"] = request_id
        result["scriptDigest"] = voice_request["scriptDigest"]
        self.finish(story_id, request_id, result=result)

    def retry_later(self, story_id: str, request_id: str, attempt: int):
        with self.database.transaction() as db:
            record = self.owned(db, story_id, request_id)
            record.leaseOwner = None
            record.leaseUntil = self.now() + RETRY_SECONDS * attempt * 1000

    def finish(self, story_id: str, request_id: str, result=None, error=None):
        previous = None
        try:
            with self.database.transaction() as db:
                record = self.owned(db, story_id, request_id)
                document = dict(record.document)
                if result:
                    previous = (document.get("voice") or {}).get("file")
                    document["voice"] = result
                    document["timings"] = result["scenes"]
                    document["voiceError"] = None
                    usage = dict(document.get("usage") or {})
                    usage["voice"] = {
                        "seconds": result["seconds"],
                        "characters": result["characters"],
                        "billedCharacters": result["billedCharacters"],
                        "costUsd": result["costUsd"],
                    }
                    document["usage"] = usage
                    record.status = "voiced"
                else:
                    code, message = error
                    document["voiceError"] = {"code": code, "message": message}
                    record.status = "voice_failed"
                record.document = document
                record.leaseOwner = record.leaseUntil = None
                record.progress = 1.0 if result else None
                record.revision += 1
                record.updatedAt = self.now()
        except Abandoned:
            if result:
                (story_dir(self.settings.media_dir, story_id) / result["file"]).unlink(
                    missing_ok=True
                )
            raise
        if previous and result and previous != result["file"]:
            (story_dir(self.settings.media_dir, story_id) / previous).unlink(missing_ok=True)
