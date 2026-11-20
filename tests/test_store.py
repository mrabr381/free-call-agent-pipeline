"""Store tests — the booking rules that must never break.

Run: python -m unittest discover -s tests -v
"""

import datetime as _dt
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from agent.store import BookingError, BookingStore

TODAY = _dt.date(2027, 1, 10)
FUTURE = "2027-01-15"  # a Friday


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.store = BookingStore(os.path.join(self.tmp, "test.db"))

    def tearDown(self):
        self.store.close()

    def _book(self, **kw):
        base = dict(business="Bright Smile Dental Clinic", resource="Dr. Amina Khan",
                    service="Cleaning", date=FUTURE, start="10:00", duration_min=45,
                    customer_name="Ayesha Khan", customer_phone="07123456789")
        base.update(kw)
        return self.store.book(today=TODAY, **base)

    def test_book_returns_unique_code(self):
        a = self._book()
        b = self._book(start="11:00")
        self.assertEqual(a["code"], "BRI-000001")
        self.assertEqual(b["code"], "BRI-000002")
        self.assertNotEqual(a["code"], b["code"])

    def test_double_booking_same_slot_rejected(self):
        self._book()
        with self.assertRaises(BookingError):
            self._book()  # same resource, same time

    def test_overlapping_slot_rejected(self):
        self._book()  # 10:00–10:45
        with self.assertRaises(BookingError):
            self._book(start="10:30")  # overlaps 10:00–10:45
        with self.assertRaises(BookingError):
            self._book(start="09:30", duration_min=60)  # 09:30–10:30 overlaps

    def test_adjacent_slot_allowed(self):
        self._book()  # 10:00–10:45
        appt = self._book(start="10:45")  # starts exactly when the first ends
        self.assertEqual(appt["start"], "10:45")

    def test_different_resource_same_time_allowed(self):
        self._book()
        appt = self._book(resource="Dr. Bilal Ahmed")
        self.assertEqual(appt["resource"], "Dr. Bilal Ahmed")

    def test_past_date_rejected(self):
        with self.assertRaises(BookingError):
            self._book(date="2020-01-01")

    def test_cancel_frees_the_slot(self):
        appt = self._book()
        self.store.cancel(appt["code"])
        again = self._book()
        self.assertEqual(again["start"], "10:00")
        self.assertEqual(again["status"], "booked")

    def test_cancel_twice_rejected(self):
        appt = self._book()
        self.store.cancel(appt["code"])
        with self.assertRaises(BookingError):
            self.store.cancel(appt["code"])

    def test_cancel_unknown_code_rejected(self):
        with self.assertRaises(BookingError):
            self.store.cancel("BRI-999999")

    def test_reschedule_moves_booking(self):
        appt = self._book()
        moved = self.store.reschedule(appt["code"], new_date="2027-01-16",
                                      new_start="11:00", today=TODAY)
        self.assertEqual((moved["date"], moved["start"]), ("2027-01-16", "11:00"))
        # old slot is free again
        self._book()

    def test_reschedule_into_clash_rejected(self):
        self._book()
        other = self._book(start="11:00")
        with self.assertRaises(BookingError):
            self.store.reschedule(other["code"], new_date=FUTURE,
                                  new_start="10:30", today=TODAY)

    def test_reschedule_to_past_rejected(self):
        appt = self._book()
        with self.assertRaises(BookingError):
            self.store.reschedule(appt["code"], new_date="2020-01-01",
                                  new_start="10:00", today=TODAY)

    def test_free_slots(self):
        self._book()  # 10:00–10:45
        free = self.store.free_slots(resource="Dr. Amina Khan", date=FUTURE,
                                     open_hhmm="09:00", close_hhmm="18:00",
                                     duration_min=45)
        self.assertIn("09:00", free)
        self.assertNotIn("10:00", free)
        self.assertNotIn("10:30", free)  # 10:30–11:15 overlaps
        self.assertIn("11:00", free)


if __name__ == "__main__":
    unittest.main()
