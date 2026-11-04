"""Voiceover assembly: decode scene audio, trim its edges, join scenes with a short
pause, and normalize loudness to about -14 LUFS (two-pass FFmpeg loudnorm).

Audio is handled as 48 kHz mono 16-bit PCM, so lengths and offsets are exact
sample counts and the pause between scenes is precisely what was asked for.
"""

import json
import re
import subprocess
import wave
from array import array
from pathlib import Path

RATE = 48_000
BYTES_PER_SECOND = RATE * 2
SILENCE_DBFS = -45.0
LEAD_MARGIN, TAIL_MARGIN = 0.04, 0.08  # keep a breath of room around the speech
TARGET_LUFS, TRUE_PEAK, LOUDNESS_RANGE = -14.0, -1.5, 11.0


class AudioFailed(RuntimeError):
    pass


def run(command: list[str], timeout: int = 300) -> subprocess.CompletedProcess:
    try:
        result = subprocess.run(command, capture_output=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AudioFailed(f"FFmpeg did not run: {exc}") from exc
    if result.returncode:
        raise AudioFailed(f"FFmpeg failed: {result.stderr.decode(errors='replace')[-300:]}")
    return result


def decode(ffmpeg: str, source: Path) -> bytes:
    """Any audio file to 48 kHz mono s16le PCM."""
    command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-i", str(source)]
    command += ["-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"]
    return run(command).stdout


def seconds(pcm: bytes) -> float:
    return len(pcm) / BYTES_PER_SECOND


def silence(duration: float) -> bytes:
    return b"\x00\x00" * round(duration * RATE)


def trim(pcm: bytes) -> tuple[bytes, float]:
    """Cut leading/trailing silence (below -45 dBFS), keeping a small margin.

    Returns (trimmed audio, seconds removed from the start) so word times can shift.
    """
    samples = array("h", pcm)
    threshold = 32767 * 10 ** (SILENCE_DBFS / 20)
    first = next((i for i, s in enumerate(samples) if abs(s) > threshold), None)
    if first is None:
        return pcm, 0.0
    last = next(i for i in range(len(samples) - 1, -1, -1) if abs(samples[i]) > threshold)
    start = max(0, first - round(LEAD_MARGIN * RATE))
    end = min(len(samples), last + 1 + round(TAIL_MARGIN * RATE))
    return samples[start:end].tobytes(), start / RATE


def join(parts: list[bytes], pause: float) -> tuple[bytes, list[tuple[float, float]]]:
    """Concatenate scene audio with `pause` seconds of silence between scenes.

    Returns the audio and each scene's (start, end) in the joined timeline.
    """
    gap = silence(pause)
    out, spans = bytearray(), []
    for index, part in enumerate(parts):
        if index:
            out += gap
        start = seconds(out)
        out += part
        spans.append((round(start, 3), round(seconds(out), 3)))
    return bytes(out), spans


def write_wav(path: Path, pcm: bytes):
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(pcm)


def read_wav(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate()) != (1, 2, RATE):
            raise AudioFailed(f"{path.name} is not 48 kHz mono 16-bit audio.")
        return source.readframes(source.getnframes())


MEASURED = re.compile(r"\{[^{}]*\"input_i\"[^{}]*\}", re.DOTALL)


def measure(ffmpeg: str, source: Path) -> dict:
    """First loudnorm pass: the file's integrated loudness, peak and range."""
    target = f"I={TARGET_LUFS}:TP={TRUE_PEAK}:LRA={LOUDNESS_RANGE}"
    command = [ffmpeg, "-hide_banner", "-nostats", "-i", str(source)]
    command += ["-af", f"loudnorm={target}:print_format=json", "-f", "null", "-"]
    match = MEASURED.search(run(command).stderr.decode(errors="replace"))
    if not match:
        raise AudioFailed("FFmpeg did not report loudness.")
    return json.loads(match.group())


def normalize(ffmpeg: str, source: Path, destination: Path) -> dict:
    """Bring the voiceover to about -14 LUFS with peaks kept under -1.5 dBFS.

    Gain plus a peak limiter, corrected from the measured result: the limiter takes
    some loudness back on peaky speech (plain loudnorm undershoots by 1-2 LU for the
    same reason), so up to three passes adjust the gain until within 0.5 LU.
    Silent input is copied unchanged. Returns {"inputLufs", "outputLufs"}.
    """
    try:
        loudness = float(measure(ffmpeg, source)["input_i"])
    except (KeyError, ValueError):  # "-inf" for silence
        loudness = None
    if loudness is None or loudness < -70:
        destination.write_bytes(source.read_bytes())
        return {"inputLufs": None, "outputLufs": None}
    # The limiter sees samples, not true (inter-sample) peaks: leave 1 dB of headroom.
    ceiling = 10 ** ((TRUE_PEAK - 1.0) / 20)
    gain, output = TARGET_LUFS - loudness, None
    for _attempt in range(3):
        chain = f"volume={gain:.2f}dB,alimiter=limit={ceiling:.4f}:attack=5:release=50:level=false"
        command = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-i", str(source)]
        command += ["-af", chain, "-ar", str(RATE), "-ac", "1", "-c:a", "pcm_s16le"]
        run(command + [str(destination)])
        output = float(measure(ffmpeg, destination)["input_i"])
        if abs(output - TARGET_LUFS) <= 0.5:
            break
        gain += min(6.0, TARGET_LUFS - output)
    return {"inputLufs": round(loudness, 2), "outputLufs": round(output, 2)}
