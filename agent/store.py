"""Booking store — SQLite, stdlib only.

One table, one rule that matters: a resource (doctor / agent) can never be
double-booked. Enforced two ways — an overlap check in Python AND a
partial UNIQUE index on booked rows, so even a race cannot create a clash.

Status is 'booked' or 'cancelled'. Cancelling frees the slot because the
unique index only covers booked rows.
"""

from __future__ import annotations

import datetime as _dt
import os
import sqlite3
from typing import Any, Optional

__all__ = ["BookingStore", "BookingError"]


class BookingError(Exception):
    """Raised for any booking rule violation (past date, clash, unknown code)."""


def _to_minutes(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _to_hhmm(minutes: int) -> str:
    return f"{minutes // 60:02d}:{minutes % 60:02d}"


class BookingStore:
    def __init__(self, path: str):
        self.path = path
        directory = os.path.dirname(os.path.abspath(path))
        os.makedirs(directory, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS appointments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                business TEXT NOT NULL,
                resource TEXT NOT NULL,
                service TEXT NOT NULL,
                date TEXT NOT NULL,
                start TEXT NOT NULL,
                end TEXT NOT NULL,
                duration_min INTEGER NOT NULL,
                customer_name TEXT NOT NULL,
                customer_phone TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'booked',
                created_at TEXT NOT NULL
            )
            """
        )
        # Only booked rows block a slot — cancelled rows never do.
        self.conn.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS idx_no_double_book
            ON appointments (resource, date, start)
            WHERE status = 'booked'
            """
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()

    # -- helpers ---------------------------------------------------------
    def _row_to_dict(self, row: sqlite3.Row) -> dict:
        return dict(row)

    def _overlaps(self, resource: str, date: str, start: str, end: str) -> Optional[dict]:
        """Return the booked appointment overlapping [start, end), if any."""
        rows = self.conn.execute(
            "SELECT * FROM appointments WHERE resource = ? AND date = ? AND status = 'booked'",
            (resource, date),
        ).fetchall()
        s, e = _to_minutes(start), _to_minutes(end)
        for row in rows:
            rs, re = _to_minutes(row["start"]), _to_minutes(row["end"])
            if s < re and rs < e:
                return self._row_to_dict(row)
        return None

    # -- public API ------------------------------------------------------
    def book(
        self,
        *,
        business: str,
        resource: str,
        service: str,
        date: str,
        start: str,
        duration_min: int,
        customer_name: str,
        customer_phone: str,
        today: Optional[_dt.date] = None,
    ) -> dict:
        """Book a slot. Raises BookingError on past date or any clash."""
        today = today or _dt.date.today()
        try:
            day = _dt.date.fromisoformat(date)
        except ValueError:
            raise BookingError(f"date must be YYYY-MM-DD, got {date!r}")
        if day < today:
            raise BookingError(f"cannot book in the past: {date} (today is {today})")
        if not customer_name.strip():
            raise BookingError("customer name is required")
        if not customer_phone.strip():
            raise BookingError("customer phone is required")
        end = _to_hhmm(_to_minutes(start) + duration_min)
        clash = self._overlaps(resource, date, start, end)
        if clash:
            raise BookingError(
                f"{resource} is already booked {date} {clash['start']}–{clash['end']} "
                f"({clash['service']}, {clash['code']})"
            )
        cur = self.conn.execute(
            """
            INSERT INTO appointments
            (code, business, resource, service, date, start, end, duration_min,
             customer_name, customer_phone, status, created_at)
            VALUES ('', ?, ?, ?, ?, ?, ?, ?, ?, ?, 'booked', ?)
            """,
            (
                business,
                resource,
                service,
                date,
                start,
                end,
                duration_min,
                customer_name.strip(),
                customer_phone.strip(),
                _dt.datetime.now(_dt.timezone.utc).isoformat(),
            ),
        )
        code = f"{business[:3].upper()}-{cur.lastrowid:06d}"
        self.conn.execute("UPDATE appointments SET code = ? WHERE id = ?", (code, cur.lastrowid))
        self.conn.commit()
        return self.get(code)

    def get(self, code: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM appointments WHERE code = ?", (code.strip().upper(),)
        ).fetchone()
        if row is None:
            raise BookingError(f"no appointment with code {code!r}")
        return self._row_to_dict(row)

    def list_for(self, *, resource: Optional[str] = None, date: Optional[str] = None,
                 status: str = "booked") -> list[dict]:
        query = "SELECT * FROM appointments WHERE status = ?"
        params: list[Any] = [status]
        if resource:
            query += " AND resource = ?"
            params.append(resource)
        if date:
            query += " AND date = ?"
            params.append(date)
        query += " ORDER BY date, start"
        return [self._row_to_dict(r) for r in self.conn.execute(query, params).fetchall()]

    def free_slots(self, *, resource: str, date: str, open_hhmm: str, close_hhmm: str,
                   duration_min: int, step_min: int = 30) -> list[str]:
        """Free start times on a date, stepping through opening hours."""
        booked = self.list_for(resource=resource, date=date)
        taken = [(_to_minutes(b["start"]), _to_minutes(b["end"])) for b in booked]
        free: list[str] = []
        start = _to_minutes(open_hhmm)
        last_start = _to_minutes(close_hhmm) - duration_min
        while start <= last_start:
            end = start + duration_min
            if not any(start < te and ts < end for ts, te in taken):
                free.append(_to_hhmm(start))
            start += step_min
        return free

    def cancel(self, code: str) -> dict:
        appt = self.get(code)
        if appt["status"] == "cancelled":
            raise BookingError(f"appointment {appt['code']} is already cancelled")
        self.conn.execute(
            "UPDATE appointments SET status = 'cancelled' WHERE code = ?", (appt["code"],)
        )
        self.conn.commit()
        return self.get(code)

    def reschedule(self, code: str, *, new_date: str, new_start: str,
                   today: Optional[_dt.date] = None) -> dict:
        appt = self.get(code)
        if appt["status"] == "cancelled":
            raise BookingError(f"cannot reschedule cancelled appointment {appt['code']}")
        today = today or _dt.date.today()
        try:
            day = _dt.date.fromisoformat(new_date)
        except ValueError:
            raise BookingError(f"date must be YYYY-MM-DD, got {new_date!r}")
        if day < today:
            raise BookingError(f"cannot move to the past: {new_date} (today is {today})")
        new_end = _to_hhmm(_to_minutes(new_start) + appt["duration_min"])
        clash = self._overlaps(appt["resource"], new_date, new_start, new_end)
        if clash and clash["code"] != appt["code"]:
            raise BookingError(
                f"{appt['resource']} is already booked {new_date} "
                f"{clash['start']}–{clash['end']} ({clash['code']})"
            )
        self.conn.execute(
            "UPDATE appointments SET date = ?, start = ?, end = ? WHERE code = ?",
            (new_date, new_start, new_end, appt["code"]),
        )
        self.conn.commit()
        return self.get(code)
