"""Approximate rendered text width from the bundled font's glyph advances."""

from functools import lru_cache

from fontTools.ttLib import TTFont

from .presets import FONTS_DIR


@lru_cache(maxsize=8)
def _metrics(font_file: str):
    font = TTFont(FONTS_DIR / font_file, lazy=True)
    os2 = font["OS/2"]
    # libass (like VSFilter) sizes text so ascent + descent equals the ASS font size.
    em_per_size = font["head"].unitsPerEm / (os2.usWinAscent + os2.usWinDescent)
    return font.getBestCmap(), font["hmtx"].metrics, font["head"].unitsPerEm, em_per_size


def css_font_size(font_file: str, size: float) -> float:
    """The em size (CSS font-size) libass renders for an ASS font size."""
    return size * _metrics(font_file)[3]


def text_width(text: str, font_file: str, size: float, spacing: float = 0) -> float:
    cmap, advances, units, em_per_size = _metrics(font_file)
    fallback = advances.get(".notdef", (units // 2, 0))[0]
    total = sum(advances.get(cmap.get(ord(char), ".notdef"), (fallback, 0))[0] for char in text)
    return total / units * size * em_per_size + spacing * len(text)
