"""Group transcript words into short karaoke caption chunks.

Pure functions over plain dicts so they are easy to test with fixed fixtures.
Times are absolute source seconds; the ASS/SRT writers shift them to the clip.
"""

import itertools
import re
from dataclasses import dataclass

FILLERS = {"um", "umm", "uh", "uhh", "uhm", "er", "erm", "ah", "hmm", "mm", "mhm"}
# A word ending in one of these closes the chunk it belongs to.
BREAK_AFTER = re.compile(r"[.,!?;:…—–]['\"”’)]*$")
EPSILON = 0.005


@dataclass(frozen=True)
class ChunkOptions:
    max_words: int = 3
    max_line_chars: int = 15
    max_lines: int = 2
    pause: float = 0.35  # a silence at least this long starts a new chunk
    min_duration: float = 0.3
    remove_fillers: bool = True


DEFAULT_OPTIONS = ChunkOptions()


def is_filler(text: str) -> bool:
    return re.sub(r"[^\w]", "", text.lower()) in FILLERS


def clip_words(segments: list[dict], start: float, end: float) -> list[dict]:
    """Words whose midpoint lies in [start, end], clamped to the clip.

    A segment whose timing was downgraded by a transcript edit stores one "word"
    holding the whole sentence; it is split into tokens with times interpolated by
    length and marked as estimated.
    """
    words = []
    for segment in segments:
        for word in segment.get("words", []):
            tokens = word["w"].split()
            if not tokens:
                continue
            if len(tokens) == 1:
                pieces = [(tokens[0], word["s"], word["e"], False)]
            else:
                total = sum(len(t) for t in tokens)
                span, cursor, pieces = word["e"] - word["s"], word["s"], []
                for token in tokens:
                    step = span * len(token) / total
                    pieces.append((token, cursor, cursor + step, True))
                    cursor += step
            for text, s, e, estimated in pieces:
                if start - EPSILON <= (s + e) / 2 <= end + EPSILON:
                    item = {"text": text, "start": max(s, start), "end": min(e, end)}
                    if estimated:
                        item["estimated"] = True
                    words.append(item)
    return words


def layout(texts: list[str], max_chars: int, max_lines: int) -> list[list[str]] | None:
    """Split words into at most `max_lines` balanced lines of at most `max_chars`.

    A single word longer than `max_chars` may occupy a line alone (words are never
    split). Returns None when the words cannot fit.
    """
    if not texts:
        return []
    best = None
    for count in range(1, min(max_lines, len(texts)) + 1):
        for cuts in itertools.combinations(range(1, len(texts)), count - 1):
            bounds = (0, *cuts, len(texts))
            lines = [texts[a:b] for a, b in itertools.pairwise(bounds)]
            lengths = [len(" ".join(line)) for line in lines]
            if any(n > max_chars and len(line) > 1 for n, line in zip(lengths, lines)):
                continue
            score = (max(lengths), count)
            if best is None or score < best[0]:
                best = (score, lines)
        if best is not None:
            return best[1]  # prefer the fewest lines that fit
    return None


def chunk_words(words: list[dict], options: ChunkOptions = DEFAULT_OPTIONS) -> list[dict]:
    if options.remove_fillers:
        words = [w for w in words if not is_filler(w["text"])]
    groups, current = [], []
    for word in words:
        if current:
            gap = word["start"] - current[-1]["end"]
            fits = layout(
                [w["text"] for w in current + [word]], options.max_line_chars, options.max_lines
            )
            if len(current) >= options.max_words or gap >= options.pause or fits is None:
                groups.append(current)
                current = []
        current.append(word)
        if BREAK_AFTER.search(word["text"]):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    return [
        {"id": f"c{index}", "start": group[0]["start"], "end": group[-1]["end"], "words": group}
        for index, group in enumerate(groups)
    ]


def apply_min_duration(chunks: list[dict], min_duration: float, start: float, end: float):
    """Extend short chunks into neighbouring silence without overlapping them."""
    for index, chunk in enumerate(chunks):
        if chunk["end"] - chunk["start"] >= min_duration:
            continue
        limit = chunks[index + 1]["start"] if index + 1 < len(chunks) else end
        chunk["end"] = max(chunk["end"], min(chunk["start"] + min_duration, limit))
        floor = chunks[index - 1]["end"] if index else start
        chunk["start"] = min(chunk["start"], max(chunk["end"] - min_duration, floor))
    for chunk in chunks:
        chunk["start"], chunk["end"] = round(chunk["start"], 3), round(chunk["end"], 3)
    return chunks


def build_chunks(
    segments: list[dict], start: float, end: float, options: ChunkOptions = DEFAULT_OPTIONS
) -> list[dict]:
    chunks = chunk_words(clip_words(segments, start, end), options)
    return apply_min_duration(chunks, options.min_duration, start, end)


class CaptionEditError(ValueError):
    pass


def normalize_chunks(
    chunks: list[dict], start: float, end: float, min_duration: float = 0.3
) -> list[dict]:
    """Validate creator-edited chunks and recompute chunk timing from their words.

    Each chunk is {"words": [{"text", "start", "end"}]}; chunks without words are
    dropped. Words must be in order, non-overlapping and inside the clip.
    """
    result, previous_end = [], start - EPSILON
    for chunk in chunks:
        words = []
        for word in chunk["words"]:
            text = " ".join(str(word["text"]).split())
            s, e = round(float(word["start"]), 3), round(float(word["end"]), 3)
            if not text:
                continue
            if s < start - EPSILON or e > end + EPSILON:
                raise CaptionEditError(f"“{text}” is outside the clip.")
            if e <= s:
                raise CaptionEditError(f"“{text}” must end after it starts.")
            if s < previous_end - EPSILON:
                raise CaptionEditError(f"“{text}” overlaps the previous word.")
            previous_end = e
            item = {"text": text, "start": max(s, start), "end": min(e, end)}
            if word.get("estimated"):
                item["estimated"] = True
            words.append(item)
        if words:
            result.append(
                {"id": f"c{len(result)}", "start": words[0]["start"], "end": words[-1]["end"]}
                | {"words": words}
            )
    return apply_min_duration(result, min_duration, start, end)
