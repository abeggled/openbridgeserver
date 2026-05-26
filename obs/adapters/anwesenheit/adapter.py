"""Anwesenheitssimulation Adapter — Presence Simulation

Replays historical switch states during absence so the building appears occupied.

How it works:
  1. When the simulation is ACTIVE (you are away), the adapter queries the history
     database for the past N days and fires each historical event at the corresponding
     time today (shifted by the configured offset).
  2. A background task checks every ~60 s for due events and fires them by publishing
     a DataValueEvent — which the WriteRouter then propagates to all DEST/BOTH bindings
     of the respective DataPoint (i.e. the actual KNX, MQTT, … devices are switched).
  3. Pre-loading strategy: events for the current hour are loaded on start/activation;
     at minute :55 of each hour the next full hour is pre-fetched.  This means only
     1 DB query per bound DataPoint per hour while keeping memory use minimal.

Activation:
  - No control DataPoint configured → simulation is always active.
  - With control DataPoint:
      control value = 1  (or 0 if inverted) → you are HOME → simulation INACTIVE.
      control value = 0  (or 1 if inverted) → you are AWAY  → simulation ACTIVE.

On-presence action (triggered when you return home):
  - "behalten"      → leave all simulated objects in their current state (default).
  - "zuruecksetzen" → publish False/0 to every bound DataPoint.

Only SOURCE bindings are valid (pure-source adapter — no write-back).
DEST and BOTH bindings are skipped with a warning.
"""

from __future__ import annotations

import asyncio
import heapq
import logging
import uuid
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.history.factory import get_history_plugin

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------


class OnPresence(str, Enum):
    KEEP = "behalten"
    RESET = "zuruecksetzen"
    SET = "setzen"


class AnwesenheitssimulationConfig(BaseModel):
    offset_days: int = Field(
        default=7,
        ge=1,
        le=30,
        title="Historischer Versatz (Tage)",
        description="Anzahl Tage in der Vergangenheit, deren Schaltzustände wiederholt werden (1–30)",
    )
    control_dp_id: str | None = Field(
        default=None,
        title="Steuerobjekt",
        description="Boolean-Datenpunkt: Wert 1 = Anwesend (Simulation aus), Wert 0 = Abwesend (Simulation an)",
    )
    control_invert: bool = Field(
        default=False,
        title="Steuerobjekt invertieren",
        description="Bei Aktivierung gilt: Wert 0 = Anwesend (Simulation aus)",
    )
    on_presence: OnPresence = Field(
        default=OnPresence.KEEP,
        title="Verhalten bei Anwesenheit",
        description="Was passiert wenn Anwesenheit erkannt wird (Steuerobjekt = 1)",
    )
    on_presence_value: str | None = Field(
        default=None,
        title="Wert bei Anwesenheit",
        description="Wert der gesetzt wird wenn on_presence='setzen'; leer = false/0",
    )


class AnwesenheitssimulationBindingConfig(BaseModel):
    offset_override: int | None = Field(
        default=None,
        ge=1,
        le=30,
        title="Versatz überschreiben (Tage)",
        description="Überschreibt den Adapter-Standard; leer = Adapter-Standard verwenden",
    )
    on_presence_override: OnPresence | None = Field(
        default=None,
        title="Verhalten bei Anwesenheit überschreiben",
        description="Überschreibt den Adapter-Standard; leer = Adapter-Standard",
    )
    on_presence_value: str | None = Field(
        default=None,
        title="Wert bei Anwesenheit",
        description="Wert der gesetzt wird wenn on_presence_override='setzen'; leer = Adapter-Standard",
    )


# Heap entry: (fire_at, seq, dp_id_str, binding_id_str, value, quality)
_HeapEntry = tuple[datetime, int, str, str, Any, str]


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class AnwesenheitssimulationAdapter(AdapterBase):
    """Presence-simulation adapter — SOURCE-only, replays historical values."""

    adapter_type = "ANWESENHEITSSIMULATION"
    config_schema = AnwesenheitssimulationConfig
    binding_config_schema = AnwesenheitssimulationBindingConfig

    def __init__(
        self,
        event_bus: Any,
        config: dict,
        instance_id: uuid.UUID | None = None,
        name: str = "",
    ) -> None:
        super().__init__(event_bus, config, instance_id, name)
        self._cfg = AnwesenheitssimulationConfig(**config)
        self._task: asyncio.Task | None = None
        self._active: bool = False
        self._pending: list[_HeapEntry] = []
        self._seq: int = 0  # tie-breaker for heap comparisons

    # ------------------------------------------------------------------
    # AdapterBase lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        from obs.core.event_bus import DataValueEvent

        self._active = await self._resolve_initial_state()
        self._bus.subscribe(DataValueEvent, self._handle_control_event)
        self._task = asyncio.create_task(
            self._simulation_loop(),
            name=f"anwesenheitssimulation_{self._instance_id}",
        )
        await self._publish_status(True, "Anwesenheitssimulation gestartet")

    async def disconnect(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        self._pending.clear()
        self._seq = 0
        await self._publish_status(False, "Anwesenheitssimulation gestoppt")

    async def read(self, binding: Any) -> None:  # noqa: ARG002
        return None

    async def write(self, binding: Any, value: Any) -> None:  # noqa: ARG002
        pass

    async def _on_bindings_reloaded(self) -> None:
        skipped = sum(1 for b in self._bindings if b.direction != "SOURCE")
        if skipped:
            logger.warning(
                "AnwesenheitssimulationAdapter: %d DEST/BOTH binding(s) ignoriert — nur SOURCE erlaubt",
                skipped,
            )
        self._pending.clear()
        self._seq = 0
        if self._active:
            await self._preload_window(*self._current_hour_window())

    # ------------------------------------------------------------------
    # Event bus subscription
    # ------------------------------------------------------------------

    async def _handle_control_event(self, event: Any) -> None:
        if not self._cfg.control_dp_id:
            return
        try:
            control_id = uuid.UUID(self._cfg.control_dp_id)
        except ValueError:
            return
        if event.datapoint_id != control_id:
            return

        raw = bool(event.value)
        # XOR: if control_invert is True, flip meaning
        at_home = raw ^ self._cfg.control_invert
        new_active = not at_home  # simulation runs when you are away

        if new_active == self._active:
            return
        self._active = new_active

        if not new_active:
            # You just arrived home
            self._pending.clear()
            self._seq = 0
            await self._handle_presence()
        else:
            # You just left home — start simulation immediately
            await self._preload_window(*self._current_hour_window())

    # ------------------------------------------------------------------
    # On-presence action
    # ------------------------------------------------------------------

    async def _handle_presence(self) -> None:
        from obs.core.event_bus import DataValueEvent

        for binding in self._bindings:
            if binding.direction != "SOURCE" or not binding.enabled:
                continue
            bc = AnwesenheitssimulationBindingConfig(**(binding.config or {}))
            effective = bc.on_presence_override if bc.on_presence_override is not None else self._cfg.on_presence

            if effective == OnPresence.KEEP:
                continue

            if effective == OnPresence.RESET:
                value: Any = False
            else:  # SET
                raw = bc.on_presence_value if bc.on_presence_value is not None else self._cfg.on_presence_value
                value = raw if raw is not None else False

            await self._bus.publish(
                DataValueEvent(
                    datapoint_id=binding.datapoint_id,
                    value=value,
                    quality="good",
                    source_adapter=self.adapter_type,
                    binding_id=binding.id,
                )
            )

    # ------------------------------------------------------------------
    # History pre-loading
    # ------------------------------------------------------------------

    @staticmethod
    def _current_hour_window() -> tuple[datetime, datetime]:
        now = datetime.now(tz=timezone.utc)
        end = now.replace(minute=59, second=59, microsecond=999999)
        return now, end

    @staticmethod
    def _next_hour_window() -> tuple[datetime, datetime]:
        now = datetime.now(tz=timezone.utc)
        start = (now + timedelta(hours=1)).replace(minute=0, second=0, microsecond=0)
        end = start.replace(minute=59, second=59, microsecond=999999)
        return start, end

    async def _preload_window(self, from_dt: datetime, to_dt: datetime) -> None:
        """Fetch historical events in [from_dt-offset … to_dt-offset] and schedule them."""
        if not self._active:
            return
        try:
            history = get_history_plugin()
        except RuntimeError:
            logger.warning("AnwesenheitssimulationAdapter: History-Plugin nicht verfügbar — Vorabruf übersprungen")
            return

        now = datetime.now(tz=timezone.utc)
        new_entries: list[_HeapEntry] = []

        for binding in self._bindings:
            if binding.direction != "SOURCE" or not binding.enabled:
                continue
            bc = AnwesenheitssimulationBindingConfig(**(binding.config or {}))
            offset_days = bc.offset_override if bc.offset_override is not None else self._cfg.offset_days
            delta = timedelta(days=offset_days)

            hist_from = from_dt - delta
            hist_to = to_dt - delta

            try:
                records = await history.query(
                    binding.datapoint_id,
                    hist_from,
                    hist_to,
                    limit=10_000,
                )
            except Exception:
                logger.exception(
                    "AnwesenheitssimulationAdapter: History-Abfrage für Binding %s fehlgeschlagen",
                    binding.id,
                )
                continue

            for rec in records:
                try:
                    raw_ts = rec["ts"]
                    hist_ts = datetime.fromisoformat(raw_ts)
                    if hist_ts.tzinfo is None:
                        hist_ts = hist_ts.replace(tzinfo=timezone.utc)
                    fire_at = hist_ts + delta
                except Exception:
                    continue

                if fire_at <= now:
                    continue  # already past — skip

                entry: _HeapEntry = (
                    fire_at,
                    self._seq,
                    str(binding.datapoint_id),
                    str(binding.id),
                    rec.get("v"),
                    rec.get("q", "good"),
                )
                self._seq += 1
                new_entries.append(entry)

        # Drop stale entries that fall within the newly loaded window, then merge.
        cutoff = to_dt + timedelta(seconds=1)
        self._pending = [e for e in self._pending if e[0] >= cutoff]
        self._pending.extend(new_entries)
        heapq.heapify(self._pending)

        logger.debug(
            "AnwesenheitssimulationAdapter: %d Ereignisse vorgeladen (%s – %s)",
            len(new_entries),
            from_dt.isoformat(),
            to_dt.isoformat(),
        )

    # ------------------------------------------------------------------
    # Firing due events
    # ------------------------------------------------------------------

    async def _fire_due(self) -> None:
        if not self._active:
            return
        from obs.core.event_bus import DataValueEvent

        now = datetime.now(tz=timezone.utc)
        while self._pending and self._pending[0][0] <= now:
            fire_at, _, dp_id_str, binding_id_str, value, quality = heapq.heappop(self._pending)
            try:
                dp_id = uuid.UUID(dp_id_str)
                b_id = uuid.UUID(binding_id_str)
            except ValueError:
                continue
            await self._bus.publish(
                DataValueEvent(
                    datapoint_id=dp_id,
                    value=value,
                    quality=quality,
                    source_adapter=self.adapter_type,
                    binding_id=b_id,
                )
            )
            logger.debug(
                "AnwesenheitssimulationAdapter: Wert %r für DP %s gesendet (historisch: %s)",
                value,
                dp_id_str,
                fire_at.isoformat(),
            )

    # ------------------------------------------------------------------
    # Initial state resolution
    # ------------------------------------------------------------------

    async def _resolve_initial_state(self) -> bool:
        """Return True if simulation should be ACTIVE at startup."""
        if not self._cfg.control_dp_id:
            return True
        try:
            from obs.core.registry import get_registry

            dp_id = uuid.UUID(self._cfg.control_dp_id)
            vs = get_registry().get_value(dp_id)
            if vs is None or vs.value is None:
                return True
            raw = bool(vs.value)
            at_home = raw ^ self._cfg.control_invert
            return not at_home
        except Exception:
            logger.exception("AnwesenheitssimulationAdapter: Steuerobjekt-Initialzustand konnte nicht ermittelt werden")
            return True

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    async def _simulation_loop(self) -> None:
        if self._active and self._bindings:
            await self._preload_window(*self._current_hour_window())

        next_hour_preloaded = False

        while True:
            try:
                now = datetime.now(tz=timezone.utc)

                # Pre-load next hour at minute :55
                if now.minute >= 55 and not next_hour_preloaded:
                    await self._preload_window(*self._next_hour_window())
                    next_hour_preloaded = True
                elif now.minute < 55:
                    next_hour_preloaded = False

                await self._fire_due()

                # Sleep to next full minute boundary (±1 s margin)
                seconds_past = now.second + now.microsecond / 1_000_000
                sleep_sec = max(1.0, 60.0 - seconds_past)
                await asyncio.sleep(sleep_sec)

            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("AnwesenheitssimulationAdapter: Unerwarteter Fehler im Simulations-Loop")
                await asyncio.sleep(60)
