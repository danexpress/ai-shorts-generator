"""Background Story Mode jobs, run inside the processing worker: voice and render.

A story waiting with status "voicing" or "rendering" is claimed with a lease (so a
crashed worker's job is picked up again) and its result is saved only if the
request is still current: a newer request or a deleted story discards it.

A finished render becomes an ordinary RenderRecord on a hidden "story" project, so
downloads, SRT/ASS exports, retention and Review & publish work unchanged.
"""

import logging
import re
import shutil
import time
import uuid
from pathlib import Path

from sqlalchemy import or_, select

from ..db_models import ProjectRecord, RenderRecord, StoryRecord, UserRecord
from ..errors import ApiError
from ..renderer import RendererUnavailable, RenderFailed, ffmpeg_path
from ..social.db_models import RenderContext
from ..store import DAY, oid
from . import music
from .audio import AudioFailed
from .render import StoryRenderPlan, render_story
from .tts import TTSFailed, TTSUnavailable, build_providers
from .visuals import resolve
from .voice import VoiceRequest, generate_voice

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
LEASE_SECONDS = 300
RETRY_SECONDS = 30  # times the attempt number, when the provider is unavailable
# Status while a job runs -> the document key holding its request.
JOBS = {"voicing": "voiceRequest", "rendering": "renderRequest"}


class Abandoned(Exception):
    """The story stopped belonging to this worker (new request, deleted, lease lost)."""


def story_dir(media_dir: Path, story_id: str) -> Path:
    return (Path(media_dir) / "stories" / story_id).resolve()


def remove_story_files(media_dir: Path, story_id: str):
    root = (Path(media_dir) / "stories").resolve()
    path = story_dir(media_dir, story_id)
    if path.parent == root and path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def render_filename(title: str) -> str:
    base = re.sub(r"[^a-zA-Z0-9_-]+", "-", title).strip("-").lower()[:60].strip("-")
    return f"story-{base or 'short'}-1080p.mp4"


def story_project(db, record: StoryRecord, settings, now: int) -> ProjectRecord:
    """The hidden project that owns this story's renders (created on first render)."""
    project = db.get(ProjectRecord, record.document.get("projectId") or "")
    if project and project.status != "deleted" and project.userId == record.userId:
        project.name = record.title
        return project
    project = ProjectRecord(
        id=oid("prj"),
        userId=record.userId,
        name=record.title,
        sourceType="story",
        status="ready",
        sourceReady=True,
        durationSec=0,
        width=1080,
        height=1920,
        fps=30,
        sizeBytes=0,
        language="en",
        goal=None,
        instruction="",
        avoid=[],
        createdAt=now,
        suggestionCount=0,
        renderCount=0,
        sourceExpiresAt=None,
        sourceExpired=False,
        analysisExpiresAt=None,
        historyExpiresAt=now + settings.history_retention_days * DAY,
        clipsStale=False,
    )
    db.add(project)
    db.flush()
    record.document = {**record.document, "projectId": project.id}
    return project


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
                    StoryRecord.status.in_(JOBS),
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
            request = record.document[JOBS[record.status]]
            return record.id, record.status, request["id"], record.attempts

    def owned(self, db, story_id: str, status: str, request_id: str) -> StoryRecord:
        record = db.get(StoryRecord, story_id)
        user = db.get(UserRecord, record.userId) if record else None
        if (
            record is None
            or record.status != status
            or record.leaseOwner != self.owner
            or (record.document.get(JOBS[status]) or {}).get("id") != request_id
            or user is None
            or not user.active
        ):
            raise Abandoned
        return record

    def run_once(self) -> bool:
        claimed = self.claim()
        if not claimed:
            return False
        story_id, status, request_id, attempt = claimed
        job = (story_id, status, request_id)
        try:
            if attempt > MAX_ATTEMPTS:
                self.fail(job, "STORY_JOB_FAILED", "This kept failing. Try again later.")
            elif status == "voicing":
                self.voice(job, attempt)
            else:
                self.render(job)
        except Abandoned:
            logger.info("Story %s %s request was replaced or deleted; discarded", story_id, status)
        except Exception:
            logger.exception("Unexpected error in %s for story %s", status, story_id)
            message = "Something went wrong. Try again."
            try:
                self.fail(job, "VOICE_FAILED" if status == "voicing" else "RENDER_FAILED", message)
            except Abandoned:
                pass
        return True

    def progress(self, job, value: float):
        with self.database.transaction() as db:
            record = self.owned(db, *job)
            record.progress = round(value, 3)
            record.leaseUntil = self.now() + LEASE_SECONDS * 1000

    def retry_later(self, job, attempt: int):
        with self.database.transaction() as db:
            record = self.owned(db, *job)
            record.leaseOwner = None
            record.leaseUntil = self.now() + RETRY_SECONDS * attempt * 1000

    def complete(self, job, status: str, changes: dict):
        """Save a job's outcome: document changes, new status, lease released."""
        with self.database.transaction() as db:
            record = self.owned(db, *job)
            record.document = {**record.document, **changes}
            record.status = status
            record.leaseOwner = record.leaseUntil = None
            record.progress = None
            record.revision += 1
            record.updatedAt = self.now()
            return record

    def fail(self, job, code: str, message: str):
        error_key = "voiceError" if job[1] == "voicing" else "renderError"
        failed = "voice_failed" if job[1] == "voicing" else "render_failed"
        logger.warning("Story %s %s failed: %s %s", job[0], job[1], code, message)
        self.complete(job, failed, {error_key: {"code": code, "message": message}})

    # ---------- voice ----------

    def voice(self, job, attempt: int):
        story_id, _status, request_id = job
        with self.database.sessions() as db:
            document = self.owned(db, *job).document
        voice_request = document["voiceRequest"]
        provider = self.providers.get(voice_request["provider"])
        request = VoiceRequest(
            voice_request["provider"], voice_request["voice"], voice_request["speed"]
        )
        destination = story_dir(self.settings.media_dir, story_id) / f"voice-{request_id}.wav"
        try:
            if provider is None:
                raise TTSFailed(f"Unknown voice provider {voice_request['provider']!r}.")
            result = generate_voice(
                document["script"],
                request,
                provider,
                self.settings,
                ffmpeg_path(self.settings.ffmpeg_binary),
                destination,
                on_progress=lambda value: self.progress(job, value),
                transcriber_factory=self.transcriber,
            )
        except TTSUnavailable as exc:
            logger.warning("Story %s voice attempt %d: %s", story_id, attempt, exc)
            if attempt >= MAX_ATTEMPTS:
                return self.fail(job, "VOICE_UNAVAILABLE", str(exc))
            return self.retry_later(job, attempt)
        except (TTSFailed, AudioFailed, RendererUnavailable) as exc:
            destination.unlink(missing_ok=True)
            return self.fail(job, "VOICE_FAILED", str(exc))
        result.update(
            file=destination.name, requestId=request_id, scriptDigest=voice_request["scriptDigest"]
        )
        previous = (document.get("voice") or {}).get("file")
        usage = {
            **(document.get("usage") or {}),
            "voice": {
                key: result[key] for key in ("seconds", "characters", "billedCharacters", "costUsd")
            },
        }
        try:
            self.complete(
                job,
                "voiced",
                {
                    "voice": result,
                    "timings": result["scenes"],
                    "voiceError": None,
                    "usage": usage,
                },
            )
        except Abandoned:
            destination.unlink(missing_ok=True)
            raise
        if previous and previous != destination.name:
            (story_dir(self.settings.media_dir, story_id) / previous).unlink(missing_ok=True)

    # ---------- render ----------

    def render(self, job):
        story_id = job[0]
        ffmpeg = ffmpeg_path(self.settings.ffmpeg_binary, need_subtitles=True)
        problem = None
        with self.database.sessions() as db:
            record = self.owned(db, *job)
            document, user_id, title = record.document, record.userId, record.title
            settings = document["renderRequest"]
            try:
                background = resolve(db, self.settings, user_id, settings["background"], ffmpeg)
            except ApiError as exc:
                problem = exc
        if problem:  # failed outside the read session: never write while it is open
            return self.fail(job, problem.code, problem.message)
        voice = document.get("voice")
        voice_file = story_dir(self.settings.media_dir, story_id) / voice["file"] if voice else None
        if not voice_file or not voice_file.is_file():
            return self.fail(job, "VOICE_REQUIRED", "Generate the voice before rendering.")
        track = None
        if settings.get("music"):
            track = music.resolve(self.settings, settings["music"])
            if track is None:
                return self.fail(
                    job, "MUSIC_UNAVAILABLE", "That music track is no longer available."
                )
        script = document["script"]
        card = {"title": script["title"], "hook": script["hook"]}.get(settings["titleCard"])
        timeline = [
            {**scene, **timing}
            for scene, timing in zip(script["scenes"], voice["scenes"], strict=False)
        ]
        plan = StoryRenderPlan(
            background=background,
            voice=voice_file,
            voice_seconds=voice["durationSec"],
            words=voice["words"],
            timeline=timeline,
            title=card,
            caption_preset=settings["captionPreset"],
            captions=settings["captions"],
            music=track,
        )
        render_id = "rnd_" + uuid.uuid4().hex
        root = (self.settings.media_dir / "renders").resolve()
        root.mkdir(parents=True, exist_ok=True)
        temporary, destination = root / f"{render_id}.tmp.mp4", root / f"{render_id}.mp4"
        try:
            result = render_story(ffmpeg, plan, temporary)
            temporary.replace(destination)
        except (RenderFailed, RendererUnavailable) as exc:
            temporary.unlink(missing_ok=True)
            return self.fail(job, "RENDER_FAILED", str(exc) or "The video could not be rendered.")
        try:
            self.save_render(job, render_id, destination, result, title, script, document)
        except Abandoned:
            destination.unlink(missing_ok=True)
            raise

    def save_render(self, job, render_id, destination, result, title, script, document):
        now = self.now()
        with self.database.transaction() as db:
            record = self.owned(db, *job)
            project = story_project(db, record, self.settings, now)
            project.durationSec = result["durationSec"]
            project.renderCount += 1
            settings = record.document["renderRequest"]
            db.add(
                RenderRecord(
                    id=render_id,
                    projectId=project.id,
                    userId=record.userId,
                    clipId=record.id,
                    storedName=destination.name,
                    fileName=render_filename(title),
                    resolution=1080,
                    durationSec=result["durationSec"],
                    sizeBytes=destination.stat().st_size,
                    createdAt=now,
                    expiresAt=now + self.settings.render_retention_days * DAY,
                    framing="crop",
                    captions=result["captions"],
                )
            )
            db.flush()
            # The narration is the creator's own script: publishing copy can use it as-is.
            db.add(
                RenderContext(
                    render_id=render_id,
                    context={
                        "clip_transcript": " ".join(s["narration"] for s in script["scenes"]),
                        "hook": script["hook"],
                        "source_title": title,
                        "creator_instruction": document["request"].get("topic") or "",
                        "goal": None,
                        "language": "en",
                        "transcript_verified": True,
                    },
                )
            )
            usage = {
                **(record.document.get("usage") or {}),
                "render": {"seconds": result["seconds"]},
            }
            record.document = {
                **record.document,
                "background": settings["background"],
                "music": {"name": settings["music"]} if settings.get("music") else None,
                "style": {
                    "captionPreset": settings["captionPreset"],
                    "captions": settings["captions"],
                    "titleCard": settings["titleCard"],
                },
                "lastRenderId": render_id,
                "renderError": None,
                "usage": usage,
            }
            record.status = "rendered"
            record.leaseOwner = record.leaseUntil = None
            record.progress = None
            record.revision += 1
            record.updatedAt = now
