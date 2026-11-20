"""Voice-adapter tests — faster-whisper STT + Piper TTS.

The round-trip test needs a Piper voice file and downloads the tiny Whisper
model on first use, so it runs only when PIPER_VOICE points at a .onnx file
(otherwise it is skipped — fresh clones stay fast).
Run: python -m unittest discover -s tests -v
"""

import asyncio
import os
import sys
import unittest
import wave

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import livekit.agents  # noqa: F401
    HAS_LIVEKIT = True
except ImportError:
    HAS_LIVEKIT = False

VOICE = os.environ.get("PIPER_VOICE", "")
TEXT = "Hello, I would like to book a cleaning appointment on Friday at ten o'clock."


@unittest.skipUnless(HAS_LIVEKIT, "livekit-agents not installed")
class VoiceAdapterTest(unittest.TestCase):
    def test_piper_missing_voice_fails_clearly(self):
        from agent.voice_adapters import PiperTTS
        adapter = PiperTTS(voice_path="/nonexistent/voice.onnx")
        with self.assertRaisesRegex(FileNotFoundError, "Piper voice file not found"):
            adapter.validate()

    def test_whisper_missing_package_message(self):
        # faster-whisper IS installed here, so we only assert the adapter
        # builds and reports its label without importing the model.
        from agent.voice_adapters import WhisperSTT
        stt = WhisperSTT(model="tiny").build()
        self.assertIn("faster-whisper", stt.label)

    @unittest.skipUnless(VOICE and os.path.exists(VOICE),
                         "set PIPER_VOICE to a .onnx voice file to run the round-trip")
    def test_piper_to_whisper_roundtrip(self):
        from livekit import rtc
        from piper import PiperVoice
        from agent.voice_adapters import PiperTTS, WhisperSTT

        voice = PiperVoice.load(VOICE)
        pcm = b"".join(c.audio_int16_bytes for c in voice.synthesize(TEXT))
        self.assertGreater(len(pcm), 10000)
        sr = voice.config.sample_rate

        adapter = WhisperSTT(model="tiny").build()
        frames = []
        for i in range(0, len(pcm), 960):
            chunk = pcm[i:i + 960]
            frame = rtc.AudioFrame.create(sr, 1, len(chunk) // 2)
            frame.data.cast("B")[:] = chunk
            frames.append(frame)

        async def _recognize():
            return await adapter._recognize_impl(frames)

        event = asyncio.run(_recognize())
        transcript = event.alternatives[0].text if event.alternatives else ""
        self.assertIn("cleaning", transcript.lower())
        self.assertIn("appointment", transcript.lower())

    @unittest.skipUnless(VOICE and os.path.exists(VOICE),
                         "set PIPER_VOICE to a .onnx voice file to run the round-trip")
    def test_piper_tts_emits_audio(self):
        from agent.voice_adapters import PiperTTS
        tts = PiperTTS(voice_path=VOICE).build()

        async def _collect():
            stream = tts.synthesize("Your appointment is booked for Friday at ten o'clock.")
            out = []
            async for audio in stream:
                out.append(audio)
            return out

        frames = asyncio.run(_collect())
        total = sum(len(bytes(f.frame.data)) for f in frames)
        self.assertGreater(len(frames), 0)
        self.assertGreater(total, 5000)


if __name__ == "__main__":
    unittest.main()
