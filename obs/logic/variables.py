"""Shared ``###NAME###`` variable resolver for opted-in logic block fields (#1301).

One controlled text substitution — no expression evaluation. Supported names:

* date/time tokens (``H``, ``HH``, ``m``, ``mm``, ``s``, ``ss``, ``d``, ``dd``,
  ``EE``/``EEE``/``EEEE``, ``M``/``MM``/``MMM``/``MMMM``, ``yy``/``yyyy``),
  formatted by :func:`obs.datetime_format.format_datetime` — case matters
  (``m`` = minute, ``M`` = month);
* ``DATE`` / ``TIME`` (configured application formats) and ``TS`` (ISO-8601);
* ``OBS<n>`` object variables, resolved through a caller-supplied callable.

The template is expanded in a single regex pass, so substituted values are never
expanded again. Unknown ``###NAME###`` placeholders stay literal (backward
compatibility) and are reported so the editor/debug view can flag them.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from obs.datetime_format import DEFAULT_DATE_FORMAT, DEFAULT_TIME_FORMAT, format_datetime

DATE_TOKENS: tuple[str, ...] = (
    "H",
    "HH",
    "m",
    "mm",
    "s",
    "ss",
    "d",
    "dd",
    "EE",
    "EEE",
    "EEEE",
    "M",
    "MM",
    "MMM",
    "MMMM",
    "yy",
    "yyyy",
)
STANDARD_TOKENS: tuple[str, ...] = ("DATE", "TIME", "TS")
_NAME_ALTERNATIVES = "|".join(sorted((*DATE_TOKENS, *STANDARD_TOKENS), key=len, reverse=True))

# Known variables only: group(1) is the variable name (``OBS3``, ``HH``, ``TS``…).
VARIABLE_RE = re.compile(rf"###(OBS[1-9][0-9]*|{_NAME_ALTERNATIVES})###")
# Any ``###NAME###``-shaped placeholder, used to detect unknown ones.
_ANY_PLACEHOLDER_RE = re.compile(r"###([A-Za-z][A-Za-z0-9_]*)###")


class VariableError(ValueError):
    """A known variable could not be resolved (e.g. unconfigured OBS slot)."""


@dataclass(frozen=True)
class TimeSnapshot:
    """One instant, shared by every variable of a logic run."""

    now: datetime
    language: str = "de"
    date_format: str = DEFAULT_DATE_FORMAT
    time_format: str = DEFAULT_TIME_FORMAT

    def value(self, name: str) -> str:
        if name == "DATE":
            return format_datetime(self.now, self.date_format, self.language)
        if name == "TIME":
            return format_datetime(self.now, self.time_format, self.language)
        if name == "TS":
            return self.now.isoformat(timespec="seconds")
        return format_datetime(self.now, name, self.language)


def make_time_snapshot(app_config: dict[str, Any] | None, now: datetime | None = None) -> TimeSnapshot:
    """Build the run's snapshot in the configured application time zone."""
    config = app_config or {}
    try:
        tz = ZoneInfo(str(config.get("timezone", "Europe/Zurich")))
    except (ValueError, ZoneInfoNotFoundError):
        tz = ZoneInfo("UTC")
    return TimeSnapshot(
        now=(now or datetime.now(UTC)).astimezone(tz),
        language=str(config.get("language", "de")),
        date_format=str(config.get("date_format", DEFAULT_DATE_FORMAT)),
        time_format=str(config.get("time_format", DEFAULT_TIME_FORMAT)),
    )


def variable_key(name: str) -> int | str:
    """``OBS3`` → ``3``; every other variable keeps its name."""
    return int(name[3:]) if name.startswith("OBS") else name


def unknown_placeholders(template: str) -> list[str]:
    """Placeholder names in *template* that are not supported variables (in order, unique)."""
    unknown: list[str] = []
    for match in _ANY_PLACEHOLDER_RE.finditer(template):
        name = match.group(1)
        if VARIABLE_RE.fullmatch(f"###{name}###") is None and name not in unknown:
            unknown.append(name)
    return unknown


def normalise_variable_slots(raw: Any) -> dict[int, dict[str, str]]:
    """Normalise a ``variables`` config (list or JSON string) to ``{slot: {datapoint_id, datapoint_name}}``."""
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            raw = []
    if not isinstance(raw, list):
        return {}

    variables: dict[int, dict[str, str]] = {}
    for idx, entry in enumerate(raw, start=1):
        if not isinstance(entry, dict):
            continue
        try:
            slot = int(entry.get("slot", idx))
        except (TypeError, ValueError):
            slot = idx
        if slot < 1:
            slot = idx
        datapoint_id = str(entry.get("datapoint_id") or "").strip()
        if not datapoint_id:
            continue
        variables[slot] = {
            "datapoint_id": datapoint_id,
            "datapoint_name": str(entry.get("datapoint_name") or datapoint_id),
        }
    return variables


def value_to_string(value: Any) -> str:
    """Render an object value the way the API Client always has (bool → true/false, JSON for containers)."""
    if value is None:
        raise VariableError("variable value is empty")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def make_obs_resolver(
    raw_variables: Any,
    lookup: Callable[[str], Any] | None,
) -> Callable[[int], str]:
    """Slot resolver over a block's ``variables`` config; *lookup* maps datapoint id → current value."""
    slots = normalise_variable_slots(raw_variables)
    cache: dict[int, str] = {}

    def _resolve(index: int) -> str:
        if index in cache:
            return cache[index]
        slot = slots.get(index)
        if slot is None:
            raise VariableError(f"variable OBS{index} is not configured")
        if lookup is None:
            raise VariableError(f"variable OBS{index} object {slot['datapoint_name']} is not available")
        try:
            value = lookup(slot["datapoint_id"])
        except Exception as exc:
            raise VariableError(f"variable OBS{index} references an invalid object") from exc
        if value is None:
            raise VariableError(f"variable OBS{index} object {slot['datapoint_name']} has no value")
        cache[index] = value_to_string(value)
        return cache[index]

    return _resolve


@dataclass
class ResolvedTemplate:
    text: str
    unknown: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def resolve_template(
    template: str,
    snapshot: TimeSnapshot,
    obs: Callable[[int], str] | None = None,
    quote: Callable[[str], str] | None = None,
) -> ResolvedTemplate:
    """Expand known variables in *template* in one pass.

    Failing ``OBS`` slots are recorded in ``errors`` and their placeholder is kept
    literally; callers must not use ``text`` when ``ok`` is false.
    """
    errors: list[str] = []

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        key = variable_key(name)
        try:
            if isinstance(key, int):
                if obs is None:
                    raise VariableError(f"variable {name} is not configured")
                value = obs(key)
            else:
                value = snapshot.value(key)
        except VariableError as exc:
            errors.append(str(exc))
            return match.group(0)
        return quote(value) if quote is not None else value

    text = VARIABLE_RE.sub(_replace, template)
    return ResolvedTemplate(text=text, unknown=unknown_placeholders(template), errors=errors)
