"""Story projects: create from a topic or pasted story, edit and regenerate the script,
and request the voiceover (generated in the background by app/story/worker.py).

LLM calls run outside database transactions. Generated scripts are cached by content
hash (see cache.py), so asking again for the same script costs nothing.
"""

import logging
import time

from sqlalchemy import select

from ..db_models import StoryRecord
from ..errors import ApiError
from ..llm import ClaudeModel, InvalidOutput, LLMUnavailable
from ..social.accounts import rate_limit
from ..store import oid
from . import cache
from .genres import GENRES
from .prompt import PROMPT_VERSION
from .script import (
    MAX_SECONDS,
    ScriptFailed,
    ScriptRequest,
    cache_key,
    check_edit,
    count_words,
    demo_script,
    generate_script,
    timing_notes,
    word_budget,
)
from .tts import build_providers
from .worker import remove_story_files, story_dir

logger = logging.getLogger(__name__)

DOCUMENT_VERSION = 1
RATE_LIMIT = 6  # script generations (and voice requests) per creator per minute
PLAYBACK_SECONDS = 30 * 60


def request_of(document: dict) -> ScriptRequest:
    return ScriptRequest(**document["request"])


def script_digest(script: dict) -> str:
    """What the voice depends on: the spoken text of each scene, in order."""
    return cache.digest([s["narration"] for s in script["scenes"]])


def voice_view(document: dict) -> dict | None:
    voice = document.get("voice")
    if not voice:
        return None
    keys = ("provider", "voice", "speed", "durationSec", "tooLong", "scenes", "words")
    keys += ("timingSources", "characters", "costUsd", "cachedScenes", "loudness")
    return {
        **{key: voice[key] for key in keys},
        # The script changed after this voice was generated: it no longer matches.
        "stale": voice["scriptDigest"] != script_digest(document["script"]),
    }


def view(record: StoryRecord) -> dict:
    document = record.document
    request, script = request_of(document), document["script"]
    low, high = word_budget(request.seconds)
    voice = voice_view(document)
    warnings = timing_notes(script, request)
    if voice and voice["tooLong"] and not voice["stale"]:
        warnings.append(
            f"The voiceover runs {voice['durationSec']:.0f} seconds; Shorts must stay under "
            f"{MAX_SECONDS}. Shorten the script, then generate the voice again."
        )
    pending = document.get("voiceRequest") if record.status == "voicing" else None
    return {
        "id": record.id,
        "title": record.title,
        "status": record.status,
        "revision": record.revision,
        "createdAt": record.createdAt,
        "updatedAt": record.updatedAt,
        "genre": request.genre,
        "targetSeconds": request.seconds,
        "tone": request.tone,
        "topic": request.topic or None,
        "story": request.story or None,
        "script": script,
        "scriptSource": document["scriptSource"],
        "wordCount": sum(count_words(s["narration"]) for s in script["scenes"]),
        "wordBudget": [low, high],
        "warnings": warnings,
        "voice": voice,
        "voiceJob": {
            "request": (
                {key: pending[key] for key in ("provider", "voice", "speed")} if pending else None
            ),
            "progress": record.progress if pending else None,
            "error": document.get("voiceError") if record.status == "voice_failed" else None,
        },
    }


def summary(record: StoryRecord) -> dict:
    document = record.document
    return {
        "id": record.id,
        "title": record.title,
        "status": record.status,
        "genre": document["request"]["genre"],
        "targetSeconds": document["request"]["seconds"],
        "estimatedSeconds": document["script"]["estimated_seconds"],
        "createdAt": record.createdAt,
        "updatedAt": record.updatedAt,
    }


class StoryService:
    def __init__(self, database, settings, clock, model_factory=None, providers=None):
        self.database, self.settings, self.clock = database, settings, clock
        self.model_factory = model_factory or self.claude
        self.providers = providers if providers is not None else build_providers(settings)

    def claude(self):
        return ClaudeModel(
            self.settings.story_model,
            self.settings.story_effort,
            self.settings.story_fallbacks,
            purpose="Story",
            max_tokens=8000,
        )

    def now(self) -> int:
        return int(self.clock() * 1000)

    def owned(self, db, user_id: str, story_id: str) -> StoryRecord:
        record = db.get(StoryRecord, story_id)
        if not record or record.userId != user_id:
            raise ApiError("NOT_FOUND", "Story not found.", 404)
        return record

    def write(self, request: ScriptRequest, extra=None, shorten: dict | None = None) -> dict:
        """A script for `request`: from the cache, the demo writer, or the model."""
        model = self.settings.story_model
        key = cache_key(request, model, self.settings.story_effort, extra)
        hit = cache.load(self.settings.media_dir, "scripts", key)
        if hit:
            logger.info("Story script cache hit (%s, %ss)", request.genre, request.seconds)
            return {**hit, "usage": None}
        if model == "demo":
            script, usage = demo_script(request), None
        else:
            llm = self.model_factory()
            started = time.monotonic()
            try:
                script, _notes = generate_script(llm, request, shorten)
            except LLMUnavailable as exc:
                raise ApiError("STORY_MODEL_UNAVAILABLE", str(exc), 503)
            except ScriptFailed as exc:
                logger.warning("Story script rejected twice: %s", exc)
                raise ApiError(
                    "SCRIPT_FAILED",
                    "Claude couldn't write a usable script. Try again or rephrase the topic.",
                    422,
                )
            used = getattr(llm, "usage", None)
            logger.info(
                "Story script written in %.1fs (%s, %ss); LLM %s",
                time.monotonic() - started,
                request.genre,
                request.seconds,
                used.summary() if used else "n/a",
            )
            usage = (
                {
                    "seconds": round(used.seconds, 3),
                    "inputTokens": used.input_tokens,
                    "outputTokens": used.output_tokens,
                    "cacheReadTokens": used.cache_read_tokens,
                    "costUsd": None if used.cost is None else round(used.cost, 6),
                }
                if used
                else None
            )
        result = {"script": script, "model": model, "prompt": PROMPT_VERSION}
        cache.save(self.settings.media_dir, "scripts", key, result)
        return {**result, "usage": usage}

    def limit(self, user_id: str):
        with self.database.transaction() as db:
            rate_limit(db, user_id, "story_script", self.now(), RATE_LIMIT)

    def genres(self) -> list[dict]:
        return [genre.public() for genre in GENRES.values()]

    def create(self, user_id: str, body: dict) -> dict:
        request = ScriptRequest(
            genre=body["genre"],
            seconds=body["targetSeconds"],
            topic=(body.get("topic") or "").strip(),
            story=(body.get("story") or "").strip(),
            tone=(body.get("tone") or "").strip(),
        )
        self.limit(user_id)
        written = self.write(request)
        now = self.now()
        document = {
            "version": DOCUMENT_VERSION,
            "request": {
                "genre": request.genre,
                "seconds": request.seconds,
                "topic": request.topic,
                "story": request.story,
                "tone": request.tone,
            },
            "script": written["script"],
            "scriptSource": "generated",
            "scriptModel": written["model"],
            "scriptPrompt": written["prompt"],
            "variant": 0,
            "usage": {"script": written["usage"]},
            # Filled by later steps: voice, scene timings, background, music, style.
            "voice": None,
            "timings": None,
            "background": None,
            "music": None,
            "style": None,
        }
        with self.database.transaction() as db:
            record = StoryRecord(
                id=oid("sty"),
                userId=user_id,
                title=written["script"]["title"],
                status="draft",
                revision=1,
                document=document,
                createdAt=now,
                updatedAt=now,
            )
            db.add(record)
            db.flush()
            return view(record)

    def list(self, user_id: str) -> list[dict]:
        with self.database.sessions() as db:
            records = db.scalars(
                select(StoryRecord)
                .where(StoryRecord.userId == user_id)
                .order_by(StoryRecord.createdAt.desc(), StoryRecord.id)
            )
            return [summary(record) for record in records]

    def get(self, user_id: str, story_id: str) -> dict:
        with self.database.sessions() as db:
            return view(self.owned(db, user_id, story_id))

    def check_revision(self, record: StoryRecord, revision: int):
        if record.revision != revision:
            raise ApiError(
                "STALE_REVISION",
                "This story changed in another tab. Reload it to see the latest script.",
                409,
            )

    def check_idle(self, record: StoryRecord):
        if record.status == "voicing":
            raise ApiError(
                "STORY_BUSY", "The voice is being generated. Wait for it to finish.", 409
            )

    def save_script(self, user_id, story_id, revision, script, source, extra=None) -> dict:
        with self.database.transaction() as db:
            record = self.owned(db, user_id, story_id)
            self.check_revision(record, revision)
            self.check_idle(record)
            document = {**record.document, "script": script, "scriptSource": source}
            if extra:
                document.update(extra)
            record.update(
                document=document,
                title=script["title"],
                revision=record.revision + 1,
                updatedAt=self.now(),
            )
            db.flush()
            return view(record)

    def edit(self, user_id: str, story_id: str, revision: int, script: dict) -> dict:
        with self.database.sessions() as db:
            request = request_of(self.owned(db, user_id, story_id).document)
        try:
            checked, _warnings = check_edit(script, request)
        except InvalidOutput as exc:
            raise ApiError("INVALID_SCRIPT", str(exc), 422)
        return self.save_script(user_id, story_id, revision, checked, "edited")

    def regenerate(self, user_id: str, story_id: str, revision: int, shorten=False) -> dict:
        """A new script for the same request; `shorten` rewrites the current one shorter."""
        with self.database.sessions() as db:
            record = self.owned(db, user_id, story_id)
            self.check_revision(record, revision)
            self.check_idle(record)
            document = record.document
        request = request_of(document)
        self.limit(user_id)
        if shorten:
            current = document["script"]
            written = self.write(request, {"shorten": cache.digest(current)}, shorten=current)
            variant = document["variant"]
        else:
            variant = document["variant"] + 1
            written = self.write(request, {"variant": variant})
        return self.save_script(
            user_id,
            story_id,
            revision,
            written["script"],
            "generated",
            {
                "variant": variant,
                "scriptModel": written["model"],
                "scriptPrompt": written["prompt"],
                "usage": {**document.get("usage", {}), "script": written["usage"]},
            },
        )

    def delete(self, user_id: str, story_id: str):
        with self.database.transaction() as db:
            db.delete(self.owned(db, user_id, story_id))
        remove_story_files(self.settings.media_dir, story_id)

    # ---------- voiceover ----------

    def voices(self) -> dict:
        default = self.settings.story_tts_provider
        return {
            "defaultProvider": default if default in self.providers else None,
            "providers": [provider.public() for provider in self.providers.values()],
        }

    def request_voice(self, user_id: str, story_id: str, body: dict) -> dict:
        """Queue voice generation; the processing worker picks it up (status "voicing")."""
        provider = self.providers.get(body["provider"])
        if provider is None:
            raise ApiError("INVALID_INPUT", "Choose one of the available voice providers.", 422)
        reason = provider.unavailable_reason()
        if reason:
            raise ApiError("VOICE_UNAVAILABLE", reason, 409)
        if body["voice"] not in {voice.id for voice in provider.voices()}:
            raise ApiError("INVALID_INPUT", f"Choose one of the {provider.label} voices.", 422)
        low, high = provider.speed_range
        speed = provider.default_speed if body.get("speed") is None else body["speed"]
        if not low <= speed <= high:
            raise ApiError(
                "INVALID_INPUT",
                f"Choose a speed between {low:g}x and {high:g}x for this voice.",
                422,
            )
        self.limit(user_id)
        with self.database.transaction() as db:
            record = self.owned(db, user_id, story_id)
            self.check_revision(record, body["revision"])
            self.check_idle(record)
            record.document = {
                **record.document,
                "voiceRequest": {
                    "id": oid("vrq"),
                    "provider": provider.id,
                    "voice": body["voice"],
                    "speed": round(speed, 2),
                    "scriptDigest": script_digest(record.document["script"]),
                    "requestedAt": self.now(),
                },
                "voiceError": None,
            }
            record.update(
                status="voicing",
                attempts=0,
                progress=0.0,
                leaseOwner=None,
                leaseUntil=None,
                revision=record.revision + 1,
                updatedAt=self.now(),
            )
            db.flush()
            return view(record)

    def playback(self, user_id: str, story_id: str) -> dict:
        from ..routers.media import media_signature

        with self.database.sessions() as db:
            voice = self.owned(db, user_id, story_id).document.get("voice")
        if not voice:
            raise ApiError("NOT_FOUND", "This story has no voiceover yet.", 404)
        expires = int(self.clock()) + PLAYBACK_SECONDS
        signature = media_signature(f"story:{story_id}", expires, self.settings.media_signing_key)
        return {
            "url": f"/v1/story-audio/{story_id}?expires={expires}&signature={signature}",
            "expiresAt": expires * 1000,
        }

    def voice_file(self, story_id: str):
        """The current voiceover file, for a request that already passed the signature."""
        with self.database.sessions() as db:
            record = db.get(StoryRecord, story_id)
            voice = record.document.get("voice") if record else None
        path = story_dir(self.settings.media_dir, story_id) / voice["file"] if voice else None
        if not path or not path.is_file():
            raise ApiError("NOT_FOUND", "Voiceover not found.", 404)
        return path
