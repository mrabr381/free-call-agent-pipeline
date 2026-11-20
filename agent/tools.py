"""Agent tools — the functions the voice agent (and the simulator) can call.

Every tool returns a plain dict with ``ok`` plus either data or ``error``.
No tool ever raises into the caller: a bad input becomes a clear error
message the agent can speak back to the caller.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from .store import BookingError, BookingStore

__all__ = ["AgentTools"]

DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


class AgentTools:
    def __init__(self, config: dict, store: BookingStore):
        self.cfg = config
        self.store = store
        self.business = config["business"]["name"]
        self._messages: list[dict] = []

    # -- lookups ---------------------------------------------------------
    def _service(self, name: str) -> Optional[dict]:
        name = name.strip().lower()
        for svc in self.cfg["services"]:
            if svc["name"].strip().lower() == name:
                return svc
            if name in svc["name"].strip().lower():
                return svc
        return None

    def _staff(self, name: str) -> Optional[dict]:
        name = name.strip().lower()
        for person in self.cfg["staff"]:
            if person["name"].strip().lower() == name:
                return person
            # match by first name or surname alone
            if name in person["name"].strip().lower():
                return person
        return None

    def _day_hours(self, date: str) -> Optional[dict]:
        day = _dt.date.fromisoformat(date).strftime("%A").lower()
        hours = self.cfg["hours"].get(day)
        return None if hours == "closed" else hours

    # -- tools -----------------------------------------------------------
    def get_hours(self) -> dict:
        lines = []
        for day in DAY_NAMES:
            val = self.cfg["hours"][day]
            label = day.capitalize()
            lines.append(f"{label}: closed" if val == "closed"
                         else f"{label}: {val['open']}–{val['close']}")
        return {"ok": True, "hours": lines}

    def get_services_and_prices(self) -> dict:
        services = [
            {
                "name": s["name"],
                "price": s["price"],
                "currency": self.cfg["business"].get("currency", "GBP"),
                "duration_min": s["duration_min"],
                "description": s.get("description", ""),
            }
            for s in self.cfg["services"]
        ]
        return {"ok": True, "services": services}

    def get_directions(self) -> dict:
        b = self.cfg["business"]
        return {
            "ok": True,
            "address": b["address"],
            "phone": b["phone"],
            "directions": b.get("directions", ""),
        }

    def check_availability(self, *, service: str, date: str, staff: str = "") -> dict:
        svc = self._service(service)
        if svc is None:
            known = ", ".join(s["name"] for s in self.cfg["services"])
            return {"ok": False, "error": f"we don't offer {service!r}. Our services are: {known}."}
        try:
            hours = self._day_hours(date)
        except ValueError:
            return {"ok": False, "error": f"date must be YYYY-MM-DD, got {date!r}."}
        if hours is None:
            day = _dt.date.fromisoformat(date).strftime("%A")
            return {"ok": False, "error": f"we are closed on {day}s ({date})."}
        people = [self._staff(staff)] if staff else list(self.cfg["staff"])
        if staff and people[0] is None:
            known = ", ".join(p["name"] for p in self.cfg["staff"])
            return {"ok": False, "error": f"we don't have {staff!r} on the team. Our team: {known}."}
        slots: dict[str, list[str]] = {}
        for person in people:
            free = self.store.free_slots(
                resource=person["name"], date=date,
                open_hhmm=hours["open"], close_hhmm=hours["close"],
                duration_min=svc["duration_min"],
            )
            if free:
                slots[person["name"]] = free
        if not slots:
            return {"ok": False, "error": f"no free slots for {svc['name']} on {date}."}
        return {"ok": True, "service": svc["name"], "date": date, "slots": slots}

    def book_appointment(self, *, service: str, date: str, time: str, staff: str = "",
                         customer_name: str, customer_phone: str,
                         today: Optional[_dt.date] = None) -> dict:
        svc = self._service(service)
        if svc is None:
            known = ", ".join(s["name"] for s in self.cfg["services"])
            return {"ok": False, "error": f"we don't offer {service!r}. Our services are: {known}."}
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", time or ""):
            return {"ok": False, "error": f"time must be HH:MM, got {time!r}."}
        try:
            hours = self._day_hours(date)
        except ValueError:
            return {"ok": False, "error": f"date must be YYYY-MM-DD, got {date!r}."}
        if hours is None:
            return {"ok": False, "error": f"we are closed on {date}."}
        people = [self._staff(staff)] if staff else list(self.cfg["staff"])
        if staff and people[0] is None:
            known = ", ".join(p["name"] for p in self.cfg["staff"])
            return {"ok": False, "error": f"we don't have {staff!r} on the team. Our team: {known}."}
        # Pick the first person free at the requested time; "any" is fine.
        for person in people:
            free = self.store.free_slots(
                resource=person["name"], date=date,
                open_hhmm=hours["open"], close_hhmm=hours["close"],
                duration_min=svc["duration_min"],
            )
            if time in free:
                try:
                    appt = self.store.book(
                        business=self.business, resource=person["name"],
                        service=svc["name"], date=date, start=time,
                        duration_min=svc["duration_min"],
                        customer_name=customer_name, customer_phone=customer_phone,
                        today=today,
                    )
                except BookingError as exc:
                    return {"ok": False, "error": str(exc)}
                return {"ok": True, "appointment": appt}
        return {
            "ok": False,
            "error": f"no free slot at {time} on {date} for {svc['name']}. "
                     "Ask me to check availability for other times.",
        }

    def cancel_appointment(self, code: str) -> dict:
        try:
            appt = self.store.cancel(code)
        except BookingError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "appointment": appt}

    def reschedule_appointment(self, code: str, *, new_date: str, new_time: str,
                               today: Optional[_dt.date] = None) -> dict:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", new_time or ""):
            return {"ok": False, "error": f"time must be HH:MM, got {new_time!r}."}
        try:
            appt = self.store.reschedule(code, new_date=new_date, new_start=new_time, today=today)
        except BookingError as exc:
            return {"ok": False, "error": str(exc)}
        return {"ok": True, "appointment": appt}

    def take_message(self, *, customer_name: str, customer_phone: str, message: str) -> dict:
        if not message.strip():
            return {"ok": False, "error": "I need the message itself to pass it on."}
        record = {
            "customer_name": customer_name.strip(),
            "customer_phone": customer_phone.strip(),
            "message": message.strip(),
            "taken_at": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        }
        self._messages.append(record)
        return {"ok": True, "message": record}

    def answer_faq(self, question: str) -> dict:
        q = question.lower()
        best = None
        best_score = 0
        for faq in self.cfg.get("faqs", []):
            words = re.findall(r"[a-z0-9]+", faq["question"].lower())
            score = sum(1 for w in words if len(w) > 3 and w in q)
            if score > best_score:
                best_score = score
                best = faq
        if best is None or best_score == 0:
            return {"ok": False,
                    "error": "I don't have an answer for that — would you like me to take a message for the team?"}
        return {"ok": True, "question": best["question"], "answer": best["answer"]}
