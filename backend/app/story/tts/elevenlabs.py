"""ElevenLabs hosted TTS with character timestamps.

Needs ELEVENLABS_API_KEY. Voices default to a few premade voices; set
ELEVENLABS_VOICES="voice_id:Label,..." to offer your own. Cost logs use
ELEVENLABS_COST_PER_1K_CHARS (your plan's price), since pricing depends on the plan.
"""

import base64

import httpx

from .base import Speech, TTSFailed, TTSProvider, TTSUnavailable, Voice

API = "https://api.elevenlabs.io/v1/text-to-speech/{voice}/with-timestamps"
# Long-standing premade voices. Availability depends on your account: copy voice IDs
# from your ElevenLabs Voice Library into ELEVENLABS_VOICES to be sure.
DEFAULT_VOICES = [
    Voice("21m00Tcm4TlvDq8ikWAM", "Rachel", "Calm · female"),
    Voice("pNInz6obpgDQGcFmaJgB", "Adam", "Deep · male"),
    Voice("EXAVITQu4vr4xnSDxMaL", "Sarah", "Soft · female"),
]


def parse_voices(spec: str) -> list[Voice]:
    voices = []
    for item in filter(None, (part.strip() for part in spec.split(","))):
        voice_id, _, label = item.partition(":")
        voices.append(Voice(voice_id.strip(), label.strip() or voice_id.strip()))
    return voices


def words_from_alignment(alignment: dict | None) -> list[dict] | None:
    """Group per-character times into words (runs of non-space characters)."""
    if not alignment:
        return None
    chars = alignment.get("characters") or []
    starts = alignment.get("character_start_times_seconds") or []
    ends = alignment.get("character_end_times_seconds") or []
    if not chars or not len(chars) == len(starts) == len(ends):
        return None
    words, current = [], None
    for char, start, end in zip(chars, starts, ends):
        if char.isspace():
            current = None
            continue
        if current is None:
            current = {"text": "", "start": None, "end": None}
            words.append(current)
        current["text"] += char
        if char.isalnum():
            current["start"] = start if current["start"] is None else current["start"]
            current["end"] = end
    return [
        {"text": w["text"], "start": round(w["start"], 3), "end": round(w["end"], 3)}
        for w in words
        if w["start"] is not None
    ]


class ElevenLabsProvider(TTSProvider):
    id = "elevenlabs"
    label = "ElevenLabs (hosted)"
    speed_range = (0.7, 1.2)

    def __init__(self, settings, http: httpx.Client | None = None):
        super().__init__(settings)
        self.http = http

    @property
    def model(self) -> str:
        return self.settings.elevenlabs_model

    @property
    def cost_per_character(self) -> float:
        return self.settings.elevenlabs_cost_per_1k_chars / 1000

    def unavailable_reason(self):
        if not self.settings.elevenlabs_api_key:
            return "Set ELEVENLABS_API_KEY on the server to use ElevenLabs voices."
        return None

    def voices(self):
        return parse_voices(self.settings.elevenlabs_voices) or DEFAULT_VOICES

    def synthesize(self, text, voice, speed, destination, context):
        if self.unavailable_reason():
            raise TTSUnavailable(self.unavailable_reason())
        body = {
            "text": text,
            "model_id": self.model,
            "voice_settings": {"stability": 0.5, "similarity_boost": 0.75, "speed": speed},
            # Neighbouring scenes keep intonation continuous across separate requests.
            "previous_text": context.get("previous") or None,
            "next_text": context.get("next") or None,
        }
        client = self.http or httpx.Client(timeout=httpx.Timeout(60, connect=10))
        try:
            response = client.post(
                API.format(voice=voice),
                params={"output_format": "mp3_44100_128"},
                headers={"xi-api-key": self.settings.elevenlabs_api_key},
                json=body,
            )
        except httpx.HTTPError as exc:
            raise TTSUnavailable("Could not reach ElevenLabs.") from exc
        finally:
            if self.http is None:
                client.close()
        if response.status_code in (401, 403):
            raise TTSUnavailable("ElevenLabs rejected the API key or plan for this voice.")
        if response.status_code == 429 or response.status_code >= 500:
            raise TTSUnavailable(f"ElevenLabs is busy (HTTP {response.status_code}); retry later.")
        if response.status_code != 200:
            raise TTSFailed(f"ElevenLabs could not speak this scene (HTTP {response.status_code}).")
        try:
            data = response.json()
            audio = base64.b64decode(data["audio_base64"])
        except (ValueError, KeyError, TypeError) as exc:
            raise TTSUnavailable("ElevenLabs returned an unreadable response.") from exc
        destination.write_bytes(audio)
        return Speech(words_from_alignment(data.get("alignment")), len(text))
