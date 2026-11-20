"""Worker tests — instructions, provider resolution, agent wiring.

Needs livekit-agents installed (requirements-voice.txt); skips otherwise.
Run: python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

try:
    import livekit.agents  # noqa: F401
    HAS_LIVEKIT = True
except ImportError:
    HAS_LIVEKIT = False

from agent.config import load_config

ROOT = os.path.join(os.path.dirname(__file__), "..")


@unittest.skipUnless(HAS_LIVEKIT, "livekit-agents not installed")
class WorkerTest(unittest.TestCase):
    def setUp(self):
        self.cfg = load_config(os.path.join(ROOT, "configs", "clinic.yaml"))
        self._env = dict(os.environ)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)

    def test_instructions_from_config(self):
        from agent.worker import build_instructions
        text = build_instructions(self.cfg)
        flat = " ".join(text.split())
        self.assertIn("Bright Smile Dental Clinic", flat)
        self.assertIn("Maya", flat)
        self.assertIn("Cleaning: GBP 45.00, 45 minutes", flat)
        self.assertIn("Dr. Amina Khan", flat)
        self.assertIn("Saturday: 09:00–14:00", flat)
        self.assertIn("Sunday: closed", flat)
        self.assertIn("never invent a slot", flat)

    def test_deepgram_without_key_fails_clearly(self):
        from agent.worker import resolve_providers
        os.environ.pop("DEEPGRAM_API_KEY", None)
        with self.assertRaisesRegex(RuntimeError, "DEEPGRAM_API_KEY"):
            resolve_providers(stt="deepgram", llm="ollama", tts="piper")

    def test_gemini_without_key_fails_clearly(self):
        from agent.worker import resolve_providers
        os.environ.pop("GEMINI_API_KEY", None)
        os.environ.pop("GOOGLE_API_KEY", None)
        with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
            resolve_providers(stt="whisper-local", llm="gemini", tts="piper")

    def test_piper_without_voice_fails_clearly(self):
        from agent.worker import resolve_providers
        os.environ.pop("PIPER_VOICE", None)
        with self.assertRaisesRegex(RuntimeError, "Piper voice"):
            resolve_providers(stt="whisper-local", llm="ollama", tts="piper")

    def test_unknown_provider_fails_clearly(self):
        from agent.worker import resolve_providers
        with self.assertRaisesRegex(RuntimeError, "unknown --stt"):
            resolve_providers(stt="magic", llm="ollama", tts="piper")

    def test_agent_has_eight_tools(self):
        from agent.store import BookingStore
        from agent.tools import AgentTools
        from agent.worker import _build_agent
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            store = BookingStore(os.path.join(tmp, "calls.db"))
            agent = _build_agent(self.cfg, AgentTools(self.cfg, store))
            names = sorted(t.info.name for t in (agent.tools or []))
            self.assertEqual(names, [
                "book_appointment", "cancel_appointment", "check_availability",
                "get_directions", "get_hours", "get_services_and_prices",
                "reschedule_appointment", "take_message",
            ])
            store.close()


if __name__ == "__main__":
    unittest.main()
