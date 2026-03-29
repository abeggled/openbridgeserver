"""
Logic Graph Executor.

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
from typing import Any

from opentws.logic.models import FlowData, LogicNode

logger = logging.getLogger(__name__)

_COMPARE_OPS = {
    ">":  operator.gt,
    "<":  operator.lt,
    "=":  operator.eq,
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
                raw   = d.get("value", "0")
                dtype = d.get("data_type", "number")
                if dtype == "bool":
                    val: Any = self._to_bool(raw)
                elif dtype == "number":
                    val = self._to_num(raw)
                else:
                    val = str(raw)
                return {"value": val}

            case "and":
                return {"out": self._to_bool(inputs.get("a")) and self._to_bool(inputs.get("b"))}
            case "or":
                return {"out": self._to_bool(inputs.get("a")) or self._to_bool(inputs.get("b"))}
            case "not":
                return {"out": not self._to_bool(inputs.get("in"))}
            case "xor":
                return {"out": self._to_bool(inputs.get("a")) ^ self._to_bool(inputs.get("b"))}

            case "compare":
                op  = _COMPARE_OPS.get(d.get("operator", ">"), operator.gt)
                a, b = inputs.get("a"), inputs.get("b")
                if a is None or b is None:
                    return {"out": False}
                # Auto-coerce to number when both values look numeric
                try:
                    return {"out": op(self._to_num(a), self._to_num(b))}
                except TypeError:
                    return {"out": op(str(a), str(b))}

            case "hysteresis":
                val = inputs.get("value")
                on_thr  = float(d.get("threshold_on",  25.0))
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
                a = self._to_num(inputs.get("a"))
                b = self._to_num(inputs.get("b"))
                result = self._safe_eval(formula, {"a": a, "b": b})
                return {"result": result}

            case "math_map":
                val     = self._to_num(inputs.get("value"))
                in_min  = float(d.get("in_min", 0))
                in_max  = float(d.get("in_max", 100))
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
                return {"_write_value": write_val, "_triggered": inputs.get("trigger")}

            case "python_script":
                script = d.get("script", "result = 0")
                result = self._run_script(script, inputs)
                return {"result": result}

            case "clamp":
                lo  = float(d.get("min", 0))
                hi  = float(d.get("max", 100))
                val = self._to_num(inputs.get("value"))
                return {"result": max(lo, min(hi, val))}

            case "statistics":
                # State stored in hysteresis_state keyed by node.id
                state = self.hysteresis_state.setdefault(node.id, {
                    "s_min": None, "s_max": None, "s_sum": 0.0, "s_count": 0
                })
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
                    "min":   state["s_min"],
                    "max":   state["s_max"],
                    "avg":   round(avg, 6) if avg is not None else None,
                    "count": cnt,
                }

            case "astro_sun":
                try:
                    from astral import LocationInfo             # noqa: PLC0415
                    from astral.sun import sun as _astral_sun  # noqa: PLC0415
                    from zoneinfo import ZoneInfo               # noqa: PLC0415
                    import datetime as _dt                      # noqa: PLC0415
                    lat     = float(d.get("latitude",  47.37))
                    lon     = float(d.get("longitude",  8.54))
                    tz_name = self.app_config.get("timezone", "Europe/Zurich")
                    tz      = ZoneInfo(tz_name)
                    loc     = LocationInfo(latitude=lat, longitude=lon, timezone=tz_name)
                    today   = _dt.datetime.now(tz).date()
                    s       = _astral_sun(loc.observer, date=today, tzinfo=tz)
                    now_dt  = _dt.datetime.now(tz)
                    is_day  = s["sunrise"] <= now_dt <= s["sunset"]
                    return {
                        "sunrise": s["sunrise"].strftime("%H:%M"),
                        "sunset":  s["sunset"].strftime("%H:%M"),
                        "is_day":  is_day,
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
                    "hours":   round(hours, 4),
                    "_active": self._to_bool(inputs.get("active")),
                    "_reset":  self._to_bool(inputs.get("reset")),
                }

            case "notify_pushover" | "notify_sms":
                # Async — fully handled by LogicManager after executor run
                return {
                    "_trigger": inputs.get("trigger"),
                    "_message": inputs.get("message"),
                    "sent":     False,
                }

            case "api_client":
                # Async — fully handled by LogicManager after executor run
                return {
                    "_trigger": inputs.get("trigger"),
                    "_body":    inputs.get("body"),
                    "response": None,
                    "status":   None,
                    "success":  False,
                }

            case "timer_delay" | "timer_pulse" | "timer_cron" | "mcp_tool":
                # Async nodes — handled by manager, not executor
                return {}

            case _:
                logger.debug("Unknown node type: %s", t)
                return {}

    @staticmethod
    def _safe_eval(expr: str, ctx: dict[str, Any]) -> Any:
        """Evaluate a math expression safely.

        Available: all math.* functions + abs, round, min, max + ctx variables.
        """
        allowed = {k: v for k, v in math.__dict__.items() if not k.startswith("_")}
        # Add Python builtins that are safe and useful in formulas
        allowed.update({"abs": abs, "round": round, "min": min, "max": max})
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
            exec(script, {"__builtins__": {"range": range, "len": len, "int": int,  # noqa: S102
                                           "float": float, "str": str, "bool": bool,
                                           "abs": abs, "min": min, "max": max,
                                           "round": round, "math": math}}, local_ns)
            return local_ns.get("result")
        except Exception as exc:
            raise ExecutionError(f"Script error: {exc}") from exc
