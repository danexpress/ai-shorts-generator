"""Optional audio-loudness and scene-change signals for re-ranking LLM candidates.

Enabled with HIGHLIGHT_SIGNALS=true. Both come from FFmpeg filters already in the
bundled binary (ebur128, scdet). The adjustment is bounded (at most +/-1.25 on the
0-10 score) so the LLM's judgement of content stays dominant.
"""

import logging
import re
import statistics
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)

LOUDNESS = re.compile(r"t:\s*([\d.]+)\s+TARGET:.*?M:\s*(-?[\d.]+)")
SCENE = re.compile(r"lavfi\.scd\.time:\s*([\d.]+)")
SILENCE_LUFS = -70.0
LOUDNESS_WEIGHT, SCENE_WEIGHT = 0.75, 0.5


def _run(ffmpeg: str, args: list[str]) -> str:
    result = subprocess.run(
        [ffmpeg, "-hide_banner", "-nostats", *args, "-f", "null", "-"],
        capture_output=True,
        text=True,
        timeout=3600,
        check=False,
    )
    return result.stderr


def loudness_timeline(source: Path, ffmpeg: str) -> list[tuple[float, float]]:
    """(time, momentary loudness in LUFS) every 0.1 s; empty without audio."""
    log = _run(ffmpeg, ["-i", str(source), "-map", "0:a:0?", "-af", "ebur128=framelog=info"])
    return [(float(t), float(m)) for t, m in LOUDNESS.findall(log)]


def scene_changes(source: Path, ffmpeg: str, threshold: float = 10.0) -> list[float]:
    """Times of detected cuts, from a downscaled decode for speed."""
    log = _run(
        ffmpeg,
        ["-i", str(source), "-map", "0:v:0?", "-vf", f"scale=160:-2,scdet=threshold={threshold}"],
    )
    return [float(t) for t in SCENE.findall(log)]


def extract(source: Path, ffmpeg: str) -> dict:
    started = time.monotonic()
    signals = {
        "loudness": loudness_timeline(source, ffmpeg),
        "scenes": scene_changes(source, ffmpeg),
    }
    logger.info(
        "Extracted signals in %.1fs (%d loudness frames, %d scene changes)",
        time.monotonic() - started,
        len(signals["loudness"]),
        len(signals["scenes"]),
    )
    return signals


def rerank(candidates: list[dict], signals: dict, duration: float) -> list[dict]:
    """Adjust scores by how loud and visually dynamic each window is vs. the whole video."""
    voiced = [m for _, m in signals.get("loudness", []) if m > SILENCE_LUFS]
    baseline = statistics.median(voiced) if voiced else None
    scenes = signals.get("scenes", [])
    average_rate = len(scenes) / duration if duration else 0.0
    ranked = []
    for candidate in candidates:
        start, end = candidate["start"], candidate["end"]
        window = [
            m for t, m in signals.get("loudness", []) if start <= t <= end and m > SILENCE_LUFS
        ]
        loud = statistics.mean(window) - baseline if window and baseline is not None else 0.0
        cuts = sum(1 for t in scenes if start <= t <= end)
        rate = cuts / (end - start)
        scene = (rate - average_rate) / max(average_rate, 0.05) if scenes else 0.0
        adjustment = LOUDNESS_WEIGHT * max(-1.0, min(1.0, loud / 6)) + SCENE_WEIGHT * max(
            -1.0, min(1.0, scene)
        )
        ranked.append(
            {
                **candidate,
                "score": round(max(0.0, min(10.0, candidate["score"] + adjustment)), 2),
                "signals": {
                    "loudnessLu": round(loud, 1),
                    "sceneChanges": cuts,
                    "adjustment": round(adjustment, 2),
                },
            }
        )
    return sorted(ranked, key=lambda c: -c["score"])
