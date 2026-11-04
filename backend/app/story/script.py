"""Story scripts: timing estimates, validation and generation.

A script is {"title", "hook", "scenes": [{"id", "narration", "visual_description",
"mood"}], "estimated_seconds"}. Model output is untrusted: every field is checked,
scene ids are renumbered and `estimated_seconds` is recomputed from the words.
Rejected answers are retried once with the problems fed back.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass

from ..llm import InvalidOutput, ask
from . import prompt
from .genres import GENRES

logger = logging.getLogger(__name__)

LENGTHS = (30, 45, 60)
WPM, WPM_LOW, WPM_HIGH = 140, 130, 150
HOOK_MAX_WORDS = 7  # under 3 seconds at 140 words per minute
SCENE_PAUSE = 0.25  # silence inserted between scenes by the voiceover
MAX_SECONDS = 60
SCENES = {30: (3, 5), 45: (4, 7), 60: (5, 8)}
MIN_SCENES, MAX_SCENES = 1, 12
LIMITS = {"title": 100, "hook": 200, "narration": 1200, "visual_description": 500, "mood": 40}
MAX_WORDS = 400  # an edited script far beyond a minute is rejected outright
WORD = re.compile(r"[\w'’-]+")


class ScriptFailed(Exception):
    """The model did not produce a usable script after one retry."""


@dataclass(frozen=True)
class ScriptRequest:
    genre: str
    seconds: int
    topic: str = ""
    story: str = ""
    tone: str = ""

    @property
    def adapting(self) -> bool:
        return bool(self.story)


def count_words(text: str) -> int:
    return len([w for w in WORD.findall(text) if any(c.isalnum() for c in w)])


def word_budget(seconds: int) -> tuple[int, int]:
    """130-150 words per minute of the target, capped so that the estimate (at 140 wpm,
    plus scene pauses) never passes the 60-second limit: about 120-135 words for 60 s."""
    scenes_high = SCENES.get(seconds, (3, 8))[1]
    cap = int((MAX_SECONDS - SCENE_PAUSE * (scenes_high - 1)) * WPM / 60)
    low, high = round(seconds * WPM_LOW / 60), round(seconds * WPM_HIGH / 60)
    if high > cap:
        low, high = min(low, cap - 15), cap
    return low, high


def estimate_seconds(scenes: list[dict], wpm: int = WPM) -> float:
    """Spoken length at `wpm`, plus the pauses between scenes."""
    words = sum(count_words(s["narration"]) for s in scenes)
    return words * 60 / wpm + SCENE_PAUSE * max(0, len(scenes) - 1)


def _normalized(text: str) -> str:
    return " ".join(w.lower() for w in WORD.findall(text))


def _text(value, field: str, where: str, problems: list[str]) -> str:
    if not isinstance(value, str) or not value.strip():
        problems.append(f'{where} needs a non-empty "{field}".')
        return ""
    value = " ".join(value.split())
    if len(value) > LIMITS[field]:
        problems.append(f'{where} "{field}" is longer than {LIMITS[field]} characters.')
    return value


def parse_script(data) -> tuple[dict, list[str]]:
    """Normalize a script and list structural problems (missing or oversized fields)."""
    if not isinstance(data, dict) or not isinstance(data.get("scenes"), list):
        raise InvalidOutput('Expected a JSON object with a "scenes" array.')
    problems: list[str] = []
    scenes = []
    for index, item in enumerate(data["scenes"], 1):
        where = f"Scene {index}"
        if not isinstance(item, dict):
            problems.append(f"{where} is not an object.")
            continue
        scenes.append(
            {
                "id": index,
                **{
                    field: _text(item.get(field), field, where, problems)
                    for field in ("narration", "visual_description", "mood")
                },
            }
        )
    if not MIN_SCENES <= len(scenes) <= MAX_SCENES:
        problems.append(f"Use between {MIN_SCENES} and {MAX_SCENES} scenes.")
    script = {
        "title": _text(data.get("title"), "title", "The script", problems),
        "hook": _text(data.get("hook"), "hook", "The script", problems),
        "scenes": scenes,
        "estimated_seconds": round(estimate_seconds(scenes)) if scenes else 0,
    }
    if sum(count_words(s["narration"]) for s in scenes) > MAX_WORDS:
        problems.append(f"The narration is longer than {MAX_WORDS} words.")
    return script, problems


def timing_notes(script: dict, request: ScriptRequest) -> list[str]:
    """Problems with length, hook and pacing (relative to the requested length)."""
    notes = []
    hook_words = count_words(script["hook"])
    if hook_words > HOOK_MAX_WORDS:
        notes.append(f"The hook has {hook_words} words; keep it to {HOOK_MAX_WORDS} or fewer.")
    first = script["scenes"][0]["narration"] if script["scenes"] else ""
    if script["hook"] and not _normalized(first).startswith(_normalized(script["hook"])):
        notes.append("The first scene's narration must begin with the hook sentence.")
    low, high = word_budget(request.seconds)
    words = sum(count_words(s["narration"]) for s in script["scenes"])
    # A little tolerance: models count words loosely, and the real check is the audio.
    if words > high * 1.05:
        notes.append(f"The narration has {words} words; the budget is {low}-{high}.")
    elif words < low * 0.9 and not request.adapting:  # a short pasted story is never padded
        notes.append(f"The narration has only {words} words; the budget is {low}-{high}.")
    if script["estimated_seconds"] > MAX_SECONDS:
        notes.append(
            f"It runs about {script['estimated_seconds']} seconds; Shorts must stay under "
            f"{MAX_SECONDS}."
        )
    return notes


def check_edit(data, request: ScriptRequest) -> tuple[dict, list[str]]:
    """Validate a script edited by the creator: structure must be valid (raises
    InvalidOutput); timing problems are returned as warnings, never rejected."""
    script, problems = parse_script(data)
    if problems:
        raise InvalidOutput(" ".join(problems))
    return script, timing_notes(script, request)


def _acceptor(request: ScriptRequest):
    attempts = []

    def accept(data):
        attempts.append(1)
        script, problems = parse_script(data)
        if problems:
            return None, problems
        notes = timing_notes(script, request)
        # Timing is retried once with feedback; after that the creator can edit or shorten.
        if notes and len(attempts) == 1:
            return None, notes
        return script, notes

    accept.schema = prompt.script_schema()
    return accept


def cached_part(request: ScriptRequest) -> str:
    genre = GENRES[request.genre]
    material = (
        prompt.ADAPT.format(story=request.story)
        if request.adapting
        else prompt.TOPIC.format(topic=request.topic)
    )
    return (
        prompt.GENRE.format(
            label=genre.label, guidance=genre.guidance, moods=", ".join(genre.moods)
        )
        + "\n\n"
        + material
    )


def guidance(request: ScriptRequest) -> str:
    low, high = word_budget(request.seconds)
    scenes_low, scenes_high = SCENES.get(request.seconds, (3, 8))
    return prompt.GUIDANCE.format(
        seconds=request.seconds,
        words_low=low,
        words_high=high,
        scenes_low=scenes_low,
        scenes_high=scenes_high,
        tone=request.tone or GENRES[request.genre].default_tone,
    )


def generate_script(llm, request: ScriptRequest, shorten: dict | None = None) -> tuple[dict, list]:
    """(script, notes). `shorten` is a previous script that ran too long."""
    variable = guidance(request)
    if shorten:
        variable += "\n\n" + prompt.SHORTEN.format(
            seconds=estimate_seconds(shorten["scenes"]),
            target=request.seconds,
            script=json.dumps(shorten, ensure_ascii=False),
        )
    accept = _acceptor(request)
    script = ask(
        llm, prompt.SYSTEM, cached_part(request), variable, accept, prompt.RETRY, ScriptFailed
    )
    return script, timing_notes(script, request)


def cache_key(request: ScriptRequest, model: str, effort: str, extra=None) -> dict:
    return {
        "prompt": prompt.PROMPT_VERSION,
        "model": model,
        "effort": effort,
        "request": asdict(request),
        "extra": extra,
    }


DEMO_LINES = [
    "Every night the light swept the same empty sea.",
    "Then one morning, something was waiting on the rocks.",
    "It was a bottle, sealed with wax the color of old blood.",
    "Inside, a single page, written in a careful, shaking hand.",
    "It gave the date, and the hour, and a name of a ship.",
    "The keeper checked the log. No such ship had ever passed.",
    "He climbed the stairs and watched the horizon all night.",
    "At the hour on the page, a light answered from the dark.",
    "He signalled back, and the far light blinked twice, then went out.",
    "In the morning a second bottle lay on the rocks.",
    "This page was in his own handwriting.",
    "It said: do not answer next time.",
]


def demo_script(request: ScriptRequest) -> dict:
    """A deterministic offline script sized to the request (STORY_MODEL=demo)."""
    _low, high = word_budget(request.seconds)
    scenes_low, scenes_high = SCENES.get(request.seconds, (3, 8))
    hook = "The bottle arrived before the storm."
    lines, words, added = [], count_words(hook), True
    while added:  # fill up to the budget with whole lines, repeating if needed
        added = False
        for line in DEMO_LINES:
            if words + count_words(line) <= high:
                lines.append(line)
                words += count_words(line)
                added = True
    count = min(scenes_high, max(scenes_low, len(lines) // 2))
    per = -(-len(lines) // count)
    scenes = [
        {
            "id": i + 1,
            "narration": " ".join(([hook] if i == 0 else []) + lines[i * per : (i + 1) * per]),
            "visual_description": "A lighthouse on dark rocks under a slow, rolling sky.",
            "mood": GENRES[request.genre].moods[min(i, len(GENRES[request.genre].moods) - 1)],
        }
        for i in range(count)
        if lines[i * per : (i + 1) * per]
    ]
    script, problems = parse_script(
        {"title": "The Second Bottle", "hook": hook, "scenes": scenes, "estimated_seconds": 0}
    )
    assert not problems, problems
    return script
