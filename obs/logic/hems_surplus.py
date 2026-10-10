"""Control engine of the ``hems_surplus`` function block (HEMS Lite, Überschussregelung).

The dispatcher branch in ``GraphExecutor._eval_node`` is a thin call into
:func:`evaluate`; the algorithm lives here because it is several hundred lines of
pure, time-driven decision logic. Nothing in this module performs I/O or reads
the clock — the caller passes ``now`` (epoch seconds) and the per-node ``state``
dict, which makes every timing rule directly testable.

Sign convention: the grid power is *positive for import* and *negative for
export*. The power budget the consumers may share is::

    budget = power currently used by the controlled consumers − grid power − reserve

Consumers are served strictly in list order (index 0 = highest priority); a
shrinking budget therefore cuts from the bottom up.
"""

from __future__ import annotations

import json
import logging
import math
import re
from typing import Any

logger = logging.getLogger(__name__)

MODES = ("bidirectional", "split", "feed_in_only")
CONSUMER_MODES = ("percent", "onoff", "trigger")

MIN_INTERVAL_S = 5.0
MAX_INTERVAL_S = 3600.0
DEFAULT_INTERVAL_S = 30.0
DEFAULT_TARGET_W = 0.0
DEFAULT_ON_DELAY_S = 60.0
DEFAULT_OFF_DELAY_S = 180.0

# A cycle may start slightly early so scheduler jitter of the periodic loop does
# not silently stretch the interval to two periods.
_CYCLE_TOLERANCE = 0.9
_EPS = 1e-9
# A fired trigger keeps its optional nominal power reserved for the firing cycle
# and the one after it, so a load that needs a moment to ramp up is not
# immediately handed to a lower priority.
_TRIGGER_RESERVE_CYCLES = 1

_ID_SAFE = re.compile(r"[^A-Za-z0-9_]")


# ── Configuration parsing ────────────────────────────────────────────────


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


def _truthy(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on", "ja", "ein")
    return bool(value)


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
        cid = _ID_SAFE.sub("", str(item.get("id") or "")) or f"n{index + 1}"
        while cid in seen:
            cid = f"{cid}_"
        seen.add(cid)
        mode = item.get("mode") if item.get("mode") in CONSUMER_MODES else "percent"
        consumers.append(
            {
                "id": cid,
                "name": str(item.get("name") or "").strip() or f"#{index + 1}",
                "active": item.get("active") is None or _truthy(item.get("active")),
                "mode": mode,
                "power_source": "input" if item.get("power_source") == "input" else "fixed",
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
                "below_min": "hold_min" if item.get("below_min") == "hold_min" else "off",
                # onoff
                "on_threshold_w": _opt_num(item.get("on_threshold_w"), 0.0),
                "off_threshold_w": _opt_num(item.get("off_threshold_w"), 0.0),
                # trigger
                "min_surplus_w": _cfg_num(item.get("min_surplus_w"), 1000.0, 0.0),
                "pulse_s": _cfg_num(item.get("pulse_s"), 1.0, 0.1, 3600.0),
                "lockout_s": _cfg_num(item.get("lockout_s"), 3600.0, 0.0),
                "rearm": item.get("rearm") if item.get("rearm") in ("after_lockout", "after_drop", "reset_input") else "after_drop",
                "reserve_w": _cfg_num(item.get("reserve_w"), 0.0, 0.0),
            }
        )
    return consumers


def grid_input_names(mode: str) -> tuple[str, ...]:
    """Meter input port ids the given measurement mode reads."""
    if mode == "split":
        return ("grid_import", "grid_export")
    if mode == "feed_in_only":
        return ("feed_in",)
    return ("grid",)


def output_handles(data: dict[str, Any]) -> set[str]:
    """Every output handle the block may emit for this configuration."""
    handles = {"grid_power", "surplus", "budget", "allocated", "remaining", "status", "warning"}
    for consumer in load_consumers(data.get("consumers")):
        handles.add(f"c_{consumer['id']}")
        handles.add(f"c_{consumer['id']}_status")
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
    """Power the consumer draws right now: measured when available, else estimated."""
    measured = _num(inputs.get(f"c_{consumer['id']}_power")) if f"c_{consumer['id']}_power" in inputs else None
    if measured is not None:
        return max(0.0, measured)
    mode = consumer["mode"]
    if mode == "percent":
        return rated_w * cs["setpoint"] / 100.0 if cs["on"] else 0.0
    if mode == "onoff":
        return rated_w if cs["on"] else 0.0
    return consumer["reserve_w"] if cs["reserve_cycles"] > 0 else 0.0


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
    mode: str, inputs: dict[str, Any], state: dict[str, Any], now: float, max_age_s: float
) -> tuple[float | None, float | None, str | None]:
    """Return ``(grid_w, feed_in_w, problem)``.

    ``grid_w`` is positive for import. ``feed_in_w`` is only set in feed-in-only
    mode, where ``0`` means "no known export" rather than a balanced meter.
    ``problem`` is ``"invalid_input"`` / ``"stale_input"`` or ``None``.
    """
    names = grid_input_names(mode)
    values: dict[str, float] = {}
    for name in names:
        value = _num(inputs.get(name))
        if value is None:
            return None, None, "invalid_input"
        values[name] = value

    if max_age_s > 0:
        last_seen = state.setdefault("last_seen", {})
        for name in names:
            changed_key = f"{name}_changed"
            if changed_key not in inputs:
                continue  # no telegram signal wired → age cannot be judged
            if _truthy(inputs.get(changed_key)) or name not in last_seen:
                last_seen[name] = now
            if now - last_seen[name] > max_age_s:
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


# ── Evaluation ───────────────────────────────────────────────────────────


def evaluate(
    data: dict[str, Any],
    inputs: dict[str, Any],
    state: dict[str, Any],
    now: float,
    node_id: str = "",
    *,
    full_snapshot: bool = False,
) -> dict[str, Any]:
    """Run one tick of the block and return the output values to emit.

    Outputs that must not be sent this tick are simply absent from the result.
    ``state`` is mutated in place and is the only memory between ticks.

    ``full_snapshot`` is for interactive debug runs: the control interval and
    "only on change" would otherwise leave the inspector empty whenever the
    scheduler evaluated the block a moment earlier, so the last emitted value
    of every output is added to what this tick emitted. Nothing is recomputed.
    """
    consumers = load_consumers(data.get("consumers"))
    states: dict[str, dict[str, Any]] = state.setdefault("consumers", {})
    for stale_id in set(states) - {c["id"] for c in consumers}:
        del states[stale_id]
    for consumer in consumers:
        states.setdefault(consumer["id"], _new_consumer_state())

    result = _tick(data, inputs, state, now, node_id, consumers, states)
    # Remember the earliest pending pulse end so the scheduler wakes in time.
    ends = [states[c["id"]]["pulse_until"] for c in consumers if states[c["id"]]["pulse_until"] is not None]
    state["next_wake"] = min(ends) if ends else None
    if full_snapshot:
        return {**state.get("last_out", {}), **result}
    return result


def _tick(
    data: dict[str, Any],
    inputs: dict[str, Any],
    state: dict[str, Any],
    now: float,
    node_id: str,
    consumers: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    mode = data.get("grid_mode") if data.get("grid_mode") in MODES else "bidirectional"
    interval_s = _cfg_num(data.get("interval_s"), DEFAULT_INTERVAL_S, MIN_INTERVAL_S, MAX_INTERVAL_S)
    only_on_change = data.get("only_on_change") is None or _truthy(data.get("only_on_change"))
    result: dict[str, Any] = {}

    def emit(key: str, value: Any) -> None:
        last = state.setdefault("last_out", {})
        if only_on_change and key in last and last[key] == value and type(last[key]) is type(value):
            return
        last[key] = value
        result[key] = value

    # Everything that depends on a *signal* rather than on the state of a value
    # is sampled on every tick, not only once per control cycle: a pulse shorter
    # than the interval must still end on time, a "changed" flag is true for
    # exactly the tick its telegram arrived on, and a reset is an edge.
    for consumer in consumers:
        cs = states[consumer["id"]]
        if cs["pulse_until"] is not None and now >= cs["pulse_until"]:
            cs["pulse_until"] = None
            emit(f"c_{consumer['id']}", False)
        if consumer["mode"] == "trigger":
            reset_now = _truthy(inputs.get(f"c_{consumer['id']}_reset"))
            if reset_now and not cs["reset_prev"]:
                cs["armed"] = True
                cs["dropped"] = True
                cs["lockout_until"] = None
                cs["trigger_pending"] = None
            cs["reset_prev"] = reset_now

    # The meter is read on every tick, even when the block is disabled, so the
    # diagnostics keep showing the measured grid power in the safe state too.
    max_age_s = _cfg_num(data.get("max_age_s"), 0.0, 0.0)
    grid_w, feed_in_w, problem = _read_meter(mode, inputs, state, now, max_age_s)
    status = problem or "ok"
    if "enable" in inputs and inputs.get("enable") is not None and not _truthy(inputs.get("enable")):
        status = "disabled"
    elif "plant_ok" in inputs and inputs.get("plant_ok") is not None and not _truthy(inputs.get("plant_ok")):
        status = "plant_not_ok"

    meter_detail = f"mode={mode}, " + ", ".join(f"{name}={inputs.get(name)!r}" for name in grid_input_names(mode))
    _log_status_change(state, node_id, status, meter_detail)

    if grid_w is None or status != "ok":
        # Safe state applies immediately, not at the next cycle, and the next
        # healthy tick starts a fresh cycle straight away.
        state["last_cycle"] = None
        if status in ("invalid_input", "stale_input") and data.get("invalid_behavior") == "hold":
            for consumer in consumers:
                emit(f"c_{consumer['id']}_status", "safe_hold")
        else:
            _apply_safe_state(consumers, states, now, emit)
        # Nothing is being distributed in the safe state; the measured grid power
        # is reported when it is known.
        if grid_w is not None:
            emit("grid_power", round(grid_w, 2) + 0.0)
        for key in ("surplus", "budget", "allocated", "remaining"):
            emit(key, 0.0)
        emit("status", status)
        emit("warning", status in ("invalid_input", "stale_input"))
        return result

    last_cycle = state.get("last_cycle")
    if last_cycle is not None and now - last_cycle < interval_s * _CYCLE_TOLERANCE:
        return result
    state["last_cycle"] = now

    on_delay_s = _cfg_num(data.get("on_delay_s"), DEFAULT_ON_DELAY_S, 0.0)
    off_delay_s = _cfg_num(data.get("off_delay_s"), DEFAULT_OFF_DELAY_S, 0.0)
    target_w = _cfg_num(data.get("target_w"), DEFAULT_TARGET_W)
    dynamic_target = _num(inputs.get("target"))
    if dynamic_target is not None:
        target_w = dynamic_target

    rated: dict[str, float | None] = {c["id"]: _rated_power(c, inputs) for c in consumers}
    used_w = sum(_consumer_power(c, states[c["id"]], inputs, rated[c["id"]] or 0.0) for c in consumers)

    if feed_in_w is not None and feed_in_w <= 0:
        # Feed-in-only meter reading 0 W: balanced or already importing — unknown.
        # Hold the current allocation, and shed one step of the lowest-priority
        # running consumer only after the off-delay has passed without feed-in.
        zero_since = state.get("zero_since")
        budget_w = used_w
        if zero_since is None:
            state["zero_since"] = now
        elif now - zero_since >= off_delay_s:
            budget_w = used_w - _shed_step_w(consumers, states, rated)
            state["zero_since"] = now
    else:
        state["zero_since"] = None
        budget_w = used_w - grid_w - target_w

    allocated_w, remaining_w = _allocate(consumers, states, rated, budget_w, now, on_delay_s, off_delay_s, emit)

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
    state_label = "safe"
    for consumer in consumers:
        cid = consumer["id"]
        cs = states[cid]
        _set_off(cs, now)
        cs["trigger_pending"] = None
        cs["pulse_until"] = None
        cs["reserve_cycles"] = 0
        emit(f"c_{cid}", 0 if consumer["mode"] == "percent" else False)
        emit(f"c_{cid}_status", state_label)


def _shed_step_w(consumers: list[dict[str, Any]], states: dict[str, dict[str, Any]], rated: dict[str, float | None]) -> float:
    """Power of one reduction step of the lowest-priority running consumer."""
    for consumer in reversed(consumers):
        cs = states[consumer["id"]]
        if not cs["on"] or consumer["mode"] == "trigger":
            continue
        power = rated[consumer["id"]] or 0.0
        if consumer["mode"] == "percent":
            return power * consumer["step"] / 100.0
        return power
    return 0.0


def _allocate(
    consumers: list[dict[str, Any]],
    states: dict[str, dict[str, Any]],
    rated: dict[str, float | None],
    budget_w: float,
    now: float,
    on_delay_s: float,
    off_delay_s: float,
    emit: Any,
) -> tuple[float, float]:
    """Serve the consumers top-down and emit their setpoints; returns (allocated, remaining)."""
    available = budget_w
    allocated = 0.0
    for consumer in consumers:
        cid = consumer["id"]
        cs = states[cid]
        power = rated[cid]
        if not consumer["active"]:
            _set_off(cs, now)
            cs["trigger_pending"] = None
            cs["pulse_until"] = None
            emit(f"c_{cid}", 0 if consumer["mode"] == "percent" else False)
            emit(f"c_{cid}_status", "inactive")
            continue
        if power is None:
            _set_off(cs, now)
            emit(f"c_{cid}", 0 if consumer["mode"] == "percent" else False)
            emit(f"c_{cid}_status", "invalid_input")
            continue

        if consumer["mode"] == "percent":
            consumed, label = _step_percent(consumer, cs, power, available, now, on_delay_s, off_delay_s)
            emit(f"c_{cid}", _pct_out(cs["setpoint"]) if cs["on"] else 0)
        elif consumer["mode"] == "onoff":
            consumed, label = _step_onoff(consumer, cs, power, available, now, on_delay_s, off_delay_s)
            emit(f"c_{cid}", cs["on"])
        else:
            consumed, label = _step_trigger(consumer, cs, available, now, on_delay_s)
            emit(f"c_{cid}", cs["pulse_until"] is not None)
        emit(f"c_{cid}_status", label)
        available -= consumed
        allocated += consumed
    return allocated, available


def _step_percent(
    consumer: dict[str, Any], cs: dict[str, Any], power: float, available: float, now: float, on_delay_s: float, off_delay_s: float
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
        if consumer["below_min"] == "hold_min":
            if cs["off_pending"] is None:
                cs["off_pending"] = now
            if now - cs["off_pending"] < _delay(consumer["off_delay_s"], off_delay_s):
                cs["setpoint"] = hold_pct
                return power * hold_pct / 100.0, "off_delay"
        _set_off(cs, now)
        return 0.0, "reduced"

    label = _step_off_consumer(consumer, cs, eligible, wanted_pct, now, on_delay_s)
    return (power * cs["setpoint"] / 100.0 if cs["on"] else 0.0), label


def _step_onoff(
    consumer: dict[str, Any], cs: dict[str, Any], power: float, available: float, now: float, on_delay_s: float, off_delay_s: float
) -> tuple[float, str]:
    on_threshold = consumer["on_threshold_w"] if consumer["on_threshold_w"] is not None else power
    off_threshold = min(consumer["off_threshold_w"] if consumer["off_threshold_w"] is not None else power, on_threshold)

    if cs["on"]:
        if available >= off_threshold - _EPS:
            cs["off_pending"] = None
            return power, "active"
        if now - cs["since"] < consumer["min_runtime_s"]:
            return power, "min_runtime"
        if cs["off_pending"] is None:
            cs["off_pending"] = now
        if now - cs["off_pending"] < _delay(consumer["off_delay_s"], off_delay_s):
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
    if now - cs["trigger_pending"] < _delay(consumer["on_delay_s"], on_delay_s):
        return reserved, "on_delay"

    cs["armed"] = False
    cs["dropped"] = False
    cs["trigger_pending"] = None
    cs["pulse_until"] = now + consumer["pulse_s"]
    cs["lockout_until"] = now + consumer["lockout_s"]
    cs["reserve_cycles"] = _TRIGGER_RESERVE_CYCLES
    return consumer["reserve_w"], "fired"
