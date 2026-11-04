"""An offline stand-in voice: a soft tone per word at 150 words per minute.

Used with STORY_TTS_PROVIDER=demo and in tests; it needs no model or API key and
returns word timestamps like a real provider.
"""

import math
import re
import struct
import wave

from .base import Speech, TTSProvider, Voice

SAMPLE_RATE = 24_000
WORD = re.compile(r"\S+")


class DemoProvider(TTSProvider):
    id = "demo"
    label = "Demo tones (no real voice)"
    speed_range = (0.5, 2.0)

    def voices(self):
        return [Voice("low", "Low tone"), Voice("high", "High tone")]

    def synthesize(self, text, voice, speed, destination, context):
        pitch = 220 if voice == "low" else 440
        per_word = 60 / 150 / speed
        spoken = per_word * 0.8
        count = int(spoken * SAMPLE_RATE)
        tone = b"".join(  # one soft tone, reused for every word
            struct.pack(
                "<h",
                int(
                    0.3
                    * 32767
                    * math.sin(math.pi * i / count)
                    * math.sin(2 * math.pi * pitch * i / SAMPLE_RATE)
                ),
            )
            for i in range(count)
        )
        gap = b"\x00\x00" * int((per_word - spoken) * SAMPLE_RATE)
        words, frames, clock = [], bytearray(), 0.0
        for match in WORD.finditer(text):
            if not any(c.isalnum() for c in match.group()):
                continue
            frames += tone + gap
            words.append(
                {"text": match.group(), "start": round(clock, 3), "end": round(clock + spoken, 3)}
            )
            clock += per_word
        with wave.open(str(destination), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(bytes(frames))
        return Speech(words, len(text))
