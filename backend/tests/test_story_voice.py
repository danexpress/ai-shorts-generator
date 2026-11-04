import base64
import json
import wave
from array import array
from dataclasses import replace
from itertools import pairwise
from types import SimpleNamespace

import httpx
import pytest

from app.renderer import ffmpeg_path
from app.story import audio, cache
from app.story.script import SCENE_PAUSE, ScriptRequest, demo_script, parse_script
from app.story.timing import scene_timings, script_words, transfer
from app.story.tts import TTSFailed, TTSUnavailable
from app.story.tts.demo import DemoProvider
from app.story.tts.elevenlabs import (
    ElevenLabsProvider,
    parse_voices,
    words_from_alignment,
)
from app.story.tts.kokoro import words_from_tokens
from app.story.voice import VoiceRequest, generate_voice

FFMPEG = ffmpeg_path(None)
DEMO = VoiceRequest("demo", "low", 1.0)


def tone(seconds, amplitude=8000, lead=0.0, tail=0.0):
    count = round(seconds * audio.RATE)
    body = array("h", [amplitude if i % 40 < 20 else -amplitude for i in range(count)])
    return audio.silence(lead) + body.tobytes() + audio.silence(tail)


def short_script(*narrations):
    return parse_script(
        {
            "title": "T",
            "hook": "One.",
            "scenes": [
                {"id": i, "narration": n, "visual_description": "v", "mood": "m"}
                for i, n in enumerate(narrations, 1)
            ],
        }
    )[0]


# ---------- audio assembly ----------


def test_trim_cuts_edge_silence_but_keeps_a_small_margin():
    trimmed, cut = audio.trim(tone(1.0, lead=0.5, tail=0.7))
    assert cut == pytest.approx(0.5 - audio.LEAD_MARGIN, abs=0.001)
    assert audio.seconds(trimmed) == pytest.approx(
        1.0 + audio.LEAD_MARGIN + audio.TAIL_MARGIN, abs=0.001
    )
    silent = audio.silence(0.3)
    assert audio.trim(silent) == (silent, 0.0)


def test_join_inserts_exact_pauses_and_reports_scene_spans():
    joined, spans = audio.join([tone(1.0), tone(2.0), tone(0.5)], 0.25)
    assert audio.seconds(joined) == pytest.approx(3.5 + 2 * 0.25)
    assert spans == [(0.0, 1.0), (1.25, 3.25), (3.5, 4.0)]
    gap = array("h", joined[audio.BYTES_PER_SECOND : int(1.25 * audio.BYTES_PER_SECOND)])
    assert max(map(abs, gap)) == 0


def test_decode_resamples_to_48k_mono(tmp_path):
    source = tmp_path / "speech.wav"
    with wave.open(str(source), "wb") as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(24_000)
        out.writeframes(b"\x10\x00" * 2 * 24_000)  # 1 s of stereo
    assert audio.seconds(audio.decode(FFMPEG, source)) == pytest.approx(1.0, abs=0.01)


def test_normalize_brings_speech_to_about_minus_14_lufs(tmp_path):
    quiet = tmp_path / "quiet.wav"
    DemoProvider(None).synthesize(" ".join(["word"] * 40), "low", 1.0, quiet, {})
    resampled = tmp_path / "in.wav"
    audio.write_wav(resampled, audio.decode(FFMPEG, quiet))
    result = audio.normalize(FFMPEG, resampled, tmp_path / "out.wav")
    assert result["outputLufs"] == pytest.approx(audio.TARGET_LUFS, abs=0.6)
    assert audio.measure(FFMPEG, tmp_path / "out.wav")["input_tp"]  # measurable output
    assert float(audio.measure(FFMPEG, tmp_path / "out.wav")["input_tp"]) <= -0.5


def test_normalize_leaves_silence_alone(tmp_path):
    source = tmp_path / "silent.wav"
    audio.write_wav(source, audio.silence(1.0))
    result = audio.normalize(FFMPEG, source, tmp_path / "out.wav")
    assert result == {"inputLufs": None, "outputLufs": None}
    assert (tmp_path / "out.wav").read_bytes() == source.read_bytes()


# ---------- word timings ----------


def test_script_words_keep_punctuation_as_written():
    assert script_words("Wait — it's 3 a.m. Right?") == ["Wait", "it's", "3", "a.m.", "Right?"]


def test_transfer_keeps_script_words_and_fills_unmatched_ones():
    timed = [
        {"text": "It", "start": 0.0, "end": 0.2},
        {"text": "was", "start": 0.2, "end": 0.4},
        {"text": "three", "start": 0.5, "end": 0.9},  # spoken form of "3"
        {"text": "o'clock.", "start": 0.9, "end": 1.4},
    ]
    words = transfer(["It", "was", "3", "o'clock."], timed, 2.0)
    assert [w["text"] for w in words] == ["It", "was", "3", "o'clock."]
    assert words[2] == {"text": "3", "start": 0.4, "end": 0.9}  # spread over the gap
    assert words[3]["start"] == 0.9


def test_without_any_timing_words_are_spread_over_the_audio_by_length():
    words = transfer(["a", "longer", "word"], None, 3.0)
    assert words[0]["start"] == 0 and words[-1]["end"] == 3.0
    assert words[1]["end"] - words[1]["start"] > words[0]["end"] - words[0]["start"]


def test_timing_sources_fall_back_in_order(tmp_path):
    settings = SimpleNamespace(whisper_align="")
    path = tmp_path / "a.wav"
    provided = [{"text": "Hello", "start": 0.1, "end": 0.5}]
    assert scene_timings("Hello", path, 1, provided, settings)[1] == "provider"

    class Transcriber:
        def transcribe(self, source):
            return {"segments": [{"words": [{"text": "Hello", "start": 0.2, "end": 0.6}]}]}

    words, source = scene_timings("Hello", path, 1, None, settings, lambda: Transcriber())
    assert source == "transcription" and words[0]["start"] == 0.2

    def broken():
        raise RuntimeError("no whisper")

    assert scene_timings("Hello", path, 1, None, settings, broken)[1] == "estimate"


# ---------- provider adapters ----------


def test_kokoro_tokens_become_words_across_chunks():
    token = lambda text, s, e: SimpleNamespace(text=text, start_ts=s, end_ts=e)
    first = SimpleNamespace(
        tokens=[token("The", 0.25, 0.4), token("end", 0.4, 0.8), token(".", 0.8, 0.9)],
        audio=[0] * 24_000,  # 1 s
    )
    second = SimpleNamespace(tokens=[token("Again", 0.1, 0.5), token("x", None, None)], audio=[])
    assert words_from_tokens([first, second]) == [
        {"text": "The", "start": 0.25, "end": 0.4},
        {"text": "end.", "start": 0.4, "end": 0.8},
        {"text": "Again", "start": 1.1, "end": 1.5},
    ]


def test_elevenlabs_characters_become_words():
    text = "Hi, you."
    alignment = {
        "characters": list(text),
        "character_start_times_seconds": [i * 0.1 for i in range(len(text))],
        "character_end_times_seconds": [i * 0.1 + 0.1 for i in range(len(text))],
    }
    assert words_from_alignment(alignment) == [
        {"text": "Hi,", "start": 0.0, "end": 0.2},
        {"text": "you.", "start": 0.4, "end": 0.7},
    ]
    assert words_from_alignment(None) is None
    assert parse_voices("abc:Narrator, def") == [
        parse_voices("abc:Narrator")[0],
        parse_voices("def:def")[0],
    ]


def eleven(handler, **overrides):
    settings = SimpleNamespace(
        elevenlabs_api_key="xi-secret",
        elevenlabs_model="eleven_multilingual_v2",
        elevenlabs_voices="",
        elevenlabs_cost_per_1k_chars=0.3,
        **overrides,
    )
    return ElevenLabsProvider(settings, httpx.Client(transport=httpx.MockTransport(handler)))


def test_elevenlabs_request_shape_and_cost(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            200,
            json={
                "audio_base64": base64.b64encode(b"mp3-bytes").decode(),
                "alignment": {
                    "characters": ["H", "i"],
                    "character_start_times_seconds": [0.0, 0.1],
                    "character_end_times_seconds": [0.1, 0.2],
                },
            },
        )

    provider = eleven(handler)
    context = {"previous": "Before.", "next": "After."}
    speech = provider.synthesize("Hi", "voice-1", 1.1, tmp_path / "out", context)
    request = seen[0]
    assert request.url.path == "/v1/text-to-speech/voice-1/with-timestamps"
    assert request.url.params["output_format"] == "mp3_44100_128"
    assert request.headers["xi-api-key"] == "xi-secret"
    body = json.loads(request.content)
    assert body["voice_settings"]["speed"] == 1.1 and body["model_id"] == "eleven_multilingual_v2"
    assert (body["previous_text"], body["next_text"]) == ("Before.", "After.")
    assert (tmp_path / "out").read_bytes() == b"mp3-bytes"
    assert speech.words == [{"text": "Hi", "start": 0.0, "end": 0.2}] and speech.characters == 2
    assert provider.cost_per_character == pytest.approx(0.0003)


@pytest.mark.parametrize(
    "status,error",
    [(401, TTSUnavailable), (429, TTSUnavailable), (503, TTSUnavailable), (400, TTSFailed)],
)
def test_elevenlabs_errors(tmp_path, status, error):
    provider = eleven(lambda request: httpx.Response(status, json={"detail": "x"}))
    with pytest.raises(error):
        provider.synthesize("Hi", "v", 1.0, tmp_path / "out", {})


def test_elevenlabs_needs_a_key():
    settings = SimpleNamespace(elevenlabs_api_key="", elevenlabs_voices="")
    provider = ElevenLabsProvider(settings)
    assert "ELEVENLABS_API_KEY" in provider.unavailable_reason()
    assert provider.public()["available"] is False and provider.public()["voices"] == []


# ---------- the voice pipeline (demo provider) ----------


class CountingDemo(DemoProvider):
    def __init__(self):
        super().__init__(None)
        self.spoken = []

    def synthesize(self, text, voice, speed, destination, context):
        self.spoken.append(text)
        return super().synthesize(text, voice, speed, destination, context)


@pytest.fixture
def voice_settings(settings):
    return replace(settings, whisper_align="")


def test_voiceover_timeline_words_and_cache(voice_settings, tmp_path):
    script = short_script("One. Two three.", "Four five six.", "Seven.")
    provider = CountingDemo()
    result = generate_voice(script, DEMO, provider, voice_settings, FFMPEG, tmp_path / "v.wav")
    scenes = result["scenes"]
    assert [s["id"] for s in scenes] == [1, 2, 3]
    for before, after in pairwise(scenes):
        assert after["start"] - before["end"] == pytest.approx(SCENE_PAUSE, abs=0.001)
    assert result["durationSec"] == pytest.approx(scenes[-1]["end"], abs=0.001)
    assert [w["text"] for w in result["words"]] == [
        "One.",
        "Two",
        "three.",
        "Four",
        "five",
        "six.",
        "Seven.",
    ]
    assert all(
        s["start"] <= w["start"] < w["end"] <= s["end"] + 0.001
        for w in result["words"]
        for s in scenes
        if s["id"] == w["scene"]
    )
    assert result["timingSources"] == ["provider"] and result["cachedScenes"] == 0
    with wave.open(str(tmp_path / "v.wav")) as out:
        assert out.getframerate() == audio.RATE and out.getnchannels() == 1
    # Unchanged script: nothing is spoken again. One edited scene: only it is.
    again = generate_voice(script, DEMO, provider, voice_settings, FFMPEG, tmp_path / "v2.wav")
    assert again["cachedScenes"] == 3 and len(provider.spoken) == 3
    edited = short_script("One. Two three.", "Four five, six!", "Seven.")
    generate_voice(edited, DEMO, provider, voice_settings, FFMPEG, tmp_path / "v3.wav")
    assert provider.spoken[3:] == ["Four five, six!"]
    other_voice = replace(DEMO, voice="high")
    generate_voice(script, other_voice, provider, voice_settings, FFMPEG, tmp_path / "v4.wav")
    assert len(provider.spoken) == 7  # a different voice is a different cache key


def test_only_newly_spoken_characters_are_billed(voice_settings, tmp_path):
    paid = type("Paid", (CountingDemo,), {"cost_per_character": 0.001})()
    script = short_script("One two.", "Three.")
    first = generate_voice(script, DEMO, paid, voice_settings, FFMPEG, tmp_path / "a")
    assert first["billedCharacters"] == len("One two.") + len("Three.")
    assert first["costUsd"] == pytest.approx(0.014)
    second = generate_voice(script, DEMO, paid, voice_settings, FFMPEG, tmp_path / "b")
    assert second["billedCharacters"] == 0 and second["costUsd"] == 0
    assert len(paid.spoken) == 2


def test_long_voiceovers_are_flagged(voice_settings, tmp_path):
    script = demo_script(ScriptRequest("mystery", 60, topic="x"))
    slow = replace(DEMO, speed=0.6)
    result = generate_voice(script, slow, CountingDemo(), voice_settings, FFMPEG, tmp_path / "v")
    assert result["durationSec"] > 60 and result["tooLong"] is True


def test_providers_without_audio_fail_cleanly(voice_settings, tmp_path):
    class Silent(DemoProvider):
        def synthesize(self, text, voice, speed, destination, context):
            audio.write_wav(destination, audio.silence(0.0))
            return super().synthesize("", voice, speed, destination, context)

    with pytest.raises(TTSFailed, match="no audio"):
        generate_voice(
            short_script("Hi."), DEMO, Silent(None), voice_settings, FFMPEG, tmp_path / "v"
        )
    assert not list(cache.cache_root(voice_settings.media_dir).rglob("*.wav"))
