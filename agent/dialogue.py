"""Dialogue engine — the text-only brain of the agent.

The LiveKit voice worker (worker.py) and the text simulator (simulate.py)
share this engine: the same intents, the same tools, the same replies.
The simulator exists so the whole pipeline can be tested with zero
accounts, zero keys and zero audio — typed input in, agent replies out.

Deterministic: given the same script and the same reference date, the
transcript is byte-identical.
"""

from __future__ import annotations

import datetime as _dt
import re
from typing import Optional

from .tools import AgentTools

__all__ = ["DialogueEngine"]

_DATE_RE = re.compile(r"\b(20\d\d-\d\d-\d\d)\b")
_TIME_RE = re.compile(r"\b([01]?\d):([0-5]\d)\s*(am|pm)?\b", re.I)
_CODE_RE = re.compile(r"\b([A-Z]{2,4}-\d{6})\b")
_PHONE_RE = re.compile(r"\+?\d[\d\s\-]{6,}\d")
_NAME_RE = re.compile(r"\b(?:my name is|i'?m|this is|name[' ]s)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)", re.I)


def _word_time_to_hhmm(hour: int, minute: int, ampm: Optional[str]) -> Optional[str]:
    if ampm:
        ampm = ampm.lower()
        if ampm == "pm" and hour < 12:
            hour += 12
        if ampm == "am" and hour == 12:
            hour = 0
    if not 0 <= hour <= 23:
        return None
    return f"{hour:02d}:{minute:02d}"


class DialogueEngine:
    def __init__(self, config: dict, tools: AgentTools, today: Optional[_dt.date] = None):
        self.cfg = config
        self.tools = tools
        self.today = today or _dt.date.today()
        self.pending: dict = {}          # partial booking being collected
        self.last_code: Optional[str] = None
        self.caller_name = ""
        self.caller_phone = ""

    # -- parsing ---------------------------------------------------------
    def _parse_date(self, text: str) -> Optional[str]:
        m = _DATE_RE.search(text)
        if m:
            return m.group(1)
        low = text.lower()
        if "tomorrow" in low:
            return (self.today + _dt.timedelta(days=1)).isoformat()
        if "today" in low:
            return self.today.isoformat()
        if "next monday" in low:
            return self._next_weekday(0)
        if "next friday" in low:
            return self._next_weekday(4)
        return None

    def _next_weekday(self, weekday: int) -> str:
        delta = (weekday - self.today.weekday()) % 7 or 7
        return (self.today + _dt.timedelta(days=delta)).isoformat()

    def _parse_time(self, text: str) -> Optional[str]:
        m = _TIME_RE.search(text)
        if not m:
            return None
        return _word_time_to_hhmm(int(m.group(1)), int(m.group(2)), m.group(3))

    def _parse_service(self, text: str) -> Optional[str]:
        low = text.lower()
        # Pass 1: exact service name.
        for svc in self.cfg["services"]:
            if svc["name"].lower() in low:
                return svc["name"]
        # Pass 2: score by how many of the service's words appear as whole
        # words in the text. Highest score wins, and only wins outright —
        # a tie (e.g. just "a viewing" for three viewing types) is ambiguous
        # and returns None so the agent asks which one.
        words_in_text = set(re.findall(r"[a-z]+", low))
        scored = []
        for svc in self.cfg["services"]:
            svc_words = set(re.findall(r"[a-z]+", svc["name"].lower()))
            scored.append((len(svc_words & words_in_text), svc["name"]))
        scored.sort(key=lambda item: -item[0])
        if scored and scored[0][0] > 0 and (len(scored) == 1 or scored[0][0] > scored[1][0]):
            return scored[0][1]
        return None

    def _parse_staff(self, text: str) -> Optional[str]:
        low = text.lower()
        for person in self.cfg["staff"]:
            name = person["name"].lower()
            if name in low:
                return person["name"]
            # Name without the honorific ("amina khan" for "Dr. Amina Khan"),
            # then the given name alone ("amina").
            bare = re.sub(r"^(dr|mr|mrs|ms|miss)\.\s+", "", name)
            if bare in low:
                return person["name"]
            first = bare.split()[0]
            if re.search(rf"\b{re.escape(first)}\b", low):
                return person["name"]
        return None

    def _harvest_identity(self, text: str) -> None:
        m = _NAME_RE.search(text)
        if m and not self.caller_name:
            self.caller_name = m.group(1).strip()
        # Dates look like phone numbers to a naive regex — hide them first.
        scrubbed = _DATE_RE.sub(" ", text)
        m = _PHONE_RE.search(scrubbed)
        if m and not self.caller_phone:
            digits = re.sub(r"\D", "", m.group(0))
            if 7 <= len(digits) <= 15:
                self.caller_phone = digits

    # -- entry point ------------------------------------------------------
    def greeting(self) -> str:
        return self.cfg["agent"]["greeting"]

    def process_turn(self, text: str) -> tuple[str, list[dict]]:
        """One caller turn in, (agent reply, tool calls made) out."""
        tool_calls: list[dict] = []
        self._harvest_identity(text)
        low = text.lower().strip()

        # --- cancel ------------------------------------------------------
        if "cancel" in low:
            code = self._code(text) or self.last_code
            if not code:
                return ("Of course — could you give me your booking reference code? "
                        "It looks like ABC-000123.", tool_calls)
            result = self._call(tool_calls, "cancel_appointment", self.tools.cancel_appointment, code)
            if result["ok"]:
                a = result["appointment"]
                return (f"Done — your {a['service']} on {a['date']} at {a['start']} "
                        f"with {a['resource']} is cancelled. The slot is free again, "
                        "so just call back whenever you'd like to rebook.", tool_calls)
            return (f"I'm sorry, {result['error']} Could you check the code and try again?",
                    tool_calls)

        # --- reschedule --------------------------------------------------
        if "reschedule" in low or "move my" in low or "change my appointment" in low:
            code = self._code(text) or self.last_code
            date = self._parse_date(text)
            time = self._parse_time(text)
            if not code:
                return ("Sure — could you give me your booking reference code first?", tool_calls)
            if not date or not time:
                return ("Sure — which new date and time would you like? "
                        "For example: move it to 2027-01-16 at 11:00.", tool_calls)
            result = self._call(tool_calls, "reschedule_appointment",
                                self.tools.reschedule_appointment, code,
                                new_date=date, new_time=time, today=self.today)
            if result["ok"]:
                a = result["appointment"]
                return (f"Done — your appointment is moved to {a['date']} at {a['start']} "
                        f"with {a['resource']}. Your reference code stays {a['code']}.",
                        tool_calls)
            return (f"I'm sorry, {result['error']}", tool_calls)

        # --- booking (new or continuing) ---------------------------------
        if self.pending or any(w in low for w in ("book", "appointment", "schedule", "viewing")):
            return self._booking_turn(text, tool_calls)

        # --- hours ---------------------------------------------------------
        if any(w in low for w in ("hours", "open", "timing", "when do you close", "when are you open")):
            result = self._call(tool_calls, "get_hours", self.tools.get_hours)
            lines = "\n".join("  " + h for h in result["hours"])
            return (f"Our opening hours are:\n{lines}", tool_calls)

        # --- services / prices ---------------------------------------------
        if any(w in low for w in ("service", "price", "cost", "how much", "what do you offer", "treatments", "listings")):
            result = self._call(tool_calls, "get_services_and_prices",
                                self.tools.get_services_and_prices)
            lines = []
            for s in result["services"]:
                cur = s["currency"]
                lines.append(f"  {s['name']}: {cur} {s['price']:.2f} ({s['duration_min']} min)"
                             + (f" — {s['description']}" if s["description"] else ""))
            return ("Here is what we offer:\n" + "\n".join(lines), tool_calls)

        # --- directions ------------------------------------------------------
        if any(w in low for w in ("where are you", "address", "directions", "location", "how do i get", "find you")):
            result = self._call(tool_calls, "get_directions", self.tools.get_directions)
            extra = f" {result['directions']}" if result["directions"] else ""
            return (f"We're at {result['address']}.{extra} Our phone number is {result['phone']}.",
                    tool_calls)

        # --- human / message ---------------------------------------------------
        if any(w in low for w in ("speak to a human", "speak to someone", "real person",
                                   "call me back", "leave a message", "take a message")):
            if not self.caller_name or not self.caller_phone:
                return ("Of course — could you give me your name and phone number, "
                        "and the message you'd like passed on?", tool_calls)
            msg = text
            result = self._call(tool_calls, "take_message", self.tools.take_message,
                                customer_name=self.caller_name,
                                customer_phone=self.caller_phone, message=msg)
            if result["ok"]:
                return (f"Thank you {self.caller_name} — I've left your message for the team "
                        "and someone will call you back on "
                        f"{self.caller_phone}. Is there anything else I can help with?",
                        tool_calls)
            return (result["error"], tool_calls)

        # --- FAQ / fallback ------------------------------------------------------
        result = self._call(tool_calls, "answer_faq", self.tools.answer_faq, text)
        if result["ok"]:
            return (result["answer"], tool_calls)
        return ("I'm sorry, I don't have an answer for that. I can help with bookings, "
                "opening hours, services and prices, or directions — or I can take a message "
                "for the team. What would you like?", tool_calls)

    # -- booking flow --------------------------------------------------------
    def _booking_turn(self, text: str, tool_calls: list[dict]) -> tuple[str, list[dict]]:
        p = self.pending
        for key, parser in (("service", self._parse_service), ("staff", self._parse_staff),
                            ("date", self._parse_date), ("time", self._parse_time)):
            if key not in p:
                val = parser(text)
                if val:
                    p[key] = val
        if "service" not in p:
            known = ", ".join(s["name"] for s in self.cfg["services"])
            return (f"Happy to help with a booking. Which service would you like? "
                    f"We offer: {known}.", tool_calls)
        if "date" not in p:
            return ("Which date works for you? You can say a date like 2027-01-15, "
                    "or just say tomorrow.", tool_calls)
        if "time" not in p:
            result = self._call(tool_calls, "check_availability",
                                self.tools.check_availability,
                                service=p["service"], date=p["date"],
                                staff=p.get("staff", ""))
            if not result["ok"]:
                self.pending = {}
                return (f"I'm sorry, {result['error']}", tool_calls)
            options = []
            for person, slots in result["slots"].items():
                options.append(f"{person}: {', '.join(slots[:6])}")
            return ("Here are the free times on "
                    f"{p['date']} for {p['service']}:\n  " + "\n  ".join(options)
                    + "\nWhich time would you like?", tool_calls)
        if not self.caller_name or not self.caller_phone:
            return ("Nearly there — could I have your name and phone number to confirm "
                    "the booking?", tool_calls)
        result = self._call(tool_calls, "book_appointment", self.tools.book_appointment,
                            service=p["service"], date=p["date"], time=p["time"],
                            staff=p.get("staff", ""), customer_name=self.caller_name,
                            customer_phone=self.caller_phone, today=self.today)
        self.pending = {}
        if not result["ok"]:
            return (f"I'm sorry, {result['error']}", tool_calls)
        a = result["appointment"]
        self.last_code = a["code"]
        return (f"You're booked in, {a['customer_name']}! {a['service']} with {a['resource']} "
                f"on {a['date']} at {a['start']}–{a['end']}. Your reference code is {a['code']} — "
                "please keep it handy. We'll see you then!", tool_calls)

    # -- helpers ---------------------------------------------------------------
    @staticmethod
    def _code(text: str) -> Optional[str]:
        m = _CODE_RE.search(text.upper())
        return m.group(1) if m else None

    @staticmethod
    def _call(tool_calls: list[dict], name: str, fn, *args, **kwargs) -> dict:
        result = fn(*args, **kwargs)
        tool_calls.append({"tool": name, "args": _jsonable(kwargs or args), "result": result})
        return result


def _jsonable(value):
    import datetime as _dt2

    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if isinstance(value, (_dt2.date, _dt2.datetime)):
        return value.isoformat()
    return value
