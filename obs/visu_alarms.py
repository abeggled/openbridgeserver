"""Visu alarms — bring a message or a page to the foreground of every Visu.

An alarm watches one datapoint. When its condition becomes true (rising edge,
e.g. the mailbox contact closes or the phone starts ringing) the alarm is
raised and pushed to every open Visu over a dedicated channel; the Visu shows
it on top of whatever is open — as a simple message window or as a Visu page
in a popup. It ends when somebody acknowledges it and/or when its condition
becomes false again, as configured per alarm.

Definitions are configured centrally in the Admin GUI and stored as one JSON
document under the `visu.alarms` app setting (like `visu.devices`), so backup,
export/import and factory reset carry them along. Active alarms live in memory
only: a restart never raises an alarm whose condition was already true before —
a datapoint without a known value at start-up gets its baseline from the first
value it reports, which is therefore never treated as a change.

What reaches the Visu is the alarm's display data the administrator wrote —
never datapoint values — so the channel can serve anonymous viewers as well.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from obs.adapters.message.adapter import evaluate_condition
from obs.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)

VISU_ALARMS_KEY = "visu.alarms"
MAX_VISU_ALARMS = 100

AlarmOperator = Literal["==", "!=", "<", "<=", ">", ">=", "contains", "contains not", "starts with", "ends with"]
AlarmDisplay = Literal["message", "page"]
AlarmSeverity = Literal["info", "warning", "critical"]

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class VisuAlarm(BaseModel):
    id: str
    name: str = Field(min_length=1, max_length=80)
    enabled: bool = True
    # Trigger: the alarm is raised when this condition becomes true.
    datapoint_id: str
    operator: AlarmOperator = "=="
    compare_value: str | float | bool | None = True
    # What the Visu shows.
    display: AlarmDisplay = "message"
    title: str = Field(default="", max_length=120)
    text: str = Field(default="", max_length=500)
    icon: str = Field(default="", max_length=200)
    severity: AlarmSeverity = "info"
    page_id: str | None = None
    # How it ends (at least one).
    ack_button: bool = True
    end_on_clear: bool = False
    sound: bool = False
    # Device profile slugs (visu.devices); empty = every Visu.
    devices: list[str] = Field(default_factory=list, max_length=50)

    @field_validator("id")
    @classmethod
    def _check_id(cls, value: str) -> str:
        if not _ID_RE.match(value):
            raise ValueError("id must consist of letters, digits, '-' or '_' (max. 64 characters)")
        return value

    @field_validator("name", "title", "text", "icon")
    @classmethod
    def _strip(cls, value: str) -> str:
        return value.strip()

    @field_validator("datapoint_id")
    @classmethod
    def _check_datapoint(cls, value: str) -> str:
        try:
            return str(uuid.UUID(value))
        except ValueError as exc:
            raise ValueError("datapoint_id must be a UUID") from exc

    @field_validator("page_id")
    @classmethod
    def _empty_page_is_none(cls, value: str | None) -> str | None:
        return value or None

    @model_validator(mode="after")
    def _check_consistency(self) -> VisuAlarm:
        if not self.name:
            raise ValueError("name must not be empty")
        if self.display == "page" and not self.page_id:
            raise ValueError("a page alarm needs page_id")
        if self.display == "message" and not (self.title or self.text):
            raise ValueError("a message alarm needs a title or a text")
        if not (self.ack_button or self.end_on_clear):
            raise ValueError("an alarm needs an end: ack_button and/or end_on_clear")
        return self


class VisuAlarmsIn(BaseModel):
    alarms: list[VisuAlarm] = Field(default_factory=list, max_length=MAX_VISU_ALARMS)

    @model_validator(mode="after")
    def _unique_ids(self) -> VisuAlarmsIn:
        seen: set[str] = set()
        for alarm in self.alarms:
            if alarm.id in seen:
                raise ValueError(f"duplicate alarm id: {alarm.id}")
            seen.add(alarm.id)
        return self


class ActiveVisuAlarm(BaseModel):
    """An active alarm as the Visu receives it: display data only, no values."""

    id: str
    name: str
    display: AlarmDisplay
    title: str
    text: str
    icon: str
    severity: AlarmSeverity
    page_id: str | None
    ack_button: bool
    sound: bool
    devices: list[str]
    since: str


def parse_visu_alarms(raw: str) -> list[VisuAlarm]:
    """Validate a stored `visu.alarms` value; raises ValueError when invalid."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{VISU_ALARMS_KEY} is not valid JSON") from exc
    return VisuAlarmsIn(alarms=data).alarms


def load_visu_alarms(raw: str | None) -> list[VisuAlarm]:
    """Read the stored alarms leniently: a broken value must not stop the server."""
    if not raw:
        return []
    try:
        return parse_visu_alarms(raw)
    except ValueError:
        logger.warning("Ignoring invalid %s setting", VISU_ALARMS_KEY)
        return []


def dump_visu_alarms(alarms: list[VisuAlarm]) -> str:
    return json.dumps([a.model_dump() for a in alarms], ensure_ascii=False)


@dataclass
class _Active:
    alarm: VisuAlarm
    since: datetime


class VisuAlarmEngine:
    """Evaluates the alarm conditions and keeps the set of active alarms.

    Listeners (one queue per open Visu channel) receive the full active list
    after every change, so a client never has to merge partial updates.
    """

    def __init__(self, value_getter: Any = None) -> None:
        # value_getter(uuid.UUID) -> object with `.value` (DataPointRegistry.get_value) or None
        self._value_getter = value_getter
        self._alarms: list[VisuAlarm] = []
        self._by_datapoint: dict[str, list[VisuAlarm]] = {}
        self._condition: dict[str, bool] = {}
        # Alarms whose datapoint had no value yet when they were configured (typically
        # right after a restart with persist_value=false): their first good value only
        # sets the baseline, it is not an edge.
        self._unknown: set[str] = set()
        self._active: dict[str, _Active] = {}
        self._listeners: set[asyncio.Queue[list[dict[str, Any]]]] = set()

    # ── configuration ────────────────────────────────────────────────────────
    def configure(self, alarms: list[VisuAlarm]) -> None:
        """Apply a new set of definitions without raising anything.

        Conditions start from the datapoints' current values, so an alarm only
        fires on a *change* after it was saved — never for a state that was
        already present. A datapoint without a value yet (after a restart, before
        the adapter's first reading) takes that first reading as its baseline.
        Active alarms that were removed or disabled end; the others keep running
        with their updated display data.
        """
        self._alarms = [a for a in alarms if a.enabled]
        by_id = {a.id: a for a in self._alarms}
        self._by_datapoint = {}
        for alarm in self._alarms:
            self._by_datapoint.setdefault(alarm.datapoint_id, []).append(alarm)
        self._condition = {}
        self._unknown = set()
        for alarm in self._alarms:
            condition = self._current_condition(alarm)
            if condition is None:
                self._unknown.add(alarm.id)
            self._condition[alarm.id] = bool(condition)
        self._active = {aid: _Active(by_id[aid], act.since) for aid, act in self._active.items() if aid in by_id}
        # An active alarm whose new definition has no acknowledge button ends only when
        # its condition clears. If the condition is already false now, that end has
        # passed — the alarm ends here instead of staying on every Visu with no button
        # until the condition happens to go true and false again.
        self._active = {aid: act for aid, act in self._active.items() if act.alarm.ack_button or self._condition[aid]}
        self._notify()

    def _current_condition(self, alarm: VisuAlarm) -> bool | None:
        """The condition for the datapoint's current value; None when there is no value yet."""
        if self._value_getter is None:
            # No registry wired (tests): nothing to know, start from "not met".
            return False
        state = self._value_getter(uuid.UUID(alarm.datapoint_id))
        if state is None or getattr(state, "value", None) is None:
            return None
        return evaluate_condition(state.value, alarm.operator, alarm.compare_value)

    # ── runtime ──────────────────────────────────────────────────────────────
    async def handle_value_event(self, event: DataValueEvent) -> None:
        # A failed read (quality bad/uncertain, usually value None) says nothing about the
        # physical state: it must neither raise nor end an alarm, nor move the baseline.
        if event.quality != "good":
            return
        alarms = self._by_datapoint.get(str(event.datapoint_id))
        if not alarms:
            return
        changed = False
        for alarm in alarms:
            met = evaluate_condition(event.value, alarm.operator, alarm.compare_value)
            was = self._condition.get(alarm.id, False)
            self._condition[alarm.id] = met
            if alarm.id in self._unknown:
                # First value since the restart: the baseline, not a change.
                self._unknown.discard(alarm.id)
                continue
            if event.initialization:
                # Start-up values (e.g. a logic sheet's first run) only set the baseline,
                # so the same value arriving again later is not mistaken for a rising edge.
                continue
            if met and not was and alarm.id not in self._active:
                self._active[alarm.id] = _Active(alarm, datetime.now(UTC))
                changed = True
            elif not met and was and alarm.end_on_clear and alarm.id in self._active:
                del self._active[alarm.id]
                changed = True
        if changed:
            self._notify()

    def acknowledge(self, alarm_id: str) -> bool:
        """End an active alarm that offers an acknowledge button."""
        active = self._active.get(alarm_id)
        if active is None or not active.alarm.ack_button:
            return False
        del self._active[alarm_id]
        self._notify()
        return True

    def active(self) -> list[dict[str, Any]]:
        items = sorted(self._active.values(), key=lambda a: a.since, reverse=True)
        return [
            ActiveVisuAlarm(
                id=a.alarm.id,
                name=a.alarm.name,
                display=a.alarm.display,
                title=a.alarm.title,
                text=a.alarm.text,
                icon=a.alarm.icon,
                severity=a.alarm.severity,
                page_id=a.alarm.page_id,
                ack_button=a.alarm.ack_button,
                sound=a.alarm.sound,
                devices=a.alarm.devices,
                since=a.since.isoformat(),
            ).model_dump()
            for a in items
        ]

    # ── listeners (Visu channels) ────────────────────────────────────────────
    def listen(self) -> asyncio.Queue[list[dict[str, Any]]]:
        queue: asyncio.Queue[list[dict[str, Any]]] = asyncio.Queue(maxsize=16)
        self._listeners.add(queue)
        return queue

    def unlisten(self, queue: asyncio.Queue[list[dict[str, Any]]]) -> None:
        self._listeners.discard(queue)

    def _notify(self) -> None:
        snapshot = self.active()
        for queue in list(self._listeners):
            # Every snapshot is the full list, so a client only needs the newest one: drop
            # whatever it has not read yet instead of replaying stale states later.
            while not queue.empty():
                queue.get_nowait()
            queue.put_nowait(snapshot)


_engine: VisuAlarmEngine | None = None


def init_visu_alarm_engine(value_getter: Any = None) -> VisuAlarmEngine:
    global _engine
    _engine = VisuAlarmEngine(value_getter)
    return _engine


def get_visu_alarm_engine() -> VisuAlarmEngine:
    global _engine
    if _engine is None:
        _engine = VisuAlarmEngine()
    return _engine


def reset_visu_alarm_engine() -> None:
    global _engine
    _engine = None


async def reload_visu_alarms(db: Any) -> None:
    """Re-read the stored definitions into the running engine (after save, import or reset)."""
    row = await db.fetchone("SELECT value FROM app_settings WHERE key = ?", (VISU_ALARMS_KEY,))
    get_visu_alarm_engine().configure(load_visu_alarms(row["value"] if row else None))
