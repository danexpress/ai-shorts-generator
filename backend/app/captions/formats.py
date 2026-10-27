"""ASS (karaoke, burned in) and SRT (plain) writers for caption chunks.

Chunk times are absolute source seconds; output times are relative to the clip
start because renders begin at the clip start.
"""

from .chunking import DEFAULT_OPTIONS, ChunkOptions, layout
from .measure import text_width
from .presets import CANVAS, CaptionPreset, max_text_width


def ass_color(hex_color: str, opacity: float = 1.0) -> str:
    value = hex_color.lstrip("#")
    red, green, blue = value[0:2], value[2:4], value[4:6]
    alpha = round((1 - opacity) * 255)
    return f"&H{alpha:02X}{blue}{green}{red}".upper()


def ass_alpha(opacity: float) -> str:
    return f"&H{round((1 - opacity) * 255):02X}&"


def ass_time(seconds: float) -> str:
    centis = max(0, round(seconds * 100))
    hours, rest = divmod(centis, 360_000)
    minutes, rest = divmod(rest, 6000)
    secs, centis = divmod(rest, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centis:02d}"


def srt_time(seconds: float) -> str:
    millis = max(0, round(seconds * 1000))
    hours, rest = divmod(millis, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def escape_ass(text: str) -> str:
    # Braces start override blocks and backslashes start tags; neither may leak in.
    return text.replace("\\", "⧵").replace("{", "(").replace("}", ")")


def chunk_lines(texts: list[str], max_chars: int, max_lines: int) -> list[list[int]]:
    """Word indexes per line. Edited chunks that no longer fit wrap greedily."""
    lines = layout(texts, max_chars, max_lines)
    if lines is None:
        lines, current = [], []
        for text in texts:
            if current and len(" ".join(current + [text])) > max_chars:
                lines.append(current)
                current = []
            current.append(text)
        lines.append(current)
    indexes, cursor = [], 0
    for line in lines:
        indexes.append(list(range(cursor, cursor + len(line))))
        cursor += len(line)
    return indexes


def display(text: str, preset: CaptionPreset) -> str:
    return text.upper() if preset.uppercase else text


def fit_scale(lines: list[str], preset: CaptionPreset) -> int:
    """Percent scale that keeps the widest line (incl. active-word pop) in the safe zone."""
    widest = max(
        text_width(line, preset.font_file, preset.font_size, preset.letter_spacing)
        for line in lines
    )
    widest = widest * max(100, preset.active_scale) / 100 + 2 * preset.outline
    return min(100, int(max_text_width() / widest * 100)) if widest else 100


def build_ass(
    chunks: list[dict],
    preset: CaptionPreset,
    clip_start: float,
    options: ChunkOptions = DEFAULT_OPTIONS,
) -> str:
    width, height = CANVAS
    style = ",".join(
        str(v)
        for v in [
            "Caption",
            preset.font_name,
            preset.font_size,
            ass_color(preset.text_color, preset.inactive_opacity),
            ass_color(preset.active_color),
            ass_color(preset.outline_color),
            ass_color(preset.shadow_color, preset.shadow_opacity),
            0,  # bold: the font file already has the intended weight
            0,
            0,
            0,
            100,
            100,
            preset.letter_spacing,
            0,
            1,  # outline + drop shadow
            preset.outline,
            preset.shadow,
            5,  # centred on \pos
            0,
            0,
            0,
            1,
        ]
    )
    events = []
    x, y = width // 2, round(height * preset.position)
    inactive, active = ass_alpha(preset.inactive_opacity), ass_alpha(1.0)
    text_c, active_c = ass_color(preset.text_color)[4:], ass_color(preset.active_color)[4:]
    for chunk in chunks:
        words = [escape_ass(display(w["text"], preset)) for w in chunk["words"]]
        lines = chunk_lines(words, options.max_line_chars, options.max_lines)
        scale = fit_scale([" ".join(words[i] for i in line) for line in lines], preset)
        pop = round(scale * preset.active_scale / 100)
        base = f"{{\\an5\\pos({x},{y})\\fscx{scale}\\fscy{scale}}}"
        for index, word in enumerate(chunk["words"]):
            start = chunk["start"] if index == 0 else word["start"]
            end = (
                chunk["words"][index + 1]["start"]
                if index + 1 < len(chunk["words"])
                else chunk["end"]
            )
            rendered = []
            for line in lines:
                parts = []
                for i in line:
                    if i == index:
                        parts.append(
                            f"{{\\1c&H{active_c}&\\1a{active}\\fscx{pop}\\fscy{pop}}}{words[i]}"
                            f"{{\\1c&H{text_c}&\\1a{inactive}\\fscx{scale}\\fscy{scale}}}"
                        )
                    elif i < index and preset.inactive_opacity < 1:
                        # Only upcoming words are dimmed; spoken ones stay at full opacity.
                        parts.append(f"{{\\1a{active}}}{words[i]}{{\\1a{inactive}}}")
                    else:
                        parts.append(words[i])
                rendered.append(" ".join(parts))
            text = "\\N".join(rendered)  # ASS hard line break
            events.append(
                f"Dialogue: 0,{ass_time(start - clip_start)},{ass_time(end - clip_start)},"
                f"Caption,,0,0,0,,{base}{text}"
            )
    return "\n".join(
        [
            "[Script Info]",
            "ScriptType: v4.00+",
            f"PlayResX: {width}",
            f"PlayResY: {height}",
            "WrapStyle: 2",
            "ScaledBorderAndShadow: yes",
            "",
            "[V4+ Styles]",
            (
                "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
                "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
                "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding"
            ),
            f"Style: {style}",
            "",
            "[Events]",
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
            *events,
            "",
        ]
    )


def build_srt(chunks: list[dict], clip_start: float, options: ChunkOptions = DEFAULT_OPTIONS):
    cues = []
    for number, chunk in enumerate(chunks, 1):
        words = [w["text"] for w in chunk["words"]]
        lines = chunk_lines(words, options.max_line_chars, options.max_lines)
        text = "\n".join(" ".join(words[i] for i in line) for line in lines)
        cues.append(
            f"{number}\n{srt_time(chunk['start'] - clip_start)} --> "
            f"{srt_time(chunk['end'] - clip_start)}\n{text}\n"
        )
    return "\n".join(cues)
