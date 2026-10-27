"""Caption style presets. Every visual value for a style lives in its preset.

Coordinates are on the 1080x1920 caption canvas (ASS PlayRes); libass scales them
to 720p renders. To add a preset, add a CaptionPreset to PRESETS (see README).
Presets are purely visual: chunking rules (words per chunk, line length) are shared
by all presets in chunking.ChunkOptions, so switching style never re-chunks edits.
"""

from dataclasses import asdict, dataclass
from pathlib import Path

FONTS_DIR = Path(__file__).resolve().parent / "fonts"
CANVAS = (1080, 1920)

# Areas covered by YouTube Shorts / TikTok / Reels overlays on a 1080x1920 frame:
# the top bar, the title/channel block at the bottom, and the action buttons on the
# right. Caption text must stay inside the remaining box.
SAFE_ZONE = {"left": 60, "top": 240, "right": 170, "bottom": 480}


@dataclass(frozen=True)
class CaptionPreset:
    id: str
    label: str
    description: str
    font_file: str  # file in FONTS_DIR
    font_name: str  # full font name libass matches against
    font_size: int  # ASS font size on the 1080x1920 canvas
    text_color: str  # "#RRGGBB" for words not being spoken
    active_color: str  # colour of the word being spoken
    outline_color: str
    outline: float  # outline thickness in canvas pixels
    shadow: float = 0
    shadow_color: str = "#000000"
    shadow_opacity: float = 0.0  # 0 transparent .. 1 opaque
    inactive_opacity: float = 1.0  # dims words not being spoken
    active_scale: int = 100  # % size of the active word ("pop")
    uppercase: bool = False
    letter_spacing: float = 0
    line_height: float = 1.1  # multiple of the font size, used for safe-zone checks
    position: float = 0.67  # vertical centre of the caption block, fraction of height

    def public(self) -> dict:
        from .measure import css_font_size

        data = asdict(self)
        style = {k: v for k, v in data.items() if k not in {"id", "label", "description"}}
        style.pop("font_file")
        # For browser previews: the CSS font size libass effectively uses, and the
        # widest line (fraction of frame width) before captions shrink to fit.
        style["css_font_size"] = round(css_font_size(self.font_file, self.font_size), 1)
        style["max_width"] = round(max_text_width() / CANVAS[0], 4)
        return {"id": self.id, "label": self.label, "description": self.description, "style": style}


PRESETS: dict[str, CaptionPreset] = {
    preset.id: preset
    for preset in [
        CaptionPreset(
            id="clean-bold",
            label="Clean Bold",
            description="White ExtraBold words with a thick outline; the spoken word turns yellow.",
            font_file="Montserrat-ExtraBold.ttf",
            font_name="Montserrat ExtraBold",
            font_size=124,
            text_color="#FFFFFF",
            active_color="#FFE500",
            outline_color="#000000",
            outline=7,
        ),
        CaptionPreset(
            id="creator-pop",
            label="Creator Pop",
            description="Uppercase Black weight; the spoken word pops larger in green.",
            font_file="Montserrat-Black.ttf",
            font_name="Montserrat Black",
            font_size=116,
            text_color="#FFFFFF",
            active_color="#39FF6A",
            outline_color="#000000",
            outline=8,
            shadow=4,
            shadow_opacity=0.6,
            active_scale=115,
            uppercase=True,
        ),
        CaptionPreset(
            id="minimal-highlight",
            label="Minimal Highlight",
            description="Smaller SemiBold text; upcoming words are dimmed until spoken.",
            font_file="Montserrat-SemiBold.ttf",
            font_name="Montserrat SemiBold",
            font_size=100,
            text_color="#FFFFFF",
            active_color="#FFFFFF",
            outline_color="#000000",
            outline=3,
            shadow=3,
            shadow_opacity=0.5,
            inactive_opacity=0.55,
        ),
    ]
}
DEFAULT_PRESET = "clean-bold"


def get_preset(preset_id: str | None) -> CaptionPreset:
    return PRESETS.get(preset_id or DEFAULT_PRESET) or PRESETS[DEFAULT_PRESET]


def max_text_width() -> float:
    """Widest centred line that stays inside the safe zone."""
    centre = CANVAS[0] / 2
    return 2 * min(centre - SAFE_ZONE["left"], CANVAS[0] - SAFE_ZONE["right"] - centre)
