"""Optional background music, ducked under the narration.

Tracks are files you add to STORY_MUSIC_DIR (default MEDIA_DIR/story-library/music):
royalty-free or your own. None are bundled. The track is measured once and set so
that under the voice it sits 18 dB below the narration (-14 LUFS voice, -32 LUFS
music); after the last word it swells back up by 8 dB for the outro and fades out.
Because the voiceover is continuous (scenes are 0.25 s apart), ducking follows the
voice timeline directly rather than a sidechain compressor, which would pump in
every short pause.
"""

from pathlib import Path

from . import audio
from .visuals import folder_files

AUDIO_EXTENSIONS = {".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac", ".opus"}
UNDER_VOICE_DB = 18.0
OUTRO_SWELL_DB = 8.0
FADE_IN, SWELL, FADE_OUT = 0.5, 0.6, 1.0


def music_dir(settings) -> Path:
    configured = settings.story_music_dir
    return (
        Path(configured).expanduser()
        if configured
        else settings.media_dir / "story-library" / "music"
    )


def available(settings) -> list[dict]:
    return [
        {"id": p.name, "label": p.stem.replace("_", " ").replace("-", " ")}
        for p in folder_files(music_dir(settings), AUDIO_EXTENSIONS)
    ]


def resolve(settings, name: str) -> Path | None:
    directory = music_dir(settings).resolve()
    path = (directory / name).resolve()
    if path.parent != directory or path.suffix.lower() not in AUDIO_EXTENSIONS:
        return None
    return path if path.is_file() else None


def bed_gain(ffmpeg: str, track: Path) -> float:
    """dB to apply so the track sits UNDER_VOICE_DB below the -14 LUFS narration."""
    try:
        loudness = float(audio.measure(ffmpeg, track)["input_i"])
    except (KeyError, ValueError):
        return 0.0
    if loudness < -70:  # silent track: leave it alone
        return 0.0
    return audio.TARGET_LUFS - UNDER_VOICE_DB - loudness


def music_filter(index: int, gain_db: float, voice_end: float, duration: float) -> str:
    """Filter chain from input `index` to [music]: ducked bed, outro swell, fades."""
    swell = 10 ** (OUTRO_SWELL_DB / 20)
    rise = f"1+{swell - 1:.4f}*(t-{voice_end:.3f})/{SWELL}"
    envelope = f"if(lt(t,{voice_end:.3f}),1,min({swell:.4f},{rise}))"
    fade_start = max(0.0, duration - FADE_OUT)
    return (
        f"[{index}:a:0]aresample={audio.RATE},aformat=channel_layouts=stereo,"
        f"volume={gain_db:.2f}dB,volume=eval=frame:volume='{envelope}',"
        f"afade=t=in:d={FADE_IN},afade=t=out:st={fade_start:.3f}:d={FADE_OUT},"
        f"atrim=duration={duration:.3f}[music]"
    )
