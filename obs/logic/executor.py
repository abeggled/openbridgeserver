"""Logic Graph Executor.

Topologically sorts the graph and evaluates each node in order.
Returns a dict of node_id → output_values.
"""

from __future__ import annotations

import ast
import logging
import math
import operator
import re
from datetime import date as _date
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from obs.logic.models import FlowData, LogicNode

logger = logging.getLogger(__name__)

_COMPARE_OPS = {
    ">": operator.gt,
    "<": operator.lt,
    "=": operator.eq,
    ">=": operator.ge,
    "<=": operator.le,
    "!=": operator.ne,
}


class ExecutionError(Exception):
    pass


class GraphExecutor:
    """Executes a logic graph with given input overrides.

    input_overrides: {node_id: {handle_id: value}} — e.g. from datapoint changes
    Returns: {node_id: {handle_id: value}}
    """

    def __init__(
        self,
        flow: FlowData,
        hysteresis_state: dict[str, Any] | None = None,
        app_config: dict[str, Any] | None = None,
    ):
        self.flow = flow
        # NOTE: use `is not None` instead of `or {}` — an empty dict {} is falsy,
        # so `hysteresis_state or {}` would silently create a *new* dict instead of
        # using the passed-in reference, breaking state persistence between runs.
        self.hysteresis_state = hysteresis_state if hysteresis_state is not None else {}
        self.app_config = app_config or {}

    def execute(self, input_overrides: dict[str, dict[str, Any]] | None = None) -> dict[str, dict[str, Any]]:
        """Run the graph. Returns output values for every node."""
        input_overrides = input_overrides or {}

        # Build adjacency: edge target_node.handle ← source_node.handle value
        # edge_map[target_node_id][target_handle] = (source_node_id, source_handle)
        edge_map: dict[str, dict[str, tuple[str, str]]] = {}
        for edge in self.flow.edges:
            src_handle = edge.sourceHandle or "out"
            tgt_handle = edge.targetHandle or "in"
            edge_map.setdefault(edge.target, {})[tgt_handle] = (edge.source, src_handle)

        # Topological sort (Kahn's algorithm)
        order = self._topo_sort()

        # Evaluate
        outputs: dict[str, dict[str, Any]] = {}

        for node in order:
            # Resolve inputs for this node
            inputs: dict[str, Any] = {}
            for handle, (src_id, src_handle) in edge_map.get(node.id, {}).items():
                src_out = outputs.get(src_id, {})
                inputs[handle] = src_out.get(src_handle)

            # Apply overrides (for datapoint_read triggers)
            if node.id in input_overrides:
                inputs.update(input_overrides[node.id])

            try:
                result = self._eval_node(node, inputs)
            except Exception as exc:
                logger.warning("Node %s (%s) error: %s", node.id, node.type, exc)
                result = {}

            outputs[node.id] = result

        return outputs

    # ── Topological Sort ──────────────────────────────────────────────────

    def _topo_sort(self) -> list[LogicNode]:
        node_map = {n.id: n for n in self.flow.nodes}
        in_degree: dict[str, int] = {n.id: 0 for n in self.flow.nodes}
        adj: dict[str, list[str]] = {n.id: [] for n in self.flow.nodes}

        for edge in self.flow.edges:
            if edge.source in adj and edge.target in in_degree:
                adj[edge.source].append(edge.target)
                in_degree[edge.target] += 1

        queue = [nid for nid, deg in in_degree.items() if deg == 0]
        order: list[LogicNode] = []

        while queue:
            nid = queue.pop(0)
            if nid in node_map:
                order.append(node_map[nid])
            for neighbor in adj.get(nid, []):
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        return order

    # ── Type coercion helpers ─────────────────────────────────────────────

    @staticmethod
    def _to_num(v: Any, default: float = 0.0) -> float:
        """Coerce any value to float. bool→1/0, str→float, None→default."""
        if v is None:
            return default
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _to_bool(v: Any) -> bool:
        """Coerce any value to bool. Strings '0'/'false'/'off' → False."""
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip().lower() not in ("0", "false", "no", "off", "")
        return bool(v)

    # ── Node Evaluators ───────────────────────────────────────────────────

    def _eval_node(self, node: LogicNode, inputs: dict[str, Any]) -> dict[str, Any]:
        t = node.type
        d = node.data

        match t:
            case "const_value":
                raw = d.get("value", "0")
                dtype = d.get("data_type", "number")
                if dtype == "bool":
                    val: Any = self._to_bool(raw)
                elif dtype == "number":
                    val = self._to_num(raw)
                else:
                    val = str(raw)
                return {"value": val}

            case "and":
                vals = self._collect_gate_inputs(inputs, d)
                result = all(vals)
                if d.get("negate_out"):
                    result = not result
                return {"out": result}
            case "or":
                vals = self._collect_gate_inputs(inputs, d)
                result = any(vals)
                if d.get("negate_out"):
                    result = not result
                return {"out": result}
            case "not":
                return {"out": not self._to_bool(inputs.get("in1"))}
            case "xor":
                vals = self._collect_gate_inputs(inputs, d)
                result = sum(vals) == 1  # exactly one input is True
                if d.get("negate_out"):
                    result = not result
                return {"out": result}

            case "gate":
                enable = self._to_bool(inputs.get("enable"))
                if d.get("negate_enable"):
                    enable = not enable
                if enable:
                    val = inputs.get("in")
                    self.hysteresis_state[node.id] = val
                    return {"out": val}
                # Gate is closed
                if d.get("closed_behavior", "retain") == "retain":
                    return {"out": self.hysteresis_state.get(node.id)}
                raw = d.get("default_value", "0")
                try:
                    out_val: Any = float(raw)
                except (TypeError, ValueError):
                    out_val = str(raw) if raw is not None else None
                return {"out": out_val}

            case "compare":
                op = _COMPARE_OPS.get(d.get("operator", ">"), operator.gt)
                a, b = inputs.get("in1"), inputs.get("in2")
                if a is None or b is None:
                    return {"out": False}
                # Auto-coerce to number when both values look numeric
                try:
                    return {"out": op(self._to_num(a), self._to_num(b))}
                except TypeError:
                    return {"out": op(str(a), str(b))}

            case "hysteresis":
                val = inputs.get("value")
                on_thr = float(d.get("threshold_on", 25.0))
                off_thr = float(d.get("threshold_off", 20.0))
                prev = self.hysteresis_state.get(node.id, False)
                if val is None:
                    return {"out": prev}
                fval = self._to_num(val)
                if fval >= on_thr:
                    state = True
                elif fval <= off_thr:
                    state = False
                else:
                    state = prev
                self.hysteresis_state[node.id] = state
                return {"out": state}

            case "math_formula":
                formula = d.get("formula", "a + b")
                # Ports are in1/in2; formula variables remain a/b for user convenience
                a = self._to_num(inputs.get("in1"))
                b = self._to_num(inputs.get("in2"))
                result = self._safe_eval(formula, {"a": a, "b": b})
                output_formula = (d.get("output_formula") or "").strip()
                if output_formula:
                    result = self._safe_eval(output_formula, {"x": result})
                return {"result": result}

            case "math_map":
                val = self._to_num(inputs.get("value"))
                in_min = float(d.get("in_min", 0))
                in_max = float(d.get("in_max", 100))
                out_min = float(d.get("out_min", 0))
                out_max = float(d.get("out_max", 1))
                if in_max == in_min:
                    return {"result": out_min}
                mapped = (val - in_min) / (in_max - in_min) * (out_max - out_min) + out_min
                return {"result": mapped}

            case "datapoint_read":
                # Value is injected via input_overrides from the manager.
                # Apply optional value_formula transform (variable: x).
                raw = inputs.get("value")
                formula = (d.get("value_formula") or "").strip()
                if formula and raw is not None:
                    try:
                        raw = self._safe_eval(formula, {"x": self._to_num(raw)})
                    except Exception as exc:
                        logger.debug("datapoint_read formula error: %s", exc)
                value_map = d.get("value_map")
                if value_map and raw is not None:
                    from obs.core.transformation import apply_value_map

                    raw = apply_value_map(raw, value_map)
                return {"value": raw, "changed": inputs.get("changed", False)}

            case "datapoint_write":
                # Apply optional value_formula transform (variable: x) before manager writes.
                write_val = inputs.get("value")
                formula = (d.get("value_formula") or "").strip()
                if formula and write_val is not None:
                    try:
                        write_val = self._safe_eval(formula, {"x": self._to_num(write_val)})
                    except Exception as exc:
                        logger.debug("datapoint_write formula error: %s", exc)
                value_map = d.get("value_map")
                if value_map and write_val is not None:
                    from obs.core.transformation import apply_value_map

                    write_val = apply_value_map(write_val, value_map)
                return {"_write_value": write_val, "_triggered": inputs.get("trigger")}

            case "python_script":
                script = d.get("script", "result = 0")
                result = self._run_script(script, inputs)
                return {"result": result}

            case "clamp":
                lo = float(d.get("min", 0))
                hi = float(d.get("max", 100))
                val = self._to_num(inputs.get("value"))
                return {"result": max(lo, min(hi, val))}

            case "random_value":
                if not self._to_bool(inputs.get("trigger")):
                    return {"value": None}
                import random

                lo = float(d.get("min", 0))
                hi = float(d.get("max", 100))
                if lo > hi:
                    lo, hi = hi, lo
                if d.get("data_type", "int") == "float":
                    decimals = max(0, min(10, int(d.get("decimal_places", 2))))
                    result: int | float = round(random.uniform(lo, hi), decimals)
                else:
                    result = random.randint(int(lo), int(hi))
                return {"value": result}

            case "string_concat":
                count = max(2, min(20, int(d.get("count", 2))))
                sep = str(d.get("separator", ""))
                parts: list[str] = []
                for i in range(1, count + 1):
                    val = inputs.get(f"in_{i}")
                    if val is not None:
                        parts.append(str(val))
                    else:
                        static = d.get(f"text_{i}")
                        parts.append(str(static) if static is not None else "")
                return {"result": sep.join(parts)}

            case "statistics":
                # State stored in hysteresis_state keyed by node.id
                state = self.hysteresis_state.setdefault(node.id, {"s_min": None, "s_max": None, "s_sum": 0.0, "s_count": 0})
                if self._to_bool(inputs.get("reset")):
                    state.update({"s_min": None, "s_max": None, "s_sum": 0.0, "s_count": 0})
                val = inputs.get("value")
                if val is not None:
                    fval = self._to_num(val)
                    state["s_min"] = fval if state["s_min"] is None else min(state["s_min"], fval)
                    state["s_max"] = fval if state["s_max"] is None else max(state["s_max"], fval)
                    state["s_sum"] += fval
                    state["s_count"] += 1
                cnt = state["s_count"]
                avg = (state["s_sum"] / cnt) if cnt > 0 else None
                return {
                    "min": state["s_min"],
                    "max": state["s_max"],
                    "avg": round(avg, 6) if avg is not None else None,
                    "count": cnt,
                }

            case "astro_sun":
                try:
                    import datetime as _dt  # noqa: PLC0415
                    from zoneinfo import ZoneInfo  # noqa: PLC0415

                    from astral import LocationInfo  # noqa: PLC0415
                    from astral.sun import sun as _astral_sun  # noqa: PLC0415

                    lat = float(d.get("latitude", 47.37))
                    lon = float(d.get("longitude", 8.54))
                    tz_name = self.app_config.get("timezone", "Europe/Zurich")
                    tz = ZoneInfo(tz_name)
                    loc = LocationInfo(latitude=lat, longitude=lon, timezone=tz_name)
                    today = _dt.datetime.now(tz).date()
                    s = _astral_sun(loc.observer, date=today, tzinfo=tz)
                    now_dt = _dt.datetime.now(tz)
                    is_day = s["sunrise"] <= now_dt <= s["sunset"]
                    return {
                        "sunrise": s["sunrise"].strftime("%H:%M"),
                        "sunset": s["sunset"].strftime("%H:%M"),
                        "is_day": is_day,
                    }
                except ImportError:
                    logger.warning("astral not installed — astro_sun needs: pip install astral")
                    return {"sunrise": None, "sunset": None, "is_day": False}
                except Exception as exc:
                    logger.warning("astro_sun error: %s", exc)
                    return {"sunrise": None, "sunset": None, "is_day": False}

            case "operating_hours":
                # _computed_hours is injected as override by LogicManager before execution
                hours = self._to_num(inputs.get("_computed_hours", 0.0))
                return {
                    "hours": round(hours, 4),
                    "_active": self._to_bool(inputs.get("active")),
                    "_reset": self._to_bool(inputs.get("reset")),
                }

            case "notify_pushover":
                # Fires when message arrives OR trigger is truthy (both optional).
                msg = inputs.get("message")
                triggered = self._to_bool(inputs.get("trigger")) if "trigger" in inputs else False
                return {
                    "_trigger": msg is not None or triggered,
                    "_message": msg,
                    "_url": inputs.get("url"),
                    "_url_title": inputs.get("url_title"),
                    "_image_url": inputs.get("image_url"),
                    "sent": False,
                }

            case "notify_sms":
                # Fires when message arrives OR trigger is truthy (both optional).
                msg = inputs.get("message")
                triggered = self._to_bool(inputs.get("trigger")) if "trigger" in inputs else False
                return {
                    "_trigger": msg is not None or triggered,
                    "_message": msg,
                    "sent": False,
                }

            case "api_client":
                # Async — fully handled by LogicManager after executor run
                return {
                    "_trigger": inputs.get("trigger"),
                    "_body": inputs.get("body"),
                    "response": None,
                    "status": None,
                    "success": False,
                }

            case "json_extractor":
                import json as _json_mod  # noqa: PLC0415

                raw = inputs.get("data")
                json_path = (d.get("json_path") or "").strip()

                # Parse raw input to Python object
                if isinstance(raw, str):
                    try:
                        data_obj: Any = _json_mod.loads(raw)
                    except (ValueError, TypeError):
                        data_obj = raw
                elif raw is not None:
                    data_obj = raw
                else:
                    data_obj = None

                # Extract value at dotted path
                value: Any = None
                if data_obj is not None:
                    if json_path:
                        try:
                            value = self._json_extract(data_obj, json_path)
                        except (KeyError, IndexError, TypeError, ValueError):
                            value = None
                    else:
                        value = None  # No path configured — user must select one

                # _preview: compact JSON snapshot for config-panel path picker (max 20 KB)
                try:
                    preview = _json_mod.dumps(data_obj, default=str, ensure_ascii=False)
                    if len(preview) > 20_000:
                        preview = preview[:20_000] + "…"
                except Exception:
                    preview = str(data_obj) if data_obj is not None else None

                return {"value": value, "_preview": preview}

            case "xml_extractor":
                import xml.etree.ElementTree as _ET  # noqa: PLC0415

                raw_xml = inputs.get("data")
                xml_path = (d.get("xml_path") or "").strip()

                value = None
                preview_str: str | None = None

                if isinstance(raw_xml, str) and raw_xml.strip():
                    preview_str = raw_xml[:20_000] if len(raw_xml) > 20_000 else raw_xml
                    try:
                        root = _ET.fromstring(raw_xml.strip())
                        if xml_path:
                            el = root.find(xml_path)
                            if el is not None:
                                value = (el.text or "").strip()
                        # no path → value stays None; user must select one
                    except _ET.ParseError:
                        pass

                return {"value": value, "_preview": preview_str}

            case "substring_extractor":
                import re as _re  # noqa: PLC0415

                raw_text = inputs.get("data")
                mode = (d.get("mode") or "rechts_von").strip()
                value = None

                if isinstance(raw_text, str) and raw_text:
                    try:
                        if mode == "links_von":
                            search = d.get("search") or ""
                            if search:
                                occ = d.get("occurrence", "first")
                                idx = raw_text.rfind(search) if occ == "last" else raw_text.find(search)
                                if idx != -1:
                                    value = raw_text[:idx]

                        elif mode == "rechts_von":
                            search = d.get("search") or ""
                            if search:
                                occ = d.get("occurrence", "first")
                                idx = raw_text.rfind(search) if occ == "last" else raw_text.find(search)
                                if idx != -1:
                                    value = raw_text[idx + len(search) :]

                        elif mode == "zwischen":
                            start_m = d.get("start_marker") or ""
                            end_m = d.get("end_marker") or ""
                            if start_m and end_m:
                                idx_s = raw_text.find(start_m)
                                if idx_s != -1:
                                    idx_s += len(start_m)
                                    idx_e = raw_text.find(end_m, idx_s)
                                    if idx_e != -1:
                                        value = raw_text[idx_s:idx_e]

                        elif mode == "ausschneiden":
                            start = int(d.get("start") or 0)
                            length = int(d.get("length") if d.get("length") is not None else -1)
                            if length < 0:
                                value = raw_text[start:]
                            else:
                                value = raw_text[start : start + length]

                        elif mode == "regex":
                            pattern = d.get("pattern") or ""
                            if pattern:
                                flag_str = (d.get("flags") or "").lower()
                                re_flags = 0
                                if "i" in flag_str:
                                    re_flags |= _re.IGNORECASE
                                if "m" in flag_str:
                                    re_flags |= _re.MULTILINE
                                if "s" in flag_str:
                                    re_flags |= _re.DOTALL
                                m = _re.search(pattern, raw_text, re_flags)
                                if m:
                                    group = int(d.get("group") or 0)
                                    value = m.group(group)
                    except Exception:
                        value = None

                preview_str = raw_text[:20_000] if raw_text and len(raw_text) > 20_000 else raw_text
                return {"value": value, "_preview": preview_str}

            case "timer_cron":
                # Fired by manager via input_overrides; pass trigger signal downstream
                return {"trigger": inputs.get("trigger", False)}

            case "timer_delay" | "timer_pulse":
                # Async nodes — handled by manager, not executor
                return {}

            case "heating_circuit":
                # DIN-Norm Sommer/Winter-Umschaltung mit Hysterese
                # Eingang: Temperaturwert (wird je nach Tageszeit T1/T2/T3 zugeordnet)
                # T1 ≈ 07:00, T2 ≈ 14:00, T3 ≈ 22:00 (doppelt gewichtet)
                # T_avg = (T1 + T2 + 2×T3) / 4
                # heating_mode=1 wenn ref_temp < temp_winter, bleibt 1 bis ref_temp > temp_summer
                import datetime as _dt

                state = self.hysteresis_state.setdefault(
                    node.id,
                    {
                        "t1": None,
                        "t1_date": None,
                        "t2": None,
                        "t2_date": None,
                        "t3": None,
                        "t3_date": None,
                        "daily_temps": [],
                        "daily_avg": None,
                        "monthly_avg": None,
                        "heating_mode": 0,
                    },
                )
                val = inputs.get("value")
                temp_winter = float(d.get("temp_winter", 15.0))
                temp_summer = float(d.get("temp_summer", 20.0))
                if val is not None:
                    fval = self._to_num(val)
                    today = _dt.date.today().isoformat()
                    # _slot allows unit tests to force a specific slot without mocking time
                    slot = inputs.get("_slot")
                    if slot is None:
                        hour = _dt.datetime.now().hour
                        if 5 <= hour <= 10:
                            slot = "t1"
                        elif 12 <= hour <= 16:
                            slot = "t2"
                        elif 20 <= hour <= 23:
                            slot = "t3"
                    if slot in ("t1", "t2", "t3"):
                        state[slot] = fval
                        state[f"{slot}_date"] = today
                    # Compute daily avg once all three slots have today's date
                    if state["t1_date"] == today and state["t2_date"] == today and state["t3_date"] == today:
                        daily_avg = (state["t1"] + state["t2"] + 2 * state["t3"]) / 4
                        state["daily_avg"] = daily_avg
                        state["daily_temps"].append(daily_avg)
                        state["daily_temps"] = state["daily_temps"][-31:]
                        state["monthly_avg"] = sum(state["daily_temps"]) / len(state["daily_temps"])
                        # Reset slots for next day
                        for k in ("t1", "t2", "t3", "t1_date", "t2_date", "t3_date"):
                            state[k] = None
                # Hysteresis: switch ON below temp_winter, OFF above temp_summer
                ref_temp = state["monthly_avg"] if state["monthly_avg"] is not None else state["daily_avg"]
                if ref_temp is not None:
                    if ref_temp < temp_winter:
                        state["heating_mode"] = 1
                    elif ref_temp > temp_summer:
                        state["heating_mode"] = 0
                    # between thresholds: keep last state (hysteresis)
                return {
                    "heating_mode": state["heating_mode"],
                    "daily_avg": state["daily_avg"],
                    "monthly_avg": state["monthly_avg"],
                    "t1": state["t1"],
                    "t2": state["t2"],
                    "t3": state["t3"],
                }

            case "min_max_tracker":
                state = self.hysteresis_state.setdefault(
                    node.id,
                    {
                        "abs_min": None,
                        "abs_max": None,
                        "day_min": None,
                        "day_max": None,
                        "last_day": None,
                        "week_min": None,
                        "week_max": None,
                        "last_week": None,
                        "month_min": None,
                        "month_max": None,
                        "last_month": None,
                        "year_min": None,
                        "year_max": None,
                        "last_year": None,
                        "initialized": False,
                    },
                )
                today = _date.today()
                day_key = today.isoformat()
                week_key = f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
                month_key = f"{today.year}-{today.month:02d}"
                year_key = str(today.year)
                # Period resets FIRST — so seeds applied afterwards survive the next call
                if state["last_day"] != day_key:
                    state["day_min"] = state["day_max"] = None
                    state["last_day"] = day_key
                if state["last_week"] != week_key:
                    state["week_min"] = state["week_max"] = None
                    state["last_week"] = week_key
                if state["last_month"] != month_key:
                    state["month_min"] = state["month_max"] = None
                    state["last_month"] = month_key
                if state["last_year"] != year_key:
                    state["year_min"] = state["year_max"] = None
                    state["last_year"] = year_key
                # Apply seed values AFTER resets (once, e.g. migrated from predecessor system)
                if not state["initialized"]:
                    for key, cfg_key in [
                        ("abs_min", "init_abs_min"),
                        ("abs_max", "init_abs_max"),
                        ("day_min", "init_day_min"),
                        ("day_max", "init_day_max"),
                        ("month_min", "init_month_min"),
                        ("month_max", "init_month_max"),
                        ("year_min", "init_year_min"),
                        ("year_max", "init_year_max"),
                    ]:
                        v = d.get(cfg_key)
                        if v not in (None, ""):
                            state[key] = float(v)
                    state["initialized"] = True
                val = inputs.get("value")
                if val is not None:
                    fval = self._to_num(val)
                    for mn_key, mx_key in [
                        ("abs_min", "abs_max"),
                        ("day_min", "day_max"),
                        ("week_min", "week_max"),
                        ("month_min", "month_max"),
                        ("year_min", "year_max"),
                    ]:
                        state[mn_key] = fval if state[mn_key] is None else min(state[mn_key], fval)
                        state[mx_key] = fval if state[mx_key] is None else max(state[mx_key], fval)
                return {
                    "min_daily": state["day_min"],
                    "max_daily": state["day_max"],
                    "min_weekly": state["week_min"],
                    "max_weekly": state["week_max"],
                    "min_monthly": state["month_min"],
                    "max_monthly": state["month_max"],
                    "min_yearly": state["year_min"],
                    "max_yearly": state["year_max"],
                    "min_abs": state["abs_min"],
                    "max_abs": state["abs_max"],
                }

            case "avg_multi":
                import datetime as _dt  # noqa: PLC0415

                state = self.hysteresis_state.setdefault(node.id, {"samples": []})
                count = max(2, min(20, int(d.get("input_count", 2))))
                # Collect all non-None inputs
                values: list[float] = []
                for i in range(1, count + 1):
                    v = inputs.get(f"in_{i}")
                    if v is not None:
                        values.append(self._to_num(v))
                if values:
                    current_avg: float | None = sum(values) / len(values)
                    now_utc = _dt.datetime.now(_dt.UTC)
                    state["samples"].append([now_utc.isoformat(), current_avg])
                    # Trim buffer: keep only samples within the max window (365 days)
                    cutoff_iso = (now_utc - _dt.timedelta(days=365)).isoformat()
                    state["samples"] = [s for s in state["samples"] if s[0] >= cutoff_iso]
                else:
                    current_avg = None
                # Compute moving averages for each time window
                _WINDOWS = {
                    "avg_1m": 60,
                    "avg_1h": 3_600,
                    "avg_1d": 86_400,
                    "avg_7d": 604_800,
                    "avg_14d": 1_209_600,
                    "avg_30d": 2_592_000,
                    "avg_180d": 15_552_000,
                    "avg_365d": 31_536_000,
                }
                now_utc2 = _dt.datetime.now(_dt.UTC)
                result: dict[str, Any] = {"avg": round(current_avg, 6) if current_avg is not None else None}
                for key, seconds in _WINDOWS.items():
                    cutoff = (now_utc2 - _dt.timedelta(seconds=seconds)).isoformat()
                    window_vals = [s[1] for s in state["samples"] if s[0] >= cutoff]
                    result[key] = round(sum(window_vals) / len(window_vals), 6) if window_vals else None
                return result

            case "consumption_counter":
                state = self.hysteresis_state.setdefault(
                    node.id,
                    {
                        "last_value": None,
                        "daily": 0.0,
                        "prev_daily": 0.0,
                        "last_day": None,
                        "weekly": 0.0,
                        "prev_weekly": 0.0,
                        "last_week": None,
                        "monthly": 0.0,
                        "prev_monthly": 0.0,
                        "last_month": None,
                        "yearly": 0.0,
                        "prev_yearly": 0.0,
                        "last_year": None,
                        "initialized": False,
                    },
                )
                today = _date.today()
                day_key = today.isoformat()
                week_key = f"{today.isocalendar()[0]}-W{today.isocalendar()[1]:02d}"
                month_key = f"{today.year}-{today.month:02d}"
                year_key = str(today.year)
                # Period resets FIRST (save previous period total before clearing)
                if state["last_day"] != day_key:
                    state["prev_daily"] = state["daily"]
                    state["daily"] = 0.0
                    state["last_day"] = day_key
                if state["last_week"] != week_key:
                    state["prev_weekly"] = state["weekly"]
                    state["weekly"] = 0.0
                    state["last_week"] = week_key
                if state["last_month"] != month_key:
                    state["prev_monthly"] = state["monthly"]
                    state["monthly"] = 0.0
                    state["last_month"] = month_key
                if state["last_year"] != year_key:
                    state["prev_yearly"] = state["yearly"]
                    state["yearly"] = 0.0
                    state["last_year"] = year_key
                # Apply seed values AFTER resets (once, e.g. migrated from predecessor system)
                if not state["initialized"]:
                    v_meter = d.get("init_meter")
                    if v_meter not in (None, ""):
                        state["last_value"] = float(v_meter)
                    for key, cfg_key in [
                        ("daily", "init_daily"),
                        ("weekly", "init_weekly"),
                        ("monthly", "init_monthly"),
                        ("yearly", "init_yearly"),
                    ]:
                        v = d.get(cfg_key)
                        if v not in (None, ""):
                            state[key] = float(v)
                    state["initialized"] = True
                val = inputs.get("value")
                if val is not None:
                    fval = self._to_num(val)
                    # Add delta (counter increments only; ignores rollovers)
                    prev = state["last_value"]
                    if prev is not None and fval >= prev:
                        delta = fval - prev
                        state["daily"] += delta
                        state["weekly"] += delta
                        state["monthly"] += delta
                        state["yearly"] += delta
                    state["last_value"] = fval
                return {
                    "daily": state["daily"],
                    "weekly": state["weekly"],
                    "monthly": state["monthly"],
                    "yearly": state["yearly"],
                    "prev_daily": state["prev_daily"],
                    "prev_weekly": state["prev_weekly"],
                    "prev_monthly": state["prev_monthly"],
                    "prev_yearly": state["prev_yearly"],
                }

            case _:
                logger.debug("Unknown node type: %s", t)
                return {}

    @staticmethod
    def _json_extract(obj: Any, path: str) -> Any:
        """Extract a value from a nested dict/list using dotted-notation path.

        Supports:
          "key"           → obj["key"]
          "parent.child"  → obj["parent"]["child"]
          "items.0.name"  → obj["items"][0]["name"]
          "a[0].b"        → obj["a"][0]["b"]  (bracket notation normalised)
        """
        # Normalise array brackets: "items[0]" → "items.0"
        path = re.sub(r"\[(\d+)\]", r".\1", path)
        parts = [p for p in path.split(".") if p]
        current = obj
        for part in parts:
            if isinstance(current, dict):
                current = current[part]
            elif isinstance(current, (list, tuple)):
                current = current[int(part)]
            else:
                raise TypeError(f"Cannot traverse {type(current).__name__} with key '{part}'")
        return current

    def _collect_gate_inputs(self, inputs: dict[str, Any], d: dict[str, Any]) -> list[bool]:
        """Collect all active gate inputs with per-input negation applied.

        Port naming: in1, in2, in3, … up to input_count.
        Negation config: "negate_in1", "negate_in2", …
        """
        count = max(2, min(30, int(d.get("input_count", 2))))
        vals: list[bool] = []
        for i in range(1, count + 1):
            port_id = f"in{i}"
            v = self._to_bool(inputs.get(port_id))
            if d.get(f"negate_{port_id}"):
                v = not v
            vals.append(v)
        return vals

    @staticmethod
    def _round_half_up(x: Any, ndigits: int = 0) -> Any:
        """Round using ROUND_HALF_UP (mathematical rounding) via Decimal.

        Python's built-in round() uses banker's rounding (round-half-to-even)
        and is affected by float representation errors — e.g. round(21.16, 1)
        returns 21.1 because 21.16 is stored as 21.159999... in IEEE 754.
        This function converts via str(x) to avoid that issue.
        """
        try:
            d = Decimal(str(x))
            quant = Decimal(10) ** -ndigits
            result = float(d.quantize(quant, rounding=ROUND_HALF_UP))
            return int(result) if ndigits <= 0 else result
        except Exception:
            return round(x, ndigits)  # fallback

    @staticmethod
    def _safe_eval(expr: str, ctx: dict[str, Any]) -> Any:
        """Evaluate a math expression safely.

        Available: all math.* functions + abs, round, min, max + ctx variables.
        """
        allowed = {k: v for k, v in math.__dict__.items() if not k.startswith("_")}
        # Add Python builtins that are safe and useful in formulas.
        # Use _round_half_up instead of built-in round to get mathematical
        # rounding (0.5 always rounds up) rather than banker's rounding.
        allowed.update({"abs": abs, "round": GraphExecutor._round_half_up, "min": min, "max": max})
        allowed.update(ctx)
        try:
            tree = ast.parse(expr, mode="eval")
            return eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, allowed)  # noqa: S307
        except Exception as exc:
            raise ExecutionError(f"Formula error: {exc}") from exc

    @staticmethod
    def _run_script(script: str, inputs: dict[str, Any]) -> Any:
        """Run a restricted Python script."""
        local_ns: dict[str, Any] = {"inputs": inputs, "result": None, "math": math}
        try:
            exec(
                script,
                {
                    "__builtins__": {
                        "range": range,
                        "len": len,
                        "int": int,
                        "float": float,
                        "str": str,
                        "bool": bool,
                        "abs": abs,
                        "min": min,
                        "max": max,
                        "round": GraphExecutor._round_half_up,
                        "math": math,
                    },
                },
                local_ns,
            )
            return local_ns.get("result")
        except Exception as exc:
            raise ExecutionError(f"Script error: {exc}") from exc
