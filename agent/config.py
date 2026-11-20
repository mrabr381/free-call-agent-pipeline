"""Free Voice Call-Agent Pipeline — config loading and validation.

One YAML file per client. A new client means a new YAML, not new code.
Only dependency: PyYAML (stdlib otherwise).
"""

from __future__ import annotations

import os
import re
from typing import Any

import yaml

__all__ = ["ConfigError", "load_config", "validate_config"]


class ConfigError(Exception):
    """Raised when a client config is missing, unreadable, or invalid."""


_TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")

REQUIRED_TOP = ("business", "agent", "hours", "services", "staff")
DAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def _fail(msg: str) -> None:
    raise ConfigError(msg)


def validate_config(cfg: Any, source: str = "<config>") -> dict:
    """Validate a parsed YAML mapping. Returns the config unchanged.

    Raises ConfigError with a plain-English message on the first problem.
    """
    if not isinstance(cfg, dict):
        _fail(f"{source}: top level must be a mapping, got {type(cfg).__name__}")

    for key in REQUIRED_TOP:
        if key not in cfg:
            _fail(f"{source}: missing required section '{key}'")

    business = cfg["business"]
    if not isinstance(business, dict):
        _fail(f"{source}: 'business' must be a mapping")
    for field in ("name", "type", "phone", "address"):
        if not business.get(field):
            _fail(f"{source}: business.{field} is required and must not be empty")

    agent = cfg["agent"]
    if not isinstance(agent, dict):
        _fail(f"{source}: 'agent' must be a mapping")
    for field in ("name", "greeting", "persona"):
        if not agent.get(field):
            _fail(f"{source}: agent.{field} is required and must not be empty")

    hours = cfg["hours"]
    if not isinstance(hours, dict):
        _fail(f"{source}: 'hours' must be a mapping")
    for day in DAYS:
        if day not in hours:
            _fail(f"{source}: hours.{day} is required (use 'closed' for a closed day)")
        val = hours[day]
        if val == "closed":
            continue
        if not isinstance(val, dict) or "open" not in val or "close" not in val:
            _fail(f"{source}: hours.{day} must be 'closed' or a mapping with open/close")
        for bound in ("open", "close"):
            if not isinstance(val[bound], str) or not _TIME_RE.match(val[bound]):
                _fail(f"{source}: hours.{day}.{bound} must be HH:MM, got {val[bound]!r}")
        if val["open"] >= val["close"]:
            _fail(f"{source}: hours.{day}.open must be before close")

    services = cfg["services"]
    if not isinstance(services, list) or not services:
        _fail(f"{source}: 'services' must be a non-empty list")
    seen = set()
    for i, svc in enumerate(services):
        if not isinstance(svc, dict):
            _fail(f"{source}: services[{i}] must be a mapping")
        for field in ("name", "duration_min", "price"):
            if field not in svc:
                _fail(f"{source}: services[{i}].{field} is required")
        name = str(svc["name"]).strip().lower()
        if name in seen:
            _fail(f"{source}: duplicate service name {svc['name']!r}")
        seen.add(name)
        if not isinstance(svc["duration_min"], int) or svc["duration_min"] <= 0:
            _fail(f"{source}: services[{i}].duration_min must be a positive integer")
        if not isinstance(svc["price"], (int, float)) or svc["price"] < 0:
            _fail(f"{source}: services[{i}].price must be a number >= 0")

    staff = cfg["staff"]
    if not isinstance(staff, list) or not staff:
        _fail(f"{source}: 'staff' must be a non-empty list")
    for i, person in enumerate(staff):
        if not isinstance(person, dict) or not person.get("name"):
            _fail(f"{source}: staff[{i}].name is required")

    faqs = cfg.get("faqs", [])
    if not isinstance(faqs, list):
        _fail(f"{source}: 'faqs' must be a list")
    for i, faq in enumerate(faqs):
        if not isinstance(faq, dict) or not faq.get("question") or not faq.get("answer"):
            _fail(f"{source}: faqs[{i}] needs 'question' and 'answer'")

    return cfg


def load_config(path: str) -> dict:
    """Load and validate a client YAML config. Raises ConfigError on any problem."""
    if not os.path.exists(path):
        _fail(f"config file not found: {path}")
    try:
        with open(path, encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        _fail(f"{path}: invalid YAML — {exc}")
    except OSError as exc:
        _fail(f"{path}: cannot read config — {exc}")
    if raw is None:
        _fail(f"{path}: config file is empty")
    return validate_config(raw, source=path)
