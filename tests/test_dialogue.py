"""Dialogue tests — scripted calls end-to-end, plus determinism.

Run: python -m unittest discover -s tests -v
"""

import datetime as _dt
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.simulate import run_simulation

ROOT = os.path.join(os.path.dirname(__file__), "..")
TODAY = _dt.date(2027, 1, 10)


def _script(name: str) -> list[str]:
    with open(os.path.join(ROOT, "sample_data", name), encoding="utf-8") as fh:
        return fh.read().splitlines()


class DialogueTest(unittest.TestCase):
    def _run(self, config: str, script: list[str]) -> dict:
        with tempfile.TemporaryDirectory() as tmp:
            return run_simulation(config_path=os.path.join(ROOT, "configs", config),
                                  script=script,
                                  store_path=os.path.join(tmp, "calls.db"),
                                  today=TODAY)

    def test_call1_books_cleaning(self):
        t = self._run("clinic.yaml", _script("call1_book.txt"))
        self.assertEqual(len(t["bookings"]), 1)
        b = t["bookings"][0]
        self.assertEqual((b["service"], b["resource"]), ("Cleaning", "Dr. Amina Khan"))
        self.assertEqual((b["date"], b["start"], b["end"]), ("2027-01-15", "10:00", "10:45"))
        self.assertEqual(b["customer_name"], "Ayesha Khan")
        self.assertEqual(b["customer_phone"], "07123456789")
        self.assertEqual(b["code"], "BRI-000001")
        last_agent = [x for x in t["turns"] if x["speaker"] == "agent"][-1]
        self.assertIn("BRI-000001", last_agent["text"])
        tools_used = [c["tool"] for turn in t["turns"] for c in turn["tool_calls"]]
        self.assertIn("book_appointment", tools_used)

    def test_call2_answers_hours_prices_directions(self):
        t = self._run("clinic.yaml", _script("call2_hours.txt"))
        self.assertEqual(t["bookings"], [])
        tools_used = [c["tool"] for turn in t["turns"] for c in turn["tool_calls"]]
        self.assertEqual(tools_used, ["get_hours", "get_services_and_prices", "get_directions"])
        replies = " ".join(x["text"] for x in t["turns"] if x["speaker"] == "agent")
        self.assertIn("Saturday: 09:00–14:00", replies)
        self.assertIn("Sunday: closed", replies)
        self.assertIn("Whitening: GBP 150.00", replies)
        self.assertIn("12 High Street", replies)

    def test_call3_book_then_cancel(self):
        t = self._run("clinic.yaml", _script("call3_cancel.txt"))
        # cancelled bookings are not in the default (booked) list
        self.assertEqual(t["bookings"], [])
        tools_used = [c["tool"] for turn in t["turns"] for c in turn["tool_calls"]]
        self.assertEqual(tools_used, ["book_appointment", "cancel_appointment"])
        last_agent = [x for x in t["turns"] if x["speaker"] == "agent"][-1]
        self.assertIn("cancelled", last_agent["text"])

    def test_call4_books_two_bed_viewing(self):
        t = self._run("real-estate.yaml", _script("call4_realestate.txt"))
        self.assertEqual(len(t["bookings"]), 1)
        b = t["bookings"][0]
        self.assertEqual(b["service"], "Two-bed flat viewing")
        self.assertEqual(b["resource"], "Emma Wilson")
        self.assertEqual((b["date"], b["start"], b["end"]), ("2027-01-15", "14:00", "14:45"))
        self.assertEqual(b["code"], "CIT-000001")

    def test_double_booking_via_dialogue_rejected(self):
        script = _script("call1_book.txt") + [
            "I'd like to book a cleaning with Dr. Amina on 2027-01-15 at 10:00",
            "My name is Second Person, phone 07999999999",
        ]
        t = self._run("clinic.yaml", script)
        self.assertEqual(len(t["bookings"]), 1)  # second booking refused
        replies = " ".join(x["text"] for x in t["turns"] if x["speaker"] == "agent")
        self.assertIn("no free slot at 10:00", replies)

    def test_unknown_service_handled(self):
        t = self._run("clinic.yaml", ["I'd like to book a brain transplant on 2027-01-15 at 10:00"])
        last_agent = [x for x in t["turns"] if x["speaker"] == "agent"][-1]
        self.assertIn("Which service", last_agent["text"])
        self.assertEqual(t["bookings"], [])

    def test_closed_day_handled(self):
        t = self._run("clinic.yaml", [
            "I'd like to book a check-up on 2027-01-17 at 10:00",  # a Sunday
            "My name is Test Person, phone 07111111111",
        ])
        self.assertEqual(t["bookings"], [])
        replies = " ".join(x["text"] for x in t["turns"] if x["speaker"] == "agent")
        self.assertIn("closed", replies)

    def test_determinism(self):
        a = self._run("clinic.yaml", _script("call1_book.txt"))
        b = self._run("clinic.yaml", _script("call1_book.txt"))
        # created_at is a wall-clock timestamp, also embedded in tool-call
        # results — strip it everywhere; everything else must match.
        def _strip(obj):
            if isinstance(obj, dict):
                return {k: _strip(v) for k, v in obj.items() if k != "created_at"}
            if isinstance(obj, list):
                return [_strip(v) for v in obj]
            return obj
        self.assertEqual(json.dumps(_strip(a), sort_keys=True),
                         json.dumps(_strip(b), sort_keys=True))


if __name__ == "__main__":
    unittest.main()
