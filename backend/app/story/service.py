"""Story projects: create from a topic or pasted story, edit and regenerate the script.

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

logger = logging.getLogger(__name__)

DOCUMENT_VERSION = 1
RATE_LIMIT = 6  # script generations per creator per minute


def request_of(document: dict) -> ScriptRequest:
    return ScriptRequest(**document["request"])


def view(record: StoryRecord) -> dict:
    document = record.document
    request, script = request_of(document), document["script"]
    low, high = word_budget(request.seconds)
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
        "warnings": timing_notes(script, request),
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
    def __init__(self, database, settings, clock, model_factory=None):
        self.database, self.settings, self.clock = database, settings, clock
        self.model_factory = model_factory or self.claude

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

    def save_script(self, user_id, story_id, revision, script, source, extra=None) -> dict:
        with self.database.transaction() as db:
            record = self.owned(db, user_id, story_id)
            self.check_revision(record, revision)
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
