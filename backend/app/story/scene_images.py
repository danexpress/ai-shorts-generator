"""One AI image per scene, from the script's `visual_description` (Story Mode v2).

Every prompt shares the genre's visual style and the same base seed, so scenes look
like one film. Images are cached by provider, model, prompt, seed and size
(MEDIA_DIR/story-cache/images/): re-rendering is free, and editing one scene's
visual description redraws only that scene. A new `variant` re-rolls all seeds.
"""

import logging
import tempfile
import time
from pathlib import Path

from . import cache
from .genres import GENRES
from .images import ImageFailed, ImageProvider

logger = logging.getLogger(__name__)

IMAGES_VERSION = "images-v1"
# Shared by every prompt: the frame is vertical, and nothing should look like text
# or a real person (the script rules forbid real names; pictures follow suit).
SUFFIX = (
    "Vertical 9:16 composition, subject centered with room above and below. "
    "No text, letters, captions, logos or watermarks. No real or famous people."
)


def scene_prompt(scene: dict, genre_id: str) -> str:
    genre = GENRES.get(genre_id)
    style = genre.image_style if genre else ""
    mood = scene.get("mood") or ""
    parts = [scene["visual_description"].strip().rstrip(".") + "."]
    if mood:
        parts.append(f"Mood: {mood}.")
    if style:
        parts.append(f"Style: {style}.")
    parts.append(SUFFIX)
    return " ".join(parts)


def base_seed(story_id: str, variant: int) -> int:
    return int(cache.digest([story_id, variant])[:8], 16)


def image_key(provider: ImageProvider, prompt: str, seed: int) -> dict:
    return {
        "version": IMAGES_VERSION,
        "provider": provider.id,
        "providerVersion": provider.version,
        "model": provider.model,
        "size": list(provider.size),
        "prompt": prompt,
        "seed": seed,
    }


def image_path(settings, key: dict) -> Path:
    return cache.file_path(settings.media_dir, "images", key, ".png")


def generate_scene_images(
    script: dict,
    genre_id: str,
    provider: ImageProvider,
    settings,
    story_id: str,
    variant: int = 0,
    on_progress=None,
) -> dict:
    """Draw (or reuse) every scene's picture. Returns the images document."""
    started = time.monotonic()
    seed0 = base_seed(story_id, variant)
    scenes, drawn = [], 0
    for index, scene in enumerate(script["scenes"]):
        prompt = scene_prompt(scene, genre_id)
        seed = (seed0 + index * 7919) % 2**31
        key = image_key(provider, prompt, seed)
        path = image_path(settings, key)
        if path.is_file():
            cache.touch(path)
            seconds, cached = 0.0, True
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.TemporaryDirectory(dir=path.parent) as work:
                draft = Path(work) / "scene.png"
                begun = time.monotonic()
                provider.generate(prompt, seed, draft)
                seconds = time.monotonic() - begun
                if not draft.is_file() or draft.stat().st_size == 0:
                    raise ImageFailed("The image provider returned an empty picture.")
                draft.replace(path)
            cached = False
            drawn += 1
        scenes.append(
            {
                "id": scene["id"],
                "prompt": prompt,
                "seed": seed,
                "key": cache.digest(key),
                "cached": cached,
                "seconds": round(seconds, 3),
            }
        )
        if on_progress:
            on_progress((index + 1) / len(script["scenes"]))
    cost = drawn * provider.cost_per_image
    elapsed = time.monotonic() - started
    logger.info(
        "Story images: %d scene(s) in %.1fs (%s, %d drawn, %d cached), $%.4f",
        len(scenes),
        elapsed,
        provider.id,
        drawn,
        len(scenes) - drawn,
        cost,
    )
    return {
        "provider": provider.id,
        "model": provider.model,
        "variant": variant,
        "scenes": scenes,
        "drawn": drawn,
        "costUsd": round(cost, 6),
        "seconds": round(elapsed, 3),
    }


def scene_image_file(settings, digest: str) -> Path:
    """The cached picture for a scene entry's `key` digest."""
    return cache.cache_root(settings.media_dir) / "images" / f"{digest}.png"
