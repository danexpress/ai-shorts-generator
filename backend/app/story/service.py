"""Story projects: create from a topic or pasted story, edit and regenerate the script,
and request the voiceover (generated in the background by app/story/worker.py).

LLM calls run outside database transactions. Generated scripts are cached by content
hash (see cache.py), so asking again for the same script costs nothing.
"""

import logging
import time

from sqlalchemy import select

from ..captions.presets import DEFAULT_PRESET, PRESETS
from ..db_models import ProjectRecord, RenderRecord, StoryRecord
from ..errors import ApiError
from ..llm import ClaudeModel, InvalidOutput, LLMUnavailable
from ..social.accounts import rate_limit
from ..store import Store, oid
from . import cache, music, visuals
from .genres import GENRES
from .images import build_image_providers
from .prompt import PROMPT_VERSION
from .scene_images import scene_image_file
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
from .worker import add_cost, remove_story_files, story_dir

logger = logging.getLogger(__name__)

DOCUMENT_VERSION = 1
FREE_SCRIPT = {
    "seconds": 0.0,
    "inputTokens": 0,
    "outputTokens": 0,
    "cacheReadTokens": 0,
    "costUsd": 0.0,
    "cached": False,
}
RENDER_KEYS = ("background", "music", "captionPreset", "captions", "titleCard")
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
        "renderJob": {
            "request": render_settings(document) if record.status == "rendering" else None,
            # While AI pictures are drawn (0-0.9), then the encode; None for plain renders.
            "progress": record.progress if record.status == "rendering" else None,
            "error": document.get("renderError") if record.status == "render_failed" else None,
        },
        "images": images_view(document.get("images")),
        "renderSettings": render_settings(document, saved=True),
        "lastRenderId": document.get("lastRenderId"),
        "usage": usage_view(document.get("usage") or {}),
    }


def images_view(images: dict | None) -> dict | None:
    """The AI pictures of the last render: engine, variant and each scene's prompt."""
    if not images:
        return None
    return {
        "provider": images["provider"],
        "variant": images["variant"],
        "scenes": [
            {"id": s["id"], "prompt": s["prompt"], "cached": s["cached"]} for s in images["scenes"]
        ],
    }


def usage_view(usage: dict) -> dict:
    """The last run of each step (time, tokens or characters, cost) and the story's total."""
    return {
        "script": usage.get("script"),
        "voice": usage.get("voice"),
        "render": usage.get("render"),
        "images": usage.get("images"),
        "totalCostUsd": round(usage.get("totalCostUsd", 0.0), 6),
    }


def render_settings(document: dict, saved=False) -> dict | None:
    """The pending render request, or (saved=True) the settings of the last render, or
    the genre defaults when the story was never rendered."""
    if not saved:
        pending = document.get("renderRequest")
        return {key: pending[key] for key in RENDER_KEYS} if pending else None
    style = document.get("style") or {}
    genre = GENRES.get(document["request"]["genre"])
    return {
        "background": document.get("background")
        or {"kind": "bundled", "id": genre.default_background if genre else "midnight"},
        "music": (document.get("music") or {}).get("name"),
        "captionPreset": style.get("captionPreset") or DEFAULT_PRESET,
        "captions": style.get("captions", True),
        "titleCard": style.get("titleCard", "title"),
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
    def __init__(
        self, database, settings, clock, model_factory=None, providers=None, image_providers=None
    ):
        self.database, self.settings, self.clock = database, settings, clock
        self.model_factory = model_factory or self.claude
        self.providers = providers if providers is not None else build_providers(settings)
        self.image_providers = (
            image_providers if image_providers is not None else build_image_providers(settings)
        )

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
            logger.info("Story script cache hit (%s, %ss): free", request.genre, request.seconds)
            return {**hit, "usage": {**FREE_SCRIPT, "cached": True}}
        if model == "demo":
            script, usage = demo_script(request), dict(FREE_SCRIPT)
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
                    "cached": False,
                }
                if used
                else dict(FREE_SCRIPT)
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
            "usage": add_cost({}, "script", written["usage"]),
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
        if record.status in ("voicing", "rendering"):
            what = (
                "voice is being generated" if record.status == "voicing" else "Short is rendering"
            )
            raise ApiError("STORY_BUSY", f"The {what}. Wait for it to finish.", 409)

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
                "usage": add_cost(document.get("usage") or {}, "script", written["usage"]),
            },
        )

    def delete(self, user_id: str, story_id: str):
        with self.database.transaction() as db:
            record = self.owned(db, user_id, story_id)
            project = db.get(ProjectRecord, record.document.get("projectId") or "")
            if project and project.status != "deleted":
                # Removes its renders and cancels queued posts, like deleting a project.
                Store(db, self.settings, self.clock).delete(project)
            db.delete(record)
        remove_story_files(self.settings.media_dir, story_id)

    # ---------- render ----------

    def assets(self, user_id: str) -> dict:
        with self.database.sessions() as db:
            backgrounds = visuals.available(db, self.settings, user_id, self.image_providers)
        return {
            "backgrounds": backgrounds,
            "music": music.available(self.settings),
            "captionPresets": [
                {"id": preset.id, "label": preset.label} for preset in PRESETS.values()
            ],
        }

    def request_render(self, user_id: str, story_id: str, body: dict) -> dict:
        """Queue the final render; the processing worker picks it up (status "rendering")."""
        if body["captionPreset"] not in PRESETS:
            raise ApiError("INVALID_INPUT", "Choose one of the available caption styles.", 422)
        if body.get("music") and music.resolve(self.settings, body["music"]) is None:
            raise ApiError("MUSIC_UNAVAILABLE", "That music track is not available.", 409)
        self.limit(user_id)
        with self.database.transaction() as db:
            record = self.owned(db, user_id, story_id)
            self.check_revision(record, body["revision"])
            self.check_idle(record)
            voice = voice_view(record.document)
            if not voice:
                raise ApiError("VOICE_REQUIRED", "Generate the voice before rendering.", 409)
            if voice["stale"]:
                raise ApiError(
                    "VOICE_STALE",
                    "The script changed since the voice was generated. Generate it again.",
                    409,
                )
            background = (
                body.get("background") or render_settings(record.document, True)["background"]
            )
            offered = {
                (item["kind"], item["id"])
                for item in visuals.available(db, self.settings, user_id, self.image_providers)
            }
            ref_id = background.get("id") or background.get("name") or background.get("projectId")
            if (background.get("kind"), ref_id) not in offered:
                raise ApiError(
                    "BACKGROUND_UNAVAILABLE", "Choose one of the available backgrounds.", 409
                )
            record.document = {
                **record.document,
                "renderRequest": {
                    "id": oid("rrq"),
                    "background": visuals.reference({"kind": background["kind"], "id": ref_id}),
                    "music": body.get("music") or None,
                    "captionPreset": body["captionPreset"],
                    "captions": body["captions"],
                    "titleCard": body["titleCard"],
                    "requestedAt": self.now(),
                },
                "renderError": None,
                # A new variant re-rolls every scene's seed: fresh pictures, same prompts.
                "imageVariant": record.document.get("imageVariant", 0)
                + bool(body.get("newImages")),
            }
            record.update(
                status="rendering",
                attempts=0,
                progress=None,
                leaseOwner=None,
                leaseUntil=None,
                revision=record.revision + 1,
                updatedAt=self.now(),
            )
            db.flush()
            return view(record)

    def renders(self, user_id: str, story_id: str) -> "list[dict]":
        """This story's renders, newest first, shaped like clip renders."""
        from ..routers.media import media_signature

        now = self.now()
        with self.database.sessions() as db:
            record = self.owned(db, user_id, story_id)
            project_id = record.document.get("projectId")
            rows = list(
                db.scalars(
                    select(RenderRecord)
                    .where(
                        RenderRecord.projectId == (project_id or ""),
                        RenderRecord.clipId == story_id,
                        RenderRecord.expiresAt > now,
                    )
                    .order_by(RenderRecord.createdAt.desc(), RenderRecord.id)
                )
            )
        expires = now // 1000 + PLAYBACK_SECONDS
        items = []
        for render in rows:
            signature = media_signature(render.id, expires, self.settings.media_signing_key)
            url = f"/v1/rendered/{render.id}?expires={expires}&signature={signature}"
            items.append(
                {
                    "id": render.id,
                    "projectId": render.projectId,
                    "clipId": render.clipId,
                    "filename": render.fileName,
                    "resolution": render.resolution,
                    "durationSec": render.durationSec,
                    "bytes": render.sizeBytes,
                    "createdAt": render.createdAt,
                    "expiresAt": render.expiresAt,
                    "url": url,
                    "downloadUrl": url + "&download=true",
                    "framing": render.framing or "crop",
                    "captions": render.captions is not None,
                    "captionPreset": (render.captions or {}).get("preset"),
                }
            )
        return items

    # ---------- voiceover ----------

    def voices(self) -> dict:
        default = self.settings.story_tts_provider
        return {
            "defaultProvider": default if default in self.providers else None,
            # The demo tone voice is for offline testing: shown only when it is the default.
            "providers": [
                provider.public()
                for name, provider in self.providers.items()
                if name != "demo" or default == "demo"
            ],
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

    def image_links(self, user_id: str, story_id: str) -> "list[dict]":
        """Signed thumbnail links for the last render's AI pictures."""
        from ..routers.media import media_signature

        with self.database.sessions() as db:
            images = self.owned(db, user_id, story_id).document.get("images")
        if not images:
            return []
        expires = int(self.clock()) + PLAYBACK_SECONDS
        links = []
        for scene in images["scenes"]:
            message = f"story-image:{story_id}:{scene['id']}"
            signature = media_signature(message, expires, self.settings.media_signing_key)
            url = (
                f"/v1/story-images/{story_id}/{scene['id']}?expires={expires}&signature={signature}"
            )
            links.append({"id": scene["id"], "url": url})
        return links

    def image_file(self, story_id: str, scene_id: int):
        """A scene's cached picture, for a request that already passed the signature."""
        with self.database.sessions() as db:
            record = db.get(StoryRecord, story_id)
            images = record.document.get("images") if record else None
        scene = next((s for s in (images or {}).get("scenes", []) if s["id"] == scene_id), None)
        path = scene_image_file(self.settings, scene["key"]) if scene else None
        if not path or not path.is_file():
            raise ApiError("NOT_FOUND", "Picture not found.", 404)
        return path

    def voice_file(self, story_id: str):
        """The current voiceover file, for a request that already passed the signature."""
        with self.database.sessions() as db:
            record = db.get(StoryRecord, story_id)
            voice = record.document.get("voice") if record else None
        path = story_dir(self.settings.media_dir, story_id) / voice["file"] if voice else None
        if not path or not path.is_file():
            raise ApiError("NOT_FOUND", "Voiceover not found.", 404)
        return path
