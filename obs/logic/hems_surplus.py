"""Control engine of the ``hems_surplus`` function block (HEMS Lite, Überschussregelung).

The dispatcher branch in ``GraphExecutor._eval_node`` is a thin call into
:func:`evaluate`; the algorithm lives here because it is several hundred lines of
pure, time-driven decision logic. Nothing in this module performs I/O or reads
the clock — the caller passes ``now`` (seconds of a monotonic clock) and the per-node ``state``
dict, which makes every timing rule directly testable.

Sign convention: the grid power is *positive for import* and *negative for
export*. The power budget the consumers may share is::

    budget = power currently used by the controlled consumers − grid power − reserve

Consumers are served strictly in list order (index 0 = highest priority); a
shrinking budget therefore cuts from the bottom up.
"""

from __future__ import annotations

import functools
import json
import logging
import math
import re
from decimal import Decimal
from typing import Any

from obs.logic.booleans import to_bool

logger = logging.getLogger(__name__)

MODES = ("bidirectional", "split", "feed_in_only")
CONSUMER_MODES = ("percent", "onoff", "trigger")

MIN_INTERVAL_S = 5.0
MAX_INTERVAL_S = 3600.0
DEFAULT_INTERVAL_S = 30.0
DEFAULT_TARGET_W = 0.0
DEFAULT_ON_DELAY_S = 60.0
DEFAULT_OFF_DELAY_S = 180.0

# A cycle may start this much early (s), so scheduler jitter of the periodic
# loop does not stretch the interval to two periods — an absolute margin, so
# frequent meter telegrams cannot shorten the interval noticeably.
_CYCLE_SLACK_S = 0.5
_EPS = 1e-9
# A fired trigger keeps its optional nominal power reserved for the firing cycle
# and the one after it, so a load that needs a moment to ramp up is not
# immediately handed to a lower priority.
_TRIGGER_RESERVE_CYCLES = 1
# Smallest reduction step (%) of a percent consumer at 0 W feed-in.
_MIN_SHED_PCT = 10.0
# The scheduler wakes this long after a meter value's age limit has passed.
_STALE_WAKE_MARGIN_S = 0.05

_ID_SAFE = re.compile(r"[^A-Za-z0-9_]")
# Port names are c_<id> plus these suffixes ("_max" because c_<id>_max + "_power"
# is the max-power port of <id>); an id ending in one would take another's port.
_RESERVED_ID_ENDINGS = ("_status", "_power", "_max", "_reset")


# ── Configuration parsing ────────────────────────────────────────────────


def _enum(value: Any) -> Any:
    """An enum setting as the config validator compares it: text case- and whitespace-insensitive."""
    return value.strip().lower() if isinstance(value, str) else value


def _num(value: Any) -> float | None:
    """Finite float from a numeric-like value; ``None`` for everything else.

    Booleans are deliberately *not* numbers here: a meter that delivers
    ``True`` is a wiring mistake, not 1 W.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _cfg_num(value: Any, default: float, minimum: float | None = None, maximum: float | None = None) -> float:
    result = _num(value)
    if result is None:
        result = default
    if minimum is not None:
        result = max(minimum, result)
    if maximum is not None:
        result = min(maximum, result)
    return result


def _opt_num(value: Any, minimum: float | None = None) -> float | None:
    """Optional number: empty / non-numeric means "not set"."""
    if value in (None, ""):
        return None
    result = _num(value)
    if result is None:
        return None
    return max(minimum, result) if minimum is not None else result


def _raw_id(value: Any) -> str:
    """A configured id as text, spelled the way the editor's ``String()`` spells it.

    The editor builds the ports from the same id (``hemsConsumers`` in
    logicHemsOutputs.js), so an imported non-text id must give the same port
    name on both sides: ``true`` not ``True``, ``2`` not ``2.0``, ``1e-7`` not
    ``1e-07``. Anything but text, numbers and booleans counts as no id.
    """
    if isinstance(value, bool):
        return "true" if value else ""
    if isinstance(value, int) and abs(value) < 2**53:  # exact in a JS number
        return str(value) if value else ""
    if isinstance(value, int | float):
        try:
            number = float(value)
        except OverflowError:  # beyond any JS number: the editor reads Infinity, i.e. no id
            return ""
        return _js_number(number) if number and math.isfinite(number) else ""
    return value if isinstance(value, str) else ""


def _js_number(number: float) -> str:
    """``String(number)`` of JavaScript: shortest digits, positional from 1e-6 up to 1e21."""
    if 1e-6 <= abs(number) < 1e21:
        text = format(Decimal(repr(number)), "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    return re.sub(r"e([+-])0*(\d)", r"e\1\2", repr(number))


def load_consumers(raw: Any) -> list[dict[str, Any]]:
    """Normalise the configured consumer list; list order is the priority.

    Entries without a usable ``id`` get a positional one and duplicate ids are
    renamed — the id (not the position) addresses the ports, so reordering the
    list never re-wires an edge.
    """
    items: Any = raw
    if isinstance(raw, str):
        try:
            items = json.loads(raw or "[]")
        except (TypeError, ValueError):
            items = []
    if not isinstance(items, list):
        return []

    consumers: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        cid = _ID_SAFE.sub("", _raw_id(item.get("id"))) or f"n{index + 1}"
        if cid.endswith(_RESERVED_ID_ENDINGS):
            cid = f"{cid}_"  # "a_status" would collide with the status port of consumer "a"
        while cid in seen:
            cid = f"{cid}_"
        seen.add(cid)
        mode = _enum(item.get("mode")) if _enum(item.get("mode")) in CONSUMER_MODES else "percent"
        consumers.append(
            {
                "id": cid,
                "name": str(item.get("name") or "").strip() or f"#{index + 1}",
                "active": item.get("active") is None or to_bool(item.get("active")),
                "mode": mode,
                "power_source": "input" if _enum(item.get("power_source")) == "input" else "fixed",
                "power_w": _cfg_num(item.get("power_w"), 1000.0, 0.0),
                "min_runtime_s": _cfg_num(item.get("min_runtime_s"), 0.0, 0.0),
                "min_off_s": _cfg_num(item.get("min_off_s"), 0.0, 0.0),
                "on_delay_s": _opt_num(item.get("on_delay_s"), 0.0),
                "off_delay_s": _opt_num(item.get("off_delay_s"), 0.0),
                # percent
                "min_power_w": _cfg_num(item.get("min_power_w"), 0.0, 0.0),
                "min_setpoint": _cfg_num(item.get("min_setpoint"), 0.0, 0.0, 100.0),
                "max_setpoint": _cfg_num(item.get("max_setpoint"), 100.0, 0.0, 100.0),
                "step": _cfg_num(item.get("step"), 1.0, 0.1, 100.0),
                "below_min": "hold_min" if _enum(item.get("below_min")) == "hold_min" else "off",
                # onoff
                "on_threshold_w": _opt_num(item.get("on_threshold_w"), 0.0),
                "off_threshold_w": _opt_num(item.get("off_threshold_w"), 0.0),
                # trigger
                "min_surplus_w": _cfg_num(item.get("min_surplus_w"), 1000.0, 0.0),
                "pulse_s": _cfg_num(item.get("pulse_s"), 1.0, 0.1, 3600.0),
                "lockout_s": _cfg_num(item.get("lockout_s"), 3600.0, 0.0),
                "rearm": _enum(item.get("rearm")) if _enum(item.get("rearm")) in ("after_lockout", "after_drop", "reset_input") else "after_drop",
                "reserve_w": _cfg_num(item.get("reserve_w"), 0.0, 0.0),
            }
        )
    return consumers


def only_on_change(data: dict[str, Any]) -> bool:
    """Whether unchanged outputs are withheld ("output only on change", default on)."""
    return data.get("only_on_change") is None or to_bool(data.get("only_on_change"))


def grid_mode(data: dict[str, Any]) -> str:
    """The configured measurement mode (see ``MODES``), ``bidirectional`` when unknown."""
    mode = _enum(data.get("grid_mode"))
    return mode if mode in MODES else "bidirectional"


def interval_s(data: dict[str, Any]) -> float:
    """The configured control interval in seconds, clamped to its supported range."""
    return _cfg_num(data.get("interval_s"), DEFAULT_INTERVAL_S, MIN_INTERVAL_S, MAX_INTERVAL_S)


def grid_input_names(mode: str) -> tuple[str, ...]:
    """Meter input port ids the given measurement mode reads."""
    if mode == "split":
        return ("grid_import", "grid_export")
    if mode == "feed_in_only":
        return ("feed_in",)
    return ("grid",)


DIAGNOSTIC_OUTPUTS = ("grid_power", "surplus", "budget", "allocated", "remaining", "status", "warning")


def level_output_handles(data: dict[str, Any]) -> set[str]:
    """Output handles that carry a level (every output but the trigger pulses)."""
    handles = set(DIAGNOSTIC_OUTPUTS)
    for consumer in load_consumers(data.get("consumers")):
        handles.add(f"c_{consumer['id']}_status")
        if consumer["mode"] != "trigger":
            handles.add(f"c_{consumer['id']}")
    return handles


def trigger_output_handles(data: dict[str, Any]) -> set[str]:
    """Output handles that carry a discrete trigger pulse (one per trigger consumer)."""
    return {f"c_{c['id']}" for c in load_consumers(data.get("consumers")) if c["mode"] == "trigger"}


# ── Per-consumer helpers ─────────────────────────────────────────────────


def _new_consumer_state() -> dict[str, Any]:
    return {
        "on": False,
        "since": None,
        "on_pending": None,
        "off_pending": None,
        "setpoint": 0.0,
        "armed": True,
        "dropped": False,
        "trigger_pending": None,
        "pulse_until": None,
        "lockout_until": None,
        "reserve_cycles": 0,
        "reset_prev": False,
    }


def _set_on(cs: dict[str, Any], now: float, setpoint: float) -> None:
    cs["on"] = True
    cs["since"] = now
    cs["on_pending"] = None
    cs["off_pending"] = None
    cs["setpoint"] = setpoint


def _set_off(cs: dict[str, Any], now: float) -> None:
    if cs["on"]:
        cs["since"] = now
    cs["on"] = False
    cs["on_pending"] = None
    cs["off_pending"] = None
    cs["setpoint"] = 0.0


def _delay(consumer_value: float | None, global_value: float) -> float:
    return global_value if consumer_value is None else consumer_value


def _quantize_down(value: float, step: float) -> float:
    return math.floor(value / step + _EPS) * step


def _quantize_up(value: float, step: float) -> float:
    return math.ceil(value / step - _EPS) * step


def _pct_out(value: float) -> int | float:
    rounded = round(value, 2)
    return int(rounded) if float(rounded).is_integer() else rounded


def _consumer_power(consumer: dict[str, Any], cs: dict[str, Any], inputs: dict[str, Any], rated_w: float) -> float:
    """Power of this consumer the block may hand out again: measured when available, else estimated.

    Only a consumer the block itself has switched on counts. One that is
    inactive or switched off by the block but still draws power — run by hand,
    or by its own thermostat — is ordinary house load: its draw is already in
    the grid power and cannot be redistributed. A trigger consumer counts
    nothing here — its reservation is not a draw and is deducted only from what
    lower priorities get (see ``_step_trigger``); a measured-power wire left over
    from a previous mode is ignored.
    """
    if consumer["mode"] == "trigger" or not consumer["active"] or not cs["on"]:
        return 0.0
    key = f"c_{consumer['id']}_power"
    measured = _num(inputs.get(key)) if key in inputs else None
    if measured is not None:
        return max(0.0, measured)
    if consumer["mode"] == "percent":
        return rated_w * cs["setpoint"] / 100.0
    return rated_w


def _rated_power(consumer: dict[str, Any], inputs: dict[str, Any]) -> float | None:
    """Rated/maximum power in W, or ``None`` when a dynamic source is unusable."""
    if consumer["mode"] == "trigger":
        return 0.0
    if consumer["power_source"] == "input":
        value = _num(inputs.get(f"c_{consumer['id']}_max_power"))
        return value if value is not None and value > 0 else None
    return consumer["power_w"]


# ── Meter evaluation ─────────────────────────────────────────────────────


def _read_meter(
    mode: str, inputs: dict[str, Any], state: dict[str, Any], now: float, max_age_s: float, hold_s: float
) -> tuple[float | None, float | None, str | None]:
    """Return ``(grid_w, feed_in_w, problem)``.

    ``grid_w`` is positive for import. ``feed_in_w`` is only set in feed-in-only
    mode, where ``0`` means "no known export" rather than a balanced meter.
    ``problem`` is ``"invalid_input"`` / ``"stale_input"`` or ``None``.
    """
    names = grid_input_names(mode)
    # Only an actual telegram makes a value fresh: a value restored from before
    # a restart stays stale until its meter reports again. Every telegram is
    # recorded before any input is judged, so in split mode a fresh telegram on
    # one side is never lost because the other side is stale or invalid.
    judged = [name for name in names if f"{name}_changed" in inputs] if max_age_s > 0 else []  # no signal → age not judged
    last_seen = state.setdefault("last_seen", {}) if judged else {}
    for name in judged:
        if to_bool(inputs.get(f"{name}_changed")):
            last_seen[name] = now

    # Every input is passed through _held before any is judged, so a value
    # that arrived on one side is kept even when the other side is invalid.
    read = {name: _num(_held(state, name, inputs.get(name), now, hold_s)) for name in names}
    if any(value is None for value in read.values()):
        return None, None, "invalid_input"
    values: dict[str, float] = {name: value for name, value in read.items() if value is not None}

    # The meter is stale only when *no* judged input has reported in time: a
    # send-on-change export side sitting at 0 W says nothing about the meter.
    # Feed-in-only at 0 W is the same case: a send-on-change meter is silent all
    # night, and 0 W is handled conservatively anyway (hold, then shed).
    silent_zero_feed_in = mode == "feed_in_only" and values["feed_in"] == 0
    if judged and not silent_zero_feed_in and all(name not in last_seen or now - last_seen[name] > max_age_s for name in judged):
        return None, None, "stale_input"

    if mode == "split":
        if values["grid_import"] < 0 or values["grid_export"] < 0:
            return None, None, "invalid_input"
        return values["grid_import"] - values["grid_export"], None, None
    if mode == "feed_in_only":
        if values["feed_in"] < 0:
            return None, None, "invalid_input"
        return -values["feed_in"], values["feed_in"], None
    return values["grid"], None, None


def _held(state: dict[str, Any], name: str, raw: Any, now: float, hold_s: float) -> Any:
    """An input's value, keeping the last one that arrived while ``None`` arrives.

    ``None`` means "nothing arrived this run" — e.g. a meter read through an
    API Client that the scheduler's own run did not trigger. The last value
    is kept for ``hold_s``; after that (or before any value ever arrived) the
    input is unknown and ``None`` is returned.
    """
    held = state.setdefault("held", {})
    if raw is not None:
        held[name] = [raw, now]
        return raw
    last = held.get(name)
    return last[0] if last is not None and now - last[1] <= hold_s else None


# ── Evaluation ───────────────────────────────────────────────────────────


def evaluate(
    data: dict[str, Any],
    inputs: dict[str, Any],
    state: dict[str, Any],
    now: float,
    node_id: str = "",
) -> dict[str, Any]:
    """Run one tick of the block and return the output values to emit.

    Outputs that must not be sent this tick are simply absent from the result.
    ``state`` is mutated in place and is the only memory between ticks; its
    ``last_out`` holds the last value sent on every output.
    """
    consumers = load_consumers(data.get("consumers"))
    states: dict[str, dict[str, Any]] = state.setdefault("consumers", {})
    removed = _forget_removed_consumers(consumers, states, state.setdefault("last_out", {}), state.setdefault("held", {}))
    for consumer in consumers:
        cs = states.get(consumer["id"])
        if cs is None or cs.get("mode", consumer["mode"]) != consumer["mode"]:
            # A changed control type starts from scratch: an on/off state, a
            # setpoint or an armed trigger of the old type means nothing for
            # the new one — nor does the value last sent under the old type.
            if cs is not None:
                state["last_out"].pop(f"c_{consumer['id']}", None)
                state["last_out"].pop(f"c_{consumer['id']}_status", None)
            cs = states[consumer["id"]] = _new_consumer_state()
        cs["mode"] = consumer["mode"]

    result = {**removed, **_tick(data, inputs, state, now, node_id, consumers, states)}
    state["last_out"].update({key: value for key, value in result.items() if key not in removed})
    state["next_wake"] = _next_wake(data, consumers, states, state, now)
    return result


def _forget_removed_consumers(
    consumers: list[dict[str, Any]], states: dict[str, dict[str, Any]], last_out: dict[str, Any], held: dict[str, Any]
) -> dict[str, Any]:
    """Drop the state of consumers no longer configured, switching them off first.

    The edge of a removed consumer's output stays in the graph, so the last value
    it carried would otherwise stay on the actuator: an output last sent as
    on / a non-zero setpoint is answered with off / 0 once before it is forgotten.
    """
    result: dict[str, Any] = {}
    for stale_id in set(states) - {c["id"] for c in consumers}:
        del states[stale_id]
        for key in (f"c_{stale_id}_power", f"c_{stale_id}_max_power"):
            held.pop(key, None)  # its held measured / maximum power must not pass to a new consumer of that id
        last = last_out.pop(f"c_{stale_id}", None)
        last_out.pop(f"c_{stale_id}_status", None)
        if isinstance(last, bool):
            if last:
                result[f"c_{stale_id}"] = False
        elif last:
            result[f"c_{stale_id}"] = 0
    return result


def _next_wake(
    data: dict[str, Any], consumers: list[dict[str, Any]], states: dict[str, dict[str, Any]], state: dict[str, Any], now: float
) -> float | None:
    """Earliest moment the scheduler must re-evaluate before the next interval.

    That is the next due control cycle, a pending pulse end — a pulse can be
    shorter than the interval — and the moment a still-fresh meter value turns
    stale, so the safe state is not delayed by up to a whole control interval.
    """
    wakes = [states[c["id"]]["pulse_until"] for c in consumers if states[c["id"]]["pulse_until"] is not None]
    if state.get("last_cycle") is not None:
        # The next cycle is due one interval after the last one, also when that
        # one was started by an event rather than by the scheduler.
        wakes.append(state["last_cycle"] + interval_s(data))
    max_age_s = _cfg_num(data.get("max_age_s"), 0.0, 0.0)
    if max_age_s > 0:
        # Stale once the freshest input is strictly older than max_age_s, so wake just after it.
        # Only the meter inputs of the current mode count (a mode change leaves
        # the old inputs' timestamps behind).
        mode = grid_mode(data)
        last_seen = state.get("last_seen", {})
        freshest = max((last_seen[name] for name in grid_input_names(mode) if name in last_seen), default=None)
        if freshest is not None and freshest + max_age_s >= now:
            wakes.append(freshest + max_age_s + _STALE_WAKE_MARGIN_S)
    return min(wakes) if wakes else None


def _tick(
    data: dict[str, Any],
    inputs: dict[str, Any],
    state: dict[str, Any],
    now: float,
    node_id: str,
    consumers: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    mode = grid_mode(data)
    cycle_s = interval_s(data)
    sends_changes_only = only_on_change(data)
    result: dict[str, Any] = {}

    def emit(key: str, value: Any, *, event: bool = False) -> None:
        # ``event``: a trigger pulse is sent once per firing even without "only
        # on change" — repeating True each cycle would read as a new firing.
        # ``last_out`` is only updated by ``evaluate`` once the tick completed,
        # so a tick that fails half-way records nothing as sent.
        last = state.setdefault("last_out", {})
        if key in result:
            previous, known = result[key], True
        else:
            previous, known = last.get(key), key in last
        if (sends_changes_only or event) and known and previous == value and type(previous) is type(value):
            return
        result[key] = value

    # Everything that depends on a *signal* rather than on the state of a value
    # is sampled on every tick, not only once per control cycle: a pulse shorter
    # than the interval must still end on time, a "changed" flag is true for
    # exactly the tick its telegram arrived on, and a reset is an edge.
    for consumer in consumers:
        cs = states[consumer["id"]]
        if cs["pulse_until"] is not None and now >= cs["pulse_until"]:
            cs["pulse_until"] = None  # only a trigger has one: a mode change resets the state
            cs["pulse_ended"] = now  # no new firing on this tick, or its True would swallow this False
            emit(f"c_{consumer['id']}", False)
        if consumer["mode"] == "trigger":
            reset_now = to_bool(inputs.get(f"c_{consumer['id']}_reset"))
            if reset_now and not cs["reset_prev"]:
                cs["armed"] = True
                cs["dropped"] = True
                cs["lockout_until"] = None
                cs["trigger_pending"] = None
            cs["reset_prev"] = reset_now

    # The meter is read on every tick, even when the block is disabled, so the
    # diagnostics keep showing the measured grid power in the safe state too.
    max_age_s = _cfg_num(data.get("max_age_s"), 0.0, 0.0)
    # How long a value that stops arriving (None) is still trusted.
    hold_s = max_age_s if max_age_s > 0 else 2 * cycle_s
    inputs = dict(inputs)
    for key in [k for k in inputs if k == "target" or (k.startswith("c_") and k.endswith("_power"))]:
        inputs[key] = _held(state, key, inputs[key], now, hold_s)  # dynamic reserve, measured and maximum power
    grid_w, feed_in_w, problem = _read_meter(mode, inputs, state, now, max_age_s, hold_s)
    status = problem or "ok"
    # A wired interlock fails safe: once no value is known (unseeded Read
    # Object, failed producer, beyond hold_s) it counts as false.
    # Both are read on every tick, so a held value never ages out unseen.
    enabled = to_bool(_held(state, "enable", inputs["enable"], now, hold_s)) if "enable" in inputs else True
    plant_ok = to_bool(_held(state, "plant_ok", inputs["plant_ok"], now, hold_s)) if "plant_ok" in inputs else True
    if not enabled:
        status = "disabled"
    elif not plant_ok:
        status = "plant_not_ok"

    meter_detail = f"mode={mode}, " + ", ".join(f"{name}={inputs.get(name)!r}" for name in grid_input_names(mode))
    _log_status_change(state, node_id, status, meter_detail)

    if grid_w is None or status != "ok":
        # Safe state applies immediately, not at the next cycle, and the next
        # healthy tick starts a fresh cycle straight away — with fresh 0 W
        # feed-in tracking, so no shedding delay is skipped afterwards.
        state["last_cycle"] = None
        state["zero_since"] = None
        state["zero_shed_w"] = 0.0
        # Without "only on change" the safe values are repeated once per
        # interval, like healthy ones — not on every run of the sheet.
        safe_cycle = state.get("safe_cycle")
        if safe_cycle is None or now - safe_cycle >= cycle_s - _CYCLE_SLACK_S:
            state["safe_cycle"] = now
        else:
            emit = functools.partial(emit, event=True)
        if status in ("invalid_input", "stale_input") and _enum(data.get("invalid_behavior")) == "hold":
            # Only an output that was actually sent can be held. Without one —
            # e.g. right after a restart — the actuator may still carry a value
            # from before, so that consumer falls back to the safe state.
            last_out = state.setdefault("last_out", {})
            held = [c for c in consumers if c["active"] and f"c_{c['id']}" in last_out]
            for consumer in held:
                # The outage interrupts any "continuously for the delay" condition.
                cs = states[consumer["id"]]
                cs["on_pending"] = cs["off_pending"] = cs["trigger_pending"] = None
                # The held value itself is sent again too (once per interval without
                # "only on change"), e.g. for an actuator with a keep-alive watchdog.
                if consumer["mode"] != "trigger":  # a trigger is an event, not a level to repeat
                    emit(f"c_{consumer['id']}", state["last_out"][f"c_{consumer['id']}"])
                emit(f"c_{consumer['id']}_status", "safe_hold")
            _apply_safe_state([c for c in consumers if c not in held], states, now, emit)
        else:
            _apply_safe_state(consumers, states, now, emit)
        # Nothing is being distributed in the safe state; the measured grid power
        # is reported when it is known.
        if grid_w is not None:
            emit("grid_power", round(grid_w, 2) + 0.0)
        else:
            # Unknown: reported as None ("no value"), so neither a last value
            # stands in for it nor does logic behind it fail on a missing input.
            emit("grid_power", None)
        for key in ("surplus", "budget", "allocated", "remaining"):
            emit(key, 0.0)
        emit("status", status)
        emit("warning", problem is not None)  # a meter fault shows even while disabled
        return result

    state["safe_cycle"] = None
    # A saved configuration change (consumers, modes, Active, limits) takes
    # effect straight away, not only at the next due cycle.
    fingerprint = json.dumps(data, sort_keys=True, default=str)
    if state.get("config") != fingerprint:
        state["config"] = fingerprint
        state["last_cycle"] = None
    last_cycle = state.get("last_cycle")
    if last_cycle is not None and now - last_cycle < cycle_s - _CYCLE_SLACK_S:
        return result
    state["last_cycle"] = now

    on_delay_s = _cfg_num(data.get("on_delay_s"), DEFAULT_ON_DELAY_S, 0.0)
    off_delay_s = _cfg_num(data.get("off_delay_s"), DEFAULT_OFF_DELAY_S, 0.0)
    target_w = _cfg_num(data.get("target_w"), DEFAULT_TARGET_W)
    dynamic_target = _num(inputs.get("target"))
    if dynamic_target is not None:
        target_w = dynamic_target

    rated: dict[str, float | None] = {c["id"]: _rated_power(c, inputs) for c in consumers}

    if feed_in_w is not None and feed_in_w <= 0:
        # Feed-in-only meter reading 0 W: balanced or already importing — unknown.
        # Hold the current allocation; once the off-delay has passed without
        # feed-in, shed one step of the lowest-priority running consumer per
        # control cycle until feed-in shows up again.
        # The hold is the *commanded* allocation, not the measured draw: a
        # consumer drawing more than its setpoint must not raise the setpoint.
        # A requested step stays requested until the consumer has actually
        # given it up — an on/off consumer still runs its own off-delay — and
        # the next step follows in the cycle after that.
        # No consumer may get more than it has now (``caps``), so power freed
        # by one is never handed to another, and a positive feed-in reserve —
        # which 0 W feed-in certainly falls short of — is cut straight away.
        zero_since = state.get("zero_since")
        caps = {c["id"]: _consumer_power(c, states[c["id"]], {}, rated[c["id"]] or 0.0) for c in consumers}
        commanded_w = sum(caps.values())
        target = next((c for c in consumers if c["id"] == state.get("zero_shed_id")), None)
        if state.get("zero_shed_w") and (target is None or not target["active"] or not states[target["id"]]["on"]):
            state["zero_shed_w"] = 0.0  # its consumer is gone, inactive or off: that step is void
        if zero_since is None:
            state["zero_since"] = now
            state["zero_shed_w"] = 0.0
        elif not state.get("zero_shed_w"):
            # The step is requested once the switch-off delay of the consumer it
            # concerns (its own, or the global one) has passed without feed-in;
            # holding, that consumer then gives it up without a second delay.
            shed_id, shed_w = _shed_step(consumers, states, rated)
            # Never more than that consumer has: the rest would be cut from a higher priority.
            shed_w = min(shed_w, caps.get(shed_id, 0.0)) if shed_id is not None else 0.0
            target = next((c for c in consumers if c["id"] == shed_id), None)
            if target is not None and now - zero_since >= _delay(target["off_delay_s"], off_delay_s):
                state["zero_shed_id"], state["zero_shed_w"] = shed_id, shed_w
        # The reserve is cut once, on entering 0 W (as if feed-in had just
        # dropped from the reserve to 0); after that the allocation is held.
        reserve_cut_w = max(0.0, target_w) if zero_since is None else 0.0
        budget_w = commanded_w - reserve_cut_w - (state.get("zero_shed_w") or 0.0)
    else:
        state["zero_since"] = None
        state["zero_shed_w"] = 0.0
        commanded_w = None
        caps = None
        used_w = sum(_consumer_power(c, states[c["id"]], inputs, rated[c["id"]] or 0.0) for c in consumers)
        budget_w = used_w - grid_w - target_w

    allocated_w, remaining_w = _allocate(consumers, states, rated, budget_w, now, on_delay_s, off_delay_s, emit, caps, inputs)
    if commanded_w is not None and state["zero_shed_w"]:
        new_commanded_w = sum(_consumer_power(c, states[c["id"]], {}, rated[c["id"]] or 0.0) for c in consumers)
        if new_commanded_w <= commanded_w - state["zero_shed_w"] + _EPS:
            state["zero_shed_w"] = 0.0  # done; with the off-delay over, the next step follows next cycle

    emit("grid_power", round(grid_w, 2) + 0.0)
    emit("surplus", round(max(0.0, -grid_w - target_w), 2) + 0.0)
    emit("budget", round(budget_w, 2) + 0.0)
    emit("allocated", round(allocated_w, 2) + 0.0)
    emit("remaining", round(max(0.0, remaining_w), 2) + 0.0)
    emit("status", status)
    emit("warning", False)
    return result


def _log_status_change(state: dict[str, Any], node_id: str, status: str, detail: str = "") -> None:
    previous = state.get("status")
    if previous == status:
        return
    state["status"] = status
    label = node_id[:8] if node_id else "?"
    if status == "ok":
        if previous is not None:
            logger.info("hems_surplus %s: resumed control (was %s)", label, previous)
    elif status in ("invalid_input", "stale_input"):
        logger.warning(
            "hems_surplus %s: %s grid measurement (%s) — consumers switched to the configured safe state", label, status.split("_")[0], detail
        )
    else:
        logger.info("hems_surplus %s: %s — consumers switched to the safe state", label, status)


def _apply_safe_state(consumers: list[dict[str, Any]], states: dict[str, dict[str, Any]], now: float, emit: Any) -> None:
    """Percent 0 %, switches off, no trigger — and every running timer restarts."""
    for consumer in consumers:
        _reset_consumer(consumer, states[consumer["id"]], now, emit, "safe")


def _reset_consumer(consumer: dict[str, Any], cs: dict[str, Any], now: float, emit: Any, label: str) -> None:
    """Switch one consumer off (0 %, off, no trigger), drop its pending timers and emit ``label``."""
    _set_off(cs, now)
    cs["trigger_pending"] = None
    cs["pulse_until"] = None
    cs["reserve_cycles"] = 0
    if consumer["mode"] == "trigger":
        emit(f"c_{consumer['id']}", False, event=True)  # an event: sent once, not every interval
    else:
        emit(f"c_{consumer['id']}", 0 if consumer["mode"] == "percent" else False)
    emit(f"c_{consumer['id']}_status", label)


def _shed_step(consumers: list[dict[str, Any]], states: dict[str, dict[str, Any]], rated: dict[str, float | None]) -> tuple[str | None, float]:
    """``(consumer id, W)`` of one reduction step of the lowest-priority running consumer.

    A percent consumer gives up at least ``_MIN_SHED_PCT`` per step, so a fine
    setpoint step does not stretch a reduction over hours of possible import.
    A consumer whose step is worth nothing (0 W) is passed over, or it would
    block every consumer above it.
    """
    for consumer in reversed(consumers):
        cs = states[consumer["id"]]
        if not cs["on"] or not consumer["active"] or consumer["mode"] == "trigger":
            continue
        power = rated[consumer["id"]] or 0.0
        step_w = power * max(consumer["step"], _MIN_SHED_PCT) / 100.0 if consumer["mode"] == "percent" else power
        if step_w > 0:
            return consumer["id"], step_w
    return None, 0.0


def _allocate(
    consumers: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
    rated: dict[str, float | None],
    budget_w: float,
    now: float,
    on_delay_s: float,
    off_delay_s: float,
    emit: Any,
    caps: dict[str, float] | None = None,
    measured: dict[str, Any] | None = None,
) -> tuple[float, float]:
    """Serve the consumers top-down and emit their setpoints; returns (allocated, remaining).

    ``caps`` limits what each consumer may be offered (W) — used to hold an
    allocation without letting any consumer grow.
    """
    available = budget_w
    allocated = 0.0
    for consumer in consumers:
        cid = consumer["id"]
        cs = states[cid]
        power = rated[cid]
        if not consumer["active"]:
            _reset_consumer(consumer, cs, now, emit, "inactive")
            continue
        if power is None:
            _set_off(cs, now)
            emit(f"c_{cid}", 0 if consumer["mode"] == "percent" else False)
            emit(f"c_{cid}_status", "invalid_input")
            continue

        if caps is None:
            offer = available
        elif cs["on"]:
            offer = min(available, caps[cid])
        else:
            offer = -math.inf  # holding: nothing may be switched on or fired, whatever its threshold
        drawn = _num(measured.get(f"c_{cid}_power")) if measured else None
        was_on, previous_setpoint = cs["on"], cs["setpoint"]
        if consumer["mode"] == "percent":
            consumed, label = _step_percent(consumer, cs, power, offer, now, on_delay_s, off_delay_s, holding=caps is not None)
            emit(f"c_{cid}", _pct_out(cs["setpoint"]) if cs["on"] else 0)
        elif consumer["mode"] == "onoff":
            consumed, label = _step_onoff(consumer, cs, power, offer, now, on_delay_s, off_delay_s, holding=caps is not None)
            emit(f"c_{cid}", cs["on"])
        else:
            consumed, label = _step_trigger(consumer, cs, offer, now, on_delay_s)
            emit(f"c_{cid}", cs["pulse_until"] is not None, event=True)
            if caps is not None:
                consumed = 0.0  # holding: the commanded shares never included the reservation
        emit(f"c_{cid}_status", label)
        if caps is None and drawn is not None and consumer["mode"] != "trigger" and was_on and cs["on"] and cs["setpoint"] <= previous_setpoint:
            # A running consumer drawing less than its share (thermostat off,
            # car full) leaves the rest to lower priorities — but not on the
            # cycle it was switched on or raised: the meter still shows the
            # old draw, and that power is already promised to it. Not while
            # holding at 0 W feed-in either: there the commanded share counts.
            consumed = min(consumed, max(0.0, drawn))
        available -= consumed
        allocated += consumed
    return allocated, available


def _step_percent(
    consumer: dict[str, Any],
    cs: dict[str, Any],
    power: float,
    available: float,
    now: float,
    on_delay_s: float,
    off_delay_s: float,
    *,
    holding: bool = False,
) -> tuple[float, str]:
    step = consumer["step"]
    max_pct = consumer["max_setpoint"]
    min_pct = min(
        100.0,
        _quantize_up(max(consumer["min_setpoint"], step, (consumer["min_power_w"] / power * 100.0) if power > 0 else 100.0), step),
    )
    cap_pct = _quantize_down(max_pct, step)
    wanted_pct = min(cap_pct, _quantize_down(max(0.0, available) / power * 100.0, step)) if power > 0 else 0.0
    eligible = power > 0 and min_pct <= cap_pct + _EPS and wanted_pct >= min_pct - _EPS

    if cs["on"]:
        protected = now - cs["since"] < consumer["min_runtime_s"]
        if eligible:
            cs["off_pending"] = None
            cs["setpoint"] = wanted_pct
            label = "active" if wanted_pct >= cap_pct - _EPS else "reduced"
            return power * wanted_pct / 100.0, label
        hold_pct = min(min_pct, cap_pct) if cap_pct > 0 else 0.0
        if protected:
            cs["setpoint"] = hold_pct
            return power * hold_pct / 100.0, "min_runtime"
        # Holding at 0 W feed-in, the shed request has already waited out the delay.
        if consumer["below_min"] == "hold_min" and not holding:
            if cs["off_pending"] is None:
                cs["off_pending"] = now
            if now - cs["off_pending"] < _delay(consumer["off_delay_s"], off_delay_s):
                cs["setpoint"] = hold_pct
                return power * hold_pct / 100.0, "off_delay"
        _set_off(cs, now)
        return 0.0, "off"

    label = _step_off_consumer(consumer, cs, eligible, wanted_pct, now, on_delay_s)
    if label == "active" and wanted_pct < cap_pct - _EPS:
        label = "reduced"  # switched on below its maximum: the same label the next cycles give
    return (power * cs["setpoint"] / 100.0 if cs["on"] else 0.0), label


def _step_onoff(
    consumer: dict[str, Any],
    cs: dict[str, Any],
    power: float,
    available: float,
    now: float,
    on_delay_s: float,
    off_delay_s: float,
    *,
    holding: bool = False,
) -> tuple[float, str]:
    on_threshold = consumer["on_threshold_w"] if consumer["on_threshold_w"] is not None else power
    off_threshold = min(consumer["off_threshold_w"] if consumer["off_threshold_w"] is not None else power, on_threshold)
    if holding:
        # 0 W feed-in: the tolerated import of a lower switch-off threshold
        # cannot be judged, so any cut of this consumer's share means off.
        off_threshold = power

    if cs["on"]:
        if available >= off_threshold - _EPS:
            cs["off_pending"] = None
            return power, "active"
        if now - cs["since"] < consumer["min_runtime_s"]:
            return power, "min_runtime"
        if cs["off_pending"] is None:
            cs["off_pending"] = now
        # Holding at 0 W feed-in, the shed request has already waited out the delay.
        if not holding and now - cs["off_pending"] < _delay(consumer["off_delay_s"], off_delay_s):
            return power, "off_delay"
        _set_off(cs, now)
        return 0.0, "off"

    eligible = available >= on_threshold - _EPS
    label = _step_off_consumer(consumer, cs, eligible, 100.0, now, on_delay_s)
    return (power if cs["on"] else 0.0), label


def _step_off_consumer(consumer: dict[str, Any], cs: dict[str, Any], eligible: bool, setpoint: float, now: float, on_delay_s: float) -> str:
    """Shared "currently off" branch: minimum off time, then the on-delay."""
    if cs["since"] is not None and now - cs["since"] < consumer["min_off_s"]:
        cs["on_pending"] = None
        return "locked"
    if not eligible:
        cs["on_pending"] = None
        return "off"
    if cs["on_pending"] is None:
        cs["on_pending"] = now
    if now - cs["on_pending"] >= _delay(consumer["on_delay_s"], on_delay_s):
        _set_on(cs, now, setpoint)
        return "active"
    return "on_delay"


def _step_trigger(consumer: dict[str, Any], cs: dict[str, Any], available: float, now: float, on_delay_s: float) -> tuple[float, str]:
    reserved = 0.0
    if cs["reserve_cycles"] > 0:
        reserved = consumer["reserve_w"]
        cs["reserve_cycles"] -= 1

    enough = available >= consumer["min_surplus_w"] - _EPS
    locked = cs["lockout_until"] is not None and now < cs["lockout_until"]
    if not cs["armed"]:
        if not enough:
            cs["dropped"] = True
        rearm = consumer["rearm"]
        if not locked and (rearm == "after_lockout" or (rearm == "after_drop" and cs["dropped"])):
            cs["armed"] = True
            cs["lockout_until"] = None

    if cs["pulse_until"] is not None:
        return reserved, "fired"
    if not cs["armed"] or locked:
        cs["trigger_pending"] = None
        return reserved, "locked"
    if not enough:
        cs["trigger_pending"] = None
        return reserved, "off"
    if cs["trigger_pending"] is None:
        cs["trigger_pending"] = now
    if now - cs["trigger_pending"] < _delay(consumer["on_delay_s"], on_delay_s) or cs.get("pulse_ended") == now:
        # (A pulse that ended on this very tick first sends its False.)
        return reserved, "on_delay"

    cs["armed"] = False
    cs["dropped"] = False
    cs["trigger_pending"] = None
    cs["pulse_until"] = now + consumer["pulse_s"]
    cs["lockout_until"] = now + consumer["lockout_s"]
    cs["reserve_cycles"] = _TRIGGER_RESERVE_CYCLES
    return consumer["reserve_w"], "fired"
