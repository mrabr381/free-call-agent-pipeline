"""Local voice adapters — faster-whisper STT and Piper TTS as LiveKit plugins.

LiveKit ships no official plugin for either (verified against livekit-agents
1.8.13: there is no livekit-plugins-whisper, -piper or -ollama on PyPI), so
these two small classes implement the LiveKit base-class protocols directly.
They are written against the installed SDK's own reference patterns
(google plugin ChunkedStream + AudioEmitter, STT._recognize_impl).

Both adapters import their heavy dependency lazily: if faster-whisper or
piper-tts is not installed, constructing the adapter raises a clear
ImportError telling you the exact pip command — the worker never crashes
with a bare traceback.
"""

from __future__ import annotations

import os
from typing import Optional

__all__ = ["PiperTTS", "WhisperSTT"]


def _require_livekit():
    try:
        from livekit.agents import stt, tts  # noqa: F401
        from livekit import rtc  # noqa: F401
    except ImportError as exc:
        raise ImportError(
            "livekit-agents is required for voice mode. Install it with: "
            "pip install livekit-agents"
        ) from exc


class WhisperSTT:
    """Non-streaming STT backed by local faster-whisper (no key, no cloud).

    The agent session buffers each spoken turn (via VAD) and calls
    ``recognize`` on it; this adapter transcribes the buffered audio with
    the local Whisper model and returns a final transcript event.
    """

    def __init__(self, *, model: str = "tiny", device: str = "cpu",
                 compute_type: str = "int8", language: str = "en"):
        _require_livekit()
        self._model_name = model
        self._device = device
        self._compute_type = compute_type
        self._language = language
        self._model = None

    def _ensure_model(self):
        if self._model is not None:
            return self._model
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ImportError(
                "faster-whisper is required for local STT. Install it with: "
                "pip install faster-whisper"
            ) from exc
        # First run downloads the model (~75 MB for tiny) into ~/.cache/huggingface.
        self._model = WhisperModel(self._model_name, device=self._device,
                                   compute_type=self._compute_type)
        return self._model

    def build(self):
        """Return a livekit.agents.stt.STT instance. Separated so the worker
        can import this module without livekit-agents installed."""
        from livekit.agents import stt

        outer = self

        class _WhisperSTT(stt.STT):
            def __init__(self):
                super().__init__(capabilities=stt.STTCapabilities(
                    streaming=False, interim_results=False))
                self._label = f"faster-whisper/{outer._model_name}"

            @property
            def label(self) -> str:  # type: ignore[override]
                return self._label

            async def _recognize_impl(self, buffer, *, language=None,
                                      conn_options=None):
                from livekit.agents import stt as _stt
                frames = buffer if isinstance(buffer, list) else [buffer]
                if not frames:
                    return _stt.SpeechEvent(type=_stt.SpeechEventType.FINAL_TRANSCRIPT,
                                            alternatives=[])
                sample_rate = frames[0].sample_rate
                pcm = b"".join(bytes(f.data) for f in frames)
                # Decode ourselves and hand faster-whisper a float32 array at
                # 16 kHz. We deliberately do NOT pass a file path: faster-whisper
                # 1.2.1 decodes files through PyAV, and it is incompatible with
                # the av>=14 that livekit-agents 1.8 requires (metadata_errors
                # was removed). The array path skips PyAV entirely.
                import numpy as np
                audio = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
                if sample_rate != 16000 and len(audio) > 0:
                    target_len = int(round(len(audio) * 16000 / sample_rate))
                    audio = np.interp(
                        np.linspace(0, len(audio) - 1, target_len),
                        np.arange(len(audio)), audio).astype(np.float32)
                model = outer._ensure_model()
                segments, _info = model.transcribe(
                    audio, language=language or outer._language, beam_size=1)
                text = " ".join(seg.text.strip() for seg in segments).strip()
                if not text:
                    return _stt.SpeechEvent(type=_stt.SpeechEventType.FINAL_TRANSCRIPT,
                                            alternatives=[])
                return _stt.SpeechEvent(
                    type=_stt.SpeechEventType.FINAL_TRANSCRIPT,
                    alternatives=[_stt.SpeechData(language=outer._language,
                                                  text=text, confidence=1.0)],
                )

        return _WhisperSTT()


class PiperTTS:
    """Local TTS backed by Piper (no key, no cloud).

    Needs a Piper voice file (.onnx + .onnx.json). Pass the path, or set
    PIPER_VOICE. Download one with, for example:
        python -m piper.download_voices en_US-lessac-medium --download-dir voices/
    """

    def __init__(self, *, voice_path: Optional[str] = None, sample_rate: int = 22050):
        _require_livekit()
        self._voice_path = voice_path or os.environ.get("PIPER_VOICE", "")
        self._sample_rate = sample_rate
        self._voice = None

    def _ensure_voice(self):
        if self._voice is not None:
            return self._voice
        if not self._voice_path or not os.path.exists(self._voice_path):
            raise FileNotFoundError(
                "Piper voice file not found. Set PIPER_VOICE to a .onnx voice file, "
                "or download one with: "
                "python -m piper.download_voices en_US-lessac-medium --download-dir voices/"
            )
        try:
            from piper import PiperVoice
        except ImportError as exc:
            raise ImportError(
                "piper-tts is required for local TTS. Install it with: "
                "pip install piper-tts"
            ) from exc
        self._voice = PiperVoice.load(self._voice_path)
        return self._voice

    def validate(self) -> str:
        """Load the voice now (fail fast on a missing file) and return its path."""
        self._ensure_voice()
        return self._voice_path

    def build(self):
        """Return a livekit.agents.tts.TTS instance."""
        from livekit.agents import DEFAULT_API_CONNECT_OPTIONS
        from livekit.agents import tts
        from livekit.agents import utils as _utils

        outer = self

        class _PiperChunkedStream(tts.ChunkedStream):
            async def _run(self, output_emitter: tts.AudioEmitter) -> None:
                voice = outer._ensure_voice()
                output_emitter.initialize(
                    request_id=_utils.shortuuid(),
                    sample_rate=outer._sample_rate,
                    num_channels=1,
                    mime_type="audio/pcm",
                )
                # Piper synthesises the whole utterance; push it in frames.
                pcm = b"".join(chunk.audio_int16_bytes
                               for chunk in voice.synthesize(self._input_text))
                output_emitter.push(pcm)

        class _PiperTTS(tts.TTS):
            def __init__(self):
                super().__init__(capabilities=tts.TTSCapabilities(streaming=False),
                                 sample_rate=outer._sample_rate, num_channels=1)
                self._label = "piper"

            @property
            def label(self) -> str:  # type: ignore[override]
                return self._label

            def synthesize(self, text: str, *, conn_options=None):
                return _PiperChunkedStream(tts=self, input_text=text,
                                          conn_options=conn_options or DEFAULT_API_CONNECT_OPTIONS)

            def stream(self, *, conn_options=None):  # non-streaming TTS: no stream API
                raise NotImplementedError("PiperTTS is non-streaming; use synthesize()")

        return _PiperTTS()
