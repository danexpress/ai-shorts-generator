"""Validate LLM highlight candidates and snap them to sentence boundaries.

Model output is untrusted: every field is type- and range-checked, timestamps must
fall inside the video, and boundaries are moved to whole sentences (falling back
to word boundaries) so a clip never starts or ends mid-word.
"""

import math

PAD_BEFORE, PAD_AFTER = 0.15, 0.3  # breathing room, only ever taken from silence


class InvalidOutput(ValueError):
    """The model's answer is unusable; the message is fed back on retry."""


def parse_candidates(data, duration: float) -> tuple[list[dict], list[str]]:
    """Return (well-formed candidates, problems) from {"candidates": [...]}."""
    if not isinstance(data, dict) or not isinstance(data.get("candidates"), list):
        raise InvalidOutput('Expected a JSON object with a "candidates" array.')
    valid, problems = [], []
    for index, item in enumerate(data["candidates"], 1):
        if not isinstance(item, dict):
            problems.append(f"Candidate {index} is not an object.")
            continue
        start, end, score = item.get("start"), item.get("end"), item.get("score")
        hook = item.get("hook").strip() if isinstance(item.get("hook"), str) else ""
        reason = item.get("reason").strip() if isinstance(item.get("reason"), str) else ""
        numbers = [start, end, score]
        if not all(
            isinstance(n, int | float) and not isinstance(n, bool) and math.isfinite(n)
            for n in numbers
        ):
            problems.append(f"Candidate {index} needs numeric start, end and score.")
            continue
        if not hook or not reason:
            problems.append(f"Candidate {index} needs a non-empty hook and reason.")
            continue
        if start < 0 or end > duration + 0.5 or end <= start:
            problems.append(
                f"Candidate {index} ({start}–{end}s) is outside the video (0–{duration:.2f}s)."
            )
            continue
        if not 0 <= score <= 10:
            problems.append(f"Candidate {index} score {score} is not between 0 and 10.")
            continue
        valid.append(
            {
                "start": float(start),
                "end": float(min(end, duration)),
                "hook": hook[:120],
                "reason": reason[:500],
                "score": float(score),
            }
        )
    return valid, problems


def _nearest(values: list[float], target: float) -> int:
    return min(range(len(values)), key=lambda i: (abs(values[i] - target), i))


def _word_cut(words: list[dict], start: float, max_seconds: float, min_seconds: float):
    """Latest word end within max_seconds, preferring the longest pause after min_seconds."""
    best, best_gap = None, -1.0
    for index, word in enumerate(words):
        length = word["end"] - start
        if length > max_seconds:
            break
        following = words[index + 1]["start"] if index + 1 < len(words) else math.inf
        gap = following - word["end"]
        if length >= min_seconds and gap >= best_gap:
            best, best_gap = word["end"], gap
    return best


def _word_snap(words: list[dict], start: float, end: float, min_seconds: float, max_seconds: float):
    """Word-boundary fallback when whole sentences cannot form a valid clip."""
    if not words:
        return None
    a = _nearest([w["start"] for w in words], start)
    b = max(a, _nearest([w["end"] for w in words], end))
    while words[b]["end"] - words[a]["start"] > max_seconds and b > a:
        b -= 1
    while words[b]["end"] - words[a]["start"] < min_seconds:
        if b + 1 < len(words) and words[b + 1]["end"] - words[a]["start"] <= max_seconds:
            b += 1
        elif a > 0 and words[b]["end"] - words[a - 1]["start"] <= max_seconds:
            a -= 1
        else:
            return None
    return words[a]["start"], words[b]["end"]


def snap(
    start: float,
    end: float,
    units: list[dict],
    min_seconds: float,
    max_seconds: float,
    duration: float,
) -> tuple[float, float] | None:
    """Move [start, end] to sentence boundaries with a length in [min, max] seconds.

    Starts are kept close to the model's choice (the hook); length is fixed by
    adding or dropping whole sentences at the end first. A single sentence longer
    than the maximum is cut at a word boundary. Returns None when impossible.
    """
    if not units:
        return None
    a = _nearest([u["start"] for u in units], start)
    b = max(a, _nearest([u["end"] for u in units], end))
    length = lambda: units[b]["end"] - units[a]["start"]
    while length() > max_seconds and b > a:
        b -= 1
    while length() < min_seconds:
        if b + 1 < len(units) and units[b + 1]["end"] - units[a]["start"] <= max_seconds:
            b += 1
        elif a > 0 and units[b]["end"] - units[a - 1]["start"] <= max_seconds:
            a -= 1
        else:
            break
    clip_start, clip_end = units[a]["start"], units[b]["end"]
    if clip_end - clip_start > max_seconds:
        words = [w for unit in units[a : b + 1] for w in unit["words"]]
        cut = _word_cut(words, clip_start, max_seconds, min_seconds)
        clip_end = cut if cut is not None else clip_end
    all_words = [w for unit in units for w in unit["words"]]
    if not min_seconds <= clip_end - clip_start <= max_seconds:
        # Long run-on sentences around the request: fall back to word boundaries.
        bounds = _word_snap(all_words, start, end, min_seconds, max_seconds)
        if bounds is None:
            return None
        clip_start, clip_end = bounds
    # Pad into the surrounding silence, never into a neighbouring word.
    before = max((w["end"] for w in all_words if w["end"] <= clip_start), default=0.0)
    after = min((w["start"] for w in all_words if w["start"] >= clip_end), default=duration)
    budget = max(0.0, max_seconds - (clip_end - clip_start))
    padded_end = min(duration, after, clip_end + min(PAD_AFTER, budget))
    budget -= padded_end - clip_end
    padded_start = max(0.0, before, clip_start - min(PAD_BEFORE, budget))
    return round(padded_start, 3), round(padded_end, 3)


def overlap(a: dict, b: dict) -> float:
    shared = max(0.0, min(a["end"], b["end"]) - max(a["start"], b["start"]))
    return shared / min(a["end"] - a["start"], b["end"] - b["start"])


def dedupe(candidates: list[dict], limit: int, max_overlap: float = 0.3) -> list[dict]:
    kept = []
    for candidate in sorted(candidates, key=lambda c: -c["score"]):
        if all(overlap(candidate, other) <= max_overlap for other in kept):
            kept.append(candidate)
        if len(kept) == limit:
            break
    return kept
