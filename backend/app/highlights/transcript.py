"""Sentence units and context windows over the stored transcript.

Sentences are the units the LLM sees and the boundaries clips snap to, so a clip
always starts at the first word of a sentence and ends at the last word of one.
"""

import re

SENTENCE_END = re.compile(r"[.!?…]['\"”’)\]]*$")
PAUSE_BREAK = 1.0  # unpunctuated speech is split at pauses this long


def words_of(segments: list[dict]) -> list[dict]:
    words = []
    for segment in segments:
        for word in segment.get("words", []):
            tokens = word["w"].split()
            if len(tokens) == 1:
                words.append({"text": tokens[0], "start": word["s"], "end": word["e"]})
            elif tokens:
                # Segment-level timing after a transcript edit: spread by length.
                total = sum(len(t) for t in tokens)
                cursor, span = word["s"], word["e"] - word["s"]
                for token in tokens:
                    step = span * len(token) / total
                    words.append({"text": token, "start": cursor, "end": cursor + step})
                    cursor += step
    return words


def sentences(segments: list[dict]) -> list[dict]:
    words = words_of(segments)
    result, current = [], []
    for index, word in enumerate(words):
        current.append(word)
        following = words[index + 1] if index + 1 < len(words) else None
        if (
            following is None
            or SENTENCE_END.search(word["text"])
            or following["start"] - word["end"] >= PAUSE_BREAK
        ):
            result.append(
                {
                    "start": round(current[0]["start"], 3),
                    "end": round(current[-1]["end"], 3),
                    "text": " ".join(w["text"] for w in current),
                    "words": current,
                }
            )
            current = []
    return result


def format_lines(units: list[dict]) -> str:
    return "\n".join(f"[{u['start']:.2f}-{u['end']:.2f}] {u['text']}" for u in units)


def estimate_tokens(text: str) -> int:
    # Conservative (English averages ~4 characters per token); only sizes windows.
    return len(text) // 3 + 1


def windows(units: list[dict], max_tokens: int, overlap_seconds: float) -> list[list[dict]]:
    """Split sentences into windows under `max_tokens`, overlapping by `overlap_seconds`.

    The overlap is longer than the longest clip, so a moment spanning a window edge
    appears whole in at least one window.
    """
    if estimate_tokens(format_lines(units)) <= max_tokens:
        return [units]
    result, start = [], 0
    while start < len(units):
        end, size = start, 0
        while end < len(units):
            size += estimate_tokens(format_lines([units[end]])) + 1
            if size > max_tokens and end > start:
                break
            end += 1
        result.append(units[start:end])
        if end >= len(units):
            break
        # Next window starts at least `overlap_seconds` before this one ended.
        boundary = units[end - 1]["end"] - overlap_seconds
        earlier = [i for i in range(start + 1, end) if units[i]["start"] <= boundary]
        start = earlier[-1] if earlier else start + 1
        # Every window must reach the first uncovered sentence; give up overlap if a
        # very long sentence leaves no room for it.
        while start < end and estimate_tokens(format_lines(units[start : end + 1])) > max_tokens:
            start += 1
    return result
