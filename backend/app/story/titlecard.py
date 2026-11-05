"""The opening title card and the karaoke captions of a story render.

Captions reuse the caption module unchanged: voiceover words are chunked with
`chunk_words` (1-3 words), given a minimum duration, and written by `build_ass`
with the chosen preset (active-word highlight, safe-zone fitting). The title card
is one extra ASS style and event added to that file: the story's title (or hook)
large in the upper third for the first ~2 seconds, on a translucent box so it reads
over any background, above the captions.
"""

from dataclasses import replace

from ..captions.chunking import DEFAULT_OPTIONS, apply_min_duration, chunk_words
from ..captions.formats import ass_color, ass_time, build_ass, escape_ass
from ..captions.measure import text_width
from ..captions.presets import CANVAS, CaptionPreset, get_preset, max_text_width

# Every narrated word is captioned: a scripted "hmm" is spoken on purpose.
OPTIONS = replace(DEFAULT_OPTIONS, remove_fillers=False)
TITLE_SECONDS = 2.0
TITLE_POSITION = 0.36  # vertical centre, fraction of height (captions sit at ~0.67)
TITLE_SIZES = (124, 108, 94, 80)  # largest size that fits in three lines wins
TITLE_MAX_LINES = 3
BOX_PADDING = 28


def wrap(text: str, preset: CaptionPreset, size: int, width: float) -> list[str]:
    """Greedy lines no wider than `width` (a single over-long word gets its own line)."""
    lines, current = [], ""
    for word in text.split():
        candidate = f"{current} {word}".strip()
        if text_width(candidate, preset.font_file, size) <= width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def fits(lines: list[str], preset: CaptionPreset, size: int, width: float) -> bool:
    return len(lines) <= TITLE_MAX_LINES and all(
        text_width(line, preset.font_file, size) <= width for line in lines
    )


def fit_title(text: str, preset: CaptionPreset) -> tuple[int, list[str]]:
    """The largest size whose wrapping fits three lines; otherwise the smallest size,
    cut to three lines with an ellipsis."""
    width = max_text_width() - 2 * BOX_PADDING
    for size in TITLE_SIZES:
        lines = wrap(text, preset, size, width)
        if fits(lines, preset, size, width):
            return size, lines
    size = TITLE_SIZES[-1]
    lines = wrap(text, preset, size, width)
    if len(lines) > TITLE_MAX_LINES:
        lines = lines[:TITLE_MAX_LINES]
        lines[-1] = lines[-1].rstrip(".,;:!?") + "…"
    return size, lines


def with_title_card(ass: str, title: str, preset: CaptionPreset, seconds=TITLE_SECONDS) -> str:
    text = " ".join(title.split())
    if not text:
        return ass
    size, lines = fit_title(text.upper() if preset.uppercase else text, preset)
    style = ",".join(
        str(v)
        for v in [
            "Title",
            preset.font_name,
            size,
            ass_color("#FFFFFF"),
            ass_color("#FFFFFF"),
            ass_color("#000000"),
            ass_color("#000000", 0.0),
            0,
            0,
            0,
            0,
            100,
            100,
            preset.letter_spacing,
            0,
            1,
            0,  # no outline: the text sits on its own box
            0,
            5,
            0,
            0,
            0,
            1,
        ]
    )
    x, y = CANVAS[0] // 2, round(CANVAS[1] * TITLE_POSITION)
    # One translucent box behind all lines (ASS line boxes would overlap per line).
    # An ASS font size is the line height, so the block is len(lines) * size tall.
    width = max(text_width(line, preset.font_file, size, preset.letter_spacing) for line in lines)
    box_w, box_h = round(width + 2 * BOX_PADDING), round(len(lines) * size + 2 * BOX_PADDING)
    shape = f"m 0 0 l {box_w} 0 {box_w} {box_h} 0 {box_h}"
    fade = "\\fad(150,300)"
    times = f"{ass_time(0)},{ass_time(seconds)}"
    box = (
        f"Dialogue: 1,{times},Title,,0,0,0,,{{\\an5\\pos({x},{y}){fade}\\p1"
        f"\\1c&H000000&\\1a&H73&\\bord0\\shad0}}{shape}"
    )
    body = "\\N".join(escape_ass(line) for line in lines)
    words = f"Dialogue: 2,{times},Title,,0,0,0,,{{\\an5\\pos({x},{y}){fade}}}{body}"
    head, events = ass.split("[Events]\n", 1)
    head = head.replace("\nStyle: Caption,", f"\nStyle: {style}\nStyle: Caption,", 1)
    return f"{head}[Events]\n{events.rstrip()}\n{box}\n{words}\n"


def story_captions(words: list[dict], duration: float, preset_id: str | None):
    """(ASS text without the title card, snapshot for SRT/ASS exports) from voice words."""
    preset = get_preset(preset_id)
    timed = [{"text": w["text"], "start": w["start"], "end": w["end"]} for w in words]
    chunks = apply_min_duration(chunk_words(timed, OPTIONS), OPTIONS.min_duration, 0.0, duration)
    snapshot = {"preset": preset.id, "clipStart": 0.0, "chunks": chunks}
    return build_ass(chunks, preset, 0.0, OPTIONS), snapshot, preset
