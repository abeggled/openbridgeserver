"""Logic Graph Executor.

Topologically sorts the graph and evaluates each node in order.
Returns a dict of node_id → output_values.
"""

from __future__ import annotations

import ast
import copy
import json
import logging
import math
import operator
import re
import sys
from datetime import UTC as _UTC
from datetime import date as _date
from datetime import datetime as _datetime
from datetime import time as _time
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo as _ZoneInfo
from zoneinfo import ZoneInfoNotFoundError

from obs.datetime_format import DEFAULT_CUSTOM_FORMAT, DEFAULT_DATE_FORMAT, DEFAULT_TIME_FORMAT, format_datetime
from obs.logic.graph_analysis import analyze_topology
from obs.logic.models import FlowData, LogicNode

logger = logging.getLogger(__name__)
_AVG_MULTI_MAX_SAMPLES = 100_000


class _OpaqueRecoveredStr(str):
    """String restored from an ``opaque_str`` persistence tag."""

    def __new__(cls, value: str, type_name: str | None = None):
        instance = super().__new__(cls, value)
        instance.type_name = type_name if isinstance(type_name, str) else None
        return instance


def _opaque_recovered_matches(left: Any, right: Any, *, allow_unmarked: bool = False) -> bool:
    if isinstance(right, _OpaqueRecoveredStr):
        # ``opaque_str`` is deliberately a lossy persistence fallback.  Even
        # the fully-qualified runtime type plus str(value) cannot prove that
        # the restored value equals the next live instance: distinct objects
        # of one type may share the same string representation. Treat the
        # first live value after restore as changed and migrate to a lossless
        # in-memory baseline for subsequent executions.
        return False
    elif not (allow_unmarked and isinstance(right, str)):
        return False
    try:
        return str(left) == right
    except Exception:  # noqa: BLE001 - opaque runtime values may define failing string conversion
        return False


class _OpaqueRecoveredSet:
    """Lossless decoded set members awaiting opaque-type recovery."""

    def __init__(self, items: list[Any], *, frozen: bool):
        self.items = items
        self.frozen = frozen


class _OpaqueRecoveredDict:
    """Lossless decoded mapping entries awaiting opaque-key recovery."""

    def __init__(self, items: list[tuple[Any, Any]]):
        self.items = items


def _is_nan(value: Any) -> bool:
    try:
        if isinstance(value, float):
            return bool(math.isnan(value))
        if isinstance(value, Decimal):
            return bool(value.is_nan())
    except Exception:  # noqa: BLE001 - numeric subclasses may fail classification or truth conversion arbitrarily
        return False
    return False


def _snapshot_debug_value(value: Any) -> Any:
    try:
        return copy.deepcopy(value)
    except Exception:  # noqa: BLE001 - arbitrary runtime values may define failing copy hooks
        try:
            return json.loads(json.dumps(value, default=str))
        except Exception:  # noqa: BLE001 - debug capture must never break graph execution
            return str(value)


def _replay_known_output_value(value: Any) -> Any:
    """Isolate a known_outputs value from in-place mutation by a downstream
    node re-executed during a replay, without changing its type when that
    isn't possible.

    Unlike _snapshot_debug_value (used for pure debug capture, where only a
    human-readable snapshot matters), a known_outputs value can be
    genuinely CONSUMED by a downstream node inside the replayed island —
    degrading its type to a JSON/str fallback there could replace that
    consumer's own valid output with "__error__" instead of merely
    misrepresenting it in a debug log. Falls back to the ORIGINAL reference
    (accepting the narrow mutation risk the deep copy exists to avoid, for
    this one value only) rather than a lossy snapshot.
    """
    try:
        return copy.deepcopy(value)
    except Exception:  # noqa: BLE001 - a value with a failing copy hook must still reach downstream consumers unmutated in type
        return value


_COMPARE_OPS = {
    ">": operator.gt,
    "gt": operator.gt,
    "<": operator.lt,
    "lt": operator.lt,
    "=": operator.eq,
    "==": operator.eq,
    "eq": operator.eq,
    ">=": operator.ge,
    "gte": operator.ge,
    "<=": operator.le,
    "lte": operator.le,
    "!=": operator.ne,
    "ne": operator.ne,
}
_TEXT_COMPARE_OPS = {"text", "text_eq", "equals_text"}
_RANGE_OPS = {"range", "between"}
_CONTAINS_OPS = {"contains"}
_STARTS_WITH_OPS = {"starts_with", "startswith", "begins_with"}
_ENDS_WITH_OPS = {"ends_with", "endswith"}
_REGEX_OPS = {"regex", "regexp"}


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
        input_capture: dict[str, dict[str, dict[str, Any]]] | None = None,
        ical_result_cache: dict[str, Any] | None = None,
        ical_cache_outputs_owned: bool = False,
        retained_boundary_handles: dict[str, set[str]] | None = None,
    ):
        self.flow = flow
        # NOTE: use `is not None` instead of `or {}` — an empty dict {} is falsy,
        # so `hysteresis_state or {}` would silently create a *new* dict instead of
        # using the passed-in reference, breaking state persistence between runs.
        self.hysteresis_state = hysteresis_state if hysteresis_state is not None else {}
        self.app_config = app_config or {}
        self.input_capture = input_capture
        # Parsed/filtered calendar results are runtime-only and intentionally
        # separate from hysteresis state, which LogicManager deep-copies for
        # async replay passes.
        self.ical_result_cache = ical_result_cache if ical_result_cache is not None else {}
        self.ical_cache_outputs_owned = ical_cache_outputs_owned
        self.retained_boundary_handles = retained_boundary_handles or {}

    def execute(
        self,
        input_overrides: dict[str, dict[str, Any]] | None = None,
        *,
        commit_memory: bool = True,
        capture_incoming_overrides: dict[str, dict[str, Any]] | None = None,
        known_outputs: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Run the graph. Returns output values for every node.

        `known_outputs` lets a caller replay only a subset of the graph: any
        node.id present there is not re-evaluated at all — its provided
        output is used as-is and downstream nodes read it like any other via
        the normal edge map. This is for replaying a specific node/descendant
        island with a fresh hyst snapshot (e.g. to correct a held
        change_filter) without re-invoking nodes outside that island a
        second time — a non-deterministic producer (random_value) or a
        real-world side effect (api_client, host_check, …) elsewhere in the
        graph must not run twice just because the replay re-executes the
        whole topological order.
        """
        input_overrides = input_overrides or {}
        capture_incoming_overrides = capture_incoming_overrides or {}
        known_outputs = known_outputs or {}

        # Build adjacency: edge target_node.handle ← source_node.handle value
        # edge_map[target_node_id][target_handle] = (source_node_id, source_handle)
        edge_map = self._build_edge_map()
        # memory: "out" is last tick's committed value, so its absence is a
        # defined boundary rather than a failed producer. edge_detect: "out"
        # is deliberately withheld on every tick without an edge ("send
        # nothing", not "send the same value again"), so a downstream node
        # must treat it as a normal missing input instead of reporting the
        # producer as broken on every single run.
        retained_boundary_handles = {node.id: {"out"} for node in self.flow.nodes if node.type in ("memory", "edge_detect")}
        for node_id, handles in self.retained_boundary_handles.items():
            retained_boundary_handles.setdefault(node_id, set()).update(handles)
        # Only failed-output paths that can influence a Change Filter need to
        # retain absence through synchronous nodes. Elsewhere, the executor's
        # longstanding missing-input/default behavior remains intentional.
        change_filter_ancestors = {node.id for node in self.flow.nodes if node.type == "change_filter"}
        pending_ancestors = list(change_filter_ancestors)
        while pending_ancestors:
            target_id = pending_ancestors.pop()
            for source_id, _source_handle in edge_map.get(target_id, {}).values():
                if source_id not in change_filter_ancestors:
                    change_filter_ancestors.add(source_id)
                    pending_ancestors.append(source_id)

        # Topological sort (Kahn's algorithm). Nodes left behind by the sort
        # are not executable in this single-pass DAG executor, so surface them
        # explicitly instead of silently dropping them from the result.
        topo = self._topo_sort()

        # Evaluate
        outputs: dict[str, dict[str, Any]] = {}

        for node in topo.order:
            if node.id in known_outputs:
                # Deep copy: this replay's own nodes may still read a
                # known_outputs entry as one of their inputs (e.g. a
                # python_script downstream of the held island consuming a
                # dict/list value from outside it) and — python_script is
                # explicitly allowed to mutate its inputs in place — mutate
                # it. Handing out the caller's own object here would let
                # that mutation corrupt the original pass's outputs entry
                # for the *same* node, which the caller (and any later
                # correction pass reusing that same outputs dict) still
                # relies on being untouched. Snapshotting per output port
                # rather than the whole dict in one deepcopy call means a
                # single non-deepcopyable value on a completely unrelated
                # node — e.g. a permitted python_script legitimately
                # returning a generator — degrades only that one port
                # instead of raising TypeError and aborting this entire
                # replay before the executor's own per-node exception
                # boundary ever runs. Uses _replay_known_output_value, NOT
                # _snapshot_debug_value: a value here may be genuinely
                # CONSUMED by a downstream node inside the replayed island,
                # so a lossy JSON/str fallback (fine for pure debug capture)
                # would silently replace that consumer's valid output with
                # "__error__" instead of merely misrepresenting it in a log.
                outputs[node.id] = {port: _replay_known_output_value(val) for port, val in known_outputs[node.id].items()}
                continue

            # Resolve inputs for this node
            inputs: dict[str, Any] = {}
            missing_inputs: list[tuple[str, str, str]] = []
            for handle, (src_id, src_handle) in edge_map.get(node.id, {}).items():
                src_out = outputs.get(src_id, {})
                has_value, value = self._try_get_output_value(src_out, src_handle)
                if has_value:
                    inputs[handle] = value
                else:
                    missing_inputs.append((handle, src_id, src_handle))

            incoming_inputs = inputs.copy()
            incoming_inputs.update(capture_incoming_overrides.get(node.id, {}))
            node_overrides = input_overrides.get(node.id, {})
            inputs.update(node_overrides)

            try:
                inputs = self._resolve_effective_inputs(node, inputs)
                # A connected port that its producer did not emit is not the
                # same thing as an unconnected/defaulted input. In particular,
                # evaluating a synchronous node with its default here can turn
                # an upstream failure into a plausible synthetic value (for
                # example, failed_script -> NOT becomes True) which a later
                # Change Filter would then commit. Keep the output absent so
                # the missing state propagates through every intermediate hop.
                unresolved = [
                    item for item in missing_inputs if item[0] not in node_overrides and item[2] not in retained_boundary_handles.get(item[1], set())
                ]
                if node.type == "hysteresis":
                    # An absent value deliberately means "emit retained
                    # state" for Hysteresis, making it a taint boundary just
                    # like Memory. Do not turn that defined fallback into an
                    # error merely because a downstream Change Filter exists.
                    unresolved = [item for item in unresolved if item[0] != "value"]
                if node.type == "edge_detect":
                    # Same reasoning as Hysteresis above, for "in": an absent
                    # input is a *defined* outcome for Edge Detect — it
                    # deliberately neither seeds nor advances its level and
                    # emits no edge, so nothing synthetic can reach a
                    # downstream Change Filter. Without this the node would
                    # become an error block on every run, and its own "reset"
                    # would stop being handled at all. Other handles still
                    # propagate: an unresolved "reset" would be read as
                    # "do not reset", which is a synthesized value.
                    unresolved = [item for item in unresolved if item[0] != "in"]
                absorbed = False
                if unresolved and node.type == "gate" and all(item[0] == "in" for item in unresolved):
                    enable = self._to_bool(inputs.get("enable"))
                    if node.data.get("negate_enable"):
                        enable = not enable
                    absorbed = not enable
                elif unresolved and node.type in {"and", "or"}:
                    resolved_values: list[bool] = []
                    count = max(2, min(30, int(node.data.get("input_count", 2))))
                    unresolved_handles = {item[0] for item in unresolved}
                    for index in range(1, count + 1):
                        port = f"in{index}"
                        if port in unresolved_handles:
                            continue
                        value = self._to_bool(inputs.get(port))
                        if node.data.get(f"negate_{port}"):
                            value = not value
                        resolved_values.append(value)
                    absorbed = any(resolved_values) if node.type == "or" else any(not value for value in resolved_values)
                if unresolved and not absorbed and node.id in change_filter_ancestors and node.type not in {"change_filter", "memory"}:
                    details = ", ".join(f"{handle} <- {src_id}.{src_handle}" for handle, src_id, src_handle in unresolved)
                    raise ExecutionError(f"Missing upstream output: {details}")

                if self.input_capture is not None:
                    self.input_capture[node.id] = {
                        port: {
                            "incoming": _snapshot_debug_value(incoming_inputs.get(port)),
                            "effective": _snapshot_debug_value(inputs.get(port)),
                            "overridden": port in node_overrides,
                        }
                        for port in inputs
                    }

                # Python scripts are the only node type that can arbitrarily
                # mutate their inputs.  Keep upstream outputs (including shared
                # iCalendar cache entries) immutable across replay passes.
                # Per-value (not a single dict-wide deepcopy): an upstream
                # non-deepcopyable value (e.g. another permitted
                # python_script's own generator/complex-object result)
                # would otherwise make the WHOLE deepcopy raise, turning
                # this node into "__error__" even though only one of its
                # inputs is actually a problem — _replay_known_output_value
                # isolates each input independently, falling back to the
                # original reference only for that one value.
                if node.type == "python_script":
                    inputs = {port: _replay_known_output_value(val) for port, val in inputs.items()}
                result = self._eval_node(node, inputs)
            except Exception as exc:
                logger.exception("Node %s (%s) error", node.id, node.type)
                result = {"__error__": str(exc)}

            outputs[node.id] = result

        if topo.skipped_node_ids:
            ordered_cyclic = [n.id for n in self.flow.nodes if n.id in topo.cyclic_node_ids]
            ordered_blocked = [n.id for n in self.flow.nodes if n.id in topo.blocked_node_ids]
            logger.warning(
                "Logic graph contains cycle(s); cyclic nodes=%s, blocked nodes=%s",
                ordered_cyclic,
                ordered_blocked,
            )
            cycle_summary = ", ".join(ordered_cyclic[:5])
            if len(ordered_cyclic) > 5:
                cycle_summary = f"{cycle_summary}, ..."
            for node in self.flow.nodes:
                if node.id in topo.cyclic_node_ids:
                    outputs[node.id] = {
                        "__error__": f"Graph cycle detected; node was not executed. Cycle nodes: {cycle_summary}",
                        "__diagnostic__": "graph_cycle",
                        "__cycle_nodes__": ordered_cyclic,
                    }
                elif node.id in topo.blocked_node_ids:
                    outputs[node.id] = {
                        "__error__": f"Graph cycle detected upstream; node was not executed. Cycle nodes: {cycle_summary}",
                        "__diagnostic__": "graph_cycle_blocked",
                        "__cycle_nodes__": ordered_cyclic,
                    }

        if commit_memory:
            self._commit_memory_inputs(outputs, input_overrides, edge_map)
        return outputs

    @staticmethod
    def _resolve_effective_inputs(node: LogicNode, inputs: dict[str, Any]) -> dict[str, Any]:
        """Include configured input fallbacks in the values used and captured."""
        effective = inputs.copy()
        data = node.data

        if node.type == "compare" and "in2" not in effective:
            operand = data.get("operand")
            if not (isinstance(operand, str) and operand.strip() == ""):
                effective["in2"] = operand
        elif node.type == "string_concat":
            count = max(2, min(20, int(data.get("count", 2))))
            for index in range(1, count + 1):
                port = f"in_{index}"
                if effective.get(port) is None:
                    static = data.get(f"text_{index}")
                    effective[port] = static if static is not None else ""

        return effective

    # ── Topological Sort ──────────────────────────────────────────────────

    def _topo_sort(self):
        return analyze_topology(self.flow)

    def _build_edge_map(self) -> dict[str, dict[str, tuple[str, str]]]:
        edge_map: dict[str, dict[str, tuple[str, str]]] = {}
        for edge in self.flow.edges:
            src_handle = edge.sourceHandle or "out"
            tgt_handle = edge.targetHandle or "in"
            edge_map.setdefault(edge.target, {})[tgt_handle] = (edge.source, src_handle)
        return edge_map

    def commit_memory_inputs(
        self,
        outputs: dict[str, dict[str, Any]],
        input_overrides: dict[str, dict[str, Any]] | None = None,
        blocked_inputs: set[tuple[str, str]] | None = None,
    ) -> None:
        self._commit_memory_inputs(outputs, input_overrides or {}, self._build_edge_map(), blocked_inputs)

    def _commit_memory_inputs(
        self,
        outputs: dict[str, dict[str, Any]],
        input_overrides: dict[str, dict[str, Any]],
        edge_map: dict[str, dict[str, tuple[str, str]]],
        blocked_inputs: set[tuple[str, str]] | None = None,
    ) -> None:
        blocked_inputs = blocked_inputs or set()
        for node in self.flow.nodes:
            if node.type != "memory":
                continue
            node_overrides = input_overrides.get(node.id, {})
            has_reset, reset_value = (
                (False, None) if (node.id, "reset") in blocked_inputs else self._memory_input_value(node, "reset", outputs, node_overrides, edge_map)
            )
            if has_reset and self._to_bool(reset_value):
                self._set_memory_value(node, self._memory_initial_value(node))
                continue
            has_input, input_value = (
                (False, None) if (node.id, "in") in blocked_inputs else self._memory_input_value(node, "in", outputs, node_overrides, edge_map)
            )
            if has_input:
                self._set_memory_value(node, self._coerce_typed_value(node, input_value))
                continue

    def _memory_input_value(
        self,
        node: LogicNode,
        handle: str,
        outputs: dict[str, dict[str, Any]],
        node_overrides: dict[str, Any],
        edge_map: dict[str, dict[str, tuple[str, str]]],
    ) -> tuple[bool, Any]:
        if handle in node_overrides:
            return True, node_overrides[handle]
        src = edge_map.get(node.id, {}).get(handle)
        if src is None:
            return False, None
        src_id, src_handle = src
        src_outputs = outputs.get(src_id)
        if not isinstance(src_outputs, dict) or "__diagnostic__" in src_outputs:
            return False, None
        return self._try_get_output_value(src_outputs, src_handle)

    # ── Type coercion helpers ─────────────────────────────────────────────

    @staticmethod
    def _get_output_value(outputs: dict[str, Any], handle: str) -> Any:
        """Read an output handle with compatibility for older result/out flows."""
        if handle in outputs:
            return outputs.get(handle)
        if handle == "result" and "out" in outputs:
            return outputs.get("out")
        if handle == "out" and "result" in outputs:
            return outputs.get("result")
        return None

    @classmethod
    def _try_get_output_value(cls, outputs: dict[str, Any], handle: str) -> tuple[bool, Any]:
        if handle in outputs:
            return True, outputs.get(handle)
        if handle == "result" and "out" in outputs:
            return True, outputs.get("out")
        if handle == "out" and "result" in outputs:
            return True, outputs.get("result")
        return False, None

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
    def _try_num(v: Any) -> float | None:
        """Return a float only when the original value is numeric-like."""
        if isinstance(v, bool):
            return 1.0 if v else 0.0
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _try_bool_literal(v: Any) -> bool | None:
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            try:
                s = v.strip().lower()
            except Exception:  # noqa: BLE001 - string subclasses may fail normalization arbitrarily
                return None
            if s in {"true", "1", "yes", "on"}:
                return True
            if s in {"false", "0", "no", "off"}:
                return False
            try:
                numeric = Decimal(s)
            except Exception:  # noqa: BLE001 - normalized string subclasses may fail Decimal conversion arbitrarily
                return None
            v = numeric
        # Numeric 0/1 are recognized too, so equality stays transitive across
        # adapter representations: 1 == "1" == "true" must all agree.
        if isinstance(v, Decimal):
            try:
                if not bool(v.is_finite()):
                    return None
                equals_zero = v == 0
                equals_one = v == 1
            except Exception:  # noqa: BLE001 - Decimal subclasses may fail classification or equality arbitrarily
                return None
            if type(equals_zero) is not bool or type(equals_one) is not bool:
                return None
            if equals_zero:
                return False
            if equals_one:
                return True
            return None
        if isinstance(v, (int, float)):
            try:
                equals_zero = v == 0
                equals_one = v == 1
            except Exception:  # noqa: BLE001 - numeric subclasses may implement hostile equality
                return None
            if type(equals_zero) is not bool or type(equals_one) is not bool:
                return None
            if equals_zero:
                return False
            if equals_one:
                return True
        return None

    @staticmethod
    def _try_exact_int(v: Any) -> int | None:
        """Return an int only when it can be represented without a float round-trip."""
        if isinstance(v, bool):
            return None
        if isinstance(v, int):
            try:
                return int(v)
            except Exception:  # noqa: BLE001 - user-provided int subclasses may fail conversion arbitrarily
                return None
        if isinstance(v, float):
            # A float that already holds a whole number carries no more
            # precision than its int() conversion — unlike converting the
            # *other* operand (an arbitrary-precision int) through float(),
            # which is exactly the precision loss this method exists to avoid.
            try:
                return int(v) if v.is_integer() else None
            except Exception:  # noqa: BLE001 - user-provided float subclasses may fail conversion arbitrarily
                return None
        if isinstance(v, str):
            try:
                s = v.strip()
            except Exception:  # noqa: BLE001 - string subclasses may fail normalization arbitrarily
                return None
            try:
                return int(s)
            except ValueError:
                pass
            # Decimal-form ("9007199254740993.0") or scientific-notation
            # strings aren't accepted by int() directly but may still be an
            # exact whole number — Decimal parses the literal digits without
            # any binary-float rounding, unlike float().
            try:
                dec = Decimal(s)
                if dec != dec.to_integral_value():
                    return None
                # A compact literal like "1e10000000000" parses instantly
                # (Decimal stores it as a coefficient+exponent pair, not the
                # expanded digit string) but int() on it would materialize
                # an actual multi-gigabyte integer — unlike plain digit
                # strings, which int(s) above already rejects past Python's
                # own conversion-length guard, this scientific-notation path
                # never touches that guard until it's too late. adjusted()
                # gives the result's digit count via the stored exponent,
                # without expanding it, so the bound check itself stays cheap.
                if dec != 0 and dec.adjusted() >= sys.get_int_max_str_digits():
                    return None
                return int(dec)
            except (InvalidOperation, OverflowError):
                # OverflowError: int() rejects a finite-looking but infinite
                # Decimal ("Infinity"/"-Infinity" are valid Decimal literals
                # and compare equal to their own to_integral_value(), so they
                # reach int() rather than being caught by the check above).
                return None
        return None

    @staticmethod
    def _try_decimal(v: Any) -> Decimal | None:
        """Return an exact Decimal only when the value is numeric-like.

        Unlike `_try_num`'s float() conversion, this preserves full
        precision for high-precision decimal strings (e.g. an adapter
        supplying "0.123456789012345678901") — float() would silently round
        both that value and a distinct neighbour like
        "0.123456789012345678902" to the same nearest binary value, making
        two genuinely different readings compare as equal.
        """
        if isinstance(v, bool):
            return Decimal(1) if v else Decimal(0)
        if isinstance(v, Decimal):
            dec = v
        elif isinstance(v, int):
            try:
                dec = Decimal(v)
            except Exception:  # noqa: BLE001 - integer subclasses may fail exact Decimal conversion arbitrarily
                return None
        elif isinstance(v, float):
            # A plain float always produces a valid Decimal literal, but a
            # float subclass may override __str__ with arbitrary behavior.
            # Let comparison fall back to exception-safe equality when that
            # conversion is unavailable.
            try:
                dec = Decimal(str(v))
            except Exception:  # noqa: BLE001 - user-provided float subclasses may fail conversion arbitrarily
                return None
        elif isinstance(v, str):
            try:
                dec = Decimal(v.strip())
            except Exception:  # noqa: BLE001 - string subclasses may fail normalization or Decimal conversion arbitrarily
                return None
        else:
            return None
        try:
            return dec if bool(dec.is_finite()) else None
        except Exception:  # noqa: BLE001 - Decimal subclasses may fail classification or truth conversion arbitrarily
            return None

    @classmethod
    def _compare_values(
        cls, left: Any, right: Any, *, right_is_recovered_str: bool = False, right_is_opaque_recovered_str: bool = False
    ) -> tuple[bool, bool]:
        """Return (are_equal, via_normalizing_path).

        via_normalizing_path is True when equality was decided by a
        normalizing equivalence — boolean/int/decimal aliasing, or
        structural dict/list `==` — and False when it fell through to one of
        the str()-recovery paths below. Those paths can equate a live value
        with a persisted, JSON-lossy string left over from `default=str`
        (e.g. a datetime.time vs. "10:30:00"), so a caller that wants to
        keep emitting one side's own representation on an "equal" result
        needs to know whether doing so is safe.

        `right_is_recovered_str` must be set only when `right` (always the
        previously-held/persisted side at the change_filter call site, per
        that node's own state layout) is known to have come from a DB
        restore rather than a live value received this session — otherwise
        a source that legitimately emits the literal string "10:30:00" and
        later emits datetime.time(10, 30) would have its genuine type
        transition swallowed by the recovery path below.

        `right_is_opaque_recovered_str` is the analogous flag for a value
        _persist_default had to fall back to str() for because it recognized
        no more specific type (e.g. a python_script's complex-number/custom-
        object result) — LogicManager._load_graphs sets it from that state's
        own "opaque_str" persistence tag. Unlike `right_is_recovered_str`
        (deliberately restricted to date/time `left` types, since a LEGACY
        pre-tag row could have flattened literally anything, including a
        list/dict that might coincidentally str()-match), this tag is
        unambiguous by construction: every JSON-native and specifically-
        recognized type (list/dict/tuple/set/frozenset/bytes/date/time/
        datetime) is tagged on its own, so an "opaque_str"-tagged value can
        never have originally been one of those — a broader match here
        carries none of that collision risk.
        """
        # IEEE NaN deliberately compares unequal to every value, including
        # itself.  For change detection, however, two repeated invalid sensor
        # readings are the same retained reading and must not emit a fresh
        # pulse on every graph execution.
        if _is_nan(left) and _is_nan(right):
            return True, True
        if _is_nan(left) or _is_nan(right):
            return False, True
        if isinstance(left, _datetime) and isinstance(right, _datetime):
            try:
                left_aware = left.tzinfo is not None and left.utcoffset() is not None
                right_aware = right.tzinfo is not None and right.utcoffset() is not None
                left_fold = left.fold
                right_fold = right.fold
            except Exception:  # noqa: BLE001 - arbitrary tzinfo implementations may fail awareness probing
                try:
                    return bool(left == right), True
                except Exception:  # noqa: BLE001 - the same tzinfo may also fail datetime equality
                    return False, True
            if left_aware and right_aware:
                try:
                    return bool(left.astimezone(_UTC) == right.astimezone(_UTC)), True
                except Exception:  # noqa: BLE001 - arbitrary tzinfo normalization may fail
                    try:
                        return bool(left == right), True
                    except Exception:  # noqa: BLE001 - arbitrary tzinfo implementations may fail equality
                        return False, True
            if not left_aware and not right_aware and left_fold != right_fold:
                return False, True
        if isinstance(left, _time) and isinstance(right, _time):
            try:
                left_fold = left.fold
                right_fold = right.fold
            except Exception:  # noqa: BLE001 - arbitrary time subclasses may fail fold classification
                try:
                    return bool(left == right), True
                except Exception:  # noqa: BLE001 - the same time subclass may also fail equality
                    return False, True
            try:
                if isinstance(left.tzinfo, _ZoneInfo) and isinstance(right.tzinfo, _ZoneInfo):
                    same_tz = left.tzinfo.key == right.tzinfo.key
                else:
                    same_tz = bool(left.tzinfo == right.tzinfo)
            except Exception:  # noqa: BLE001 - arbitrary tzinfo equality may raise or be ambiguous
                same_tz = False
            if left_fold != right_fold or not same_tz:
                return False, True
        bool_left, bool_right = cls._try_bool_literal(left), cls._try_bool_literal(right)
        if bool_left is not None and bool_right is not None:
            return bool_left == bool_right, True
        # Compare integral values exactly first — round-tripping through
        # float() loses precision beyond 2**53 (e.g. 64-bit counters/IDs).
        int_left, int_right = cls._try_exact_int(left), cls._try_exact_int(right)
        if int_left is not None and int_right is not None:
            return int_left == int_right, True
        dec_left, dec_right = cls._try_decimal(left), cls._try_decimal(right)
        if dec_left is not None and dec_right is not None:
            try:
                return bool(dec_left == dec_right), True
            except Exception:  # noqa: BLE001 - Decimal subclasses may implement unsafe equality
                dec_left = dec_right = None
        container_types = (dict, list, tuple, set, frozenset, _OpaqueRecoveredSet, _OpaqueRecoveredDict)
        if isinstance(left, container_types) and isinstance(right, container_types):
            if right_is_opaque_recovered_str:
                # An opaque_str tag can be nested arbitrarily deep inside a
                # persisted container (e.g. a python_script's [3 + 4j]
                # baseline persists as a list holding one opaque-tagged
                # item, decoded to ['(3+4j)']) — plain structural equality
                # would never match the live [3+4j] against that lossy
                # stand-in. _opaque_aware_container_equal applies the same
                # unambiguous str()-fallback recursively at every leaf,
                # exactly like the scalar case below does for a top-level
                # opaque value.
                # Restored v2 state carries a marker subclass on each exact
                # opaque leaf. Older in-memory/tests may only have the
                # historical container-wide flag, so retain its broad
                # behavior only when no precise leaf marker is available.
                precise = cls._contains_opaque_recovered_leaf(right)
                return cls._opaque_aware_container_equal(left, right, allow_unmarked=not precise), False
            return cls._nan_aware_equal(left, right), True
        # A persisted datetime.date/time/datetime (e.g. a KNX DPT10/11 value)
        # survives a restart only as its str() form (`default=str` in
        # _persist_node_state) — recognize *that specific* recovery, and
        # only when `right` is actually flagged as such a restored value. A
        # blanket str(left) == str(right) fallback would also equate a
        # list/dict with a string that happens to match its repr (e.g.
        # [1, 2] and "[1, 2]"), which are genuinely different values/types,
        # not a persistence artifact, so it must not be treated as
        # "unchanged".
        if right_is_recovered_str and isinstance(right, str) and isinstance(left, (_date, _time)):
            try:
                return str(left) == right, False
            except Exception:  # noqa: BLE001 - date/time subclasses may fail legacy string recovery arbitrarily
                right_is_recovered_str = False
        # See the docstring above: an "opaque_str" tag unambiguously means
        # `right` is a lossy str() of some type _persist_default didn't
        # otherwise recognize, so any `left` type may safely be compared via
        # str() here — except dict/list, excluded defensively for the same
        # coincidental-repr reason as above, even though a dict/list could
        # never actually be the type this tag was generated from.
        if right_is_opaque_recovered_str and isinstance(right, str) and not isinstance(left, container_types):
            return _opaque_recovered_matches(left, right), False
        try:
            return bool(left == right), True
        except Exception:  # noqa: BLE001 - arbitrary runtime values may define failing/non-scalar equality
            return False, True

    @classmethod
    def _contains_opaque_recovered_leaf(cls, value: Any) -> bool:
        pending = [value]
        seen: set[int] = set()
        while pending:
            current = pending.pop()
            if isinstance(current, _OpaqueRecoveredStr):
                return True
            if isinstance(current, (dict, list, tuple, set, frozenset, _OpaqueRecoveredSet, _OpaqueRecoveredDict)):
                if id(current) in seen:
                    continue
                seen.add(id(current))
            if isinstance(current, dict):
                for key, item in current.items():
                    pending.extend((key, item))
            elif isinstance(current, (list, tuple, set, frozenset)):
                pending.extend(current)
            elif isinstance(current, _OpaqueRecoveredSet):
                pending.extend(current.items)
            elif isinstance(current, _OpaqueRecoveredDict):
                for key, item in current.items:
                    pending.extend((key, item))
        return False

    @classmethod
    def _nan_aware_equal(cls, left: Any, right: Any, seen: set[tuple[int, int]] | None = None) -> bool:
        """Structural equality for leaves with non-standard equality.

        Besides treating matching NaNs as one retained reading, compare aware
        datetimes by UTC instant. Container ``==`` cannot be used before these
        leaf checks: signaling Decimal NaNs may raise from it, and datetimes
        using the same ZoneInfo can compare opposite DST folds as equal.
        """
        if _is_nan(left) and _is_nan(right):
            return True
        if _is_nan(left) or _is_nan(right):
            return False
        if isinstance(left, _datetime) and isinstance(right, _datetime):
            try:
                left_aware = left.tzinfo is not None and left.utcoffset() is not None
                right_aware = right.tzinfo is not None and right.utcoffset() is not None
                left_fold = left.fold
                right_fold = right.fold
            except Exception:  # noqa: BLE001 - arbitrary tzinfo implementations may fail awareness probing
                try:
                    return bool(left == right)
                except Exception:  # noqa: BLE001 - the same tzinfo may also fail datetime equality
                    return False
            if left_aware and right_aware:
                try:
                    return bool(left.astimezone(_UTC) == right.astimezone(_UTC))
                except Exception:  # noqa: BLE001 - arbitrary tzinfo normalization may fail
                    try:
                        return bool(left == right)
                    except Exception:  # noqa: BLE001 - arbitrary tzinfo implementations may fail equality
                        return False
            if not left_aware and not right_aware and left_fold != right_fold:
                return False
        if isinstance(left, _time) and isinstance(right, _time):
            try:
                left_fold = left.fold
                right_fold = right.fold
            except Exception:  # noqa: BLE001 - arbitrary time subclasses may fail fold classification
                try:
                    return bool(left == right)
                except Exception:  # noqa: BLE001 - the same time subclass may also fail equality
                    return False
            try:
                if isinstance(left.tzinfo, _ZoneInfo) and isinstance(right.tzinfo, _ZoneInfo):
                    same_tz = left.tzinfo.key == right.tzinfo.key
                else:
                    same_tz = bool(left.tzinfo == right.tzinfo)
            except Exception:  # noqa: BLE001 - arbitrary tzinfo equality may raise or be ambiguous
                same_tz = False
            if left_fold != right_fold or not same_tz:
                return False
        container_types = (dict, list, tuple, set, frozenset)
        if isinstance(left, container_types) and isinstance(right, container_types):
            seen = set() if seen is None else seen
            pair = (id(left), id(right))
            if pair in seen:
                return True
            seen.add(pair)
        # Keep ordinary JSON/container comparisons linear. Recursive
        # matching is only needed when a leaf has deliberately unusual
        # equality (NaN or an aware datetime whose DST fold Python's
        # same-tzinfo comparison can hide).
        if (
            isinstance(left, container_types)
            and isinstance(right, container_types)
            and not cls._contains_nonstandard_equality_leaf(left)
            and not cls._contains_nonstandard_equality_leaf(right)
        ):
            try:
                return bool(left == right)
            except RecursionError:
                return cls._plain_container_equal_iterative(left, right)
            except Exception:  # noqa: BLE001 - arbitrary runtime values may define failing equality
                return False
        if isinstance(left, dict) and isinstance(right, dict):
            return cls._nonstandard_container_equal_iterative(left, right)
        if isinstance(left, (list, tuple)) and isinstance(right, type(left)):
            try:
                return len(left) == len(right) and all(cls._nan_aware_equal(le, re, seen) for le, re in zip(left, right))
            except RecursionError:
                return cls._nonstandard_container_equal_iterative(left, right)
        if isinstance(left, (set, frozenset)) and isinstance(right, type(left)):
            if len(left) != len(right):
                return False
            remaining = list(right)
            for left_item in left:
                match = None
                matched_seen = None
                for index, right_item in enumerate(remaining):
                    candidate_seen = set(seen)
                    if cls._nan_aware_equal(left_item, right_item, candidate_seen):
                        match = index
                        matched_seen = candidate_seen
                        break
                if match is None:
                    return False
                if matched_seen is not None:
                    seen.update(matched_seen)
                remaining.pop(match)
            return True
        try:
            return bool(left == right)
        except Exception:  # noqa: BLE001 - arbitrary runtime values may define failing equality
            return False

    @classmethod
    def _contains_nonstandard_equality_leaf(cls, value: Any, seen: set[int] | None = None) -> bool:
        visited = set() if seen is None else seen
        pending = [value]
        while pending:
            current = pending.pop()
            if _is_nan(current):
                return True
            if isinstance(current, _datetime):
                try:
                    if (current.tzinfo is not None and current.utcoffset() is not None) or bool(current.fold):
                        return True
                except Exception:  # noqa: BLE001 - force arbitrary failing tzinfo through the guarded recursive path
                    return True
                continue
            if isinstance(current, _time):
                try:
                    if current.tzinfo is not None or bool(current.fold):
                        return True
                except Exception:  # noqa: BLE001 - force arbitrary failing time subclasses through guarded comparison
                    return True
                continue
            if isinstance(current, (dict, list, tuple, set, frozenset)):
                if id(current) in visited:
                    continue
                visited.add(id(current))
                if isinstance(current, dict):
                    try:
                        for key, item in current.items():
                            pending.extend((key, item))
                    except Exception:  # noqa: BLE001 - custom mappings may fail traversal arbitrarily
                        return True
                else:
                    try:
                        pending.extend(current)
                    except Exception:  # noqa: BLE001 - custom containers may fail traversal arbitrarily
                        return True
        return False

    @classmethod
    def _plain_container_equal_iterative(cls, left: Any, right: Any) -> bool:
        """Compare deeply nested ordinary containers without Python recursion."""
        pending = [(left, right)]
        seen: set[tuple[int, int]] = set()
        while pending:
            current_left, current_right = pending.pop()
            if type(current_left) is not type(current_right):
                return False
            if isinstance(current_left, (list, tuple)):
                if len(current_left) != len(current_right):
                    return False
                pair = (id(current_left), id(current_right))
                if pair in seen:
                    continue
                seen.add(pair)
                pending.extend(zip(current_left, current_right))
                continue
            if isinstance(current_left, dict):
                if len(current_left) != len(current_right):
                    return False
                pair = (id(current_left), id(current_right))
                if pair in seen:
                    continue
                seen.add(pair)
                try:
                    for key, item in current_left.items():
                        if key not in current_right:
                            return False
                        pending.append((item, current_right[key]))
                except Exception:  # noqa: BLE001 - arbitrary mapping keys may define unsafe equality
                    return False
                continue
            if isinstance(current_left, (set, frozenset)):
                try:
                    if not bool(current_left == current_right):
                        return False
                except Exception:  # noqa: BLE001 - arbitrary set members may define unsafe equality
                    return False
                continue
            try:
                if not bool(current_left == current_right):
                    return False
            except Exception:  # noqa: BLE001 - arbitrary leaves may define unsafe equality
                return False
        return True

    @classmethod
    def _nonstandard_container_equal_iterative(
        cls,
        left: Any,
        right: Any,
        seen_pairs: set[tuple[int, int]] | None = None,
    ) -> bool:
        """Compare deeply nested containers while preserving NaN semantics."""
        states: list[tuple[list[tuple[str, Any, Any]], set[tuple[int, int]]]] = [
            ([("pair", left, right)], set() if seen_pairs is None else set(seen_pairs))
        ]
        while states:
            work, seen = states.pop()
            while work:
                kind, current_left, current_right = work.pop()
                if kind == "dict":
                    left_items = current_left
                    right_items = current_right
                    if not left_items:
                        continue
                    left_key, left_value = left_items[0]
                    candidates = [
                        index
                        for index, (right_key, _right_value) in enumerate(right_items)
                        if cls._nonstandard_container_equal_iterative(left_key, right_key, seen)
                    ]
                    if not candidates:
                        break
                    if len(candidates) == 1:
                        index = candidates[0]
                        work.append(("dict", left_items[1:], right_items[:index] + right_items[index + 1 :]))
                        work.append(("pair", left_value, right_items[index][1]))
                        continue
                    if not cls._ambiguous_dictionary_entries_equal(left_items, right_items, seen):
                        break
                    continue
                if kind == "set":
                    left_items = current_left
                    right_items = current_right
                    if not left_items:
                        continue
                    left_item = left_items[0]
                    candidates = [
                        index for index, right_item in enumerate(right_items) if cls._nonstandard_container_equal_iterative(left_item, right_item)
                    ]
                    if not candidates:
                        break
                    if len(candidates) == 1:
                        index = candidates[0]
                        work.append(("set", left_items[1:], right_items[:index] + right_items[index + 1 :]))
                        continue
                    adjacency = [
                        [index for index, right_item in enumerate(right_items) if cls._nonstandard_container_equal_iterative(item, right_item)]
                        for item in left_items
                    ]
                    if not cls._has_perfect_bipartite_matching(adjacency):
                        break
                    continue

                if _is_nan(current_left) or _is_nan(current_right):
                    if not (_is_nan(current_left) and _is_nan(current_right)):
                        break
                    continue
                left_is_container = isinstance(current_left, (list, tuple, dict, set, frozenset))
                right_is_container = isinstance(current_right, (list, tuple, dict, set, frozenset))
                # Container kinds are structural and must match exactly, but
                # scalar leaves retain ordinary Python equality semantics.
                # In particular, 1 == 1.0 at shallow nesting and must remain
                # equal when recursion depth selects this iterative fallback.
                if (left_is_container or right_is_container) and type(current_left) is not type(current_right):
                    break
                if left_is_container:
                    pair = (id(current_left), id(current_right))
                    if pair in seen:
                        continue
                    seen.add(pair)
                if isinstance(current_left, (list, tuple)):
                    if len(current_left) != len(current_right):
                        break
                    work.extend(("pair", left_item, right_item) for left_item, right_item in zip(current_left, current_right))
                    continue
                if isinstance(current_left, dict):
                    try:
                        if len(current_left) != len(current_right):
                            break
                        left_items = list(current_left.items())
                        right_items = list(current_right.items())
                    except Exception:  # noqa: BLE001 - custom mappings may fail sizing or traversal arbitrarily
                        break
                    work.append(("dict", left_items, right_items))
                    continue
                if isinstance(current_left, (set, frozenset)):
                    if len(current_left) != len(current_right):
                        break
                    work.append(("set", list(current_left), list(current_right)))
                    continue
                if not cls._nan_aware_equal(current_left, current_right):
                    break
            else:
                return True
        return False

    @classmethod
    def _ambiguous_dictionary_entries_equal(
        cls,
        left_items: list[tuple[Any, Any]],
        right_items: list[tuple[Any, Any]],
        seen: set[tuple[int, int]],
    ) -> bool:
        """Polynomial bipartite matching for dictionaries with ambiguous keys."""
        adjacency: list[list[int]] = []
        for left_key, left_value in left_items:
            candidates = []
            for index, (right_key, right_value) in enumerate(right_items):
                candidate_seen = set(seen)
                if cls._nonstandard_container_equal_iterative(left_key, right_key, candidate_seen) and cls._nonstandard_container_equal_iterative(
                    left_value, right_value, candidate_seen
                ):
                    candidates.append(index)
            if not candidates:
                return False
            adjacency.append(candidates)

        return cls._has_perfect_bipartite_matching(adjacency)

    @staticmethod
    def _has_perfect_bipartite_matching(adjacency: list[list[int]]) -> bool:
        if any(not candidates for candidates in adjacency):
            return False
        match_left: dict[int, int] = {}
        match_right: dict[int, int] = {}
        for start in range(len(adjacency)):
            queue = [start]
            queue_index = 0
            parents: dict[int, tuple[int, int]] = {}
            seen_left = {start}
            seen_right: set[int] = set()
            endpoint: tuple[int, int] | None = None
            while queue_index < len(queue) and endpoint is None:
                left_index = queue[queue_index]
                queue_index += 1
                for right_index in adjacency[left_index]:
                    if right_index in seen_right:
                        continue
                    seen_right.add(right_index)
                    matched_left = match_right.get(right_index)
                    if matched_left is None:
                        endpoint = (left_index, right_index)
                        break
                    if matched_left not in seen_left:
                        seen_left.add(matched_left)
                        parents[matched_left] = (left_index, right_index)
                        queue.append(matched_left)
            if endpoint is None:
                return False
            left_index, right_index = endpoint
            while True:
                match_left[left_index] = right_index
                match_right[right_index] = left_index
                if left_index == start:
                    break
                left_index, right_index = parents[left_index]
        return True

    @classmethod
    def _ambiguous_opaque_dictionary_entries_equal(
        cls,
        left_items: list[tuple[Any, Any]],
        right_items: list[tuple[Any, Any]],
        seen: set[tuple[int, int]],
        *,
        allow_unmarked: bool,
    ) -> bool:
        """Polynomial matching for recovered dictionaries with ambiguous keys."""
        adjacency: list[list[int]] = []
        for left_key, left_value in left_items:
            candidates = []
            for index, (right_key, right_value) in enumerate(right_items):
                if cls._opaque_aware_container_equal(
                    left_key,
                    right_key,
                    allow_unmarked=allow_unmarked,
                    _seen_pairs=seen,
                ) and cls._opaque_aware_container_equal(
                    left_value,
                    right_value,
                    allow_unmarked=allow_unmarked,
                    _seen_pairs=seen,
                ):
                    candidates.append(index)
            if not candidates:
                return False
            adjacency.append(candidates)
        return cls._has_perfect_bipartite_matching(adjacency)

    @classmethod
    def _opaque_aware_container_equal(
        cls,
        left: Any,
        right: Any,
        *,
        allow_unmarked: bool = False,
        _seen_pairs: set[tuple[int, int]] | None = None,
    ) -> bool:
        """Compare recovered opaque containers without Python call-stack recursion."""
        states: list[tuple[list[tuple[str, Any, Any]], set[tuple[int, int]]]] = [
            ([("pair", left, right)], set() if _seen_pairs is None else set(_seen_pairs))
        ]
        while states:
            work, seen = states.pop()
            while work:
                kind, current_left, current_right = work.pop()
                if kind == "dict":
                    left_items = current_left
                    right_items = current_right
                    if not left_items:
                        continue
                    if not cls._ambiguous_opaque_dictionary_entries_equal(
                        left_items,
                        right_items,
                        seen,
                        allow_unmarked=allow_unmarked,
                    ):
                        break
                    continue
                if kind == "set":
                    left_items = current_left
                    right_items = current_right
                    if not left_items:
                        continue
                    adjacency = [
                        [
                            index
                            for index, right_item in enumerate(right_items)
                            if cls._opaque_aware_container_equal(item, right_item, allow_unmarked=allow_unmarked)
                        ]
                        for item in left_items
                    ]
                    if not cls._has_perfect_bipartite_matching(adjacency):
                        break
                    continue

                if _is_nan(current_left) or _is_nan(current_right):
                    if not (_is_nan(current_left) and _is_nan(current_right)):
                        break
                    continue
                if isinstance(current_right, _OpaqueRecoveredStr):
                    if not _opaque_recovered_matches(current_left, current_right, allow_unmarked=allow_unmarked):
                        break
                    continue
                pair = (id(current_left), id(current_right))
                if isinstance(current_left, (dict, list, tuple, set, frozenset)) and isinstance(
                    current_right, (dict, list, tuple, set, frozenset, _OpaqueRecoveredSet, _OpaqueRecoveredDict)
                ):
                    if pair in seen:
                        continue
                    seen.add(pair)
                if isinstance(current_left, dict) and isinstance(current_right, dict):
                    if len(current_left) != len(current_right):
                        break
                    work.append(("dict", list(current_left.items()), list(current_right.items())))
                    continue
                if isinstance(current_left, dict) and isinstance(current_right, _OpaqueRecoveredDict):
                    if len(current_left) != len(current_right.items):
                        break
                    work.append(("dict", list(current_left.items()), list(current_right.items)))
                    continue
                if isinstance(current_left, (list, tuple)) and isinstance(current_right, type(current_left)):
                    if len(current_left) != len(current_right):
                        break
                    work.extend(("pair", left_item, right_item) for left_item, right_item in zip(current_left, current_right))
                    continue
                if isinstance(current_left, (set, frozenset)) and isinstance(current_right, type(current_left)):
                    if len(current_left) != len(current_right):
                        break
                    work.append(("set", list(current_left), list(current_right)))
                    continue
                if isinstance(current_left, (set, frozenset)) and isinstance(current_right, _OpaqueRecoveredSet):
                    if isinstance(current_left, frozenset) != current_right.frozen or len(current_left) != len(current_right.items):
                        break
                    work.append(("set", list(current_left), list(current_right.items)))
                    continue
                try:
                    equal = bool(current_left == current_right)
                except Exception:  # noqa: BLE001 - opaque runtime equality may fail
                    equal = False
                if not equal and not _opaque_recovered_matches(current_left, current_right, allow_unmarked=allow_unmarked):
                    break
            else:
                return True
        return False

    @classmethod
    def _values_equal(cls, left: Any, right: Any) -> bool:
        """Type-tolerant equality for Decision/Value Mapping rule conditions.

        Their `expected` value is entered through a NodeConfigPanel text
        input and is therefore always a string — e.g. an API/JSON list
        `[1, 2]` must still match a rule configured as the string `"[1, 2]"`.
        Unlike `_compare_values` (change_filter's own persisted-state
        comparison, which needs the stricter behavior to avoid silently
        losing type information across a restart), this keeps the lenient
        str(left) == str(right) fallback for any pair not already handled by
        the normalizing paths below — narrowing it here too would silently
        break existing rules built around that fallback.
        """
        bool_left, bool_right = cls._try_bool_literal(left), cls._try_bool_literal(right)
        if bool_left is not None and bool_right is not None:
            return bool_left == bool_right
        int_left, int_right = cls._try_exact_int(left), cls._try_exact_int(right)
        if int_left is not None and int_right is not None:
            return int_left == int_right
        num_left, num_right = cls._try_num(left), cls._try_num(right)
        if num_left is not None and num_right is not None:
            return num_left == num_right
        if isinstance(left, (dict, list)) and isinstance(right, (dict, list)):
            return left == right
        return str(left) == str(right)

    @staticmethod
    def _to_bool(v: Any) -> bool:
        """Coerce any value to bool. Strings '0'/'false'/'off' → False."""
        if v is None:
            return False
        if isinstance(v, str):
            return v.strip().lower() not in ("0", "false", "no", "off", "")
        return bool(v)

    def _memory_initial_value(self, node: LogicNode) -> Any:
        return self._coerce_typed_value(node, node.data.get("initial_value"))

    def _memory_value(self, node: LogicNode) -> Any:
        state = self.hysteresis_state.get(node.id)
        if isinstance(state, dict) and "value" in state:
            return state["value"]
        if state is not None and not isinstance(state, dict):
            return state
        return self._memory_initial_value(node)

    def _set_memory_value(self, node: LogicNode, value: Any) -> None:
        self.hysteresis_state[node.id] = {"value": value}

    def _coerce_typed_value(self, node: LogicNode, value: Any, default_type: str = "auto") -> Any:
        """Apply a node's ``data_type`` config to a raw configured/stored value.

        Shared by Memory and Edge Detect, which declare the same ``data_type``
        field with the same meaning. ``default_type`` is the node's own schema
        default, used when a graph carries no explicit ``data_type`` (an
        imported or hand-written flow, or one saved before the field existed).
        """
        dtype = node.data.get("data_type", default_type)
        if dtype == "bool":
            return self._to_bool(value)
        if dtype == "number":
            return self._to_num(value)
        if dtype == "string":
            return "" if value is None else str(value)
        return value

    @staticmethod
    def _load_rule_list(value: Any) -> list[dict[str, Any]]:
        if isinstance(value, list):
            return [r for r in value if isinstance(r, dict)]
        if isinstance(value, str):
            try:
                parsed = json.loads(value or "[]")
            except (TypeError, ValueError):
                return []
            if isinstance(parsed, list):
                return [r for r in parsed if isinstance(r, dict)]
        return []

    @staticmethod
    def _condition_value(rule: dict[str, Any], key: str, fallback: Any = None, *, blank_is_missing: bool = False) -> Any:
        value = rule.get(key, fallback)
        if blank_is_missing and isinstance(value, str) and value.strip() == "":
            return fallback
        return value

    @classmethod
    def _condition_matches(cls, input_value: Any, rule: dict[str, Any]) -> bool:
        operator_key = str(rule.get("operator") or rule.get("op") or "eq").strip().lower()
        expected = cls._condition_value(rule, "value")
        if input_value is None:
            return False

        if operator_key in _RANGE_OPS:
            lo = cls._condition_value(rule, "min", expected, blank_is_missing=True)
            hi = cls._condition_value(rule, "max", cls._condition_value(rule, "value_to", blank_is_missing=True), blank_is_missing=True)
            num_value, num_lo, num_hi = cls._try_num(input_value), cls._try_num(lo), cls._try_num(hi)
            if num_value is None or num_lo is None or num_hi is None:
                return False
            lower, upper = sorted((num_lo, num_hi))
            return lower <= num_value <= upper

        if operator_key in _REGEX_OPS:
            pattern = str(expected or "")
            if not pattern:
                return False
            flags = 0 if rule.get("case_sensitive") else re.IGNORECASE
            try:
                return re.search(pattern, str(input_value), flags=flags) is not None
            except re.error:
                return False

        if operator_key in _CONTAINS_OPS | _STARTS_WITH_OPS | _ENDS_WITH_OPS | _TEXT_COMPARE_OPS:
            left = str(input_value)
            right = str(expected if expected is not None else "")
            if operator_key in _CONTAINS_OPS | _STARTS_WITH_OPS | _ENDS_WITH_OPS and right == "":
                return False
            if not rule.get("case_sensitive"):
                left = left.casefold()
                right = right.casefold()
            if operator_key in _CONTAINS_OPS:
                return right in left
            if operator_key in _STARTS_WITH_OPS:
                return left.startswith(right)
            if operator_key in _ENDS_WITH_OPS:
                return left.endswith(right)
            return left == right

        if input_value is None or expected is None:
            return False
        equality_ops = {"=", "==", "eq"}
        inequality_ops = {"!=", "ne"}
        if operator_key in equality_ops:
            return cls._values_equal(input_value, expected)
        if operator_key in inequality_ops:
            return not cls._values_equal(input_value, expected)
        op = _COMPARE_OPS.get(operator_key, operator.eq)
        num_left, num_right = cls._try_num(input_value), cls._try_num(expected)
        if num_left is not None and num_right is not None:
            return op(num_left, num_right)
        return False

    @classmethod
    def _coerce_mapping_result(cls, value: Any, output_type: str) -> Any:
        output_type = str(output_type or "string").lower()
        if output_type == "bool":
            return cls._to_bool(value)
        if output_type == "int":
            try:
                return int(float(value))
            except (TypeError, ValueError):
                return 0
        if output_type == "float":
            return cls._to_num(value)
        return "" if value is None else str(value)

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

            case "merge":
                return {"out": self._eval_merge(node, inputs)}

            case "memory":
                return {"out": self._memory_value(node)}

            case "change_filter":
                state = self.hysteresis_state.get(node.id)
                prev_value = state["value"] if isinstance(state, dict) and "value" in state else None
                if inputs.get("_suppress_change_filter"):
                    # Held by LogicManager: an upstream async node (api_client/
                    # host_check/message_archive/notify) hasn't resolved on
                    # this pass, so its output feeding "in" is a placeholder,
                    # not a real value. Treat this exactly like an absent
                    # input — no state write, no pulse — whether or not prior
                    # state already exists; the manager re-runs this node with
                    # the real value once the async result is known.
                    return {"out": prev_value, "changed": False}
                if "in" not in inputs:
                    # Unwired input: this graph execution was not driven by
                    # this node's source, so an absent value must not look
                    # like "first value received" and fire a spurious pulse.
                    return {"out": prev_value, "changed": False}
                value = inputs["in"]
                has_prev = isinstance(state, dict) and "value" in state
                # `value` is stored as an isolated deep copy — never the same
                # object (or containing the same nested mutable objects) as
                # the one handed out as `out` — because a downstream node (a
                # "python_script" is explicitly allowed to mutate its inputs
                # in place) could otherwise mutate the object this filter is
                # using as its own comparison baseline, corrupting both the
                # next comparison and the persisted state without ever
                # passing through "in" again. Deep-copying unconditionally
                # (rather than only when the outer container is itself
                # dict/list/set) is required because an outer immutable
                # container — a tuple, e.g. ([1],) — can still hold a nested
                # mutable member; deciding isolation from the outer type
                # alone would miss that. A deepcopy of a genuinely atomic
                # value (int/str/bool/None/float/datetime/date/time/bytes)
                # is a cheap no-op, so there's no cost to doing this always.
                # A bare copy.deepcopy() would raise for a permitted
                # python_script legitimately returning a generator, or any
                # other value with a failing __deepcopy__/__reduce__ hook,
                # turning this node into "__error__" on its very first
                # input. Use _replay_known_output_value's fallback (keep the
                # ORIGINAL reference on a copy failure), not
                # _snapshot_debug_value's (json round-trip, then str()):
                # this baseline is semantic persisted state compared again
                # on every future pass, not a one-off debug capture — a
                # string stand-in here would permanently change its type,
                # so a live value of the original (non-copyable) type could
                # never compare equal to it again, reporting changed=True
                # forever and repeating downstream side effects on every
                # unrelated execution.
                baseline = _replay_known_output_value(value)
                if not has_prev:
                    self.hysteresis_state[node.id] = {"value": baseline}
                    return {"out": value, "changed": True}
                equal, via_normalizing_path = self._compare_values(
                    value,
                    state["value"],
                    right_is_recovered_str=bool(state.get("_recovered_str")),
                    right_is_opaque_recovered_str=bool(state.get("_opaque_recovered_str")),
                )
                if not equal:
                    self.hysteresis_state[node.id] = {"value": baseline}
                    return {"out": value, "changed": True}
                # On an "equal" result, normally keep emitting the persisted
                # representation (matches existing numeric/boolean-alias and
                # dict/list-key-order normalization). But when equality only
                # held via the str() fallback, the two sides can be
                # genuinely different types (e.g. a live datetime.time vs. a
                # persisted, JSON-lossy string left over from a restart's
                # `default=str` encoding) — emit the current input there
                # instead, so `out`'s type doesn't silently degrade to
                # whatever survived persistence. When emitting the persisted
                # representation, hand out a deep copy unconditionally — same
                # reasoning as the baseline above, since state["value"] is
                # otherwise the exact object (or container of objects) this
                # filter keeps comparing against on every future "unchanged"
                # tick. Same _replay_known_output_value fallback as the
                # baseline store above, for the same reason: state["value"]
                # can now genuinely be a non-deepcopyable object (e.g. a
                # generator a repeatedly-identical Memory source emits) once
                # its baseline is no longer forced through a lossy str()
                # snapshot — a bare copy.deepcopy() here would raise on the
                # very next "unchanged" tick instead of just handing out the
                # same reference again.
                if via_normalizing_path:
                    out_value = _replay_known_output_value(state["value"])
                    if state.get("_recovered_str") or state.get("_opaque_recovered_str"):
                        # A live value just confirmed the persisted,
                        # restart-recovered representation via ORDINARY
                        # equality (not either str()-recovery fallback
                        # above) — e.g. a source that genuinely emits the
                        # literal string "10:30:00" live, matching a
                        # persisted string tagged "_recovered_str" from
                        # before tagged persistence existed, or a live value
                        # matching an "_opaque_recovered_str"-tagged string.
                        # That resolves the ambiguity whichever marker
                        # exists for: this source evidently emits this
                        # representation live, not some other type that
                        # only happens to str()-match it. Clear the marker
                        # now so a LATER genuine type transition is reported
                        # as a real change instead of being silently
                        # swallowed by a recovery fallback forever.
                        self.hysteresis_state[node.id] = {"value": out_value}
                else:
                    # Equality here only held through one of the str()-
                    # recovery fallbacks above: state["value"] is still the
                    # pre-migration persisted string (or the "opaque_str"-
                    # tagged one) and its marker is still set. Migrate
                    # stored state to the live, typed value now (dropping
                    # the marker) so the *next* persist writes it properly
                    # tagged under the version-2 envelope — otherwise a
                    # future restart's loader (which only re-applies a
                    # recovery heuristic to an untagged legacy string or an
                    # "opaque_str"-tagged one, never to an already-migrated
                    # value) would keep comparing this same unmigrated
                    # value against a live value on every restart and
                    # report a spurious changed=True each time.
                    out_value = value
                    self.hysteresis_state[node.id] = {"value": baseline}
                return {"out": out_value, "changed": False}

            case "edge_detect":
                # Remembered previous *level* (already boolean) — Edge Detect
                # compares levels, not values, so unlike change_filter there is
                # nothing to deep-copy or type-recover across a restart.
                state = self.hysteresis_state.get(node.id)
                has_prev = isinstance(state, dict) and "value" in state
                idle: dict[str, Any] = {"rising": False, "falling": False}
                if self._to_bool(inputs.get("reset")):
                    # Reset wins over a value arriving on the same tick (same
                    # precedence as memory's deferred commit): the remembered
                    # level is dropped entirely, so the next value re-seeds the
                    # baseline and produces no edge — and nothing is persisted
                    # for this node until then.
                    self.hysteresis_state.pop(node.id, None)
                    return idle
                if "in" not in inputs or inputs["in"] is None:
                    # Unwired/absent input, or an explicit "nothing arrived":
                    # this run was driven by some other source, so it must
                    # neither seed nor advance the level. None is treated as
                    # absent — not coerced to False — exactly like the other
                    # stateful blocks (hysteresis' `if val is None`,
                    # statistics' `if val is not None`). Otherwise an unseeded
                    # Read Object would seed a bogus False level and the first
                    # REAL value would fire a rising edge, contradicting "the
                    # first value after a start produces no edge". It is also
                    # how LogicManager neutralizes a Change Filter's no-pulse
                    # placeholder on an unrelated branch (issue #1090).
                    return idle
                current = self._to_bool(inputs["in"])
                # The level is tracked unconditionally — `mode` filters only
                # the outputs, so an ignored counter-edge still moves the
                # baseline and the next relevant edge is recognised correctly.
                self.hysteresis_state[node.id] = {"value": current}
                if not has_prev or current == bool(state["value"]):
                    # First value after start/reset, or a repeated level: no
                    # edge, and "out" stays absent so nothing downstream sees a
                    # (repeated) value at all.
                    return idle
                rising = current
                # Per-direction action: "off" stays silent (the level above is
                # still tracked, so the next edge in the other direction is
                # recognised), "trigger" pulses only, anything else — "value"
                # or an unknown/legacy setting — pulses and sends.
                action = str(d.get("on_rising", "value") if rising else d.get("on_falling", "value"))
                if action == "off":
                    return idle
                edge_result: dict[str, Any] = {"rising": rising, "falling": not rising}
                if action != "trigger":
                    configured = d.get("value_rising", "true") if rising else d.get("value_falling", "false")
                    edge_result["out"] = self._coerce_typed_value(node, configured, "bool")
                return edge_result

            case "compare":
                operator_key = str(d.get("operator", ">")).strip().lower()
                op = _COMPARE_OPS.get(operator_key, operator.gt)
                a, b = inputs.get("in1"), inputs.get("in2")
                if a is None or b is None:
                    return {"out": False}
                # Auto-coerce to number when both values look numeric
                num_a, num_b = self._try_num(a), self._try_num(b)
                if num_a is not None and num_b is not None:
                    return {"out": op(num_a, num_b)}
                equality_ops = {"=", "==", "eq"}
                inequality_ops = {"!=", "ne"}
                if (num_a is None) != (num_b is None):
                    if operator_key in equality_ops:
                        return {"out": False}
                    if operator_key in inequality_ops:
                        return {"out": True}
                    return {"out": False}
                if operator_key not in equality_ops | inequality_ops:
                    return {"out": False}
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

            case "decision":
                value = inputs.get("value")
                conditions = self._load_rule_list(d.get("conditions"))
                if not conditions:
                    conditions = [{"handle": "out_1"}, {"handle": "out_2"}]
                result: dict[str, Any] = {}
                for idx, rule in enumerate(conditions):
                    handle = str(rule.get("handle") or f"out_{idx + 1}")
                    result[handle] = self._condition_matches(value, rule)
                return result

            case "value_mapping":
                value = inputs.get("value")
                output_type = str(d.get("output_type", "string")).lower()
                for rule in self._load_rule_list(d.get("rules")):
                    if self._condition_matches(value, rule):
                        return {"result": self._coerce_mapping_result(rule.get("result"), output_type)}
                if self._to_bool(d.get("has_default")):
                    return {"result": self._coerce_mapping_result(d.get("default_value"), output_type)}
                return {"result": None}

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
                    except ExecutionError as exc:
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
                    except ExecutionError as exc:
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
                    parts.append(str(val) if val is not None else "")
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
                    import datetime as _dt
                    from zoneinfo import ZoneInfo

                    from astral import LocationInfo
                    from astral.sun import sun as _astral_sun

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
                except Exception:
                    logger.exception("astro_sun error")
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

            case "notify_sms" | "notify_message":
                # Fires when message arrives OR trigger is truthy (both optional).
                msg = inputs.get("message")
                triggered = self._to_bool(inputs.get("trigger")) if "trigger" in inputs else False
                return {
                    "_trigger": msg is not None or triggered,
                    "_message": msg,
                    "sent": False,
                }

            case "message_archive":
                # Fires when message arrives OR trigger is truthy (both optional).
                msg = inputs.get("message")
                title = inputs.get("title")
                triggered = self._to_bool(inputs.get("trigger")) if "trigger" in inputs else False
                return {
                    "_trigger": msg is not None or triggered,
                    "_message": msg,
                    "_title": title,
                    "stored": False,
                }

            case "wake_on_lan":
                return {
                    "_trigger": self._to_bool(inputs.get("trigger")),
                    "sent": False,
                }

            case "host_check":
                # Async — fully handled by LogicManager after executor run
                return {
                    "_trigger": self._to_bool(inputs.get("trigger")),
                    "reachable": False,
                    "latency_ms": None,
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
                import json as _json_mod

                raw = inputs.get("data")
                json_path = (d.get("json_path") or "").strip()
                json_paths_raw = (d.get("json_paths") or "").strip()

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

                # _preview: compact JSON snapshot for config-panel path picker (max 20 KB)
                try:
                    preview = _json_mod.dumps(data_obj, default=str, ensure_ascii=False)
                    if len(preview) > 20_000:
                        preview = preview[:20_000] + "…"
                except (TypeError, ValueError, RecursionError):
                    preview = str(data_obj) if data_obj is not None else None

                # Multi-path mode: json_paths is a JSON array of {label, path} entries
                if json_paths_raw:
                    try:
                        path_list = _json_mod.loads(json_paths_raw)
                    except (_json_mod.JSONDecodeError, TypeError):
                        path_list = []

                    if isinstance(path_list, list) and path_list:
                        result: dict[str, Any] = {"_preview": preview}
                        for i, entry in enumerate(path_list):
                            p = (entry.get("path") or "").strip() if isinstance(entry, dict) else ""
                            val: Any = None
                            if data_obj is not None and p:
                                try:
                                    val = self._json_extract(data_obj, p)
                                except (KeyError, IndexError, TypeError, ValueError):
                                    val = None
                            result[f"out_{i + 1}"] = val
                        return result

                # Legacy single-path mode
                value: Any = None
                if data_obj is not None and json_path:
                    try:
                        value = self._json_extract(data_obj, json_path)
                    except (KeyError, IndexError, TypeError, ValueError):
                        value = None

                return {"value": value, "_preview": preview}

            case "xml_extractor":
                import json as _json_xml
                import xml.etree.ElementTree as _ET

                raw_xml = inputs.get("data")
                xml_path = (d.get("xml_path") or "").strip()
                xml_paths_raw = (d.get("xml_paths") or "").strip()

                _xml_root = None
                preview_str: str | None = None

                if isinstance(raw_xml, str) and raw_xml.strip():
                    preview_str = raw_xml[:20_000] if len(raw_xml) > 20_000 else raw_xml
                    try:
                        _xml_root = _ET.fromstring(raw_xml.strip())
                    except _ET.ParseError:
                        pass

                # Multi-path mode: xml_paths is a JSON array of {label, path} entries
                if xml_paths_raw:
                    try:
                        path_list = _json_xml.loads(xml_paths_raw)
                    except (_json_xml.JSONDecodeError, TypeError):
                        path_list = []

                    if isinstance(path_list, list) and path_list:
                        result: dict[str, Any] = {"_preview": preview_str}
                        for i, entry in enumerate(path_list):
                            p = (entry.get("path") or "").strip() if isinstance(entry, dict) else ""
                            val: Any = None
                            if _xml_root is not None and p:
                                el = _xml_root.find(p)
                                if el is not None:
                                    val = (el.text or "").strip()
                            result[f"out_{i + 1}"] = val
                        return result

                # Legacy single-path mode
                value = None
                if _xml_root is not None and xml_path:
                    el = _xml_root.find(xml_path)
                    if el is not None:
                        value = (el.text or "").strip()

                return {"value": value, "_preview": preview_str}

            case "substring_extractor":
                import re as _re

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
                    except (_re.error, ValueError, IndexError):
                        value = None

                preview_str = raw_text[:20_000] if raw_text and len(raw_text) > 20_000 else raw_text
                return {"value": value, "_preview": preview_str}

            case "timer_cron":
                # Fired by manager via input_overrides; pass trigger signal downstream
                return {"trigger": inputs.get("trigger", False)}

            case "datetime":
                try:
                    tz = _ZoneInfo(str(self.app_config.get("timezone", "Europe/Zurich")))
                except (ValueError, ZoneInfoNotFoundError):
                    tz = _ZoneInfo("UTC")
                now = _datetime.now(tz)
                language = str(self.app_config.get("language", "de"))
                return {
                    "date": format_datetime(now, str(self.app_config.get("date_format", DEFAULT_DATE_FORMAT)), language),
                    "time": format_datetime(now, str(self.app_config.get("time_format", DEFAULT_TIME_FORMAT)), language),
                    "custom": format_datetime(now, str(d.get("custom_format") or DEFAULT_CUSTOM_FORMAT), language),
                }

            case "timer_delay" | "timer_pulse":
                # Async nodes — handled by manager, not executor
                return {}

            case "value_sequence":
                # The manager owns the async task; execution only exposes the
                # current control values and never sleeps in this synchronous pass.
                return {"_triggered": inputs.get("trigger"), "_condition": inputs.get("condition", True)}

            case "heating_circuit":
                # Mannheimer Methode (DIN 4710): Sommer/Winter-Umschaltung anhand Tagesmittel.
                # Messzeitpunkte (Erste-Kreuzung-Semantik — kein exakter Sensor-Takt nötig):
                #   T1 = anliegender Wert beim ersten Eintreffen einer Messung ab 07:00
                #   T2 = anliegender Wert beim ersten Eintreffen einer Messung ab 14:00
                #   T3 = anliegender Wert beim ersten Eintreffen einer Messung ab 21:00
                # T_avg = (T1 + T2 + 2×T3) / 4
                # Heizmodus EIN wenn T_avg < threshold_temp, AUS wenn >= threshold_temp + hysteresis.
                # Fehlende Slots werden aus history-Vorberechnungen des Managers ergänzt (_history_*).
                import datetime as _dt

                state = self.hysteresis_state.setdefault(
                    node.id,
                    {
                        "last_value": None,
                        "t1": None,
                        "t1_date": None,
                        "t2": None,
                        "t2_date": None,
                        "t3": None,
                        "t3_date": None,
                        "daily_temps": [],
                        "daily_avg": None,
                        "daily_avg_date": None,
                        "monthly_avg": None,
                        "heating_mode": 0,
                    },
                )
                # Migrate states persisted before these fields were introduced
                for _k in ("last_value", "t1", "t1_date", "t2", "t2_date", "t3", "t3_date", "daily_avg", "daily_avg_date", "monthly_avg"):
                    state.setdefault(_k, None)
                state.setdefault("daily_temps", [])
                state.setdefault("heating_mode", 0)

                # Read new config keys; fall back to legacy temp_winter/temp_summer for
                # graphs saved before this change so existing configurations are preserved.
                _tw = d.get("temp_winter")
                _ts = d.get("temp_summer")
                if "threshold_temp" not in d and _tw is not None:
                    threshold = float(_tw)
                    hysteresis = float(_ts) - float(_tw) if _ts is not None else 2.0
                else:
                    threshold = float(d.get("threshold_temp", 14.0))
                    hysteresis = float(d.get("hysteresis", 2.0))
                try:
                    tz = _ZoneInfo(str(self.app_config.get("timezone", "Europe/Zurich")))
                except (ValueError, ZoneInfoNotFoundError):
                    tz = _ZoneInfo("UTC")
                today = inputs.get("_date") or _dt.datetime.now(tz).date().isoformat()
                hour = inputs.get("_hour", _dt.datetime.now(tz).hour)
                val = inputs.get("value")

                # History fallback: fill missing slots pre-queried by the manager
                for _slot in ("t1", "t2", "t3"):
                    _hist_val = inputs.get(f"_history_{_slot}")
                    if state[f"{_slot}_date"] != today and _hist_val is not None:
                        state[_slot] = float(_hist_val)
                        state[f"{_slot}_date"] = today

                if val is not None:
                    fval = self._to_num(val)
                    prev_value = state["last_value"]

                    slot_override = inputs.get("_slot")
                    if slot_override in ("t1", "t2", "t3"):
                        # Test override: inject slot value directly
                        state[slot_override] = fval
                        state[f"{slot_override}_date"] = today
                    else:
                        # Erste-Kreuzung: capture the value already on the bus AT the threshold
                        # hour (prev_value), not the triggering measurement (fval).
                        # Falls back to fval on cold start (no prior reading).
                        capture_val = prev_value if prev_value is not None else fval
                        if hour >= 7 and state["t1_date"] != today:
                            state["t1"] = capture_val
                            state["t1_date"] = today
                        if hour >= 14 and state["t2_date"] != today:
                            state["t2"] = capture_val
                            state["t2_date"] = today
                        if hour >= 21 and state["t3_date"] != today:
                            state["t3"] = capture_val
                            state["t3_date"] = today

                    # Update last_value AFTER slot capture so prev_value was valid at threshold time
                    state["last_value"] = fval

                # Calculate daily average once all three slots are captured for today
                if state["t1_date"] == today and state["t2_date"] == today and state["t3_date"] == today and state["daily_avg_date"] != today:
                    daily_avg = (state["t1"] + state["t2"] + 2 * state["t3"]) / 4
                    state["daily_avg"] = daily_avg
                    state["daily_avg_date"] = today
                    state["daily_temps"].append(daily_avg)
                    state["daily_temps"] = state["daily_temps"][-31:]
                    state["monthly_avg"] = sum(state["daily_temps"]) / len(state["daily_temps"])

                # Heating mode: ON below threshold, OFF at or above threshold + hysteresis
                ref_temp = state["daily_avg"]
                if ref_temp is not None:
                    if ref_temp < threshold:
                        state["heating_mode"] = 1
                    elif ref_temp >= threshold + hysteresis:
                        state["heating_mode"] = 0
                    # Between thresholds: maintain current state (hysteresis band)
                elif val is not None:
                    # No daily avg yet: immediate estimate from current value
                    state["heating_mode"] = 1 if self._to_num(val) < threshold else 0
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
                # Min/max periods, like consumption periods, belong to the
                # configured application timezone rather than the server
                # process timezone (usually UTC in Docker).
                try:
                    tz = _ZoneInfo(str(self.app_config.get("timezone", "Europe/Zurich")))
                except (ValueError, ZoneInfoNotFoundError):
                    tz = _ZoneInfo("Europe/Zurich")
                today = _datetime.now(tz).date()
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
                import datetime as _dt

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
                    # Keep the 365-day window bounded even for high-frequency inputs.
                    cutoff_iso = (now_utc - _dt.timedelta(days=365)).isoformat()
                    state["samples"] = [s for s in state["samples"] if s[0] >= cutoff_iso]
                    if len(state["samples"]) > _AVG_MULTI_MAX_SAMPLES:
                        state["samples"] = state["samples"][-_AVG_MULTI_MAX_SAMPLES:]
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
                # Consumption periods belong to the configured application timezone,
                # not the timezone of the server process (usually UTC in Docker).
                try:
                    tz = _ZoneInfo(str(self.app_config.get("timezone", "Europe/Zurich")))
                except (ValueError, ZoneInfoNotFoundError):
                    tz = _ZoneInfo("Europe/Zurich")
                today = _datetime.now(tz).date()
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

            case "ical":
                # The raw iCal text is pre-fetched by LogicManager and stored in
                # hysteresis_state[node.id]["raw"] before each executor run.
                import json as _json_ic
                import re as _re_ic

                hyst_node = self.hysteresis_state.setdefault(node.id, {})
                raw_text: str = hyst_node.get("raw", "")
                filters_json = (d.get("filters") or "[]").strip()
                try:
                    filters: list[dict] = _json_ic.loads(filters_json) if filters_json else []
                    if not isinstance(filters, list):
                        filters = []
                except (_json_ic.JSONDecodeError, TypeError):
                    filters = []

                out: dict[str, Any] = {"raw": raw_text}

                if not raw_text:
                    for i in range(len(filters)):
                        out[f"f{i}_array"] = []
                        out[f"f{i}_next_date"] = None
                        out[f"f{i}_tomorrow"] = False
                        out[f"f{i}_today"] = False
                    return out

                cache_key: tuple[str, str, str] | None = None
                try:
                    import datetime as _dt_ic
                    from zoneinfo import ZoneInfo as _ZI

                    from icalendar import Calendar as _ICal

                    try:
                        import recurring_ical_events as _rie

                        _HAS_RIE = True
                    except ImportError:
                        _HAS_RIE = False

                    tz_name = self.app_config.get("timezone", "Europe/Zurich")
                    tz = _ZI(tz_name)
                    today = _datetime.now(tz).date()
                    cache_key = (filters_json, tz_name, today.isoformat())
                    cached = self.ical_result_cache.get(node.id)
                    if (
                        isinstance(cached, dict)
                        and cached.get("raw") is raw_text
                        and cached.get("key") == cache_key
                        and isinstance(cached.get("outputs"), dict)
                    ):
                        cached_outputs = cached["outputs"]
                        out.update(cached_outputs if self.ical_cache_outputs_owned else copy.deepcopy(cached_outputs))
                        return out

                    tomorrow = today + _dt_ic.timedelta(days=1)
                    window_end = today + _dt_ic.timedelta(days=365)

                    # Some generators produce malformed property names (e.g.
                    # X-WR-TIMEZONE','EUROPE/BERLIN: from steffisburg.ch).
                    # icalendar v7 is strict and raises on these lines.
                    # Strip them so the rest of the file parses correctly.
                    _clean_lines = []
                    for _ln in raw_text.splitlines(keepends=True):
                        _prop = _re_ic.split(r"[;:]", _ln, maxsplit=1)[0]
                        if _re_ic.search(r"['\"]", _prop):
                            logger.debug("ical node %s: skipping malformed line %r", node.id[:8], _ln.rstrip())
                            continue
                        _clean_lines.append(_ln)
                    cal = _ICal.from_ical("".join(_clean_lines))

                    if _HAS_RIE:
                        raw_events = _rie.of(cal).between(today, window_end)
                    else:
                        raw_events = [c for c in cal.walk() if c.name == "VEVENT"]

                    def _event_to_row(ev: Any) -> tuple[_dt_ic.date, list] | None:  # type: ignore[return]
                        dtstart = ev.get("DTSTART")
                        if dtstart is None:
                            return None
                        dtend = ev.get("DTEND")
                        start_raw = dtstart.dt
                        end_raw = dtend.dt if dtend else start_raw

                        if isinstance(start_raw, _dt_ic.datetime):
                            if start_raw.tzinfo is not None:
                                start_raw = start_raw.astimezone(tz)
                            event_date = start_raw.date()
                            start_time = start_raw.strftime("%H:%M")
                        else:
                            event_date = start_raw
                            start_time = ""

                        if isinstance(end_raw, _dt_ic.datetime):
                            if end_raw.tzinfo is not None:
                                end_raw = end_raw.astimezone(tz)
                            end_time = end_raw.strftime("%H:%M")
                        else:
                            end_time = ""

                        summary = str(ev.get("SUMMARY", "") or "")
                        location = str(ev.get("LOCATION", "") or "")
                        description = str(ev.get("DESCRIPTION", "") or "")
                        return event_date, [event_date.isoformat(), start_time, end_time, summary, location, description]

                    event_rows: list[tuple[_dt_ic.date, list]] = []
                    for ev in raw_events:
                        r = _event_to_row(ev)
                        if r:
                            event_rows.append(r)
                    event_rows.sort(key=lambda x: x[0])

                    _FIELD_IDX = {"summary": 3, "location": 4, "description": 5}

                    def _matches(row_data: list, flt: dict) -> bool:
                        case_sensitive = bool(flt.get("case_sensitive", False))
                        flags = 0 if case_sensitive else _re_ic.IGNORECASE
                        field_logic = str(flt.get("field_logic", "or")).lower()

                        def _pat_matches(pattern: str, text: str) -> bool:
                            if not pattern:
                                return True  # empty pattern = ignore this field
                            try:
                                return bool(_re_ic.search(pattern, text, flags))
                            except _re_ic.error:
                                needle = pattern if case_sensitive else pattern.lower()
                                haystack = text if case_sensitive else text.lower()
                                return needle in haystack

                        # New format: per-field patterns
                        if any(k in flt for k in ("summary_pattern", "location_pattern", "description_pattern")):
                            checks = [
                                (flt.get("summary_pattern") or "", row_data[3]),
                                (flt.get("location_pattern") or "", row_data[4]),
                                (flt.get("description_pattern") or "", row_data[5]),
                            ]
                            active = [(pat, val) for pat, val in checks if pat]
                            if not active:
                                return True  # all patterns empty = match all
                            if field_logic == "and":
                                return all(_pat_matches(p, v) for p, v in active)
                            return any(_pat_matches(p, v) for p, v in active)

                        # Legacy format: single pattern across selected fields
                        pattern = str(flt.get("pattern") or "")
                        if not pattern:
                            return True
                        fields = flt.get("fields") or ["summary"]
                        match_all = bool(flt.get("match_all_fields", False))
                        active_fields = [f for f in fields if f in _FIELD_IDX]
                        if not active_fields:
                            return False
                        results = [_pat_matches(pattern, row_data[_FIELD_IDX[f]]) for f in active_fields]
                        return all(results) if match_all else any(results)

                    for i, flt in enumerate(filters):
                        matching = [(ev_date, row) for ev_date, row in event_rows if _matches(row, flt)]
                        future = [(ev_date, row) for ev_date, row in matching if ev_date >= today]
                        out[f"f{i}_array"] = [row for _, row in future]
                        out[f"f{i}_next_date"] = future[0][0].isoformat() if future else None
                        out[f"f{i}_today"] = any(ev_date == today for ev_date, _ in matching)
                        out[f"f{i}_tomorrow"] = any(ev_date == tomorrow for ev_date, _ in matching)

                except ImportError as exc:
                    logger.warning("ical node %s: missing library — %s", node.id[:8], exc)
                except Exception:
                    logger.exception("ical node %s: parse error", node.id[:8])
                    for i in range(len(filters)):
                        out.setdefault(f"f{i}_array", [])
                        out.setdefault(f"f{i}_next_date", None)
                        out.setdefault(f"f{i}_today", False)
                        out.setdefault(f"f{i}_tomorrow", False)

                if cache_key is not None:
                    self.ical_result_cache[node.id] = {
                        "raw": raw_text,
                        "key": cache_key,
                        "outputs": copy.deepcopy({key: value for key, value in out.items() if key != "raw"}),
                    }
                return out

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

    def _eval_merge(self, node: LogicNode, inputs: dict[str, Any]) -> Any:
        """Route whichever wired input last produced a new value to ``out``.

        The graph is fully re-evaluated every tick, so every wired input has a
        current value on every pass — "last changed wins" therefore needs
        persisted per-input state (like ``hysteresis``) to detect which input
        actually changed *this* tick, rather than always picking a fixed port.
        If several inputs change in the same tick, the highest port number
        wins. Before any input has ever changed, the first wired input that
        currently has a value is used.
        """
        d = node.data
        count = max(2, min(30, int(d.get("input_count", 2))))
        port_ids = [f"in{i}" for i in range(1, count + 1)]
        state = self.hysteresis_state.get(node.id, {})
        prev_values: dict[str, Any] = state.get("values", {})
        active = state.get("active")

        wired = [p for p in port_ids if p in inputs]
        changed = [p for p in wired if inputs[p] is not None and (p not in prev_values or not self._values_equal(prev_values[p], inputs[p]))]
        if changed:
            active = changed[-1]  # highest port number among those changed this tick
        elif active not in wired or inputs.get(active) is None:
            active = None

        if active is None:
            active = next((p for p in wired if inputs[p] is not None), None)

        self.hysteresis_state[node.id] = {
            "values": {p: inputs[p] for p in wired},
            "active": active,
        }
        return inputs.get(active) if active is not None else None

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
        except (InvalidOperation, TypeError):
            return round(x, ndigits)  # fallback

    @staticmethod
    def _validate_formula_ast(tree: ast.AST) -> None:
        """Allow only a constrained subset of expression AST nodes."""
        allowed_nodes = (
            ast.Expression,
            ast.BinOp,
            ast.UnaryOp,
            ast.BoolOp,
            ast.Compare,
            ast.Call,
            ast.Name,
            ast.Attribute,
            ast.Load,
            ast.Constant,
            ast.IfExp,
            ast.List,
            ast.Tuple,
            ast.Dict,
            ast.Subscript,
            ast.Slice,
            ast.Add,
            ast.Sub,
            ast.Mult,
            ast.Div,
            ast.FloorDiv,
            ast.Mod,
            ast.Pow,
            ast.UAdd,
            ast.USub,
            ast.And,
            ast.Or,
            ast.Not,
            ast.Eq,
            ast.NotEq,
            ast.Lt,
            ast.LtE,
            ast.Gt,
            ast.GtE,
            ast.In,
            ast.NotIn,
        )
        for node in ast.walk(tree):
            if not isinstance(node, allowed_nodes):
                raise ExecutionError(f"Formula contains disallowed syntax: {type(node).__name__}")
            if isinstance(node, ast.Attribute) and not (
                isinstance(node.value, ast.Name) and node.value.id == "math" and not node.attr.startswith("_")
            ):
                raise ExecutionError("Formula attribute access is not allowed")

    @staticmethod
    def _validate_script_ast(tree: ast.AST) -> None:
        """Disallow dangerous script syntax while preserving basic script support."""
        blocked = (
            ast.Import,
            ast.ImportFrom,
            ast.ClassDef,
            ast.FunctionDef,
            ast.AsyncFunctionDef,
            ast.Lambda,
            ast.Try,
            ast.With,
            ast.AsyncWith,
            ast.Global,
            ast.Nonlocal,
            ast.Raise,
            ast.Delete,
            ast.Yield,
            ast.YieldFrom,
            ast.Await,
        )
        for node in ast.walk(tree):
            if isinstance(node, blocked):
                raise ExecutionError(f"Script contains disallowed syntax: {type(node).__name__}")
            if isinstance(node, ast.Attribute) and not (
                isinstance(node.value, ast.Name) and node.value.id == "math" and not node.attr.startswith("_")
            ):
                raise ExecutionError("Script attribute access is not allowed")

    @staticmethod
    def _safe_eval(expr: str, ctx: dict[str, Any]) -> Any:
        """Evaluate a math expression safely.

        Available: all math.* functions + abs, round, min, max + ctx variables.
        """
        allowed = {k: v for k, v in math.__dict__.items() if not k.startswith("_")}
        # Add Python builtins that are safe and useful in formulas.
        # Use _round_half_up instead of built-in round to get mathematical
        # rounding (0.5 always rounds up) rather than banker's rounding.
        allowed.update({"abs": abs, "round": GraphExecutor._round_half_up, "min": min, "max": max, "math": math})
        allowed.update(ctx)
        try:
            tree = ast.parse(expr, mode="eval")
            GraphExecutor._validate_formula_ast(tree)
            return eval(compile(tree, "<formula>", "eval"), {"__builtins__": {}}, allowed)
        except Exception as exc:
            raise ExecutionError(f"Formula error: {exc}") from exc

    @staticmethod
    def _run_script(script: str, inputs: dict[str, Any]) -> Any:
        """Run a restricted Python script."""
        local_ns: dict[str, Any] = {"inputs": inputs, "result": None, "math": math}
        try:
            tree = ast.parse(script, mode="exec")
            GraphExecutor._validate_script_ast(tree)
            exec(  # noqa: S102 -- the "python_script" node feature; AST-validated above and run with a locked-down __builtins__ dict
                compile(tree, "<script>", "exec"),
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
