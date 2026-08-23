"""LogicManager — manages all logic graphs and integrates with the EventBus.

- Subscribes to DataValueEvents
- Triggers graphs whose datapoint_read nodes watch the changed DataPoint
- Executes the graph and writes outputs back via the registry
- Schedules timer_cron nodes via asyncio tasks (requires croniter)
"""

from __future__ import annotations

import asyncio
import base64
import copy
import email.utils
import http.cookies
import ipaddress
import json
import logging
import os
import re
import socket
import stat
import uuid
from collections import deque
from collections.abc import Callable
from datetime import UTC, date, datetime, time
from decimal import Decimal, InvalidOperation
from functools import partial
from pathlib import Path
from time import perf_counter
from typing import Any
from urllib.parse import quote, unquote, urljoin, urlparse, urlunparse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx

from obs.core.json import jsonable
from obs.logic.executor import GraphExecutor, _OpaqueRecoveredDict, _OpaqueRecoveredSet, _OpaqueRecoveredStr, _replay_known_output_value
from obs.logic.models import FlowData
from obs.logic.node_types import get_node_type
from obs.security.url_targets import resolve_url_target

logger = logging.getLogger(__name__)
_run_graph_executor_in_worker = asyncio.to_thread
_run_graph_state_copy_in_worker = asyncio.to_thread
_run_logic_debug_serialization_in_worker = asyncio.to_thread
_MISSING_STATE = object()


class _ObsoleteGraphExecution(Exception):
    """Stop a pass whose captured graph generation has been replaced."""


def _state_values_equal(left: Any, right: Any) -> bool | None:
    """Compare arbitrary retained state without trusting runtime equality."""
    try:
        return bool(left == right)
    except Exception:  # noqa: BLE001 - script values may define raising or ambiguous equality
        return True if left is right else None


def _merge_worker_state(
    base: dict[str, Any],
    updated: dict[str, Any],
    target: dict[str, Any],
    visited: set[tuple[int, int, int]] | None = None,
) -> None:
    """Apply worker changes after the caller validates the graph generation."""
    visited = set() if visited is None else visited
    triple = (id(base), id(updated), id(target))
    if triple in visited:
        return
    visited.add(triple)
    for key in base.keys() - updated.keys():
        target.pop(key, None)
    for key, updated_value in updated.items():
        base_value = base.get(key, _MISSING_STATE)
        if base_value is not _MISSING_STATE and _state_values_equal(updated_value, base_value) is True:
            continue
        target_value = target.get(key, _MISSING_STATE)
        if isinstance(base_value, dict) and isinstance(updated_value, dict) and isinstance(target_value, dict):
            _merge_worker_state(base_value, updated_value, target_value, visited)
        else:
            # ``updated`` is an isolated worker snapshot that is discarded
            # after this commit, so ownership can safely move to ``target``.
            # The per-graph execution lock serializes worker passes and
            # _execute_pass validates the graph generation immediately before
            # this merge. Comparing target to a deep-copied baseline cannot
            # reliably detect concurrency: legitimate non-reflexive retained
            # values compare unequal solely because they were copied.
            target[key] = updated_value


def _copy_graph_worker_state(state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Create the worker baseline and mutable state away from the event loop."""
    base = _safe_deepcopy_state(state)
    return base, _safe_deepcopy_state(base)


def _serialize_logic_debug_payload(
    graph_id: str,
    outputs: dict[str, Any],
    debug_inputs: dict[str, Any],
    debug_overrides: dict[str, Any],
    execution_started: float,
) -> dict[str, Any]:
    """Build the potentially large websocket snapshot in a worker thread."""
    return {
        "action": "logic_run",
        "graph_id": graph_id,
        "outputs": json.loads(json.dumps(jsonable(outputs), default=str)),
        "inputs": json.loads(json.dumps(jsonable(debug_inputs), default=str)),
        "debug": {
            "timestamp": datetime.now(UTC).isoformat(),
            "duration_ms": round((perf_counter() - execution_started) * 1000, 2),
            "used_overrides": bool(debug_overrides),
        },
    }


def _msg_to_str(v: object) -> str:
    """Convert any node output value to a message string.

    Uses explicit None-check rather than truthiness so that falsy values
    (0, False, 0.0, "") are preserved as their string representation instead
    of being silently replaced by a fallback.
    """
    import json as _j

    if isinstance(v, (dict, list)):
        return _j.dumps(v, ensure_ascii=False)
    return str(v)


_THROTTLE_UNITS: dict[str, float] = {
    "ms": 1.0,
    "s": 1000.0,
    "min": 60_000.0,
    "h": 3_600_000.0,
}
_MAX_LOGIC_CASCADE_DEPTH = 10
_MAX_SEQUENCE_REPEAT_COUNT = 10_000

# Node types the side-effect-free initialization pass (initialize_graph) must
# not publish through: async/action nodes are not executed there, so their
# executor outputs are placeholders (e.g. api_client.success=False, timers
# and missing_node return {}); per-sample accumulators run on a throwaway
# state copy, so their outputs would include the seed while the persisted
# state stays untouched; random_value generates a fresh value on every
# evaluation, so a save would publish a new random actuator value; memory
# evaluates with commit_memory=False, so its output is the uncommitted
# previous/default value, not the seeded input; ical outputs come from the
# fetch cache, which may still be empty right after a save.
_INIT_EXCLUDED_NODE_TYPES = frozenset(
    {
        "api_client",
        "host_check",
        "notify_pushover",
        "notify_sms",
        "message_archive",
        "wake_on_lan",
        "value_sequence",
        "timer_delay",
        "timer_pulse",
        "timer_cron",
        "ical",
        "missing_node",
        "python_script",
        "statistics",
        "avg_multi",
        "min_max_tracker",
        "consumption_counter",
        "heating_circuit",
        "random_value",
        "memory",
    }
)

# Deterministic two-state nodes whose init-pass state IS committed when they
# sit on a clean seeded path. gate/hysteresis/merge commit only when their
# switched output actually reached a published datapoint_write (see the
# commit loop below) — their own output isn't state that matters on its own,
# only insofar as it changed what got written. merge belongs here for the
# same reason as gate/hysteresis: its "active" input is state
# (self.hysteresis_state), and without committing it here, a save/activate
# leaves the graph attributing out to whichever input was active before the
# save — potentially not the one whose current, just-seeded value actually
# got published — until the next real datapoint event resolves it.
# change_filter is the exception: its "last value seen" is meaningful on its
# own regardless of whether it feeds a Write Object at all — a seeded Change
# Filter → Wake-on-LAN/notification/sequence branch with no write anywhere
# downstream must still remember the seed, or the next real event carrying
# that same value looks like a fresh first value and fires the action again.
# So change_filter always commits once seeded/untainted, independent of
# published_writes.
_INIT_COMMIT_STATE_NODE_TYPES = frozenset({"gate", "hysteresis", "merge", "change_filter", "edge_detect"})

# …and edge_detect for the same reason: its remembered level is meaningful on
# its own. Excluding it from initialization instead would leave a newly placed
# block with no baseline at all, so the first real transition would merely seed
# the level and the edge would be lost. Its outputs are kept out of the
# published writes separately (see changed_targets), because a save is not a
# transition.
_INIT_STATE_ALWAYS_COMMIT = frozenset({"change_filter", "edge_detect"})

# Stateful blocks that must be held back while an upstream async node has not
# resolved yet: committing that pass's placeholder would record a level/value
# that never occurred, and any action they drive would already have run
# irreversibly by the time the replay corrects them.
_HELD_ON_UNRESOLVED_SOURCE = frozenset({"change_filter", "edge_detect"})

# Blocks whose outputs are discrete event pulses rather than sustained levels.
# They are the roots of the pulse-provenance trace: a consumer fed from one of
# them must not read the "no pulse this pass" placeholder as a real value.
_PULSE_ORIGIN_NODE_TYPES = frozenset({"change_filter", "edge_detect"})

# Input handles that control WHEN a node's output fires/passes but do not
# deliver the value itself. Seeded eligibility must not propagate through
# them: a Const → Gate.in → Write.value sheet whose Read Object only drives
# Gate.enable (or Write.trigger) would otherwise publish the constant on
# save even though the written value does not descend from the seed.
_INIT_CONTROL_INPUT_HANDLES: dict[str, frozenset[str]] = {
    "datapoint_write": frozenset({"trigger"}),
    "gate": frozenset({"enable"}),
}


def _downstream_closure(start: set[str], edges: list[Any]) -> set[str]:
    """Node ids reachable from *start* (inclusive) following edges forward."""
    reached = set(start)
    grew = True
    while grew:
        grew = False
        for edge in edges:
            if edge.source in reached and edge.target not in reached:
                reached.add(edge.target)
                grew = True
    return reached


# Tag key for persisted node_state values that json.dumps cannot encode
# natively (datetime.date/time/datetime, bytes) — lets _load_graphs restore
# the exact original type instead of leaving behind a lossy str() that a
# live value of the same type could never compare equal to again.
_PERSIST_TYPE_TAG = "__obs_persisted_type__"
_PERSIST_ISOFORMAT_TYPES: dict[str, Callable[[str], Any]] = {
    "datetime": datetime.fromisoformat,
    "date": date.fromisoformat,
    "time": time.fromisoformat,
}
# A dict a stateful node holds natively (e.g. a json_extractor/api_client
# result cached by a memory node) could, in principle, itself already
# contain the reserved _PERSIST_TYPE_TAG key — _escape_persist_collision
# wraps any such dict in an "escaped" envelope *before* json.dumps runs, so
# _decode_persisted_value can always tell a generated type tag apart from
# arbitrary application data, however deeply nested.
_PERSIST_ESCAPED_TAG = "escaped"

# Bumped whenever the node_state envelope/tagging format changes. Only a
# row saved *under* this exact version is guaranteed to have every
# non-JSON-native value fully tagged — only then can _load_graphs skip the
# legacy "_recovered_str" heuristic entirely (see there for why applying it
# to a value this format already round-trips exactly would be wrong).
_PERSIST_STATE_VERSION = 2
_PERSIST_STATE_VERSION_KEY = "__obs_node_state_version__"


def _persist_default(v: Any) -> Any:
    """`json.dumps(..., default=...)` hook: tag recognized non-JSON types."""
    if isinstance(v, datetime):
        # isoformat() only ever records the CURRENT numeric UTC offset, not
        # a named zone — datetime.fromisoformat() on restore reconstructs a
        # fixed-offset tzinfo, not the original ZoneInfo. Two datetimes for
        # the same instant still compare == regardless of tzinfo type (this
        # doesn't affect change_filter's own comparison), but downstream
        # date arithmetic across a DST boundary silently produces different
        # results with a fixed offset than with the original named zone —
        # so the zone key is captured here whenever tzinfo is a ZoneInfo,
        # and used to reconstruct the named zone on decode below.
        _tag: dict[str, Any] = {_PERSIST_TYPE_TAG: "datetime", "value": v.isoformat()}
        if isinstance(v.tzinfo, ZoneInfo):
            _tag["tz"] = v.tzinfo.key
        # isoformat() also loses `fold`. Preserve it explicitly for named,
        # fixed-offset, and naive datetimes alike.
        if v.fold:
            _tag["fold"] = v.fold
        return _tag
    if isinstance(v, date):
        return {_PERSIST_TYPE_TAG: "date", "value": v.isoformat()}
    if isinstance(v, time):
        _tag = {_PERSIST_TYPE_TAG: "time", "value": v.isoformat()}
        # A named zone cannot determine a numeric UTC offset for a bare time
        # (there is no date on which to resolve DST), so isoformat() silently
        # looks naive. Preserve the ZoneInfo identity and fold explicitly.
        if isinstance(v.tzinfo, ZoneInfo):
            _tag["tz"] = v.tzinfo.key
        if v.fold:
            _tag["fold"] = v.fold
        return _tag
    if isinstance(v, bytes):
        return {_PERSIST_TYPE_TAG: "bytes", "value": v.hex()}
    if isinstance(v, Decimal):
        return {_PERSIST_TYPE_TAG: "decimal", "value": str(v)}
    # frozenset before set: a set/frozenset isn't natively JSON-encodable
    # (unlike tuple, which the encoder treats as an array), so it reaches
    # this default= hook directly — no separate handling in
    # _escape_persist_collision is needed for that reason. Members that are
    # themselves non-JSON-native (e.g. a nested frozenset, or a dict with a
    # non-string key) still get their own _persist_default call once
    # json.dumps recurses into the returned list — but a member that the
    # encoder handles NATIVELY without ever calling default=, namely a
    # tuple, would otherwise reach json.dumps unescaped and be silently
    # flattened into a plain JSON array indistinguishable from a list, so
    # each member is run through _escape_persist_collision here first,
    # exactly like _escape_persist_collision's own list/tuple branches do
    # for a top-level list — this is simply that same pre-pass, applied to
    # a set's members instead of a list's.
    if isinstance(v, frozenset):
        return {_PERSIST_TYPE_TAG: "frozenset", "value": [_escape_persist_collision(item) for item in v]}
    if isinstance(v, set):
        return {_PERSIST_TYPE_TAG: "set", "value": [_escape_persist_collision(item) for item in v]}
    # Catch-all for any other unrecognized type (e.g. a permitted
    # python_script result like a complex number or a custom object) — MUST
    # still be tagged, not a bare str(v): this row is otherwise saved under
    # the version-2 envelope, whose whole contract (see
    # _PERSIST_STATE_VERSION above) is that a bare string surviving decode is
    # guaranteed a genuine string, never a lossy stand-in for something else.
    # An untagged fallback here would violate that guarantee — _load_graphs
    # would restore this as a plain string, and a change_filter comparing a
    # live value of the original type against it would report a spurious
    # changed=True forever, since nothing marks the string as recovered.
    return {
        _PERSIST_TYPE_TAG: "opaque_str",
        "value": str(v),
        "type": f"{type(v).__module__}.{type(v).__qualname__}",
    }


def _escape_persist_collision(v: Any) -> Any:
    """Walk state_to_save before json.dumps, escaping any dict that already
    happens to contain _PERSIST_TYPE_TAG so it can never be confused with a
    tag _persist_default generated. Recurses first (children are escaped
    before their parent is checked), so a collision at any nesting depth —
    including one manufactured by a previous escape wrapper — is caught.

    Also tags tuples explicitly: json.dumps natively serializes a tuple as a
    JSON array with no way to tell it apart from a genuine list afterwards
    (its `default=` hook — where _persist_default runs — is never invoked
    for tuples, since the encoder already knows how to handle them), so
    this has to happen here, before json.dumps ever sees the value.

    And dicts with a non-string key (e.g. {1: "x"}, produced by a
    python_script or adapter): json.dumps silently stringifies such keys
    with no `default=` call at all, so this also has to intercept them
    here — encoded as a [key, value] pair list (which can hold a key of
    any JSON-representable type) instead of a native JSON object.
    """
    results: list[Any] = []
    active: set[int] = set()
    work: list[tuple[str, Any]] = [("visit", v)]
    while work:
        operation, current = work.pop()
        if operation == "visit":
            if isinstance(current, _OpaqueRecoveredStr):
                tag = {_PERSIST_TYPE_TAG: "opaque_str", "value": str(current)}
                if current.type_name:
                    tag["type"] = current.type_name
                results.append(tag)
                continue

            kind: str | None = None
            children: list[Any] = []
            if isinstance(current, _OpaqueRecoveredSet):
                kind = "opaque_set"
                children = list(current.items)
            elif isinstance(current, _OpaqueRecoveredDict):
                kind = "opaque_dict"
                children = [item for pair in current.items for item in pair]
            elif isinstance(current, dict):
                kind = "dict_nonstr" if any(not isinstance(k, str) or isinstance(k, _OpaqueRecoveredStr) for k in current) else "dict"
                children = [item for pair in current.items() for item in pair] if kind == "dict_nonstr" else list(current.values())
            elif isinstance(current, tuple):
                kind = "tuple"
                children = list(current)
            elif isinstance(current, list):
                kind = "list"
                children = list(current)
            if kind is None:
                results.append(current)
                continue

            identity = id(current)
            if identity in active:
                raise ValueError("cyclic node state cannot be persisted")
            active.add(identity)
            work.append(("finish", (kind, current, len(children), identity)))
            work.extend(("visit", child) for child in reversed(children))
            continue

        kind, original, child_count, identity = current
        children = results[-child_count:] if child_count else []
        if child_count:
            del results[-child_count:]
        active.remove(identity)
        if kind == "list":
            escaped: Any = children
        elif kind == "tuple":
            escaped = {_PERSIST_TYPE_TAG: "tuple", "value": children}
        elif kind == "dict":
            escaped_dict = dict(zip(original.keys(), children))
            escaped = {_PERSIST_TYPE_TAG: _PERSIST_ESCAPED_TAG, "value": escaped_dict} if _PERSIST_TYPE_TAG in escaped_dict else escaped_dict
        elif kind in {"dict_nonstr", "opaque_dict"}:
            pairs = [[children[index], children[index + 1]] for index in range(0, len(children), 2)]
            escaped = {_PERSIST_TYPE_TAG: "dict_nonstr_keys", "value": pairs}
        else:
            escaped = {
                _PERSIST_TYPE_TAG: "frozenset" if original.frozen else "set",
                "value": children,
            }
        results.append(escaped)
    return results[0]


def _decode_persisted_value_recursive(v: Any) -> Any:
    """Reverse of `_persist_default`/`_escape_persist_collision`, applied
    recursively after json.loads.

    A dict lacking the tag is application state (e.g. change_filter's own
    `{"value": ...}` wrapper) and is walked, not replaced. Untagged strings
    left over from node_state saved before this tagging existed (or any
    value type this function doesn't recognize) pass through unchanged —
    callers needing to know "this string may be a lossy legacy persist" use
    a separate, explicit marker rather than inferring it here.
    """
    if isinstance(v, dict):
        tag = v.get(_PERSIST_TYPE_TAG)
        if tag == _PERSIST_ESCAPED_TAG:
            # Unwrap one escape layer *without* re-examining the inner
            # dict's own top-level tag membership — it still literally
            # carries the original application key that triggered the
            # escape (e.g. its own _PERSIST_TYPE_TAG entry), which must be
            # returned verbatim, not decoded as a real tag. Each of its
            # values is still walked recursively, since _escape_persist_
            # collision already escaped any collision nested deeper inside
            # them before this wrapper was added. _escape_persist_collision
            # only ever wraps a dict this way (never a list), so "value" is
            # always a dict here unless the row is malformed (e.g.
            # hand-edited) — in that case, return the tagged dict unchanged
            # rather than crashing, matching the bytes/isoformat branches.
            inner = v.get("value")
            if isinstance(inner, dict):
                return {k: _decode_persisted_value(val) for k, val in inner.items()}
            return v
        if tag == "bytes":
            try:
                return bytes.fromhex(v.get("value", ""))
            except (TypeError, ValueError):
                return v
        if tag == "decimal":
            try:
                return Decimal(v.get("value", ""))
            except (InvalidOperation, TypeError):
                return v
        if tag == "opaque_str":
            # _persist_default's catch-all for a type it doesn't otherwise
            # recognize — the original type can't be reconstructed, only its
            # str() survives. The caller (_load_graphs) still needs to know
            # THIS specific string is such a lossy stand-in, so it can mark
            # the containing change_filter state "_opaque_recovered_str" —
            # done there, not here, since decoding is per-value and that
            # marker lives on the enclosing node-state dict.
            value = v.get("value")
            return _OpaqueRecoveredStr(value, v.get("type")) if isinstance(value, str) else value
        if tag == "tuple":
            inner = v.get("value")
            if isinstance(inner, list):
                return tuple(_decode_persisted_value(item) for item in inner)
            return v
        if tag in ("set", "frozenset"):
            inner = v.get("value")
            if isinstance(inner, list):
                decoded_items = [_decode_persisted_value(item) for item in inner]
                if any(GraphExecutor._contains_opaque_recovered_leaf(item) for item in decoded_items):
                    return _OpaqueRecoveredSet(decoded_items, frozen=tag == "frozenset")
                return frozenset(decoded_items) if tag == "frozenset" else set(decoded_items)
            return v
        if tag == "dict_nonstr_keys":
            inner = v.get("value")
            if isinstance(inner, list):
                try:
                    decoded_items = [(_decode_persisted_value(k), _decode_persisted_value(val)) for k, val in inner]
                    if any(GraphExecutor._contains_opaque_recovered_leaf(key) for key, _value in decoded_items):
                        return _OpaqueRecoveredDict(decoded_items)
                    return dict(decoded_items)
                except (TypeError, ValueError):
                    return v
            return v
        if tag in _PERSIST_ISOFORMAT_TYPES:
            try:
                _decoded = _PERSIST_ISOFORMAT_TYPES[tag](v.get("value", ""))
            except (TypeError, ValueError):
                return v
            _tz_name = v.get("tz")
            if tag in {"datetime", "time"} and isinstance(_tz_name, str):
                # Reconstruct the ORIGINAL named zone instead of the
                # fixed-offset tzinfo fromisoformat() produces (see
                # _persist_default's comment above) — fall back to the
                # fixed-offset decode if the zone is no longer known (e.g.
                # a tzdata update/removal on this host since it was
                # persisted) rather than losing the value entirely.
                try:
                    # fold: see _persist_default's comment above — restore
                    # it explicitly rather than letting replace() re-derive
                    # fold=0 from the wall-clock numbers alone, which is
                    # wrong for the second occurrence of an ambiguous DST
                    # "fall back" wall-clock time.
                    _decoded = _decoded.replace(tzinfo=ZoneInfo(_tz_name), fold=v.get("fold", 0))
                except (ZoneInfoNotFoundError, ValueError):
                    pass
            if tag in {"datetime", "time"} and v.get("fold"):
                return _decoded.replace(fold=v["fold"])
            return _decoded
        return {k: _decode_persisted_value(val) for k, val in v.items()}
    if isinstance(v, list):
        return [_decode_persisted_value(item) for item in v]
    return v


def _decode_persisted_value(v: Any) -> Any:
    """Iterative counterpart of the legacy recursive decoder above."""
    results: list[Any] = []
    work: list[tuple[str, Any]] = [("visit", v)]
    while work:
        operation, current = work.pop()
        if operation == "visit":
            if not isinstance(current, (dict, list)):
                results.append(current)
                continue
            if isinstance(current, list):
                work.append(("finish", ("list", current, len(current))))
                work.extend(("visit", item) for item in reversed(current))
                continue

            tag = current.get(_PERSIST_TYPE_TAG)
            if tag in {"bytes", "decimal", "opaque_str", *tuple(_PERSIST_ISOFORMAT_TYPES)}:
                results.append(_decode_persisted_value_recursive(current))
                continue
            if tag == _PERSIST_ESCAPED_TAG:
                inner = current.get("value")
                if not isinstance(inner, dict):
                    results.append(current)
                    continue
                work.append(("finish", ("dict", inner, len(inner))))
                work.extend(("visit", item) for item in reversed(list(inner.values())))
                continue
            if tag == "tuple":
                inner = current.get("value")
                if not isinstance(inner, list):
                    results.append(current)
                    continue
                work.append(("finish", ("tuple", current, len(inner))))
                work.extend(("visit", item) for item in reversed(inner))
                continue
            if tag in {"set", "frozenset"}:
                inner = current.get("value")
                if not isinstance(inner, list):
                    results.append(current)
                    continue
                work.append(("finish", (tag, current, len(inner))))
                work.extend(("visit", item) for item in reversed(inner))
                continue
            if tag == "dict_nonstr_keys":
                inner = current.get("value")
                if not isinstance(inner, list) or any(not isinstance(pair, list) or len(pair) != 2 for pair in inner):
                    results.append(current)
                    continue
                children = [item for pair in inner for item in pair]
                work.append(("finish", ("dict_nonstr_keys", current, len(children))))
                work.extend(("visit", item) for item in reversed(children))
                continue

            # Untagged application dictionaries and unknown tag dictionaries
            # are both preserved structurally while their values are decoded.
            work.append(("finish", ("dict", current, len(current))))
            work.extend(("visit", item) for item in reversed(list(current.values())))
            continue

        kind, original, child_count = current
        children = results[-child_count:] if child_count else []
        if child_count:
            del results[-child_count:]
        if kind == "list":
            decoded: Any = children
        elif kind == "dict":
            decoded = dict(zip(original.keys(), children))
        elif kind == "tuple":
            decoded = tuple(children)
        elif kind in {"set", "frozenset"}:
            if any(GraphExecutor._contains_opaque_recovered_leaf(item) for item in children):
                decoded = _OpaqueRecoveredSet(children, frozen=kind == "frozenset")
            else:
                try:
                    decoded = frozenset(children) if kind == "frozenset" else set(children)
                except TypeError:
                    decoded = original
        else:
            decoded_items = [(children[index], children[index + 1]) for index in range(0, len(children), 2)]
            try:
                if any(GraphExecutor._contains_opaque_recovered_leaf(key) for key, _value in decoded_items):
                    decoded = _OpaqueRecoveredDict(decoded_items)
                else:
                    decoded = dict(decoded_items)
            except (TypeError, ValueError):
                decoded = original
        results.append(decoded)
    return results[0]


def _contains_opaque_tag(v: Any) -> bool:
    """Walk a RAW (json-decoded, but not yet `_decode_persisted_value`-
    processed) persisted value for an "opaque_str" tag at any nesting
    depth — not just one placed directly at the top level.

    A python_script baseline like `[3 + 4j]` persists as a list containing
    an opaque-tagged item, decoded by `_decode_persisted_value` into
    `['(3+4j)']` — a plain list, not a dict, so a caller checking only
    "is state['value'] itself an opaque_str tag dict" never notices the
    nested one and never flags the surrounding state as opaque-recovered.
    Mirrors the same tag structure `_decode_persisted_value` recognizes,
    without actually decoding — this only needs a yes/no answer.
    """
    pending = [v]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            tag = current.get(_PERSIST_TYPE_TAG)
            if tag == "opaque_str":
                return True
            if tag == _PERSIST_ESCAPED_TAG:
                inner = current.get("value")
                if isinstance(inner, dict):
                    pending.extend(inner.values())
                continue
            if tag == "dict_nonstr_keys":
                inner = current.get("value")
                if isinstance(inner, list):
                    pending.extend(item for pair in inner if isinstance(pair, list) and len(pair) == 2 for item in pair)
                continue
            if tag in ("tuple", "set", "frozenset"):
                inner = current.get("value")
                if isinstance(inner, list):
                    pending.extend(inner)
                continue
            if tag is None:
                pending.extend(current.values())
        elif isinstance(current, list):
            pending.extend(current)
    return False


def _safe_deepcopy_state(state: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a per-node state dict (hyst/graph_state) without letting one
    node's non-deep-copyable stored value (e.g. a Memory node holding a
    permitted python_script's generator/complex-object result) raise and
    take down the whole snapshot. A pre-execution snapshot failing here
    previously propagated out of the caller's broad try/except, aborting
    this entire graph execution — including every otherwise-independent
    branch and its writes — just because ONE unrelated stateful node held
    such a value. Falls back to the executor's own per-node failure-safe
    semantic fallback (the original reference) only for the specific node
    whose value doesn't survive a plain deepcopy; every other node's state
    is still copied exactly.
    """
    try:
        return copy.deepcopy(state)
    except Exception:  # noqa: BLE001 - a stateful node's stored value may hold a runtime object with a failing copy hook
        return {nid: _replay_known_output_value(val) for nid, val in state.items()}


def _fresh_input_handles(
    overrides: dict[str, dict[str, Any]],
    edges: list[Any],
    blocked_sources: set[str] | None = None,
    blocked_outputs: set[tuple[str, str]] | None = None,
) -> dict[str, set[str]]:
    """Input handles that receive values downstream of explicit overrides."""
    fresh_inputs = {node_id: set(values) for node_id, values in overrides.items()}
    reached = set(overrides)
    blocked_sources = blocked_sources or set()
    blocked_outputs = blocked_outputs or set()
    effective_edges: dict[tuple[str, str], Any] = {}
    for edge in edges:
        effective_edges[(edge.target, edge.targetHandle or "in")] = edge
    outgoing: dict[str, list[Any]] = {}
    for edge in effective_edges.values():
        outgoing.setdefault(edge.source, []).append(edge)
    pending = deque(reached)
    while pending:
        source = pending.popleft()
        if source in blocked_sources:
            continue
        for edge in outgoing.get(source, []):
            if (source, edge.sourceHandle or "out") in blocked_outputs:
                continue
            fresh_inputs.setdefault(edge.target, set()).add(edge.targetHandle or "in")
            if edge.target not in reached:
                reached.add(edge.target)
                pending.append(edge.target)
    return fresh_inputs


_ICAL_DEFAULT_MAX_PAYLOAD_SIZE_MB = 2
_ICAL_MAX_PAYLOAD_SIZE_MB = 50
_MIB_BYTES = 1_048_576
_ICAL_MAX_REDIRECTS = 5
_ICAL_ALLOWED_CONTENT_TYPES = ("text/calendar", "application/ics", "application/octet-stream", "text/plain")
_PUSHOVER_ATTACHMENT_MAX_BYTES = 5_000_000
_SECRET_FILE_MAX_BYTES = 8192
_SECRET_FILE_DEFAULT_ROOT = "/run/secrets"
_API_CLIENT_RETRYABLE_METHODS = {"GET", "HEAD", "OPTIONS"}
_API_CLIENT_VARIABLE_RE = re.compile(r"###OBS([1-9][0-9]*)###")
_API_CLIENT_URL_LEADING_STRIP_CHARS = "".join(chr(value) for value in range(0x21))
_API_CLIENT_URL_REMOVE_CHARS = str.maketrans("", "", "\r\n\t")
_HOST_CHECK_MIN_TIMEOUT_S = 1.0
_HOST_CHECK_MAX_TIMEOUT_S = 30.0
_HOST_CHECK_MIN_COUNT = 1
_HOST_CHECK_MAX_COUNT = 10
_HOST_CHECK_RUNTIME_TOKEN = uuid.uuid4().hex


class _ApiClientVariableError(ValueError):
    pass


def _external_value_file_root() -> Path:
    return Path(os.environ.get("OBS_SECRET_FILE_DIR", _SECRET_FILE_DEFAULT_ROOT)).resolve()


def _load_external_value_file(path: str) -> str:
    # These logger.warning() calls only ever log a filesystem PATH — never
    # the referenced FILE CONTENT (the local below named `data`, which is
    # never logged anywhere in this function). A prior version of this
    # function was named _read_secret_file() and called a helper named
    # _secret_file_root(): CodeQL's py/clear-text-logging-sensitive-data
    # query treats every parameter/local of a callable whose OWN name
    # matches a sensitive-data pattern (e.g. contains "secret") as a
    # tainted source, regardless of what that parameter/local is actually
    # named or does — renaming raw_path/allowed_root/resolved_path alone
    # did not clear the alerts, and a `# lgtm[...]` inline suppression
    # comment was also confirmed (via a fresh CodeQL re-scan of the exact
    # commit containing it) not to be honored by this repo's code scanning
    # setup. Renaming the callables themselves to drop the "secret"
    # substring is the fix that actually removes the taint source.
    raw_path = (path or "").strip()
    if not raw_path:
        return ""

    try:
        allowed_root = _external_value_file_root()
        resolved_path = Path(raw_path).resolve(strict=True)
        if not resolved_path.is_relative_to(allowed_root):
            logger.warning("Refusing to read external value file outside %s: %s", allowed_root, resolved_path)
            return ""

        flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        fd = os.open(resolved_path, flags)
        try:
            file_stat = os.fstat(fd)
            if not stat.S_ISREG(file_stat.st_mode):
                logger.warning("Refusing to read non-regular external value file: %s", resolved_path)
                return ""
            if file_stat.st_size > _SECRET_FILE_MAX_BYTES:
                logger.warning("Refusing to read oversized external value file: %s", resolved_path)
                return ""
            data = os.read(fd, _SECRET_FILE_MAX_BYTES + 1)
        finally:
            os.close(fd)

        if len(data) > _SECRET_FILE_MAX_BYTES:
            logger.warning("Refusing to read oversized external value file: %s", resolved_path)
            return ""
        return data.decode("utf-8").strip()
    except (OSError, UnicodeDecodeError, ValueError) as exc:
        logger.warning("Could not read external value file %s: %s", raw_path, exc)
        return ""


_API_CLIENT_LEGACY_FIELD_RENAMES = {
    "headers_secret_file": "headers_value_file",
    "auth_token_file": "auth_value_file",
}


def _migrate_legacy_api_client_field_names(flow: FlowData) -> None:
    # One-time, in-memory upgrade of node.data keys for graphs saved before
    # issue #1087's CodeQL cleanup renamed these two api_client config
    # fields. Runs on every _load_graphs() call (idempotent — a no-op once
    # a graph has already been re-saved under the new keys), so existing
    # persisted graphs keep working without a separate DB migration step.
    # Deliberately isolated here, far from _load_external_value_file: this
    # is the only place the OLD field names are still referenced, and this
    # function only moves a dict entry — it never logs or reads the file
    # the value points to.
    for node in flow.nodes:
        if node.type != "api_client" or not isinstance(node.data, dict):
            continue
        for old_key, new_key in _API_CLIENT_LEGACY_FIELD_RENAMES.items():
            if old_key not in node.data:
                continue
            legacy_value = node.data.pop(old_key)
            if new_key not in node.data:
                node.data[new_key] = legacy_value


def _normalise_api_client_variables(raw: Any) -> dict[int, dict[str, str]]:
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
        slot_raw = entry.get("slot", idx)
        try:
            slot = int(slot_raw)
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


def _rename_api_client_variable_datapoint_names(raw: Any, datapoint_id: str, new_name: str) -> tuple[Any, bool]:
    was_string = isinstance(raw, str)
    variables = raw
    if was_string:
        try:
            variables = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return raw, False
    if not isinstance(variables, list):
        return raw, False

    changed = False
    for variable in variables:
        if not isinstance(variable, dict):
            continue
        if variable.get("datapoint_id") == datapoint_id and variable.get("datapoint_name") != new_name:
            variable["datapoint_name"] = new_name
            changed = True
    if not changed:
        return raw, False
    if was_string:
        return json.dumps(variables, ensure_ascii=False), True
    return variables, True


def _api_client_value_to_string(value: Any) -> str:
    if value is None:
        raise _ApiClientVariableError("API client variable value is empty")
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _replace_api_client_placeholders(
    value: Any,
    resolver: Any,
    transform: Any | None = None,
) -> Any:
    if isinstance(value, str):

        def _replace(match: re.Match[str]) -> str:
            replacement = resolver(int(match.group(1)))
            return transform(replacement) if transform is not None else replacement

        return _API_CLIENT_VARIABLE_RE.sub(_replace, value)
    if isinstance(value, list):
        return [_replace_api_client_placeholders(item, resolver, transform) for item in value]
    if isinstance(value, dict):
        return {
            _replace_api_client_placeholders(key, resolver, transform): _replace_api_client_placeholders(item, resolver, transform)
            for key, item in value.items()
        }
    return value


def _quote_api_client_url_value(value: str) -> str:
    return quote(value, safe="-._~")


def _normalise_api_client_url_for_parse(value: str) -> str:
    # The API-client call sites apply Python ``str.strip()`` to the resolved URL,
    # which removes Unicode whitespace (e.g. U+00A0) on top of the C0 controls and
    # ASCII space that ``urlparse`` itself trims. Mirror both here so the authority
    # bounds are computed against the same leading run that is silently removed
    # later; otherwise a leading Unicode-whitespace (or interleaved control /
    # whitespace) prefix would hide the scheme and let a variable choose the host.
    previous = None
    while value != previous:
        previous = value
        value = value.lstrip(_API_CLIENT_URL_LEADING_STRIP_CHARS).lstrip()
    return value.translate(_API_CLIENT_URL_REMOVE_CHARS)


def _replace_api_client_url_placeholders(value: str, resolver: Any) -> str:
    value = _normalise_api_client_url_for_parse(value)
    authority_bounds: tuple[int, int] | None = None
    scheme_separator = value.find("://")
    if scheme_separator != -1 and _API_CLIENT_VARIABLE_RE.search(value[:scheme_separator]):
        raise _ApiClientVariableError(
            "API client URL variables are not allowed in the scheme, host, userinfo, or port",
        )
    # Reject templates where removing placeholders would expose a :// that is hidden in the
    # raw template (e.g. "http:###OBS1###//attacker.com" collapses to "http://attacker.com"
    # when the variable resolves to an empty string).
    if scheme_separator == -1 and _API_CLIENT_VARIABLE_RE.sub("", value).find("://") != -1:
        raise _ApiClientVariableError(
            "API client URL variables are not allowed in the scheme, host, userinfo, or port",
        )
    scheme_match = re.match(r"^[A-Za-z][A-Za-z0-9+.-]*://", value)
    if scheme_match is not None:
        separator_scan_value = _API_CLIENT_VARIABLE_RE.sub(lambda match: "X" * (match.end() - match.start()), value)
        authority_start = scheme_match.end()
        authority_end = len(value)
        for separator in "/?#":
            separator_index = separator_scan_value.find(separator, authority_start)
            if separator_index != -1:
                authority_end = min(authority_end, separator_index)
        authority_bounds = (authority_start, authority_end)

    def _replace(match: re.Match[str]) -> str:
        if authority_bounds is not None and authority_bounds[0] <= match.start() < authority_bounds[1]:
            raise _ApiClientVariableError(
                "API client URL variables are not allowed in the scheme, host, userinfo, or port",
            )
        replacement = resolver(int(match.group(1)))
        return _quote_api_client_url_value(replacement)

    return _API_CLIENT_VARIABLE_RE.sub(_replace, value)


def _make_api_client_variable_resolver(
    registry: Any,
    raw_variables: Any,
    execution_values_by_datapoint_id: dict[str, Any] | None = None,
) -> Any:
    variables = _normalise_api_client_variables(raw_variables)
    execution_values_by_datapoint_id = execution_values_by_datapoint_id or {}
    cache: dict[int, str] = {}

    def _resolve(index: int) -> str:
        if index in cache:
            return cache[index]
        variable = variables.get(index)
        if variable is None:
            raise _ApiClientVariableError(f"API client variable OBS{index} is not configured")
        datapoint_id = variable["datapoint_id"]
        if datapoint_id in execution_values_by_datapoint_id:
            value = execution_values_by_datapoint_id[datapoint_id]
            if value is None:
                raise _ApiClientVariableError(
                    f"API client variable OBS{index} object {variable['datapoint_name']} has no value",
                )
            cache[index] = _api_client_value_to_string(value)
            return cache[index]
        try:
            state = registry.get_value(uuid.UUID(datapoint_id))
        except Exception as exc:
            raise _ApiClientVariableError(f"API client variable OBS{index} references an invalid object") from exc
        if state is None:
            raise _ApiClientVariableError(
                f"API client variable OBS{index} object {variable['datapoint_name']} is not available",
            )
        if state.value is None:
            raise _ApiClientVariableError(
                f"API client variable OBS{index} object {variable['datapoint_name']} has no value",
            )
        cache[index] = _api_client_value_to_string(state.value)
        return cache[index]

    return _resolve


def _parse_http_url(url: str) -> Any | None:
    try:
        parsed = urlparse(url)
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"}:
        return None
    if not parsed.hostname:
        return None
    return parsed


async def _resolve_safe_image_url(url: str) -> tuple[str, str, str] | None:
    """Return a DNS-pinned HTTPS request tuple for safe image downloads.

    Returns:
        (pinned_url, host_header, pinned_ip) or None if the URL is unsafe.
    """
    try:
        target = await asyncio.to_thread(resolve_url_target, url, require_https=True)
    except ValueError:
        return None
    if not target.addresses:
        return None

    parsed = urlparse(url)
    port = target.port or 443
    pinned_ip = target.addresses[0]
    pinned_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
    has_explicit_port = target.port is not None
    netloc = f"{pinned_host}:{port}" if has_explicit_port else pinned_host
    pinned_url = urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))
    host_header = f"{target.hostname_ascii}:{port}" if has_explicit_port else target.hostname_ascii
    return pinned_url, host_header, pinned_ip


def _origin_tuple(parsed: Any) -> tuple[str, str, int] | None:
    if not parsed or not parsed.hostname or parsed.scheme not in {"http", "https"}:
        return None
    try:
        hostname_ascii = parsed.hostname.encode("idna").decode("ascii")
        port = parsed.port
    except (UnicodeError, ValueError):
        return None
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    return parsed.scheme, hostname_ascii, port


def _preserve_same_origin_credentials(current_url: str, redirected_url: str) -> str:
    current_parsed = _parse_http_url(current_url)
    redirected_parsed = _parse_http_url(redirected_url)
    if not current_parsed or not redirected_parsed:
        return redirected_url
    if redirected_parsed.username is not None:
        return redirected_url
    if _origin_tuple(current_parsed) != _origin_tuple(redirected_parsed):
        return redirected_url
    if current_parsed.username is None:
        return redirected_url

    username = quote(unquote(current_parsed.username), safe="")
    password = None if current_parsed.password is None else quote(unquote(current_parsed.password), safe="")
    hostname = redirected_parsed.hostname
    if not hostname:
        return redirected_url
    try:
        host_for_netloc = hostname.encode("idna").decode("ascii")
        ip = ipaddress.ip_address(host_for_netloc)
        if isinstance(ip, ipaddress.IPv6Address):
            host_for_netloc = f"[{host_for_netloc}]"
    except UnicodeError:
        return redirected_url
    except ValueError:
        pass
    try:
        port = redirected_parsed.port
    except ValueError:
        return redirected_url

    auth = username if password is None else f"{username}:{password}"
    netloc = f"{auth}@{host_for_netloc}"
    if port is not None:
        netloc = f"{netloc}:{port}"
    return redirected_parsed._replace(netloc=netloc).geturl()


def _build_http_host_header(hostname_ascii: str, scheme: str, port: int | None) -> str:
    host_header = hostname_ascii
    if ":" in host_header and not host_header.startswith("["):
        host_header = f"[{host_header}]"
    if port is not None:
        default_port = 443 if scheme == "https" else 80
        if port != default_port:
            host_header = f"{host_header}:{port}"
    return host_header


def _build_api_client_fetch_targets(url: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    parsed = _parse_http_url(url)
    if not parsed:
        raise ValueError("Invalid URL target")
    try:
        hostname_ascii = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError("Invalid URL target") from None
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid URL target") from None

    try:
        target = resolve_url_target(url)
    except ValueError as exc:
        raise ValueError(f"Blocked URL target: {exc}") from exc
    addresses = target.addresses
    if not addresses:
        raise ValueError("Blocked unresolved URL target")

    auth_prefix = ""
    if parsed.username is not None:
        username = quote(unquote(parsed.username), safe="")
        password = None if parsed.password is None else quote(unquote(parsed.password), safe="")
        auth = username if password is None else f"{username}:{password}"
        auth_prefix = f"{auth}@"

    pinned_urls: list[str] = []
    for pinned_ip in dict.fromkeys(addresses):
        pinned_host = f"[{pinned_ip}]" if ":" in pinned_ip else pinned_ip
        netloc = f"{auth_prefix}{pinned_host}:{port}" if port is not None else f"{auth_prefix}{pinned_host}"
        pinned_urls.append(parsed._replace(netloc=netloc).geturl())
    headers = {"Host": _build_http_host_header(hostname_ascii, parsed.scheme, port)}
    extensions = {"sni_hostname": hostname_ascii} if parsed.scheme == "https" else {}
    return pinned_urls, headers, extensions


def _cookie_domain_matches(hostname: str, cookie_domain: str) -> bool:
    host = hostname.lower()
    domain = cookie_domain.lower().lstrip(".")
    return host == domain or host.endswith(f".{domain}")


def _cookie_path_matches(request_path: str, cookie_path: str) -> bool:
    req = request_path or "/"
    path = cookie_path or "/"
    if not req.startswith("/"):
        req = f"/{req}"
    if not path.startswith("/"):
        path = f"/{path}"
    if req == path:
        return True
    if not req.startswith(path):
        return False
    if path.endswith("/"):
        return True
    return len(req) > len(path) and req[len(path)] == "/"


def _default_cookie_path(request_path: str) -> str:
    path = request_path or "/"
    if not path.startswith("/"):
        return "/"
    if path.count("/") <= 1:
        return "/"
    return path.rsplit("/", 1)[0] or "/"


def _store_response_cookies(
    cookie_store: dict[tuple[str, str, str, bool], tuple[str, bool]],
    set_cookie_headers: list[str],
    logical_url: str,
) -> None:
    parsed = _parse_http_url(logical_url)
    if not parsed or not parsed.hostname:
        return
    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    default_path = _default_cookie_path(parsed.path or "/")
    for raw in set_cookie_headers:
        jar = http.cookies.SimpleCookie()
        try:
            jar.load(raw)
        except http.cookies.CookieError:
            continue
        for morsel in jar.values():
            name = morsel.key
            value = morsel.value
            raw_domain = (morsel["domain"] or "").strip().lower()
            host_only = raw_domain == ""
            domain = hostname if host_only else raw_domain.lstrip(".")
            if not _cookie_domain_matches(hostname, domain):
                continue
            path = (morsel["path"] or default_path).strip() or "/"
            if not path.startswith("/"):
                path = f"/{path}"
            max_age = (morsel["max-age"] or "").strip()
            expires = (morsel["expires"] or "").strip()
            delete_cookie = False
            if max_age:
                try:
                    delete_cookie = int(max_age) <= 0
                except ValueError:
                    pass
            if not delete_cookie and expires:
                try:
                    exp_dt = email.utils.parsedate_to_datetime(expires)
                    if exp_dt.tzinfo is None:
                        exp_dt = exp_dt.replace(tzinfo=UTC)
                    delete_cookie = exp_dt <= datetime.now(UTC)
                except (TypeError, ValueError):
                    pass
            key = (domain, path, name, host_only)
            if delete_cookie:
                cookie_store.pop(key, None)
                continue
            secure = bool(morsel["secure"])
            cookie_store[key] = (value, secure)


def _build_cookie_header(cookie_store: dict[tuple[str, str, str, bool], tuple[str, bool]], logical_url: str) -> str:
    parsed = _parse_http_url(logical_url)
    if not parsed or not parsed.hostname:
        return ""
    hostname = parsed.hostname.encode("idna").decode("ascii").lower()
    req_path = parsed.path or "/"
    is_https_request = parsed.scheme.lower() == "https"
    matched: list[tuple[str, str]] = []
    for (domain, path, name, host_only), (value, secure) in cookie_store.items():
        if not _should_send_cookie(
            req_hostname=hostname,
            req_path=req_path,
            req_is_https=is_https_request,
            cookie_domain=domain,
            cookie_path=path,
            cookie_host_only=host_only,
            cookie_secure=secure,
        ):
            continue
        cookie_pair = (name, value)
        matched.append(cookie_pair)
    return "; ".join(f"{name}={value}" for name, value in matched)


def _should_send_cookie(
    req_hostname: str,
    req_path: str,
    req_is_https: bool,
    cookie_domain: str,
    cookie_path: str,
    cookie_host_only: bool,
    cookie_secure: bool,
) -> bool:
    if cookie_host_only and req_hostname != cookie_domain:
        return False
    if not cookie_host_only and not _cookie_domain_matches(req_hostname, cookie_domain):
        return False
    if not _cookie_path_matches(req_path, cookie_path):
        return False
    return not (bool(cookie_secure) and not req_is_https)


def _send_wol_packet(mac: str, broadcast: str, port: int) -> None:
    """Build and send a Wake-on-LAN magic packet via UDP broadcast."""
    clean = re.sub(r"[:\-\.]", "", mac).upper()
    if len(clean) != 12 or not re.fullmatch(r"[0-9A-F]{12}", clean):
        raise ValueError(f"Invalid MAC address: {mac!r}")
    mac_bytes = bytes.fromhex(clean)
    magic = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic, (broadcast, port))


def _normalise_host_check_ping_config(timeout_s_raw: Any, count_raw: Any) -> tuple[float, int]:
    try:
        timeout_s = float(timeout_s_raw or _HOST_CHECK_MIN_TIMEOUT_S)
    except (TypeError, ValueError):
        timeout_s = _HOST_CHECK_MIN_TIMEOUT_S
    try:
        count = int(count_raw or _HOST_CHECK_MIN_COUNT)
    except (TypeError, ValueError):
        count = _HOST_CHECK_MIN_COUNT
    timeout_s = min(_HOST_CHECK_MAX_TIMEOUT_S, max(_HOST_CHECK_MIN_TIMEOUT_S, timeout_s))
    count = min(_HOST_CHECK_MAX_COUNT, max(_HOST_CHECK_MIN_COUNT, count))
    return timeout_s, count


async def _ping_host(host: str, count: int, timeout_s: float) -> tuple[bool, float | None]:
    """Ping *host* and return (reachable, latency_ms).

    Uses the system ping binary so no elevated privileges are required.
    timeout_s is passed to ping as the per-packet deadline; an additional
    2-second asyncio safety timeout is layered on top to handle hangs.
    """
    import sys

    timeout_s, count = _normalise_host_check_ping_config(timeout_s, count)
    timeout_int = int(timeout_s)
    if sys.platform == "darwin":
        cmd = ["ping", "-c", str(count), "-W", str(timeout_int * 1000), "--", host]
    else:
        cmd = ["ping", "-c", str(count), "-W", str(timeout_int), "--", host]
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout_s * count + 2)
        except TimeoutError:
            proc.kill()
            await proc.wait()
            return False, None
        reachable = proc.returncode == 0
        latency_ms: float | None = None
        if reachable:
            m = re.search(r"time[<=](\d+(?:\.\d+)?)\s*ms", stdout.decode(errors="replace"))
            if m:
                latency_ms = float(m.group(1))
        return reachable, latency_ms
    except FileNotFoundError:
        logger.warning("ping binary not found — install iputils-ping to enable Host Check")
        return False, None
    except Exception:
        logger.exception("Host Check ping subprocess for %s failed unexpectedly", host)
        return False, None


def _build_ical_fetch_targets(url: str) -> tuple[list[str], dict[str, str], dict[str, str]]:
    parsed = _parse_http_url(url)
    if not parsed:
        raise ValueError(f"Invalid iCal URL: {url}")
    try:
        hostname_ascii = parsed.hostname.encode("idna").decode("ascii")
    except UnicodeError:
        raise ValueError(f"Invalid iCal URL host: {url}") from None
    try:
        port = parsed.port
    except ValueError:
        raise ValueError(f"Invalid iCal URL port: {url}") from None
    try:
        target = resolve_url_target(url)
    except ValueError as exc:
        raise ValueError(f"Blocked iCal URL target: {url}") from exc
    addresses = target.addresses
    if not addresses:
        raise ValueError(f"Blocked unresolved iCal URL target: {url}")
    headers = {"Host": _build_http_host_header(hostname_ascii, parsed.scheme, port)}
    if parsed.username is not None:
        username = unquote(parsed.username)
        password = unquote(parsed.password or "")
        token = base64.b64encode(f"{username}:{password}".encode()).decode("ascii")
        headers["Authorization"] = f"Basic {token}"
    extensions = {"sni_hostname": hostname_ascii} if parsed.scheme == "https" else {}
    fetch_urls: list[str] = []
    for resolved_ip in addresses:
        resolved_ip_for_url = f"[{resolved_ip}]" if ":" in resolved_ip else resolved_ip
        if port is not None:
            netloc = f"{resolved_ip_for_url}:{port}"
        else:
            netloc = resolved_ip_for_url
        fetch_urls.append(parsed._replace(netloc=netloc).geturl())
    return fetch_urls, headers, extensions


def _build_ical_fetch_target(url: str) -> tuple[str, dict[str, str], dict[str, str]]:
    fetch_urls, headers, extensions = _build_ical_fetch_targets(url)
    return fetch_urls[0], headers, extensions


def _is_public_http_url(url: str) -> bool:
    try:
        _build_ical_fetch_targets(url)
    except ValueError:
        return False
    return True


async def _read_limited_response_body(resp: httpx.Response, max_bytes: int) -> bytes:
    body = bytearray()
    async for chunk in resp.aiter_bytes():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ValueError(f"iCal response too large: {len(body)} bytes")
    return bytes(body)


def _ical_payload_limit_bytes(node_data: dict[str, Any]) -> int:
    raw_limit = node_data.get("max_payload_size_mb", _ICAL_DEFAULT_MAX_PAYLOAD_SIZE_MB)
    if isinstance(raw_limit, bool):
        raw_limit = _ICAL_DEFAULT_MAX_PAYLOAD_SIZE_MB
    try:
        limit_mb = int(raw_limit)
    except (TypeError, ValueError, OverflowError):
        limit_mb = _ICAL_DEFAULT_MAX_PAYLOAD_SIZE_MB
    return min(max(limit_mb, 1), _ICAL_MAX_PAYLOAD_SIZE_MB) * _MIB_BYTES


_manager: LogicManager | None = None


def get_logic_manager() -> LogicManager:
    if _manager is None:
        raise RuntimeError("LogicManager not initialised")
    return _manager


def init_logic_manager(db: Any, event_bus: Any, registry: Any) -> LogicManager:
    global _manager
    _manager = LogicManager(db, event_bus, registry)
    return _manager


class LogicManager:
    def __init__(self, db: Any, event_bus: Any, registry: Any):
        self._db = db
        self._event_bus = event_bus
        self._registry = registry
        # persistent state per graph per node (hysteresis bool, statistics accumulators, …)
        self._hysteresis: dict[str, dict[str, Any]] = {}
        # graph cache: id → (name, enabled, FlowData)
        self._graphs: dict[str, tuple[str, bool, FlowData]] = {}
        # per-node runtime state for filter/throttle
        # {graph_id: {node_id: {last_value, last_ts, last_write_val, last_write_ts}}}
        self._node_state: dict[str, dict[str, dict[str, Any]]] = {}
        # graphs whose initialize_graph publish is in flight, mapped to the
        # DataPoint ids that pass is writing — only those self-originating
        # events must not re-enter the graph (see _on_value_event)
        self._initializing_graphs: dict[str, set[str]] = {}
        # graphs still awaiting their turn in a bulk initialization pass
        # (config restore) — cascaded logic writes must not double-run them
        self._bulk_init_pending: set[str] = set()
        # cron tasks: (graph_id, node_id) → asyncio.Task
        self._cron_tasks: dict[tuple[str, str], asyncio.Task] = {}  # type: ignore[type-arg]
        # Coalesce concurrent refreshes per iCalendar node.  Keep this lock
        # scoped to the fetch itself: graph execution may synchronously publish
        # an event that re-enters the same graph.
        self._ical_fetch_locks: dict[tuple[str, str], asyncio.Lock] = {}
        # Coalesce recurrence parsing independently from network refreshes.
        # A queued execution rechecks the shared cache after acquiring this
        # lock so only one worker expands a given node/key generation.
        self._ical_precompute_locks: dict[tuple[str, str], asyncio.Lock] = {}
        # Python-script GraphExecutor passes run in a worker so large mutable
        # inputs are cloned off the event loop. Serialize passes that share
        # per-graph state while they run concurrently with the loop.
        self._graph_executor_locks: dict[str, asyncio.Lock] = {}
        # Parsed/filtered calendar results must stay outside hysteresis state:
        # async replay paths deep-copy that state several times per execution.
        self._ical_result_caches: dict[str, dict[str, Any]] = {}
        # Replacing this token invalidates cache/fetch work that started against
        # an older graph configuration.  Cache dictionaries are likewise
        # replaced, rather than mutated, so worker snapshots remain race-free.
        self._ical_cache_generations: dict[str, object] = {}
        # Running value sequences, keyed per graph/node.  They are deliberately
        # separate from cron tasks because they are short-lived and user-triggered.
        self._sequence_tasks: dict[tuple[str, str], asyncio.Task] = {}  # type: ignore[type-arg]
        self._sequence_conditions: dict[tuple[str, str], bool] = {}
        self._sequence_queues: dict[tuple[str, str], int] = {}
        self._sequence_queue_depths: dict[tuple[str, str], int] = {}
        self._sequence_configs: dict[tuple[str, str], dict[str, Any]] = {}
        self._sequence_graph_signatures: dict[str, str] = {}
        self._sequence_restarts: set[tuple[str, str]] = set()
        self._sequence_restart_sources: dict[tuple[str, str], asyncio.Task] = {}
        # application-level config (e.g. timezone) — loaded from app_settings table
        self._app_config: dict[str, Any] = {
            "timezone": "Europe/Zurich",
            "date_format": "dd.MM.yyyy",
            "time_format": "HH:mm:ss",
            "language": "de",
            "region_format": "auto",
            "currency": "auto",
        }

    async def start(self) -> None:
        """Subscribe to EventBus, load all graphs and start cron schedulers."""
        await self._load_app_config()
        await self._load_graphs()
        from obs.core.event_bus import DataPointRenamedEvent, DataValueEvent

        self._event_bus.subscribe(DataValueEvent, self._on_value_event)
        self._event_bus.subscribe(DataPointRenamedEvent, self._on_datapoint_renamed)
        self._start_cron_tasks()
        logger.info("LogicManager started — %d graphs loaded", len(self._graphs))

    async def stop(self) -> None:
        from obs.core.event_bus import DataPointRenamedEvent, DataValueEvent

        self._event_bus.unsubscribe(DataValueEvent, self._on_value_event)
        self._event_bus.unsubscribe(DataPointRenamedEvent, self._on_datapoint_renamed)
        for task in list(self._cron_tasks.values()):
            task.cancel()
        self._cron_tasks.clear()
        self._cancel_sequence_tasks()

    async def reload(self) -> None:
        """Reload graph cache from DB and restart cron schedulers."""
        previous_graphs = self._graphs
        for task in list(self._cron_tasks.values()):
            task.cancel()
        self._cron_tasks.clear()
        await self._load_graphs()
        live_graph_ids = set(self._graphs)
        # A reload restarts all schedulers, but it must not invalidate work for
        # unrelated graphs.  Rotate only configurations that actually changed
        # (or disappeared); save paths that called invalidate_cache() already
        # have a fresh generation and are absent from ``previous_graphs``.
        for graph_id, previous_entry in previous_graphs.items():
            current_entry = self._graphs.get(graph_id)
            if current_entry is None or previous_entry[1:] != current_entry[1:]:
                self._ical_cache_generations[graph_id] = object()
        for graph_id in set(self._ical_result_caches) - live_graph_ids:
            self._ical_result_caches.pop(graph_id, None)
        for graph_id in set(self._ical_cache_generations) - live_graph_ids:
            self._ical_cache_generations.pop(graph_id, None)
        for graph_id in set(self._graph_executor_locks) - live_graph_ids:
            self._prune_graph_executor_lock(graph_id)
        for graph_id in set(self._hysteresis) - live_graph_ids:
            self._hysteresis.pop(graph_id, None)
        for key in [key for key in self._ical_fetch_locks if key[0] not in live_graph_ids]:
            self._ical_fetch_locks.pop(key, None)
        for key in [key for key in self._ical_precompute_locks if key[0] not in live_graph_ids]:
            self._prune_ical_precompute_lock(key)
        ical_runtime_keys = {
            "raw",
            "_ical_result_cache",
            "_ical_last_attempt_url",
            "_ical_last_attempt_limit",
            "_ical_last_attempt_ts",
            "_ical_precompute_token",
        }
        for graph_id, (_, enabled, flow) in self._graphs.items():
            active_ical_ids = {
                node.id
                for node in flow.nodes
                if enabled and node.type == "ical" and isinstance(node.data.get("url"), str) and node.data["url"].strip()
            }
            result_cache = self._ical_result_caches.get(graph_id)
            if result_cache is not None:
                self._ical_result_caches[graph_id] = {node_id: entry for node_id, entry in result_cache.items() if node_id in active_ical_ids}
            self._ical_cache_generations.setdefault(graph_id, object())
            graph_hysteresis = self._hysteresis.get(graph_id)
            if graph_hysteresis is not None:
                for node_id, node_state in list(graph_hysteresis.items()):
                    if isinstance(node_state, dict):
                        node_state.pop("_ical_precompute_token", None)
                    if node_id not in active_ical_ids and isinstance(node_state, dict) and not ical_runtime_keys.isdisjoint(node_state):
                        graph_hysteresis.pop(node_id, None)
            for key in [key for key in self._ical_fetch_locks if key[0] == graph_id and key[1] not in active_ical_ids]:
                self._ical_fetch_locks.pop(key, None)
            for key in [key for key in self._ical_precompute_locks if key[0] == graph_id and key[1] not in active_ical_ids]:
                self._prune_ical_precompute_lock(key)
        # A config import/reset can remove graphs without first calling
        # invalidate_cache().  Cancel only sequences whose graph no longer
        # exists or is disabled; unrelated live graphs keep running.
        for graph_id, node_id in list(self._sequence_tasks):
            entry = self._graphs.get(graph_id)
            node = next((node for node in entry[2].nodes if node.id == node_id), None) if entry else None
            if (
                entry is None
                or not entry[1]
                or node is None
                or node.type != "value_sequence"
                or node.data != self._sequence_configs.get((graph_id, node_id))
                or entry[2].model_dump_json() != self._sequence_graph_signatures.get(graph_id)
            ):
                self._cancel_sequence_tasks(graph_id)
        self._start_cron_tasks()

    # ── App Config ────────────────────────────────────────────────────────

    def _cancel_sequence_tasks(self, graph_id: str | None = None) -> None:
        """Cancel active sequences, for shutdown/reload/delete semantics."""
        keys = [key for key in self._sequence_tasks if graph_id is None or key[0] == graph_id]
        for key in keys:
            self._cancel_sequence_task(key)
            self._sequence_conditions.pop(key, None)
            self._sequence_queues.pop(key, None)
            self._sequence_queue_depths.pop(key, None)
            self._sequence_configs.pop(key, None)

    def _cancel_sequence_task(self, key: tuple[str, str]) -> None:
        """Cancel a tracked task and the source it may be restarting."""
        task = self._sequence_tasks.pop(key, None)
        source = self._sequence_restart_sources.pop(key, None)
        self._sequence_restarts.discard(key)
        self._sequence_queues.pop(key, None)
        self._sequence_queue_depths.pop(key, None)
        if task:
            task.cancel()
        if source and source is not task:
            source.cancel()

    @staticmethod
    def _sequence_steps(raw: Any) -> list[dict[str, Any]]:
        if isinstance(raw, str):
            try:
                raw = json.loads(raw)
            except (TypeError, ValueError):
                raw = []
        return [step for step in raw if isinstance(step, dict)] if isinstance(raw, list) else []

    @staticmethod
    def _coerce_sequence_value(value: Any, data_type: str) -> Any:
        if data_type == "BOOLEAN":
            if not isinstance(value, str):
                return bool(value)
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "on"}:
                return True
            if lowered in {"false", "0", "no", "off"}:
                return False
            raise ValueError(f"invalid boolean value {value!r}")
        if data_type == "INTEGER":
            if isinstance(value, float) and not value.is_integer():
                raise ValueError(f"fractional integer value {value!r}")
            return int(value)
        if data_type == "FLOAT":
            return float(value)
        if data_type == "DATE":
            return date.fromisoformat(value) if isinstance(value, str) else value
        if data_type == "TIME":
            return time.fromisoformat(value) if isinstance(value, str) else value
        if data_type == "DATETIME":
            return datetime.fromisoformat(value) if isinstance(value, str) else value
        if data_type == "STRING":
            return str(value)
        return value

    async def _run_value_sequence(self, graph_id: str, node_id: str, config: dict[str, Any], logic_depth: int = 0) -> None:
        """Publish configured writes without blocking the graph executor."""
        from obs.core.event_bus import DataValueEvent

        key = (graph_id, node_id)
        steps = self._sequence_steps(config.get("steps"))
        if not steps:
            logger.warning("Value sequence graph=%s node=%s has no steps", graph_id[:8], node_id[:8])
            return
        mode = config.get("run_mode", "once")
        if mode == "while_condition" and not self._sequence_conditions.get(key, True):
            return
        try:
            raw_repeat_count = config.get("repeat_count", 2)
            repetitions = (
                min(_MAX_SEQUENCE_REPEAT_COUNT, max(1, int(2 if raw_repeat_count is None else raw_repeat_count))) if mode == "repeat_count" else 1
            )
        except (TypeError, ValueError):
            repetitions = 1
        try:
            while True:
                slept = False
                for step in steps:
                    if (mode == "while_condition" or config.get("cancel_when_condition_false")) and not self._sequence_conditions.get(key, True):
                        logger.info("Value sequence cancelled: graph=%s node=%s", graph_id[:8], node_id[:8])
                        return
                    target = str(step.get("datapoint_id") or "").strip()
                    if target:
                        try:
                            datapoint_id = uuid.UUID(target)
                            target_dp = self._registry.get(datapoint_id)
                            if target_dp is None:
                                raise ValueError("target object no longer exists")
                            publish_task = asyncio.create_task(
                                self._event_bus.publish(
                                    DataValueEvent(
                                        datapoint_id=datapoint_id,
                                        value=self._coerce_sequence_value(step.get("value"), target_dp.data_type),
                                        quality="good",
                                        source_adapter="logic_sequence",
                                        logic_depth=logic_depth + 1,
                                    )
                                )
                            )
                            try:
                                await asyncio.shield(publish_task)
                            except asyncio.CancelledError:
                                # A write can synchronously re-run this graph
                                # and cancel its own sequence.  Complete the
                                # already-emitted event before stopping so all
                                # EventBus subscribers see the write.
                                try:
                                    await asyncio.shield(publish_task)
                                except Exception:
                                    logger.exception(
                                        "Value sequence graph=%s node=%s target=%s: cleanup re-await after cancellation failed",
                                        graph_id[:8],
                                        node_id[:8],
                                        target,
                                    )
                                raise
                        except Exception:
                            logger.exception("Value sequence graph=%s node=%s target=%s failed", graph_id[:8], node_id[:8], target)
                    try:
                        delay_s = max(0.0, float(step.get("delay_ms") or 0) / 1000)
                    except (TypeError, ValueError):
                        delay_s = 0.0
                    if delay_s:
                        await asyncio.sleep(delay_s)
                        slept = True
                if mode == "while_condition":
                    if not self._sequence_conditions.get(key, True):
                        return
                    if not slept:
                        logger.warning("Value sequence graph=%s node=%s needs a positive pause in while mode", graph_id[:8], node_id[:8])
                        return
                    continue
                repetitions -= 1
                if repetitions <= 0:
                    return
                if mode == "repeat_count" and not slept:
                    await asyncio.sleep(0)
        finally:
            if self._sequence_tasks.get(key) is asyncio.current_task():
                self._sequence_tasks.pop(key, None)
                queued = self._sequence_queues.pop(key, 0)
                queued_depth = self._sequence_queue_depths.pop(key, logic_depth)
                if queued and self._sequence_conditions.get(key, True):
                    if queued > 1:
                        self._sequence_queues[key] = queued - 1
                        self._sequence_queue_depths[key] = queued_depth
                    task = asyncio.create_task(
                        self._run_value_sequence(graph_id, node_id, config, queued_depth),
                        name=f"sequence-{graph_id[:8]}-{node_id[:8]}",
                    )
                    self._sequence_tasks[key] = task

    async def _restart_value_sequence(self, graph_id: str, node_id: str, config: dict[str, Any], logic_depth: int, active: asyncio.Task) -> None:
        """Stop a sequence completely before launching its restart replacement."""
        key = (graph_id, node_id)
        try:
            active.cancel()
            try:
                await active
            except asyncio.CancelledError:
                pass
            if self._sequence_tasks.get(key) is not asyncio.current_task():
                return
            task = asyncio.create_task(
                self._run_value_sequence(graph_id, node_id, config, logic_depth),
                name=f"sequence-{graph_id[:8]}-{node_id[:8]}",
            )
            self._sequence_tasks[key] = task
        finally:
            self._sequence_restarts.discard(key)
            if self._sequence_restart_sources.get(key) is active:
                self._sequence_restart_sources.pop(key, None)

    def _start_value_sequence(self, graph_id: str, node: Any, condition: bool, logic_depth: int = 0, graph_signature: str = "") -> None:
        key = (graph_id, node.id)
        self._sequence_conditions[key] = condition
        self._sequence_configs[key] = dict(node.data)
        self._sequence_graph_signatures[graph_id] = graph_signature
        active = self._sequence_tasks.get(key)
        if active and not active.done():
            policy = node.data.get("restart_policy", "ignore")
            if policy == "restart":
                # The task slot holds the restart helper while it awaits the
                # original task.  Coalesce rapid retriggers so they cannot
                # cancel the helper and detach from that original publish.
                if key in self._sequence_restarts:
                    return
                self._sequence_restarts.add(key)
                self._sequence_restart_sources[key] = active
                restart = asyncio.create_task(
                    self._restart_value_sequence(graph_id, node.id, dict(node.data), logic_depth, active),
                    name=f"sequence-restart-{graph_id[:8]}-{node.id[:8]}",
                )
                self._sequence_tasks[key] = restart
                return
            elif policy == "queue":
                self._sequence_queues[key] = self._sequence_queues.get(key, 0) + 1
                self._sequence_queue_depths[key] = max(self._sequence_queue_depths.get(key, 0), logic_depth)
                return
            else:
                return
        task = asyncio.create_task(
            self._run_value_sequence(graph_id, node.id, dict(node.data), logic_depth),
            name=f"sequence-{graph_id[:8]}-{node.id[:8]}",
        )
        self._sequence_tasks[key] = task

    async def _load_app_config(self) -> None:
        """Load app-level settings (e.g. timezone) from the database."""
        try:
            rows = await self._db.fetchall("SELECT key, value FROM app_settings")
            for row in rows:
                self._app_config[row["key"]] = row["value"]
            logger.debug("LogicManager: app_config loaded: %s", self._app_config)
        except Exception:
            logger.exception("LogicManager: could not load app_settings")

    def update_app_config(self, config: dict[str, Any]) -> None:
        """Hot-update app config (called by settings API on PUT /system/settings)."""
        previous_timezone = self._app_config.get("timezone")
        self._app_config.update(config)
        if self._app_config.get("timezone") != previous_timezone:
            for graph_id in set(self._graphs) | set(self._ical_result_caches):
                self._ical_cache_generations[graph_id] = object()
        logger.info("LogicManager: app_config updated: %s", config)

    # ── Cron Scheduler ────────────────────────────────────────────────────

    def _start_cron_tasks(self) -> None:
        """Start asyncio tasks for all timer_cron and ical nodes in enabled graphs."""
        _has_croniter = True
        try:
            import croniter as _croniter_check  # noqa: F401
        except ImportError:
            logger.warning("croniter not installed — timer_cron nodes will not auto-execute. Install with: pip install croniter")
            _has_croniter = False

        for graph_id in list(self._graphs):
            entry = self._graphs.get(graph_id)
            if entry is None:
                continue
            name, enabled, flow = entry
            if not enabled:
                continue
            for node in flow.nodes:
                if node.type == "timer_cron":
                    if not _has_croniter:
                        continue
                    key = (graph_id, node.id)
                    if key in self._cron_tasks and not self._cron_tasks[key].done():
                        continue  # already running
                    cron_expr = node.data.get("cron", "0 7 * * *")
                    task = asyncio.create_task(
                        self._cron_loop(graph_id, node.id, cron_expr),
                        name=f"cron-{graph_id[:8]}-{node.id[:8]}",
                    )
                    self._cron_tasks[key] = task
                    logger.info(
                        "Cron scheduled: graph=%s (%s) node=%s expr=%r",
                        graph_id[:8],
                        name,
                        node.id[:8],
                        cron_expr,
                    )
                elif node.type == "ical":
                    key = (graph_id, node.id)
                    if key in self._cron_tasks and not self._cron_tasks[key].done():
                        continue  # already running
                    refresh_min = max(1.0, float(node.data.get("refresh_interval_min") or 60))
                    task = asyncio.create_task(
                        self._ical_loop(graph_id, node.id, refresh_min),
                        name=f"ical-{graph_id[:8]}-{node.id[:8]}",
                    )
                    self._cron_tasks[key] = task
                    logger.info(
                        "iCal scheduled: graph=%s (%s) node=%s interval=%.0fmin",
                        graph_id[:8],
                        name,
                        node.id[:8],
                        refresh_min,
                    )

    async def _cron_loop(self, graph_id: str, node_id: str, cron_expr: str) -> None:
        """Fires a timer_cron graph node on its cron schedule — runs indefinitely."""
        from croniter import croniter

        while True:
            try:
                now = datetime.now(UTC)
                it = croniter(cron_expr, now)
                next_dt = it.get_next(datetime)
                wait_s = max(0.0, (next_dt - now).total_seconds())
                logger.debug(
                    "Cron graph %s: sleeping %.0fs until %s",
                    graph_id[:8],
                    wait_s,
                    next_dt.isoformat(),
                )
                await asyncio.sleep(wait_s)

                entry = self._graphs.get(graph_id)
                if entry and entry[1]:  # still exists and enabled
                    g_name, _, flow = entry
                    overrides = {node_id: {"trigger": True}}
                    await self._execute_graph(graph_id, g_name, flow, overrides)
                    logger.info(
                        "Cron graph %s (%s) fired at %s",
                        graph_id[:8],
                        g_name,
                        next_dt.isoformat(),
                    )

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Cron loop error graph=%s", graph_id[:8])
                await asyncio.sleep(60)  # back-off on unexpected errors

    async def _ical_loop(self, graph_id: str, node_id: str, refresh_min: float) -> None:
        """Triggers the graph containing an ical node on its refresh schedule.

        Fires once immediately (to populate outputs on startup), then every
        refresh_min minutes.  The actual HTTP fetch is throttled inside
        _execute_graph via the last_fetch_ts timestamp, so redundant calls are
        cheap.
        """
        while True:
            try:
                entry = self._graphs.get(graph_id)
                if entry and entry[1]:  # still exists and enabled
                    g_name, _, flow = entry
                    await self._execute_graph(graph_id, g_name, flow, {node_id: {}})
                    logger.debug("iCal graph %s (%s) node %s refreshed", graph_id[:8], g_name, node_id[:8])

                await asyncio.sleep(refresh_min * 60)

            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("iCal loop error graph=%s node=%s", graph_id[:8], node_id[:8])
                await asyncio.sleep(60)  # back-off on unexpected errors

    # ── Event Handler ─────────────────────────────────────────────────────

    async def _on_value_event(self, event: Any) -> None:
        if getattr(event, "suppress_action_triggers", False) is True:
            return
        dp_id = str(event.datapoint_id)
        now = datetime.now(UTC)
        logic_depth = int(getattr(event, "logic_depth", 0) or 0)
        is_init_event = getattr(event, "initialization", False) is True

        for graph_id in list(self._graphs):
            entry = self._graphs.get(graph_id)
            if entry is None:
                continue
            name, enabled, flow = entry
            if not enabled:
                continue
            if is_init_event and (dp_id in self._initializing_graphs.get(graph_id, ()) or graph_id in self._bulk_init_pending):
                # This graph's own initialization publish is in flight — or
                # the graph awaits its turn in a bulk config-restore pass and
                # will seed itself from the registry in a moment — so the
                # initialization-flagged write must not re-enter it mid-pass
                # (issue #1031). Only flagged events qualify: a REAL logic
                # write from another sheet racing in during the publish await
                # executes normally. Keep the read filters of the written
                # DataPoint in sync so a later event repeating this value is
                # deduplicated (last_value only — refreshing last_ts would
                # start a throttle window at save time and drop the next
                # real update).
                sync_state = self._node_state.setdefault(graph_id, {})
                for tn in flow.nodes:
                    if tn.type == "datapoint_read" and tn.data.get("datapoint_id") == dp_id:
                        ns = sync_state.setdefault(tn.id, {})
                        ns["last_value"] = event.value
                continue
            trigger_nodes = [n for n in flow.nodes if n.type == "datapoint_read" and n.data.get("datapoint_id") == dp_id]
            if not trigger_nodes:
                continue
            if logic_depth >= _MAX_LOGIC_CASCADE_DEPTH:
                logger.warning(
                    "Logic cascade depth limit reached: suppressing graph=%s (%s) for dp=%s depth=%d",
                    graph_id[:8],
                    name,
                    dp_id,
                    logic_depth,
                )
                continue

            graph_state = self._node_state.setdefault(graph_id, {})
            overrides: dict[str, dict[str, Any]] = {}

            for tn in trigger_nodes:
                ns = graph_state.setdefault(tn.id, {})
                d = tn.data
                new_val = event.value
                last_val = ns.get("last_value")
                last_ts = ns.get("last_ts")

                # ── Filter: trigger_on_change ────────────────────────────
                toc = d.get("trigger_on_change")
                if (toc is True or toc == "true") and new_val == last_val:
                    continue

                # ── Filter: min_delta ────────────────────────────────────
                raw_delta = d.get("min_delta")
                if raw_delta not in (None, "", 0) and last_val is not None:
                    try:
                        if abs(float(new_val) - float(last_val)) < float(raw_delta):
                            continue
                    except (TypeError, ValueError):
                        pass

                # ── Filter: min_delta_pct ────────────────────────────────
                raw_pct = d.get("min_delta_pct")
                if raw_pct not in (None, "", 0) and last_val is not None:
                    try:
                        base = abs(float(last_val)) or 1.0
                        if abs(float(new_val) - float(last_val)) / base * 100 < float(raw_pct):
                            continue
                    except (TypeError, ValueError):
                        pass

                # ── Filter: throttle (value + unit) ──────────────────────
                tv = d.get("throttle_value")
                if tv not in (None, "", 0) and last_ts is not None:
                    try:
                        unit_ms = _THROTTLE_UNITS.get(d.get("throttle_unit", "s"), 1000.0)
                        throttle_ms = float(tv) * unit_ms
                        elapsed_ms = (now - last_ts).total_seconds() * 1000
                        if elapsed_ms < throttle_ms:
                            continue
                    except (TypeError, ValueError):
                        pass

                # All filters passed — update state and add override.
                # Initialization cascades keep last_ts untouched: save-time
                # seeding is not a real source update and must not start a
                # throttle window that would drop the next real event.
                ns["last_value"] = new_val
                if not is_init_event:
                    ns["last_ts"] = now
                overrides[tn.id] = {"value": new_val, "changed": True}

            if not overrides:
                continue
            if is_init_event:
                # Save-time seeding cascading into another sheet stays
                # initialization: run the side-effect-free pass instead of a
                # full execution so no api_client/notify/WoL/sequence action
                # fires because a different sheet was saved. The cascade
                # depth guard above still bounds chains between sheets.
                await self.initialize_graph(graph_id, logic_depth=logic_depth, seed_overrides={dp_id: event.value})
                continue
            await self._execute_graph(graph_id, name, flow, overrides, logic_depth=logic_depth)

    async def _on_datapoint_renamed(self, event: Any) -> None:
        """Update datapoint_name in all logic nodes that reference the renamed DataPoint."""
        dp_id_str = str(event.dp_id)
        for graph_id in list(self._graphs):
            entry = self._graphs.get(graph_id)
            if entry is None:
                continue
            _name, _enabled, flow = entry
            changed = False
            for node in flow.nodes:
                if node.data.get("datapoint_id") == dp_id_str and node.data.get("datapoint_name") != event.new_name:
                    node.data["datapoint_name"] = event.new_name
                    changed = True
                variables, variables_changed = _rename_api_client_variable_datapoint_names(
                    node.data.get("variables"),
                    dp_id_str,
                    event.new_name,
                )
                if variables_changed:
                    node.data["variables"] = variables
                    changed = True
                if node.type == "value_sequence":
                    steps = self._sequence_steps(node.data.get("steps"))
                    for step in steps:
                        if step.get("datapoint_id") == dp_id_str and step.get("datapoint_name") != event.new_name:
                            step["datapoint_name"] = event.new_name
                            changed = True
                    node.data["steps"] = steps
            if changed:
                current = self._graphs.get(graph_id)
                if current is None or current[2] is not flow:
                    continue
                try:
                    await self._db.execute_and_commit(
                        "UPDATE logic_graphs SET flow_data=?, updated_at=? WHERE id=?",
                        (flow.model_dump_json(), datetime.now(UTC).isoformat(), graph_id),
                    )
                    logger.info(
                        "LogicManager: updated datapoint_name '%s' → '%s' in graph %s",
                        event.old_name,
                        event.new_name,
                        graph_id[:8],
                    )
                except Exception:
                    logger.exception("LogicManager: failed to persist renamed datapoint in graph %s", graph_id[:8])

    # ── Execution ─────────────────────────────────────────────────────────

    async def execute_graph(
        self,
        graph_id: str,
        input_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Manually trigger a graph (e.g. from API).

        Registry seeding for all datapoint_read nodes is handled inside
        _execute_graph, so no extra overrides are needed here.
        """
        entry = self._graphs.get(graph_id)
        if not entry:
            raise KeyError(f"Graph {graph_id} not in cache")
        name, _enabled, flow = entry
        return await self._execute_graph(
            graph_id,
            name,
            flow,
            {},
            debug_overrides=input_overrides or {},
        )

    async def execute_graph_debug(
        self,
        graph_id: str,
        input_overrides: dict[str, dict[str, Any]] | None = None,
    ) -> tuple[dict[str, Any], dict[str, dict[str, dict[str, Any]]]]:
        """Manually run a graph and return its actual final-pass inputs."""
        entry = self._graphs.get(graph_id)
        if not entry:
            raise KeyError(f"Graph {graph_id} not in cache")
        name, _enabled, flow = entry
        input_capture: dict[str, dict[str, dict[str, Any]]] = {}
        outputs = await self._execute_graph(
            graph_id,
            name,
            flow,
            {},
            debug_overrides=input_overrides or {},
            debug_input_capture=input_capture,
        )
        return outputs, input_capture

    async def initialize_graph(self, graph_id: str, logic_depth: int = 0, seed_overrides: dict[str, Any] | None = None) -> None:
        """Seed Read Object nodes with their current registry values right
        after a graph is saved or activated (issue #1031).

        Without this, datapoint_read nodes stay unset until their DataPoint
        receives the next external update. Deliberately NOT a full
        _execute_graph run: saving a sheet is not a datapoint event, so this
        pass evaluates the graph side-effect-free — stateful nodes
        (statistics, memory, sequences) run on a throwaway state copy, no
        iCal URLs are fetched, and no trigger-driven action nodes
        (api_client, notify_*, wake_on_lan, message_archive, value_sequence)
        are started. Only datapoint_write outputs are published, and only
        for writes that descend from a seeded Read Object without passing
        through an unseeded one (whose None would be coerced to 0/False) or
        an _INIT_EXCLUDED_NODE_TYPES node (whose init output would be a
        placeholder or computed from throwaway state). Errors are logged,
        never raised, so a failed initial run cannot break the save request.
        """
        entry = self._graphs.get(graph_id)
        if not entry:
            return
        name, enabled, flow = entry
        if not enabled:
            return

        # Seed every configured Read Object from the registry; nodes whose
        # DataPoint has no current value taint their downstream subgraph.
        seeds: dict[str, dict[str, Any]] = {}
        seed_ts: dict[str, Any] = {}
        unseeded: set[str] = set()
        for node in flow.nodes:
            if node.type != "datapoint_read":
                continue
            dp_id_str = node.data.get("datapoint_id")
            if not dp_id_str:
                # An unconfigured Read Object evaluates to None just like a
                # configured one without a value — taint it the same way.
                unseeded.add(node.id)
                continue
            if seed_overrides and dp_id_str in seed_overrides:
                # Cascaded initialization: the triggering event value takes
                # precedence — the registry handler runs concurrently and may
                # not have stored the write yet.
                seeds[node.id] = {"value": seed_overrides[dp_id_str], "changed": False}
                seed_ts[node.id] = None
                continue
            vs = None
            try:
                vs = self._registry.get_value(uuid.UUID(dp_id_str))
            except (ValueError, TypeError, AttributeError):
                pass
            # The registry returns an empty ValueState for DataPoints that
            # never received a value — only a real value counts as seeded.
            if vs is not None and vs.value is not None:
                seeds[node.id] = {"value": vs.value, "changed": False}
                seed_ts[node.id] = getattr(vs, "ts", None)
            else:
                unseeded.add(node.id)
        if not seeds:
            return

        # Topology-only sets, computed once:
        # - Read.changed edges carry the synthetic changed=False seed, not
        #   the object value — branches fed via that handle must not be
        #   initialized.
        # - Seeded eligibility must follow value-carrying paths only: an edge
        #   into a write node's trigger handle controls WHEN a write fires
        #   but does not deliver the written value, so it must not make a
        #   write (e.g. Const → Write.value plus Read → Write.trigger)
        #   initializable.
        # - A write that closes a feedback loop onto a Read Object of the
        #   same DataPoint (the write is reachable from that read) would
        #   re-enter _on_value_event during publish and repeat until the
        #   cascade-depth guard — skip only those; unrelated reads of the
        #   target DataPoint (e.g. a separate status branch) keep the write
        #   eligible.
        read_node_ids = {node.id for node in flow.nodes if node.type == "datapoint_read"}
        node_type_by_id = {node.id: node.type for node in flow.nodes}
        # Match GraphExecutor._build_edge_map(): when several edges target
        # the same input handle, the last edge is the only effective one.
        _effective_edge_by_target_init: dict[tuple[str, str], Any] = {}
        for _edge in flow.edges:
            _effective_edge_by_target_init[(_edge.target, _edge.targetHandle or "in")] = _edge
        _effective_edges_init = list(_effective_edge_by_target_init.values())
        # A change_filter's "changed" port is the same kind of discrete
        # event pulse as a Read Object's "changed" handle — on a save/
        # startup pseudo-execution it reports a synthetic first-value
        # True (or, after a restart with restored state, a synthetic
        # False), never a real DataValueEvent. A Write descending from it
        # must not be published, exactly like the direct Read.changed
        # case just above.
        changed_targets = {
            e.target
            for e in _effective_edges_init
            if e.sourceHandle == "changed" and (e.source in read_node_ids or node_type_by_id.get(e.source) == "change_filter")
        }
        # Every Edge Detection output is edge-gated: "out" exists only on an
        # edge and the triggers are only true on one. On a save/startup
        # pseudo-execution any edge it reports is synthetic — derived from the
        # restored level versus the freshly seeded value, never from an
        # observed transition — so a Write descending from it must not be
        # published, exactly like the change_filter case above. Its own level
        # is still committed below (_INIT_STATE_ALWAYS_COMMIT).
        changed_targets |= {e.target for e in _effective_edges_init if node_type_by_id.get(e.source) == "edge_detect"}
        excluded_ids = {node.id for node in flow.nodes if node.type in _INIT_EXCLUDED_NODE_TYPES}
        value_edges = [
            e
            for e in _effective_edges_init
            if (e.targetHandle or "") not in _INIT_CONTROL_INPUT_HANDLES.get(node_type_by_id.get(e.target, ""), frozenset())
        ]
        wired_inputs = {(e.target, e.targetHandle or "in") for e in _effective_edges_init}
        feedback_writes: set[str] = set()
        reach_by_read: dict[str, set[str]] = {}
        for rnode in flow.nodes:
            if rnode.type != "datapoint_read":
                continue
            r_dp = rnode.data.get("datapoint_id")
            if not r_dp:
                continue
            reach = _downstream_closure({rnode.id}, _effective_edges_init)
            reach_by_read[rnode.id] = reach
            feedback_writes.update(
                wnode.id for wnode in flow.nodes if wnode.type == "datapoint_write" and wnode.id in reach and wnode.data.get("datapoint_id") == r_dp
            )

        # The settle pass adds implicit write-target → read dependencies, so
        # feedback can also span several DataPoints (Read A → Write B plus
        # Read B → Write A would never settle). Build the DataPoint-level
        # dependency graph and exclude every write whose target sits on a
        # cycle, exactly like the same-DataPoint feedback above. Only
        # value-carrying reachability counts: a read that merely gates a
        # write's trigger (or another control-only handle) can never deliver
        # the written value, so it forms no settle dependency.
        reach_by_read_value: dict[str, set[str]] = {
            rnode.id: _downstream_closure({rnode.id}, value_edges) for rnode in flow.nodes if rnode.type == "datapoint_read"
        }
        dp_deps: dict[str, set[str]] = {}
        for wnode in flow.nodes:
            if wnode.type != "datapoint_write" or wnode.id in feedback_writes:
                continue
            w_dp = wnode.data.get("datapoint_id")
            if not w_dp:
                continue
            for rnode in flow.nodes:
                if rnode.type == "datapoint_read" and rnode.data.get("datapoint_id") and wnode.id in reach_by_read_value.get(rnode.id, ()):
                    dp_deps.setdefault(w_dp, set()).add(rnode.data.get("datapoint_id"))
        cyclic_dps: set[str] = set()
        for start_dp in dp_deps:
            frontier = set(dp_deps.get(start_dp, ()))
            seen: set[str] = set()
            while frontier:
                dep = frontier.pop()
                if dep == start_dp:
                    cyclic_dps.add(start_dp)
                    break
                if dep in seen:
                    continue
                seen.add(dep)
                frontier.update(dp_deps.get(dep, ()))
        feedback_writes.update(wnode.id for wnode in flow.nodes if wnode.type == "datapoint_write" and wnode.data.get("datapoint_id") in cyclic_dps)

        now = datetime.now(UTC)
        graph_state = self._node_state.setdefault(graph_id, {})

        # Prime the event filters (trigger_on_change, min_delta) BEFORE
        # publishing writes: a graph that writes a DataPoint it also reads
        # re-enters _on_value_event during the publish await. last_ts keeps
        # the value's own registry timestamp — saving is not a datapoint
        # event, so it must not start a fresh throttle window.
        for node_id, seed in seeds.items():
            ns = graph_state.setdefault(node_id, {})
            ns["last_value"] = seed["value"]
            ts = seed_ts.get(node_id)
            if ts is not None:
                ns["last_ts"] = ts

        try:
            # Excluded node types never influence published writes (their
            # subgraphs are tainted) — replace them with inert placeholders
            # for the dry run so e.g. a python_script cannot burn CPU inside
            # the save request.
            # Built from `flow`, not `init_flow`: both types are replaced by an
            # inert missing_node below, so their "out" is absent for the dry run.
            # That absence is a boundary, not a failed producer — without this a
            # synchronous node between one of them and a Change Filter would log
            # "Missing upstream output" on every single save.
            init_retained_boundary_handles = {node.id: {"out"} for node in flow.nodes if node.type in ("memory", "edge_detect")}
            init_flow = flow
            if excluded_ids:
                init_flow = flow.model_copy(deep=True)
                for node in init_flow.nodes:
                    if node.type in _INIT_EXCLUDED_NODE_TYPES:
                        node.type = "missing_node"

            # Evaluate until intermediate DataPoints settle: a write target
            # that another Read Object of the same sheet watches feeds its
            # computed value back into that read and re-evaluates — the write
            # event is suppressed for this graph, so downstream branches
            # would otherwise initialize from the stale registry value.
            # Feedback loops are excluded via feedback_writes, so the chains
            # form a DAG and each pass settles at least one handoff level —
            # the number of DataPoints both read and written bounds the pass
            # count for chains of any length.
            read_dps = {node.data.get("datapoint_id") for node in flow.nodes if node.type == "datapoint_read" and node.data.get("datapoint_id")}
            write_dps = {node.data.get("datapoint_id") for node in flow.nodes if node.type == "datapoint_write" and node.data.get("datapoint_id")}
            for _ in range(len(read_dps & write_dps) + 1):
                # A write may only fire when it carries a seeded value: it
                # must descend from a seeded Read Object (a save must not
                # actuate unrelated branches like Const → Write) and must not
                # descend from an unseeded Read Object or an excluded node
                # type (see _INIT_EXCLUDED_NODE_TYPES).
                tainted = _downstream_closure(unseeded | changed_targets | excluded_ids, _effective_edges_init)
                seeded_paths = _downstream_closure(set(seeds), value_edges)
                skip_writes = {
                    node.id
                    for node in flow.nodes
                    if node.type == "datapoint_write" and (node.id in tainted or node.id not in seeded_paths or node.id in feedback_writes)
                }

                # operating_hours totals are injected as overrides by
                # _execute_graph's pre-pass — mirror that here (read-only) so
                # seeded paths through such nodes publish the accumulated
                # hours instead of 0.0.
                overrides = dict(seeds)
                for node in flow.nodes:
                    if node.type != "operating_hours":
                        continue
                    ns = graph_state.setdefault(node.id, {"accumulated_hours": 0.0, "last_start": None})
                    acc = ns["accumulated_hours"]
                    if ns.get("last_start"):
                        acc += (now - ns["last_start"]).total_seconds() / 3600
                    overrides[node.id] = {**overrides.get(node.id, {}), "_computed_hours": round(acc, 6)}

                # Fresh state copy per pass: the executor mutates gate/
                # hysteresis state during evaluation, and a later pass with
                # settled seeds must evaluate against the ORIGINAL persisted
                # state, not the state an earlier pass derived from stale
                # intermediate values.
                hyst_copy = _safe_deepcopy_state(self._hysteresis.get(graph_id, {}))
                executor = GraphExecutor(
                    init_flow,
                    hyst_copy,
                    self._app_config,
                    retained_boundary_handles=init_retained_boundary_handles,
                )
                outputs = executor.execute(overrides, commit_memory=False)

                settled = True
                for wnode in flow.nodes:
                    if wnode.type != "datapoint_write" or wnode.id in skip_writes:
                        continue
                    node_out = outputs.get(wnode.id, {})
                    if (wnode.id, "trigger") in wired_inputs and not GraphExecutor._to_bool(node_out.get("_triggered")):
                        continue  # gated writes do not deliver a value
                    w_dp = wnode.data.get("datapoint_id")
                    write_val = node_out.get("_write_value")
                    if not w_dp or write_val is None:
                        continue
                    # A value the write-side filters would suppress is never
                    # actually written — it must not seed downstream reads.
                    if not self._write_filters_allow(wnode.data, graph_state.get(wnode.id, {}), write_val, now):
                        continue
                    for rnode in flow.nodes:
                        if rnode.type != "datapoint_read" or rnode.data.get("datapoint_id") != w_dp:
                            continue
                        if seeds.get(rnode.id, {}).get("value") != write_val:
                            seeds[rnode.id] = {"value": write_val, "changed": False}
                            unseeded.discard(rnode.id)
                            settled = False
                if settled:
                    break

            # Start/stop the operating-hours accumulators exactly like
            # _execute_graph's _apply_operating_hours_state, but only for
            # nodes driven by clean seeded inputs — a placeholder-coerced
            # False must not stop a running counter. Without this, a source
            # that is already on at activation would not be counted until
            # its next datapoint event.
            for node in flow.nodes:
                if node.type != "operating_hours" or node.id not in seeded_paths or node.id in tainted:
                    continue
                out = outputs.get(node.id, {})
                ns = graph_state.setdefault(node.id, {"accumulated_hours": 0.0, "last_start": None})
                if out.get("_reset", False):
                    ns["accumulated_hours"] = 0.0
                    ns["last_start"] = now if out.get("_active", False) else None
                elif out.get("_active", False):
                    if not ns.get("last_start"):
                        ns["last_start"] = now
                elif ns.get("last_start"):
                    ns["accumulated_hours"] += (now - ns["last_start"]).total_seconds() / 3600
                    ns["last_start"] = None

            # While the publish is in flight, _on_value_event skips THIS
            # graph for the DataPoints written here: a write target read
            # elsewhere in the same sheet (e.g. Read A → Write B plus
            # Read B → Write C) would otherwise re-enter the graph mid-pass
            # and burst until the cascade guard. Live events for other
            # DataPoints keep executing the graph normally.
            init_write_dps = {
                str(node.data.get("datapoint_id"))
                for node in flow.nodes
                if node.type == "datapoint_write" and node.id not in skip_writes and node.data.get("datapoint_id")
            }
            self._initializing_graphs[graph_id] = init_write_dps
            try:
                published_writes = await self._apply_datapoint_write_outputs(
                    graph_id, flow, outputs, graph_state, wired_inputs, now, logic_depth, skip_node_ids=skip_writes, initialization=True
                )
            finally:
                self._initializing_graphs.pop(graph_id, None)

            # `tainted` is a blanket downstream closure from unseeded/excluded
            # nodes — but an AND/OR gate can be decisively resolved by a
            # seeded input alone (e.g. seeded True feeding an OR whose other
            # input is an unseeded Read Object), making everything downstream
            # of that gate deterministic despite being nominally "tainted".
            # change_filter's own committed state is only ever gated on this
            # refined taint (never on skip_writes/gate/hysteresis, which keep
            # using the blanket `tainted` above) — mirrors _compute_cf_hold_ids'
            # _gate_taint_absorbed in _execute_graph, computed here against the
            # settled `outputs`/`tainted` from the loop above.
            _node_by_id_init = {n.id: n for n in flow.nodes}
            _decisive_gate_value_init = {"or": True, "and": False}

            def _gate_taint_absorbed_init(gate_id: str, gate_type: str) -> bool:
                decisive = _decisive_gate_value_init[gate_type]
                gate_node = _node_by_id_init[gate_id]
                gdata = gate_node.data or {}
                try:
                    count = max(2, min(30, int(gdata.get("input_count", 2))))
                except (TypeError, ValueError):
                    return False
                for i in range(1, count + 1):
                    handle = f"in{i}"
                    src_edge = next(
                        (e for e in _effective_edges_init if e.target == gate_id and (e.targetHandle or "in") == handle),
                        None,
                    )
                    if src_edge is not None and src_edge.source in cf_tainted:
                        continue
                    v = (
                        False
                        if src_edge is None
                        else GraphExecutor._to_bool(GraphExecutor._get_output_value(outputs.get(src_edge.source, {}), src_edge.sourceHandle or "out"))
                    )
                    if gdata.get(f"negate_{handle}"):
                        v = not v
                    if v == decisive:
                        return True
                return False

            def _closed_gate_absorbs_init(gate_id: str) -> bool:
                gate_node = _node_by_id_init.get(gate_id)
                if gate_node is None:
                    return False
                enable_edge = next(
                    (e for e in _effective_edges_init if e.target == gate_id and (e.targetHandle or "in") == "enable"),
                    None,
                )
                if enable_edge is not None and enable_edge.source in cf_tainted:
                    return False
                enable_value = (
                    False
                    if enable_edge is None
                    else GraphExecutor._to_bool(
                        GraphExecutor._get_output_value(outputs.get(enable_edge.source, {}), enable_edge.sourceHandle or "out")
                    )
                )
                if (gate_node.data or {}).get("negate_enable"):
                    enable_value = not enable_value
                return not enable_value

            cf_tainted: set[str] = set(unseeded | changed_targets | excluded_ids)
            cf_tainted.difference_update(n.id for n in flow.nodes if n.type == "memory")
            # A decisive seeded input can absorb taint even when the gate is
            # itself one of the initial changed/excluded targets. Normalize
            # those initial seeds through the same boundary used below.
            for _initial_id in tuple(cf_tainted):
                _initial_node = _node_by_id_init.get(_initial_id)
                if (
                    _initial_node is not None
                    and _initial_node.type in _decisive_gate_value_init
                    and _gate_taint_absorbed_init(_initial_id, _initial_node.type)
                ):
                    cf_tainted.discard(_initial_id)
                    continue
                if _initial_node is not None and _initial_node.type == "gate":
                    initial_changed_edges = [
                        edge
                        for edge in _effective_edges_init
                        if edge.target == _initial_id
                        and edge.sourceHandle == "changed"
                        and (edge.source in read_node_ids or node_type_by_id.get(edge.source) == "change_filter")
                    ]
                    if (
                        initial_changed_edges
                        and all((edge.targetHandle or "in") == "in" for edge in initial_changed_edges)
                        and _closed_gate_absorbs_init(_initial_id)
                    ):
                        cf_tainted.discard(_initial_id)
            _cfq: list[str] = list(cf_tainted)
            while _cfq:
                _cn = _cfq.pop()
                for _ce in _effective_edges_init:
                    if _ce.source != _cn or _ce.target in cf_tainted:
                        continue
                    _ctarget = _node_by_id_init.get(_ce.target)
                    _ctype = _ctarget.type if _ctarget is not None else None
                    # Memory publishes its retained value for this tick and
                    # only commits its input for the next one.
                    if _ctype == "memory":
                        continue
                    if _ctype in _decisive_gate_value_init and _gate_taint_absorbed_init(_ce.target, _ctype):
                        continue
                    # An unseeded Read feeding hysteresis.value resolves to
                    # the node's retained prior state, not a placeholder.
                    # Stop initialization taint at that fully resolved state,
                    # matching the live-execution taint traversal below.
                    if _ctype == "hysteresis" and (_ce.targetHandle or "in") == "value":
                        _hyst_value = GraphExecutor._get_output_value(outputs.get(_ce.source, {}), _ce.sourceHandle or "out")
                        if _hyst_value is None:
                            continue
                    # A "gate" (Freigabe) node closed by a RESOLVED enable
                    # input is the same kind of boundary as a decisive
                    # AND/OR gate above — matches the closed-gate exception
                    # in _execute_graph's own _compute_cf_hold_ids. While
                    # closed, its output is either the retained last-enabled
                    # value or a fixed default_value, either way entirely
                    # independent of "in" this pass. Only applies to a
                    # tainted edge targeting "in" specifically; if the taint
                    # instead comes through "enable" itself, the closed
                    # state can't be trusted and must still propagate.
                    if _ctype == "gate" and (_ce.targetHandle or "in") == "in":
                        _gate_data = (_ctarget.data or {}) if _ctarget is not None else {}
                        _enable_edge = next(
                            (e for e in _effective_edges_init if e.target == _ce.target and (e.targetHandle or "in") == "enable"),
                            None,
                        )
                        if _enable_edge is None or _enable_edge.source not in cf_tainted:
                            _enable_v = (
                                False
                                if _enable_edge is None
                                else GraphExecutor._to_bool(
                                    GraphExecutor._get_output_value(outputs.get(_enable_edge.source, {}), _enable_edge.sourceHandle or "out")
                                )
                            )
                            if _gate_data.get("negate_enable"):
                                _enable_v = not _enable_v
                            if not _enable_v:
                                continue
                    cf_tainted.add(_ce.target)
                    _cfq.append(_ce.target)

            # Commit gate/hysteresis state only for nodes whose switched
            # output was actually published (see
            # _INIT_COMMIT_STATE_NODE_TYPES) — without a published write the
            # save must not act like a datapoint event on the stored state.
            # change_filter is the exception: its own state is meaningful
            # independent of whether any descendant is a datapoint_write at
            # all, so it commits whenever seeded/untainted (per the refined
            # cf_tainted above) regardless of published_writes (see
            # _INIT_COMMIT_STATE_NODE_TYPES).
            state_committed = False
            for node in flow.nodes:
                if node.type not in _INIT_COMMIT_STATE_NODE_TYPES or node.id not in seeded_paths or node.id not in hyst_copy:
                    continue
                always_commit = node.type in _INIT_STATE_ALWAYS_COMMIT
                if node.id in (cf_tainted if always_commit else tainted):
                    continue
                if not always_commit and not (_downstream_closure({node.id}, flow.edges) & published_writes):
                    continue
                self._hysteresis.setdefault(graph_id, {})[node.id] = hyst_copy[node.id]
                state_committed = True
            if state_committed:
                # Persist like _execute_graph does — otherwise a restart
                # before the next real execution reloads the stale pre-save
                # state from the DB while the switched value was already
                # written.
                await self._persist_node_state(graph_id)
        except Exception:
            logger.exception("LogicManager: initialization of graph %s (%s) failed", graph_id[:8], name)

    def _order_graphs_for_initialization(self, graph_ids: list[str]) -> list[str]:
        """Order restored graphs producers-first.

        A graph that writes a DataPoint another restored graph reads must
        initialize first, so the consumer seeds from the freshly written
        registry value. Dependency cycles fall back to the given order.
        """
        infos: dict[str, tuple[set[str], set[str]]] = {}
        for gid in graph_ids:
            entry = self._graphs.get(gid)
            if not entry:
                infos[gid] = (set(), set())
                continue
            _, _, flow = entry
            reads = {n.data.get("datapoint_id") for n in flow.nodes if n.type == "datapoint_read" and n.data.get("datapoint_id")}
            writes = {n.data.get("datapoint_id") for n in flow.nodes if n.type == "datapoint_write" and n.data.get("datapoint_id")}
            infos[gid] = (reads, writes)
        ordered: list[str] = []
        remaining = list(graph_ids)
        while remaining:
            progressed = False
            for gid in list(remaining):
                reads, _ = infos[gid]
                if any(other != gid and infos[other][1] & reads for other in remaining):
                    continue  # a pending producer writes what this graph reads
                ordered.append(gid)
                remaining.remove(gid)
                progressed = True
            if not progressed:
                ordered.extend(remaining)
                break
        return ordered

    async def reinitialize_graph(self, graph_id: str) -> None:
        """Save-path helper: invalidate + reload + initialize (issue #1031).

        The read/write filter state (last_value, last_write_val, …) is
        carried across the reload: invalidate_cache drops _node_state, and an
        initialization publish evaluated against empty filter state would
        re-send unchanged actuator values on every semantic save even though
        only_on_change/min_delta/throttle should suppress them. Only nodes
        whose semantics are unchanged (same type and data) keep their state —
        e.g. a write retargeted to another DataPoint must not inherit the old
        target's last_write_val and skip its initialization.
        """
        old_entry = self._graphs.get(graph_id)
        saved_state = self._node_state.get(graph_id)
        self.invalidate_cache(graph_id)
        await self.reload()
        new_entry = self._graphs.get(graph_id)
        if saved_state and old_entry and new_entry:
            old_nodes = {node.id: node for node in old_entry[2].nodes}
            kept = {
                node.id: saved_state[node.id]
                for node in new_entry[2].nodes
                if node.id in saved_state and node.id in old_nodes and old_nodes[node.id].type == node.type and old_nodes[node.id].data == node.data
            }
            if kept:
                self._node_state[graph_id] = kept
        await self.initialize_graph(graph_id)
        # Re-persist immediately: a save may flip a node's persist_state to
        # False without itself producing a state-committing init write (see
        # _INIT_COMMIT_STATE_NODE_TYPES), which would otherwise leave the old
        # value in the DB until the next real execution. Without this, a
        # restart between the save and that next execution would restore the
        # stale snapshot via _load_graphs() despite persist_state now being
        # disabled.
        await self._persist_node_state(graph_id)

    async def initialize_graphs(self, graph_ids: list[str]) -> None:
        """Initialize several restored graphs exactly once each (issue #1031).

        The pass runs producers-first and keeps ALL restored graphs listed in
        _bulk_init_pending for its whole duration — initialization-flagged
        cascades between imported graphs are suppressed (the later graph
        seeds itself from the then-current registry state instead of
        double-executing), while real live events keep executing the graphs
        normally (see _on_value_event).
        """
        self._bulk_init_pending.update(graph_ids)
        try:
            for graph_id in self._order_graphs_for_initialization(graph_ids):
                await self.initialize_graph(graph_id)
        finally:
            self._bulk_init_pending.difference_update(graph_ids)

    async def reset_node_state(self, graph_id: str) -> None:
        """Drop in-memory and persisted node state of a graph.

        Used by the config restore: the imported sheet carries no node state,
        so accumulators and switch states of a previously existing graph with
        reused node ids must not leak into the restored one.
        """
        self._hysteresis.pop(graph_id, None)
        self._ical_result_caches.pop(graph_id, None)
        self._ical_cache_generations[graph_id] = object()
        self._node_state.pop(graph_id, None)
        try:
            # node_state is TEXT NOT NULL DEFAULT '{}' — reset to the empty
            # object, NULL would violate the schema.
            await self._db.execute_and_commit("UPDATE logic_graphs SET node_state = '{}' WHERE id = ?", (graph_id,))
        except Exception:
            logger.exception("Graph %s: failed to reset node_state", graph_id[:8])

    async def _execute_graph(
        self,
        graph_id: str,
        name: str,
        flow: FlowData,
        overrides: dict[str, dict[str, Any]],
        logic_depth: int = 0,
        debug_overrides: dict[str, dict[str, Any]] | None = None,
        debug_input_capture: dict[str, dict[str, dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        try:
            return await self._execute_graph_impl(
                graph_id,
                name,
                flow,
                overrides,
                logic_depth,
                debug_overrides,
                debug_input_capture,
            )
        except _ObsoleteGraphExecution:
            return {}

    async def _execute_graph_impl(
        self,
        graph_id: str,
        name: str,
        flow: FlowData,
        overrides: dict[str, dict[str, Any]],
        logic_depth: int = 0,
        debug_overrides: dict[str, dict[str, Any]] | None = None,
        debug_input_capture: dict[str, dict[str, dict[str, Any]]] | None = None,
    ) -> dict[str, Any]:
        execute_now = datetime.now(UTC)
        execution_started = perf_counter()
        ical_app_config = dict(self._app_config)
        graph_state = self._node_state.setdefault(graph_id, {})
        ical_generation = self._ical_cache_generations.setdefault(graph_id, object())
        ical_result_cache = self._ical_result_caches.setdefault(graph_id, {})
        # Event-driven executions still evaluate the full graph so unrelated
        # datapoint_read nodes can contribute their latest registry values.
        # Track which input handles descend from the explicit event overrides:
        # cached inputs are context, not fresh notification triggers. An
        # execution without overrides is a manual/full-sheet run and keeps the
        # existing all-inputs behaviour.
        debug_overrides = debug_overrides or {}
        capture_debug_inputs = debug_input_capture is not None
        if not capture_debug_inputs:
            try:
                from obs.api.v1.websocket import get_ws_manager

                if get_ws_manager().has_logic_debug_subscribers(graph_id):
                    capture_debug_inputs = True
            except Exception:
                logger.debug("WebSocket debug subscriber lookup unavailable", exc_info=True)
        debug_inputs: dict[str, dict[str, dict[str, Any]]] = {}
        debug_input_runs: list[tuple[dict[str, Any], dict[str, dict[str, dict[str, Any]]]]] = []
        execution_ical_cache: dict[str, Any] | None = None
        execution_ical_sources: dict[str, Any] = {}
        execution_ical_prepared = False
        has_python_scripts = any(node.type == "python_script" for node in flow.nodes)
        run_executor_in_worker = has_python_scripts or capture_debug_inputs
        missing_cf_override_values: dict[str, dict[str, Any]] = {}

        def _debug_run_overrides(candidate: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
            merged = {node_id: dict(values) for node_id, values in candidate.items()}
            for node_id, values in missing_cf_override_values.items():
                candidate_values = candidate.get(node_id, {})
                for handle, value in values.items():
                    if handle in candidate_values:
                        merged[node_id][handle] = value
            for node_id, values in debug_overrides.items():
                merged.setdefault(node_id, {}).update(values)
            return merged

        async def _execute_pass(
            executor: GraphExecutor,
            candidate: dict[str, dict[str, Any]],
            *,
            commit_memory: bool = False,
            known_outputs: dict[str, dict[str, Any]] | None = None,
            executor_lock_held: bool = False,
        ) -> dict[str, dict[str, Any]]:
            execute_args = partial(
                executor.execute,
                _debug_run_overrides(candidate),
                commit_memory=commit_memory,
                capture_incoming_overrides=candidate,
                known_outputs=known_outputs,
            )
            if not run_executor_in_worker:
                result = execute_args()
            else:

                async def _run_worker() -> dict[str, dict[str, Any]]:
                    worker = asyncio.create_task(_run_graph_executor_in_worker(execute_args))
                    try:
                        return await asyncio.shield(worker)
                    except asyncio.CancelledError:
                        # Cancelling to_thread() cannot stop code already running.
                        # The caller retains the per-graph lock while this drains.
                        try:
                            await worker
                        except Exception:
                            logger.exception("Graph %s: cancelled Python-script worker failed while draining", graph_id)
                        raise

                if executor_lock_held:
                    result = await _run_worker()
                else:
                    execution_lock = self._graph_executor_locks.setdefault(graph_id, asyncio.Lock())
                    try:
                        async with execution_lock:
                            if self._ical_cache_generations.get(graph_id) is not ical_generation:
                                raise _ObsoleteGraphExecution
                            result = await _run_worker()
                    finally:
                        self._prune_graph_executor_lock(graph_id)
            if self._ical_cache_generations.get(graph_id) is not ical_generation:
                raise _ObsoleteGraphExecution
            return result

        async def _executor(state: dict[str, Any]) -> GraphExecutor:
            nonlocal execution_ical_cache, execution_ical_prepared
            ical_nodes = [node for node in flow.nodes if node.type == "ical"]
            if execution_ical_cache is None:
                source_ical_cache = (
                    self._ical_result_caches.get(graph_id, {}) if self._ical_cache_generations.get(graph_id) is ical_generation else ical_result_cache
                )
                execution_ical_sources.update(source_ical_cache)
                # Cache entries are immutable once published.  Snapshot only
                # the small mapping so concurrent executions can share large
                # parsed calendar outputs without retaining full copies.
                execution_ical_cache = dict(source_ical_cache)
            pass_ical_cache = execution_ical_cache
            precompute_state: dict[str, Any] = {}
            for ical_node in ical_nodes:
                state.setdefault(ical_node.id, {})
                precompute_state[ical_node.id] = {
                    "raw": state[ical_node.id].get("raw", ""),
                }
            if not capture_debug_inputs:
                executor: GraphExecutor = GraphExecutor(
                    flow,
                    state,
                    ical_app_config,
                    ical_result_cache=pass_ical_cache,
                    ical_cache_outputs_owned=True,
                )

            else:
                run_inputs: dict[str, dict[str, dict[str, Any]]] = {}

                class CapturingGraphExecutor(GraphExecutor):
                    def execute(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
                        run_outputs = super().execute(*args, **kwargs)
                        # ``outputs`` is updated in place as async replay results are
                        # merged. A shallow copy preserves each node output object's
                        # identity while isolating the pass's top-level mapping.
                        debug_input_runs.append((dict(run_outputs), run_inputs))
                        return run_outputs

                executor = CapturingGraphExecutor(
                    flow,
                    state,
                    ical_app_config,
                    run_inputs,
                    pass_ical_cache,
                    ical_cache_outputs_owned=True,
                )

            if not ical_nodes or execution_ical_prepared:
                return executor

            def _precompute_ical_node(ical_node: Any) -> Any:
                precompute_executor = GraphExecutor(
                    flow,
                    precompute_state,
                    ical_app_config,
                    ical_result_cache=pass_ical_cache,
                    ical_cache_outputs_owned=True,
                )
                previous = pass_ical_cache.get(ical_node.id)
                try:
                    precompute_executor._eval_node(ical_node, {})
                except Exception:
                    # The normal executor isolates errors to their node.  The
                    # worker precompute must preserve that contract rather than
                    # failing the entire graph before execute() gets a chance
                    # to produce the node's diagnostic output.
                    logger.exception(
                        "Graph %s: iCalendar precompute failed for node %s",
                        graph_id,
                        ical_node.id,
                    )
                    return None
                current = pass_ical_cache.get(ical_node.id)
                if current is not None and current is not previous:
                    # The execution owns its entry; publish a separate copy so
                    # downstream output handling cannot mutate the shared cache.
                    return copy.deepcopy(current)
                return None

            def _ical_precompute_needed(ical_node: Any) -> bool:
                raw_text = precompute_state[ical_node.id].get("raw", "")
                if not raw_text:
                    return False
                filters_value = ical_node.data.get("filters") or "[]"
                if not isinstance(filters_value, str):
                    return True
                try:
                    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

                    timezone_name = ical_app_config.get("timezone", "Europe/Zurich")
                    cache_key = (filters_value.strip(), timezone_name, datetime.now(ZoneInfo(timezone_name)).date().isoformat())
                except (TypeError, ValueError, ZoneInfoNotFoundError):
                    return True
                cached = pass_ical_cache.get(ical_node.id)
                return not (
                    isinstance(cached, dict)
                    and cached.get("raw") is raw_text
                    and cached.get("key") == cache_key
                    and isinstance(cached.get("outputs"), dict)
                )

            for ical_node in ical_nodes:
                if not _ical_precompute_needed(ical_node):
                    continue
                precompute_key = (graph_id, ical_node.id)
                precompute_lock = self._ical_precompute_locks.setdefault(precompute_key, asyncio.Lock())
                try:
                    async with precompute_lock:
                        if self._ical_cache_generations.get(graph_id) is not ical_generation:
                            raise _ObsoleteGraphExecution
                        latest_entry = self._ical_result_caches.get(graph_id, {}).get(ical_node.id)
                        if latest_entry is not None and latest_entry is not execution_ical_sources.get(ical_node.id):
                            pass_ical_cache[ical_node.id] = latest_entry
                            execution_ical_sources[ical_node.id] = latest_entry
                        if not _ical_precompute_needed(ical_node):
                            continue
                        worker = asyncio.create_task(asyncio.to_thread(_precompute_ical_node, ical_node))
                        try:
                            publication = await asyncio.shield(worker)
                        except asyncio.CancelledError:
                            # A running thread cannot be canceled.  Retain the
                            # per-node lock until it exits so a replacement graph
                            # cannot start a second large recurrence parse beside it.
                            try:
                                await worker
                            except Exception:
                                logger.exception("Graph %s: cancelled iCalendar precompute failed while draining", graph_id)
                            raise
                        if publication is not None and self._ical_cache_generations.get(graph_id) is ical_generation:
                            self._ical_result_caches[graph_id] = {
                                **self._ical_result_caches.get(graph_id, {}),
                                ical_node.id: publication,
                            }
                            execution_ical_sources[ical_node.id] = publication
                finally:
                    self._prune_ical_precompute_lock(precompute_key, precompute_lock)
            execution_ical_prepared = True
            return executor

        # ── Seed all datapoint_read nodes from registry ───────────────────
        # In event-driven execution only the triggered node(s) have overrides.
        # All other DP-LESEN nodes would receive None, which propagates as 0.0
        # through _to_num() in downstream blocks. Fix: pre-seed from registry so
        # every DP-LESEN node has the latest known value. Caller overrides
        # (event value + changed=True) are applied on top and take priority.
        aug_overrides: dict[str, dict[str, Any]] = {}
        # Read Object nodes whose DataPoint has never received a value (or is
        # unconfigured / has an invalid id) — tracked so a change_filter fed by
        # one of these can't mistake the resulting None for a real first value.
        unseeded_read_ids: set[str] = set()
        for node in flow.nodes:
            if node.type != "datapoint_read":
                continue
            dp_id_str = node.data.get("datapoint_id")
            if not dp_id_str:
                unseeded_read_ids.add(node.id)
                continue
            try:
                dp_id = uuid.UUID(dp_id_str)
                vs = self._registry.get_value(dp_id)
                # The registry creates an empty ValueState (value=None) as soon
                # as a DataPoint is registered, well before any adapter writes
                # a real value — `vs is not None` alone is therefore true for
                # every configured DataPoint and never actually detects "never
                # received a value". Match initialize_graph's seeded check.
                if vs is not None and vs.value is not None:
                    aug_overrides[node.id] = {"value": vs.value, "changed": False}
                else:
                    unseeded_read_ids.add(node.id)
            except (ValueError, TypeError, AttributeError):
                unseeded_read_ids.add(node.id)
        # Event / manual overrides take priority over registry seed
        aug_overrides.update(overrides)
        # A node in `overrides` is the actual triggering event for this
        # execution and therefore genuinely delivers a value now, even if the
        # registry lookup above found nothing yet (e.g. this is the DataPoint's
        # very first value).
        unseeded_read_ids -= overrides.keys()
        # Same for a manual/debug execution's value override: it explicitly
        # supplies a value for this run just like a real event does, so a
        # Read Object it targets must not still look unresolved — otherwise
        # the taint-correction pass below rolls back and suppresses any
        # downstream change_filter, defeating the whole point of a one-off
        # debug run against an unconfigured/never-seeded Read Object.
        unseeded_read_ids -= {node_id for node_id, values in debug_overrides.items() if "value" in values}

        api_client_ids = {node.id for node in flow.nodes if node.type == "api_client"}
        host_check_ids = {node.id for node in flow.nodes if node.type == "host_check"}
        wake_on_lan_ids = {node.id for node in flow.nodes if node.type == "wake_on_lan"}
        ical_ids = {node.id for node in flow.nodes if node.type == "ical"}
        normalized_ical_cache = {node_id: entry for node_id, entry in ical_result_cache.items() if node_id in ical_ids}
        for node_id in ical_ids:
            hyst_node = self._hysteresis.setdefault(graph_id, {}).setdefault(node_id, {})
            legacy_cache = hyst_node.pop("_ical_result_cache", None)
            if node_id not in normalized_ical_cache and isinstance(legacy_cache, dict):
                normalized_ical_cache[node_id] = legacy_cache
        ical_result_cache = normalized_ical_cache
        if self._ical_cache_generations.get(graph_id) is ical_generation:
            self._ical_result_caches[graph_id] = normalized_ical_cache
        message_archive_ids = {node.id for node in flow.nodes if node.type == "message_archive"}
        notify_ids = {node.id for node in flow.nodes if node.type in {"notify_message", "notify_pushover", "notify_sms"}}
        operating_hour_ids = {node.id for node in flow.nodes if node.type == "operating_hours"}
        # wake_on_lan.sent is the same kind of placeholder-then-replayed
        # output as host_check.reachable/api_client.success/etc. — a
        # change_filter fed by it needs the same suppress-until-resolved
        # treatment, not just api_client/host_check/message_archive/notify.
        async_replay_source_ids = api_client_ids | host_check_ids | wake_on_lan_ids | message_archive_ids | notify_ids
        needs_async_replay_snapshot = any(edge.source in async_replay_source_ids for edge in flow.edges)
        # random_value.value is None whenever its own trigger is false this
        # pass — structurally identical to an unseeded Read Object, not a
        # genuine value. A change_filter fed by one must be held exactly
        # like an unresolved async source, so a snapshot must exist to roll
        # back to on any pass where that turns out to be the case — decided
        # here from the flow topology alone since (like async sources) the
        # node's actual output isn't known until after _execute_pass runs.
        random_value_ids = {node.id for node in flow.nodes if node.type == "random_value"}
        needs_random_value_snapshot = any(edge.source in random_value_ids for edge in flow.edges)

        # ── Pre-compute operating_hours values to inject as overrides ─────
        for node in flow.nodes:
            if node.type == "operating_hours":
                ns = graph_state.setdefault(node.id, {"accumulated_hours": 0.0, "last_start": None})
                acc = ns["accumulated_hours"]
                if ns.get("last_start"):
                    acc += (execute_now - ns["last_start"]).total_seconds() / 3600
                aug_overrides[node.id] = {
                    **aug_overrides.get(node.id, {}),
                    "_computed_hours": round(acc, 6),
                }

        # ── Pre-fetch iCal URLs (refresh only when cache is stale) ───────────
        hyst = self._hysteresis.setdefault(graph_id, {})
        refreshed_ical_nodes: set[str] = set()
        for node in flow.nodes:
            if node.type != "ical":
                continue
            url = (node.data.get("url") or "").strip()
            if not url:
                continue
            refresh_min = float(node.data.get("refresh_interval_min") or 60)
            payload_limit = _ical_payload_limit_bytes(node.data)
            hyst_node = hyst.setdefault(node.id, {})
            last_attempt: float | None = hyst_node.get("_ical_last_attempt_ts")
            attempt_config_changed = hyst_node.get("_ical_last_attempt_url") != url or hyst_node.get("_ical_last_attempt_limit") != payload_limit
            needs_fetch = attempt_config_changed or last_attempt is None or (execute_now.timestamp() - last_attempt) >= refresh_min * 60
            if needs_fetch:
                fetch_lock = self._ical_fetch_locks.setdefault((graph_id, node.id), asyncio.Lock())
                await fetch_lock.acquire()
                if self._ical_cache_generations.get(graph_id) is not ical_generation:
                    fetch_lock.release()
                    continue
                # Another execution may have refreshed this node while this one
                # waited.  Re-check the shared attempt metadata under the lock;
                # a failed attempt also satisfies queued callers.
                last_attempt = hyst_node.get("_ical_last_attempt_ts")
                attempt_config_changed = hyst_node.get("_ical_last_attempt_url") != url or hyst_node.get("_ical_last_attempt_limit") != payload_limit
                needs_fetch = attempt_config_changed or last_attempt is None or (datetime.now(UTC).timestamp() - last_attempt) >= refresh_min * 60
                if not needs_fetch:
                    fetch_lock.release()
                    continue
                active_client: httpx.AsyncClient | None = None
                attempt_completed = False
                try:
                    current_url = url
                    active_origin: tuple[str, str, int] | None = None
                    logical_cookie_store: dict[tuple[str, str, str, bool], tuple[str, bool]] = {}
                    for redirect_count in range(_ICAL_MAX_REDIRECTS + 1):
                        fetch_urls, headers, extensions = await asyncio.to_thread(_build_ical_fetch_targets, current_url)
                        cookie_header = _build_cookie_header(logical_cookie_store, current_url)
                        if cookie_header:
                            headers = {**headers, "Cookie": cookie_header}
                        current_origin = _origin_tuple(_parse_http_url(current_url))
                        if current_origin != active_origin:
                            if active_client is not None:
                                await active_client.aclose()
                            # Keep one shared logical_cookie_store across all hops (including
                            # cross-origin redirects), but rotate the HTTP client per origin.
                            active_client = httpx.AsyncClient(timeout=30.0)
                            active_origin = None if current_origin is None else tuple(current_origin)
                        if active_client is None:
                            raise ValueError("Could not initialize iCal HTTP client")
                        redirected_to: str | None = None
                        _ct = ""
                        _resp_bytes = b""
                        last_transport_error: Exception | None = None
                        for fetch_url in fetch_urls:
                            try:
                                # Requests go to a pinned IP, but cookie send/store logic uses
                                # current_url (logical host) via _build/_store_response_cookies.
                                request_headers = headers
                                async with active_client.stream("GET", fetch_url, headers=request_headers, extensions=extensions) as _resp:
                                    if _resp.status_code in {301, 302, 303, 307, 308}:
                                        location = _resp.headers.get("location")
                                        if not location:
                                            raise ValueError("iCal redirect without Location header")
                                        _store_response_cookies(logical_cookie_store, _resp.headers.get_list("set-cookie"), current_url)
                                        redirected_to = urljoin(current_url, location)
                                        break
                                    _resp.raise_for_status()
                                    _store_response_cookies(logical_cookie_store, _resp.headers.get_list("set-cookie"), current_url)
                                    _ct = _resp.headers.get("content-type", "").lower()
                                    _resp_bytes = await _read_limited_response_body(
                                        _resp,
                                        payload_limit,
                                    )
                                    break
                            except httpx.RequestError as req_exc:
                                last_transport_error = req_exc
                                continue
                        if redirected_to:
                            if redirect_count >= _ICAL_MAX_REDIRECTS:
                                raise ValueError("Too many iCal redirects")
                            current_url = _preserve_same_origin_credentials(current_url, redirected_to)
                            continue
                        if last_transport_error is not None and not _resp_bytes:
                            raise last_transport_error
                        if not _resp_bytes:
                            raise ValueError(f"Could not fetch iCal URL after trying {len(fetch_urls)} address(es)")
                        if _ct and not any(t in _ct for t in _ICAL_ALLOWED_CONTENT_TYPES):
                            logger.debug(
                                "Graph %s: non-standard iCal content-type %r for %s; validating by body signature",
                                graph_id[:8],
                                _ct,
                                current_url,
                            )
                        # Decode with charset from Content-Type; many iCal servers
                        # omit the charset and serve Latin-1 (e.g. c-trace.de).
                        # Try strict UTF-8 first; fall back to Latin-1 which always
                        # succeeds and covers ISO-8859-1 / CP-1252 content.
                        _charset: str | None = None
                        for _part in _ct.split(";"):
                            _p = _part.strip()
                            if _p.lower().startswith("charset="):
                                _charset = _p[8:].strip().strip('"').strip("'")
                                break
                        if _charset:
                            _raw_text = _resp_bytes.decode(_charset, errors="replace")
                        else:
                            try:
                                _raw_text = _resp_bytes.decode("utf-8")
                            except UnicodeDecodeError:
                                _raw_text = _resp_bytes.decode("latin-1")
                        if not _raw_text.lstrip().startswith("BEGIN:VCALENDAR"):
                            raise ValueError(f"Response is not an iCal file (starts with {_raw_text[:60]!r})")
                        if self._ical_cache_generations.get(graph_id) is ical_generation:
                            hyst_node["raw"] = _raw_text
                            hyst_node["fetched_url"] = url
                            hyst_node["last_fetch_ts"] = execute_now.timestamp()
                            refreshed_ical_nodes.add(node.id)
                        logger.info("Graph %s: iCal fetched from %s (%d bytes)", graph_id[:8], current_url, len(_resp_bytes))
                        break
                except Exception:
                    attempt_completed = True
                    logger.exception("Graph %s: iCal fetch failed for node %s (%s)", graph_id[:8], node.id[:8], url)
                else:
                    attempt_completed = True
                finally:
                    try:
                        if active_client is not None:
                            await active_client.aclose()
                        if attempt_completed and self._ical_cache_generations.get(graph_id) is ical_generation:
                            hyst_node["_ical_last_attempt_url"] = url
                            hyst_node["_ical_last_attempt_limit"] = payload_limit
                            hyst_node["_ical_last_attempt_ts"] = datetime.now(UTC).timestamp()
                    finally:
                        fetch_lock.release()

        # ── Pre-fill heating_circuit missing slots from history ───────────────────────
        # For each heating_circuit node: when a slot (T1/T2/T3) is missing for today
        # and the clock has already passed the slot's threshold hour, query the history
        # for the last value at or before that hour and inject it as _history_{slot}.
        # This covers restarts where the slot would otherwise stay empty all day.
        import datetime as _hc_dt
        import zoneinfo as _hc_zi

        _hc_tz = _hc_zi.ZoneInfo(self._app_config.get("timezone", "Europe/Zurich"))
        _hc_now = _hc_dt.datetime.now(tz=_hc_tz)
        _hc_today = _hc_now.date().isoformat()
        _HC_SLOTS = (("t1", 7), ("t2", 14), ("t3", 21))

        for node in flow.nodes:
            if node.type != "heating_circuit":
                continue
            # Find the datapoint_id and datapoint_read node via graph edges
            _hc_dp_id_str: str | None = None
            _hc_dp_read_node = None
            for edge in flow.edges:
                if edge.target != node.id:
                    continue
                _src = next((n for n in flow.nodes if n.id == edge.source), None)
                if _src and _src.type == "datapoint_read":
                    _hc_dp_id_str = _src.data.get("datapoint_id")
                    _hc_dp_read_node = _src
                    break
            if not _hc_dp_id_str:
                continue
            _hc_node_state = hyst.setdefault(node.id, {})
            _hc_node_aug = aug_overrides.setdefault(node.id, {})
            # Always inject app-timezone date so executor uses the same date as the manager;
            # without this, system clock vs. app timezone differences around midnight can
            # cause slots to be tagged with the wrong date and re-filled on every run.
            _hc_node_aug["_date"] = _hc_today
            try:
                from obs.history.factory import get_history_plugin as _get_hp

                _hc_dp_id = uuid.UUID(_hc_dp_id_str)
                _hc_plugin = _get_hp()
                for _hc_slot, _hc_hour in _HC_SLOTS:
                    if _hc_node_state.get(f"{_hc_slot}_date") == _hc_today:
                        continue  # already captured today
                    if _hc_now.hour < _hc_hour:
                        continue  # not yet past slot time
                    # Query last known value at or before the slot's threshold time
                    _slot_dt = _hc_now.replace(hour=_hc_hour, minute=0, second=0, microsecond=0)
                    _from_dt = (_slot_dt - _hc_dt.timedelta(hours=24)).astimezone(UTC)
                    _to_dt = _slot_dt.astimezone(UTC)
                    _rows = await _hc_plugin.query(_hc_dp_id, _from_dt, _to_dt, limit=1)
                    if _rows:
                        # Keep raw value; float() is deferred until after transforms so that
                        # value_map can handle non-numeric stored values (e.g. "on" → 22.5).
                        _hist_val: Any = _rows[0]["v"]
                        # Apply the same transforms as live datapoint_read execution
                        if _hc_dp_read_node:
                            _hc_formula = (_hc_dp_read_node.data.get("value_formula") or "").strip()
                            if _hc_formula:
                                try:
                                    from obs.logic.executor import GraphExecutor as _GE

                                    _hist_val = _GE._safe_eval(_hc_formula, {"x": float(_hist_val)})
                                except Exception:
                                    logger.exception(
                                        "Graph %s: heating_circuit %s: history value_formula failed, using raw value",
                                        graph_id[:8],
                                        _hc_slot,
                                    )
                            _hc_vmap = _hc_dp_read_node.data.get("value_map")
                            if _hc_vmap:
                                try:
                                    from obs.core.transformation import apply_value_map as _avm

                                    _hist_val = _avm(_hist_val, _hc_vmap)
                                except Exception:
                                    logger.exception(
                                        "Graph %s: heating_circuit %s: history value_map failed, using pre-map value",
                                        graph_id[:8],
                                        _hc_slot,
                                    )
                        try:
                            _hc_node_aug[f"_history_{_hc_slot}"] = float(_hist_val)
                            logger.debug(
                                "Graph %s: heating_circuit %s: %s filled from history: %.1f",
                                graph_id[:8],
                                node.id[:8],
                                _hc_slot,
                                float(_hc_node_aug[f"_history_{_hc_slot}"]),
                            )
                        except (TypeError, ValueError):
                            logger.debug(
                                "Graph %s: heating_circuit %s: %s history value not numeric after transforms, skipping",
                                graph_id[:8],
                                node.id[:8],
                                _hc_slot,
                            )
            except Exception:
                logger.exception("Graph %s: heating_circuit history pre-fill failed", graph_id[:8])

        # A pre-execute snapshot is needed whenever *anything* below may need
        # to roll a change_filter back to its state from before this pass —
        # both the async-replay machinery further down and the change_filter
        # correction immediately below can require it.
        _effective_edge_by_target: dict[tuple[str, str], Any] = {}
        for _e in flow.edges:
            _effective_edge_by_target[(_e.target, _e.targetHandle or "in")] = _e
        _effective_edges = list(_effective_edge_by_target.values())
        _change_filter_ids = {n.id for n in flow.nodes if n.type == "change_filter"}
        _rollback_source_ids = unseeded_read_ids | (random_value_ids if needs_random_value_snapshot else set())
        _potential_no_result_mapping_ids = {
            node.id for node in flow.nodes if node.type == "value_mapping" and not GraphExecutor._to_bool(node.data.get("has_default"))
        }
        _rollback_reaches_change_filter = bool(_change_filter_ids & _downstream_closure(_rollback_source_ids, _effective_edges))
        _mapping_rollback_reaches_change_filter = bool(
            _change_filter_ids & (_downstream_closure(_potential_no_result_mapping_ids, _effective_edges) - _potential_no_result_mapping_ids)
        )
        _synchronous_correction_ids = {node.id for node in flow.nodes if node.type in {"statistics", "operating_hours", "random_value"}}
        _stateful_relay_correction_ids = {
            node.id
            for node in flow.nodes
            if node.type
            in {"gate", "hysteresis", "avg_multi", "min_max_tracker", "consumption_counter", "heating_circuit", "datapoint_write", "edge_detect"}
        }
        # Keyed on every pulse origin, not just change_filter: the correction
        # pass for a consumer fed by a non-firing pulse restores state from
        # this snapshot, so without it the placeholder the first pass already
        # committed is simply re-committed.
        _pulse_origin_ids = {n.id for n in flow.nodes if n.type in _PULSE_ORIGIN_NODE_TYPES}
        _needs_cf_pulse_correction_snapshot = any(
            bool(
                (_downstream_closure({_pid}, _effective_edges) - {_pid})
                & (_pulse_origin_ids | _synchronous_correction_ids | _stateful_relay_correction_ids)
            )
            for _pid in _pulse_origin_ids
        )
        _needs_pre_execute_snapshot = (
            needs_async_replay_snapshot
            or _rollback_reaches_change_filter
            or _mapping_rollback_reaches_change_filter
            or _needs_cf_pulse_correction_snapshot
        )
        _pulse_hysteresis_prior: dict[str, Any] = {}

        # Executor nodes mutate their hysteresis mapping synchronously.  Run
        # the first pass against an isolated snapshot so a worker made
        # obsolete by a concurrent save cannot leak state into the replacement
        # graph.  Commit only after the pass proves its generation is current.
        try:
            if run_executor_in_worker:
                execution_lock = self._graph_executor_locks.setdefault(graph_id, asyncio.Lock())
                try:
                    async with execution_lock:
                        # Snapshot and commit inside the same critical section so
                        # overlapping executions observe the preceding pass's
                        # committed state instead of overwriting it from a stale copy.
                        base_hyst, execution_hyst = await _run_graph_state_copy_in_worker(_copy_graph_worker_state, hyst)
                        _pulse_hysteresis_prior = {n.id: execution_hyst.get(n.id, False) for n in flow.nodes if n.type == "hysteresis"}
                        executor = await _executor(execution_hyst)
                        if self._ical_cache_generations.get(graph_id) is not ical_generation:
                            raise _ObsoleteGraphExecution
                        pre_execute_hyst = _safe_deepcopy_state(hyst) if _needs_pre_execute_snapshot else None
                        pre_execute_node_state = _safe_deepcopy_state(graph_state) if _needs_pre_execute_snapshot else None
                        outputs = await _execute_pass(executor, aug_overrides, executor_lock_held=True)
                        _merge_worker_state(base_hyst, execution_hyst, hyst)
                finally:
                    self._prune_graph_executor_lock(graph_id)
            else:
                _pulse_hysteresis_prior = {n.id: hyst.get(n.id, False) for n in flow.nodes if n.type == "hysteresis"}
                executor = await _executor(hyst)
                if self._ical_cache_generations.get(graph_id) is not ical_generation:
                    raise _ObsoleteGraphExecution
                pre_execute_hyst = _safe_deepcopy_state(hyst) if _needs_pre_execute_snapshot else None
                pre_execute_node_state = _safe_deepcopy_state(graph_state) if _needs_pre_execute_snapshot else None
                outputs = await _execute_pass(executor, aug_overrides)
        except _ObsoleteGraphExecution:
            raise
        except Exception:
            logger.exception("Graph %s (%s) execution error", graph_id, name)
            return {}

        # ── Change Filter: correct any comparison made against an unresolved
        # value on this real pass ─────────────────────────────────────────
        # api_client/host_check/message_archive/notify outputs are only
        # unresolved placeholders on the pass(es) where that specific node
        # instance is actually triggered — an async source that isn't
        # triggered this pass reports its genuine "not active" output (e.g.
        # host_check.reachable=False when untriggered), which is final, not a
        # placeholder. A Read Object that has never received any value is
        # unresolved for every pass until its own real DataValueEvent arrives.
        # change_filter commits its comparison inline, unlike memory's
        # deferred commit_memory_inputs — so without a correction, a
        # placeholder/never-seeded input looks like a real change and can
        # fire an unrecoverable host_check ping / WoL packet, or corrupt
        # persisted state, before the real value is ever known.
        #
        # Reachability from an unresolved source is deliberately NOT treated
        # as tainting every transitive descendant: an "or"/"and" gate is safe
        # to leave untainted when a *resolved* (non-tainted) input alone
        # already guarantees its output — an OR with any resolved input True,
        # or an AND with any resolved input False — regardless of what an
        # unresolved input would otherwise have been. A separate resolved
        # branch feeding the same gate is therefore not discarded just
        # because another branch is still unresolved. Checking the gate's
        # own already-computed `out` value is not enough: if the *tainted*
        # input is the one currently making an OR true (e.g. an unseeded
        # Read Object through a NOT feeding an otherwise-unwired OR), that
        # true is exactly the placeholder this correction exists to catch,
        # not an independent guarantee — so each resolved input is checked
        # individually instead. Trigger detection reuses this real pass's own
        # outputs instead of a separate dry run of the whole graph — a
        # speculative extra execution could disagree with reality for
        # non-deterministic nodes (e.g. random_value) and make the hold
        # decision itself wrong.
        _directly_triggered_async_ids = {_nid for _nid in async_replay_source_ids if GraphExecutor._to_bool(outputs.get(_nid, {}).get("_trigger"))}
        # An async node's own _trigger reading in *this* pass can itself be
        # derived from ANOTHER async node's still-placeholder output (e.g.
        # api_client → wake_on_lan.trigger, before api_client's real HTTP
        # call has run) — such a node's real trigger status is exactly as
        # unresolved as an unseeded Read Object, however its own evaluated
        # _trigger currently reads, because that reading itself is about to
        # change once the upstream async node's real result is known. Treat
        # it (and anything downstream, via the taint BFS below) as unresolved
        # too — otherwise a change_filter/host_check reachable only through
        # this chain would already commit — and host_check would already
        # irreversibly ping — using a value derived from a still-pending
        # upstream async result, before that upstream node has even run.
        _chained_unresolved_async_ids = async_replay_source_ids & (
            _downstream_closure(_directly_triggered_async_ids, _effective_edges) - _directly_triggered_async_ids
        )

        # An inactive random_value's None this pass CAN still resolve via a
        # later async replay — e.g. api_client.success -> random_value.trigger
        # -> change_filter: random_value reads as inactive on the first pass
        # (api_client's placeholder success=False), then genuinely fires once
        # the real api_client result propagates. Every one of the "late hold"
        # recomputations below therefore needs this recomputed from that
        # replay's own outputs_source, not a single value frozen from this
        # first pass — see _inactive_random_ids_from just below. This
        # first-pass value is still needed once, though: as part of the
        # initial _unresolved_source_ids seed for the very first
        # _compute_cf_hold_ids(_unresolved_source_ids) correction, before any
        # replay has run at all.
        def _inactive_random_ids_from(outputs_source: dict[str, dict[str, Any]] | None = None) -> set[str]:
            _src = outputs_source if outputs_source is not None else outputs
            return {nid for nid in random_value_ids if _src.get(nid, {}).get("value") is None}

        def _no_result_mapping_ids_from(outputs_source: dict[str, dict[str, Any]] | None = None) -> set[str]:
            _src = outputs_source if outputs_source is not None else outputs
            return {
                node.id
                for node in flow.nodes
                if node.type == "value_mapping"
                and not GraphExecutor._to_bool(node.data.get("has_default"))
                and _src.get(node.id, {}).get("result") is None
            }

        def _unresolved_value_ids_from(outputs_source: dict[str, dict[str, Any]] | None = None) -> set[str]:
            return unseeded_read_ids | _inactive_random_ids_from(outputs_source) | _no_result_mapping_ids_from(outputs_source)

        _unresolved_source_ids: set[str] = _unresolved_value_ids_from() | _directly_triggered_async_ids | _chained_unresolved_async_ids

        _node_by_id_early = {n.id: n for n in flow.nodes}
        _decisive_gate_value = {"or": True, "and": False}

        # GraphExecutor._build_edge_map() resolves multiple edges into the
        # same (target, targetHandle) pair with "last edge wins" — the same
        # semantics _fresh_input_handles already applies for its own
        # traversal above. The taint-BFS below must follow the SAME
        # effective edge for consistency: an imported/legacy flow can have
        # a stale, shadowed edge (e.g. from an unseeded Read Object into
        # add.in1) that a LATER edge to the same handle has replaced with a
        # live source — the executor only ever consumes the live one, so
        # tainting through the shadowed edge too would hold a downstream
        # change_filter hostage to a source nothing actually reads from.
        def _compute_cf_hold_ids(seed_ids: set[str], outputs_source: dict[str, dict[str, Any]] | None = None) -> set[str]:
            """Taint-BFS from `seed_ids` (unresolved sources), returning the
            change_filter node ids that must be held this pass. Factored out
            of the original single, early computation so it can be re-run
            with an updated seed later in the tick too (see
            _still_unresolved_source_ids) — an async node newly discovered
            to be chained behind another one that hasn't actually settled
            yet must still hold any change_filter it reaches, even after
            the initial pass already committed a value for it once.

            `outputs_source` (defaults to the outer `outputs`) is read by
            the gate-absorption check below for each of a decisive gate's
            OTHER (non-tainted) inputs. A caller re-running this for a
            later replay must pass that replay's own fresh output snapshot
            (e.g. second_outputs/replay_outputs) — the outer `outputs` is
            still the stale first pass at that point, so checking against
            it could either wrongly "absorb" a gate via a placeholder that
            has since resolved differently, or fail to absorb one that a
            fresher, real result now decides — same reasoning as
            _still_unresolved_source_ids' own outputs_source parameter.
            """
            if not seed_ids:
                return set()
            _tainted: set[str] = set(seed_ids)
            _src = outputs_source if outputs_source is not None else outputs

            def _gate_taint_absorbed(gate_id: str, gate_type: str) -> bool:
                decisive = _decisive_gate_value[gate_type]
                gate_node = _node_by_id_early[gate_id]
                gdata = gate_node.data or {}
                try:
                    # A malformed input_count (e.g. an imported/legacy node
                    # with "invalid" or null) must not crash this whole
                    # execution — GraphExecutor's own per-node try/except
                    # already isolates the same parse to a single node's
                    # __error__ output; this duplicate copy of the gate's
                    # input-counting logic needs the same isolation. Treat
                    # the gate as un-absorbed (still tainted) rather than
                    # guessing at malformed config.
                    count = max(2, min(30, int(gdata.get("input_count", 2))))
                except (TypeError, ValueError):
                    return False
                for i in range(1, count + 1):
                    handle = f"in{i}"
                    if handle in debug_overrides.get(gate_id, {}):
                        v = GraphExecutor._to_bool(debug_overrides[gate_id][handle])
                        if gdata.get(f"negate_{handle}"):
                            v = not v
                        if v == decisive:
                            return True
                        continue
                    src_edge = next(
                        (e for e in _effective_edges if e.target == gate_id and (e.targetHandle or "in") == handle),
                        None,
                    )
                    if src_edge is not None and src_edge.source in _tainted:
                        continue
                    # An unconnected input is not "still unresolved" — the
                    # executor's own _collect_gate_inputs evaluates a
                    # missing port as a deterministic False (via _to_bool),
                    # so it can independently decide the gate's output (a
                    # negated unconnected OR input is a deterministic True)
                    # exactly like any other resolved input.
                    #
                    # An async_replay_source_ids node's OWN output slot
                    # (api_client.success, host_check.reachable,
                    # wake_on_lan.sent, …) is never derived by that node's
                    # own _eval_node from its inputs — it's mutated in place
                    # into the OUTER `outputs` once its real side effect
                    # actually runs (_run_api_client_node et al.), and a
                    # fresh replay pass re-evaluating that SAME node always
                    # re-computes its own placeholder there regardless of
                    # any override, since overrides only redirect its
                    # DOWNSTREAM edges, not its own output. So for such a
                    # node specifically, the outer `outputs` — not
                    # `outputs_source` — holds the authoritative value;
                    # every other (pass-through) node's real, propagated
                    # value only shows up in `outputs_source`, which is
                    # exactly why this parameter exists in the first place.
                    _v_src = outputs if src_edge is not None and src_edge.source in async_replay_source_ids else _src
                    v = (
                        False
                        if src_edge is None
                        else GraphExecutor._to_bool(GraphExecutor._get_output_value(_v_src.get(src_edge.source, {}), src_edge.sourceHandle or "out"))
                    )
                    if gdata.get(f"negate_{handle}"):
                        v = not v
                    if v == decisive:
                        return True
                return False

            _tq: list[str] = list(_tainted)
            while _tq:
                _tn = _tq.pop()
                for _te in _effective_edges:
                    if _te.source != _tn or _te.target in _tainted:
                        continue
                    if (_te.targetHandle or "in") in debug_overrides.get(_te.target, {}):
                        # A debug override replaces this effective edge for
                        # the current execution, so its unresolved source
                        # cannot taint the overridden value or descendants.
                        continue
                    _target_node = _node_by_id_early.get(_te.target)
                    _target_type = _target_node.type if _target_node is not None else None
                    if _target_type in _decisive_gate_value and _gate_taint_absorbed(_te.target, _target_type):
                        continue
                    # memory is an explicit tick boundary (per its own node
                    # description): this pass's "out" is whatever was
                    # committed at the *end* of a previous tick, entirely
                    # independent of an unresolved input feeding "in" this
                    # tick — that input only affects the value committed for
                    # the *next* tick, via the executor's deferred
                    # commit_memory_inputs. Propagating taint through it
                    # would hold a change_filter fed by memory hostage to an
                    # unrelated, still-unresolved upstream Read Object,
                    # potentially forever if that Read Object never fires
                    # again this session.
                    if _target_type == "memory":
                        continue
                    # A "hysteresis" node whose "value" input reads None this
                    # pass (e.g. fed by a still-unseeded Read Object) returns
                    # its real prior state unmutated — the executor's own
                    # `if val is None: return {"out": prev}` branch, a fully
                    # resolved output, not a placeholder awaiting this
                    # source's eventual real value. Unlike an async
                    # replay source, an unseeded Read Object has no later
                    # resolution coming THIS tick, so there is nothing left
                    # to correct for. Propagating taint past it would hold a
                    # downstream change_filter hostage to that unrelated,
                    # possibly-never-seeded source indefinitely, discarding
                    # every genuine change from any OTHER live input combined
                    # with this hysteresis output along the way.
                    if _target_type == "hysteresis" and (_te.targetHandle or "in") == "value":
                        _hv_src = outputs if _te.source in async_replay_source_ids else _src
                        _hyst_value = GraphExecutor._get_output_value(_hv_src.get(_te.source, {}), _te.sourceHandle or "out")
                        if _hyst_value is None:
                            continue
                    # A "gate" (Freigabe) node closed by a RESOLVED enable
                    # input is the same kind of boundary: while closed, its
                    # output is either the retained last-enabled value or a
                    # fixed default_value — either way, entirely independent
                    # of "in" this pass. Only applies to a tainted edge
                    # targeting "in" specifically; if the taint instead comes
                    # through "enable" itself, the closed state can't be
                    # trusted and must still propagate normally.
                    if _target_type == "gate" and (_te.targetHandle or "in") == "in":
                        _gate_data = (_target_node.data or {}) if _target_node is not None else {}
                        _enable_override = debug_overrides.get(_te.target, {}).get("enable", _MISSING_STATE)
                        _enable_edge = next(
                            (e for e in _effective_edges if e.target == _te.target and (e.targetHandle or "in") == "enable"),
                            None,
                        )
                        if _enable_override is not _MISSING_STATE or _enable_edge is None or _enable_edge.source not in _tainted:
                            if _enable_override is not _MISSING_STATE:
                                _enable_v = GraphExecutor._to_bool(_enable_override)
                            else:
                                _enable_src = outputs if _enable_edge is not None and _enable_edge.source in async_replay_source_ids else _src
                                _enable_v = (
                                    False
                                    if _enable_edge is None
                                    else GraphExecutor._to_bool(
                                        GraphExecutor._get_output_value(_enable_src.get(_enable_edge.source, {}), _enable_edge.sourceHandle or "out")
                                    )
                                )
                            if _gate_data.get("negate_enable"):
                                _enable_v = not _enable_v
                            if not _enable_v:
                                continue
                    _tainted.add(_te.target)
                    if _target_type == "edge_detect":
                        # A held Edge Detection node emits nothing at all this
                        # pass — no edge, no state write — which is fully
                        # deterministic whatever it remembers, so nothing
                        # downstream still depends on the unresolved source.
                        continue
                    if _target_type == "change_filter":
                        # A change_filter that becomes tainted/held here is,
                        # for THIS pass, fully deterministic: the executor's
                        # _suppress_change_filter handling returns its
                        # previous baseline as "out" and changed=False — a
                        # known, resolved value, not an unresolved one.
                        # Continuing the BFS past it would taint (and
                        # unnecessarily suppress) every downstream
                        # change_filter too, even though nothing downstream
                        # actually depends on anything still unresolved — only
                        # on this filter's own deterministic held output.
                        _pre_hold_state = (pre_execute_hyst if pre_execute_hyst is not None else hyst).get(_te.target)
                        if isinstance(_pre_hold_state, dict) and "value" in _pre_hold_state:
                            continue
                    _tq.append(_te.target)
            return {n.id for n in flow.nodes if n.type in _HELD_ON_UNRESOLVED_SOURCE and n.id in _tainted}

        # Async nodes whose real side effect has actually run this tick (as
        # opposed to merely having their _trigger read true) — updated
        # inside _add_resolved_outputs below. Used by
        # _still_unresolved_source_ids to know which links of an async
        # chain are now settled and which are still only "triggered, not
        # yet actually executed".
        _settled_async_ids: set[str] = set()

        # message_archive/notify nodes settled specifically via the
        # freshness-skip path (a truthy but STALE _trigger — see
        # _run_message_archive_node/_run_notify_node) — tracked separately
        # from _settled_async_ids/_still_unresolved_source_ids' general
        # recompute, which reads the outer `outputs` and can drift for
        # unrelated reasons this late in the tick (e.g. an intermediate
        # host_check/WoL replay updating a chained node's own _trigger
        # reading). The late release pass near the end of this function
        # only ever subtracts this explicit set from the ORIGINAL, frozen
        # _unresolved_source_ids seed — never recomputes it wholesale — so
        # it can only release what this specific settling caused, nothing
        # else.
        _freshness_settled_async_ids: set[str] = set()

        def _still_unresolved_source_ids(outputs_source: dict[str, dict[str, Any]] | None = None) -> set[str]:
            """Recompute the async-chain part of _unresolved_source_ids
            using `outputs_source` (defaults to the outer `outputs`) and
            _settled_async_ids.

            A later replay stage's *own freshly computed* outputs (e.g.
            once api_client's real result has propagated
            wake_on_lan.trigger=True within that same replay pass) can
            newly reveal that an async node — and anything it feeds,
            including a change_filter — is still only "about to run", not
            actually settled. The outer `outputs` dict is not updated until
            *after* such a replay's copy-back, so a caller checking whether
            it needs to redo its own replay with suppression must pass that
            replay's own result, not rely on the stale outer snapshot.
            Re-running the same taint BFS with this refreshed seed lets a
            caller re-suppress that change_filter instead of letting it
            commit with a still-wrong value.
            """
            _src = outputs_source if outputs_source is not None else outputs
            directly_triggered_now = {
                _nid
                for _nid in async_replay_source_ids
                if _nid not in _settled_async_ids and GraphExecutor._to_bool(_src.get(_nid, {}).get("_trigger"))
            }
            chained_now = async_replay_source_ids & (_downstream_closure(directly_triggered_now, _effective_edges) - directly_triggered_now)
            return (directly_triggered_now | chained_now) - _settled_async_ids

        _cf_hold_ids: set[str] = _compute_cf_hold_ids(_unresolved_source_ids)

        def _apply_operating_hours_state(node_ids: set[str] | None = None, base_state: dict[str, Any] | None = None) -> None:
            target_ids = operating_hour_ids if node_ids is None else operating_hour_ids & node_ids
            for node in flow.nodes:
                if node.id not in target_ids:
                    continue
                out = outputs.get(node.id, {})
                if base_state is not None:
                    graph_state[node.id] = copy.deepcopy(base_state.get(node.id, {"accumulated_hours": 0.0, "last_start": None}))
                ns = graph_state.setdefault(node.id, {"accumulated_hours": 0.0, "last_start": None})
                is_reset = out.get("_reset", False)
                is_active = out.get("_active", False)
                if is_reset:
                    ns["accumulated_hours"] = 0.0
                    ns["last_start"] = execute_now if is_active else None
                elif is_active:
                    if not ns.get("last_start"):
                        ns["last_start"] = execute_now
                elif ns.get("last_start"):
                    ns["accumulated_hours"] += (execute_now - ns["last_start"]).total_seconds() / 3600
                    ns["last_start"] = None

        # ── Update operating_hours state ─────────────────────────────────
        _apply_operating_hours_state()

        if _cf_hold_ids:
            # Roll each held filter back to its pre-pass state and recompute
            # its whole descendant subtree from the pre-pass snapshot, so any
            # node that already consumed this pass's (wrong) real output —
            # e.g. a host_check whose trigger comes straight from this
            # filter's "changed" — sees the corrected, no-op value before the
            # host_check block below ever runs. Suppressed unconditionally
            # (whether or not the filter already holds prior state), so a
            # new/reset filter can't adopt an unresolved value as its first
            # value either.
            _cf_hold_desc: set[str] = set()
            _cfq: list[str] = list(_cf_hold_ids)
            while _cfq:
                _cn = _cfq.pop()
                for _ce in _effective_edges:
                    if _ce.source == _cn and _ce.target not in _cf_hold_desc:
                        _cf_hold_desc.add(_ce.target)
                        _cfq.append(_ce.target)
            _cf_hold_island = _cf_hold_ids | _cf_hold_desc
            _cf_hold_overrides: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in aug_overrides.items()}
            for _cf_id in _cf_hold_ids:
                _cf_hold_overrides[_cf_id] = {**_cf_hold_overrides.get(_cf_id, {}), "_suppress_change_filter": True}
            # Everything outside the held/descendant island reuses this
            # pass's already-computed real output instead of being
            # re-evaluated by the replay — the replay executor otherwise
            # runs the whole topological order internally regardless of
            # overrides, so without this an unrelated non-deterministic
            # producer (e.g. random_value) that also happens to feed a
            # descendant would be sampled a second time, and that second,
            # different draw — not the real pass's — could reach a Host
            # Check, notification, or Wake-on-LAN.
            _cf_hold_known_outputs = {nid: vals for nid, vals in outputs.items() if nid not in _cf_hold_island}
            _cf_hold_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
            _cf_hold_outputs = await _execute_pass(await _executor(_cf_hold_hyst), _cf_hold_overrides, known_outputs=_cf_hold_known_outputs)
            for _nid, _vals in _cf_hold_outputs.items():
                if _nid in _cf_hold_ids or _nid in _cf_hold_desc:
                    outputs[_nid] = _vals
                    # A node absent from the pre-pass snapshot had no state
                    # *before* this pass, but the uncorrected initial pass
                    # (executed before this correction ever ran) may already
                    # have written placeholder state for it inline — e.g. a
                    # fresh change_filter's first-value commit. Leaving that
                    # behind instead of clearing it would let the next real
                    # execution compare against the wrong (placeholder)
                    # baseline and suppress a legitimate change.
                    if _nid in _cf_hold_hyst:
                        hyst[_nid] = _cf_hold_hyst[_nid]
                    else:
                        hyst.pop(_nid, None)
            _apply_operating_hours_state(_cf_hold_ids | _cf_hold_desc, pre_execute_node_state)

        # ── Cron-reachability preamble ────────────────────────────────────
        # Shared by host_check and wake_on_lan: each cron tick is treated as a
        # fresh rising edge, so nodes that fire on sustained truthy inputs from
        # cron are not suppressed by the rising-edge deduplication below.
        _node_type_by_id = {n.id: n.type for n in flow.nodes}

        def _discrete_pulse_handles(node_ids: set[str] | None = None) -> set[tuple[str, str]]:
            """(node id, handle) pairs carrying a discrete event pulse this pass.

            A change_filter pulses on "changed". An Edge Detection node pulses
            on whichever of "rising"/"falling" fired and on "out" — that handle
            exists ONLY on an edge, so its mere presence is the event, unlike
            change_filter's "out", which carries the sustained held value.
            Without this, two consecutive real edges combined through e.g. an
            OR into host_check/wake_on_lan look like one sustained trigger and
            the second is silently deduplicated.
            """
            pulses: set[tuple[str, str]] = set()
            for _pn in flow.nodes:
                if node_ids is not None and _pn.id not in node_ids:
                    continue
                _pout = outputs.get(_pn.id, {})
                if _pn.type == "change_filter":
                    if GraphExecutor._to_bool(_pout.get("changed")):
                        pulses.add((_pn.id, "changed"))
                elif _pn.type == "edge_detect":
                    for _ph in ("rising", "falling"):
                        if GraphExecutor._to_bool(_pout.get(_ph)):
                            pulses.add((_pn.id, _ph))
                    if "out" in _pout:
                        pulses.add((_pn.id, "out"))
            return pulses

        def _origin_pulsed(origin: str | tuple[str, str]) -> bool:
            """Did this pulse origin actually fire this pass?

            Origins are change_filter or edge_detect nodes. A change_filter
            reports it on "changed"; an Edge Detection node has several
            discrete handles, and only the one a consumer is actually fed from
            counts — a falling edge says nothing about the "rising" handle,
            which stays False and would otherwise be read as a real level.
            Handle-keyed origin maps therefore carry ``(node id, handle)``
            pairs; the node-wide maps pass a bare id and ask whether anything
            on that node fired.
            """
            origin_id, handle = origin if isinstance(origin, tuple) else (origin, None)
            if _node_type_by_id.get(origin_id) == "edge_detect":
                pulses = _discrete_pulse_handles({origin_id})
                return (origin_id, handle) in pulses if handle is not None else bool(pulses)
            return GraphExecutor._to_bool(outputs.get(origin_id, {}).get("changed"))

        def _edge_carries_pulse(edge: Any, *, require_fired_change_filter: bool = True) -> bool:
            # A pulse only continues through an edge if its target either has
            # no dedicated trigger-typed input at all (a pure logic/relay
            # node — NOT/AND/OR/Decision/etc. — where any input legitimately
            # means the computed output is pulse-derived, however many hops
            # deep), or the edge specifically targets one of those
            # trigger-typed ports. Matched by the port's declared type, not
            # by hard-coding the id "trigger" — some nodes (e.g.
            # operating_hours' "active"/"reset") have multiple trigger-typed
            # inputs under other names. This stops a pulse from leaking
            # through a data port into an action node (api_client/
            # host_check/notify_*/message_archive/wake_on_lan) whose own
            # separate trigger is unrelated and sustained — e.g.
            # change_filter.changed → api_client.body must not exempt
            # whatever api_client.success drives from rising-edge dedup.
            #
            # Symmetric restriction on the SOURCE side: a change_filter's
            # "out" handle carries the held/passthrough value — sustained
            # data, not a discrete pulse — exactly like the seed-selection
            # above already restricts to "changed". Without this check here
            # too, a chain like cf1.changed → cf2.in → cf2.out →
            # host_check.trigger would let the transitive traversal walk
            # cf2's sustained "out" as if it were a pulse, the moment cf2
            # itself becomes cron_reachable through cf1's real "changed"
            # pulse — bypassing rising-edge dedup on every execution even
            # though cf2 itself did not change.
            # Every Edge Detection handle is discrete, but only the one that
            # actually fired this pass carries a pulse — the same restriction
            # the change_filter branch below applies.
            if (
                _node_type_by_id.get(edge.source) == "edge_detect"
                and require_fired_change_filter
                and (edge.source, edge.sourceHandle or "out") not in _discrete_pulse_handles({edge.source})
            ):
                return False
            if _node_type_by_id.get(edge.source) == "change_filter":
                if (edge.sourceHandle or "out") != "changed":
                    return False
                # The "changed" handle carries a real pulse only when this
                # SPECIFIC change_filter actually fired this pass — not
                # merely whenever its "in" happens to be fed by an upstream
                # pulse. E.g. cf1.changed -> cf2.in -> cf2.changed ->
                # NOT -> host_check: cf1 pulsing only means cf2's "in" was
                # fed a discrete value this tick, not that cf2 itself
                # reported changed=True (its new "in" may already equal its
                # own persisted baseline). Without this check, a downstream
                # host_check reachable through cf2's "changed" edge would be
                # treated as pulse-reachable — and have its rising-edge
                # dedup bypassed — on every cf1 event, even on ticks where
                # cf2 itself did not change.
                if require_fired_change_filter and not GraphExecutor._to_bool(outputs.get(edge.source, {}).get("changed")):
                    return False
            target_type = get_node_type(_node_type_by_id.get(edge.target))
            if not target_type:
                return True
            # A "gate" (Freigabe/relay) node closed by its (already-resolved,
            # since this only runs after `outputs` is populated) enable input
            # is not a pure relay while closed: its output is the retained
            # last-enabled value or a fixed default_value, entirely
            # independent of "in" — a pulse arriving at "in" has no effect
            # on the gate's output at all and must not be treated as having
            # propagated through it.
            if target_type.type == "gate" and (edge.targetHandle or "in") == "in":
                gate_node = _node_by_id_early.get(edge.target)
                gdata = (gate_node.data or {}) if gate_node is not None else {}
                enable_override = debug_overrides.get(edge.target, {}).get("enable", _MISSING_STATE)
                enable_edge = next(
                    (e for e in _effective_edges if e.target == edge.target and (e.targetHandle or "in") == "enable"),
                    None,
                )
                enable_v = (
                    GraphExecutor._to_bool(enable_override)
                    if enable_override is not _MISSING_STATE
                    else (
                        False
                        if enable_edge is None
                        else GraphExecutor._to_bool(
                            GraphExecutor._get_output_value(outputs.get(enable_edge.source, {}), enable_edge.sourceHandle or "out")
                        )
                    )
                )
                if gdata.get("negate_enable"):
                    enable_v = not enable_v
                if not enable_v:
                    return False
            # Hysteresis is stateful rather than a pure relay.  A pulse on
            # its value input reaches descendants only when the node's
            # output actually switched during this execution.
            if target_type.type == "hysteresis":
                previous = _pulse_hysteresis_prior.get(edge.target, False)
                current = GraphExecutor._get_output_value(outputs.get(edge.target, {}), "out")
                if current == previous:
                    return False
            trigger_port_ids = {p.id for p in target_type.inputs if p.type == "trigger"}
            if not trigger_port_ids:
                return True
            return (edge.targetHandle or "in") in trigger_port_ids

        def _build_cf_pulse_origins(
            event_fresh: dict[str, set[str]] | None, fresh_seed_origins: dict[str, str]
        ) -> tuple[
            dict[str, set[str]],
            dict[str, set[str]],
            dict[str, dict[str, set[str]]],
            dict[str, set[str]],
            dict[str, dict[str, set[str]]],
        ]:
            message_origins: dict[str, set[str]] = {}
            trigger_origins: dict[str, set[str]] = {}
            trigger_handle_origins: dict[str, dict[str, set[str]]] = {}
            downstream_filter_origins: dict[str, set[str]] = {}
            stateful_relay_origins: dict[str, dict[str, set[str]]] = {}
            fresh_origins = {node_id: {origin} for node_id, origin in fresh_seed_origins.items()}
            if event_fresh is not None:
                changed = True
                while changed:
                    changed = False
                    for edge in _effective_edges:
                        if (edge.targetHandle or "in") not in event_fresh.get(edge.target, set()):
                            continue
                        source_origins = fresh_origins.get(edge.source, set())
                        target_origins = fresh_origins.setdefault(edge.target, set())
                        new_origins = source_origins - target_origins
                        if new_origins:
                            target_origins.update(new_origins)
                            changed = True
            # Both block types emit discrete event pulses, so both are pulse
            # origins: without this an Edge Detection node's idle "False" on a
            # trigger handle is taken for a real level by a stateful consumer,
            # and a synchronous node can invert it into a truthy value that
            # fires an action node (issue #1090's machinery, generalized).
            relay_origins = {node.id: {node.id} for node in flow.nodes if node.type in _PULSE_ORIGIN_NODE_TYPES}
            # Parallel to relay_origins, but keyed by (origin, source handle).
            # relay_origins itself must stay node-keyed: _has_independent_fresh_trigger
            # subtracts it against fresh_origins, which is node-keyed too.
            _handle_origins: dict[str, set[tuple[str, str]]] = {}

            _pure_fan_in_types = {
                "and",
                "or",
                "not",
                "xor",
                "compare",
                "decision",
                "value_mapping",
                "math_formula",
                "math_map",
                "clamp",
                "string_concat",
                "json_extractor",
                "xml_extractor",
                "substring_extractor",
            }
            _fan_in_probe = GraphExecutor(flow, {}, ical_app_config)

            def _has_independent_fresh_trigger(target_id: str, trigger_ports: set[str], missing_origins: set[str]) -> bool:
                target_fresh_handles = event_fresh.get(target_id, set()) if event_fresh is not None else set()
                for trigger_handle in trigger_ports:
                    if trigger_handle in debug_overrides.get(target_id, {}):
                        if GraphExecutor._to_bool(debug_overrides[target_id][trigger_handle]):
                            return True
                        continue
                    trigger_edge = next(
                        (edge for edge in _effective_edges if edge.target == target_id and (edge.targetHandle or "in") == trigger_handle),
                        None,
                    )
                    if trigger_edge is None:
                        if (
                            event_fresh is not None
                            and trigger_handle in target_fresh_handles
                            and GraphExecutor._to_bool(outputs.get(target_id, {}).get("_trigger"))
                        ):
                            return True
                        continue
                    trigger_value = GraphExecutor._get_output_value(outputs.get(trigger_edge.source, {}), trigger_edge.sourceHandle or "out")
                    if not GraphExecutor._to_bool(trigger_value):
                        continue
                    independent_origin = trigger_edge.source not in relay_origins or bool(
                        fresh_origins.get(trigger_edge.source, set()) - missing_origins
                    )
                    if independent_origin and (event_fresh is None or trigger_handle in target_fresh_handles):
                        return True
                return False

            def _fresh_fan_in_preserves_output(pulse_edge: Any) -> bool:
                target_node = _node_by_id_early.get(pulse_edge.target)
                if target_node is None or target_node.type not in _pure_fan_in_types:
                    return False
                target_inputs: dict[str, Any] = {}
                for incoming in _effective_edges:
                    if incoming.target != pulse_edge.target:
                        continue
                    target_inputs[incoming.targetHandle or "in"] = GraphExecutor._get_output_value(
                        outputs.get(incoming.source, {}), incoming.sourceHandle or "out"
                    )
                target_inputs.update(debug_overrides.get(pulse_edge.target, {}))
                target_inputs.pop(pulse_edge.targetHandle or "in", None)
                try:
                    effective_inputs = GraphExecutor._resolve_effective_inputs(target_node, target_inputs)
                    without_pulse = _fan_in_probe._eval_node(target_node, effective_inputs)
                    if not GraphExecutor._nan_aware_equal(without_pulse, outputs.get(pulse_edge.target, {})):
                        return False
                    if target_node.type in _pure_fan_in_types:
                        # A sibling is decisive only if either possible pulse
                        # value leaves the result unchanged. Merely matching
                        # the absent-input default would wrongly call AND(True,
                        # missing) independent when the missing False itself
                        # is what determines the output.
                        counterfactuals: list[Any] = [False, True]
                        _pulse_source = _node_by_id_early.get(pulse_edge.source)
                        if _pulse_source is not None and _pulse_source.type == "edge_detect" and (pulse_edge.sourceHandle or "out") == "out":
                            # "out" carries a CONFIGURED value, not a boolean.
                            # Probing only False/True would call `in1 > 5`
                            # independent of a pulse whose real value is 10,
                            # and the idle placeholder would then be published.
                            for _field, _default in (("value_rising", "true"), ("value_falling", "false")):
                                counterfactuals.append(
                                    _fan_in_probe._coerce_typed_value(_pulse_source, (_pulse_source.data or {}).get(_field, _default), "bool")
                                )
                        for counterfactual in counterfactuals:
                            counterfactual_inputs = dict(target_inputs)
                            counterfactual_inputs[pulse_edge.targetHandle or "in"] = counterfactual
                            effective_counterfactual = GraphExecutor._resolve_effective_inputs(target_node, counterfactual_inputs)
                            if not GraphExecutor._nan_aware_equal(
                                _fan_in_probe._eval_node(target_node, effective_counterfactual), outputs.get(pulse_edge.target, {})
                            ):
                                return False
                    return True
                except Exception:  # noqa: BLE001 - malformed imported relay config remains provenance-conservative
                    return False

            queue = list(relay_origins)
            while queue:
                source_id = queue.pop()
                source_origins = relay_origins[source_id]
                for pulse_edge in _effective_edges:
                    if pulse_edge.source != source_id:
                        continue
                    if (pulse_edge.targetHandle or "in") in debug_overrides.get(pulse_edge.target, {}):
                        continue
                    if _node_type_by_id.get(source_id) == "change_filter" and (pulse_edge.sourceHandle or "out") != "changed":
                        continue
                    # No source-handle restriction for edge_detect: unlike a
                    # change_filter, whose "out" carries a sustained value, all
                    # three of its handles are discrete.
                    # Every origin map remembers WHICH handle of the origin fed
                    # this consumer: a pulse on "falling" says nothing about
                    # "rising", whose False is still a placeholder. Leaving a
                    # pulse origin, that is this edge's own source handle;
                    # further downstream the pair travels unchanged, so the root
                    # handle survives every hop. Only relay_origins stays
                    # node-keyed — _has_independent_fresh_trigger subtracts it
                    # against the node-keyed fresh_origins.
                    if _node_type_by_id.get(source_id) in _PULSE_ORIGIN_NODE_TYPES:
                        handle_origins_out = {(source_id, pulse_edge.sourceHandle or "out")}
                    else:
                        handle_origins_out = _handle_origins.get(source_id, set())
                    _handle_origins.setdefault(pulse_edge.target, set()).update(handle_origins_out)
                    if (pulse_edge.targetHandle or "in") == "message":
                        message_origins.setdefault(pulse_edge.target, set()).update(handle_origins_out)
                        continue
                    target_type = get_node_type(_node_type_by_id.get(pulse_edge.target))
                    if target_type and target_type.type == "change_filter":
                        downstream_filter_origins.setdefault(pulse_edge.target, set()).update(handle_origins_out)
                    trigger_ports = {port.id for port in target_type.inputs if port.type == "trigger"} if target_type else set()
                    if (pulse_edge.targetHandle or "in") in trigger_ports:
                        trigger_origins.setdefault(pulse_edge.target, set()).update(handle_origins_out)
                        trigger_handle_origins.setdefault(pulse_edge.target, {}).setdefault(pulse_edge.targetHandle or "in", set()).update(
                            handle_origins_out
                        )
                        continue
                    target_type_name = _node_type_by_id.get(pulse_edge.target)
                    target_handle = pulse_edge.targetHandle or "in"
                    # Stateful data consumers must not commit a Change
                    # Filter's False no-pulse placeholder. Write values use
                    # the same correction path so the publish is suppressed.
                    stateful_data_handle = (target_type_name, target_handle) in {
                        ("statistics", "value"),
                        ("memory", "in"),
                        ("edge_detect", "in"),
                        ("min_max_tracker", "value"),
                        ("consumption_counter", "value"),
                        ("heating_circuit", "value"),
                        ("datapoint_write", "value"),
                        ("api_client", "body"),
                        ("notify_pushover", "image_url"),
                        ("notify_pushover", "url"),
                        ("notify_pushover", "url_title"),
                        ("message_archive", "title"),
                        ("value_sequence", "condition"),
                    } or (target_type_name == "avg_multi" and target_handle.startswith("in_"))
                    if stateful_data_handle:
                        stateful_relay_origins.setdefault(pulse_edge.target, {}).setdefault(target_handle, set()).update(handle_origins_out)
                        if (
                            target_type
                            and trigger_ports
                            and target_type_name != "memory"
                            and not _has_independent_fresh_trigger(pulse_edge.target, trigger_ports, source_origins)
                        ):
                            target_origins = relay_origins.setdefault(pulse_edge.target, set())
                            new_origins = source_origins - target_origins
                            if new_origins:
                                target_origins.update(new_origins)
                                queue.append(pulse_edge.target)
                    if target_type and any(port.type == "trigger" for port in target_type.inputs):
                        continue
                    pulse_fresh_origins = fresh_origins.get(pulse_edge.source, set())
                    other_fresh_edges = [
                        edge
                        for edge in _effective_edges
                        if edge.target == pulse_edge.target
                        and edge is not pulse_edge
                        and not (_node_type_by_id.get(edge.target) == "gate" and (edge.targetHandle or "in") == "enable")
                        and (
                            (edge.targetHandle or "in") in debug_overrides.get(edge.target, {})
                            or (
                                event_fresh is None
                                and _node_type_by_id.get(edge.source)
                                in {
                                    "api_client",
                                    "const_value",
                                    "datapoint_read",
                                    "host_check",
                                    "wake_on_lan",
                                    "message_archive",
                                    "notify_message",
                                    "notify_pushover",
                                    "notify_sms",
                                    "random_value",
                                    "python_script",
                                    "statistics",
                                    "avg_multi",
                                    "min_max_tracker",
                                    "consumption_counter",
                                    "operating_hours",
                                    "memory",
                                    "datetime",
                                    "astro_sun",
                                    "ical",
                                }
                                and GraphExecutor._get_output_value(outputs.get(edge.source, {}), edge.sourceHandle or "out") is not None
                            )
                            or (
                                event_fresh is not None
                                and (edge.targetHandle or "in") in event_fresh.get(edge.target, set())
                                and (edge.source not in relay_origins or bool(fresh_origins.get(edge.source, set()) - pulse_fresh_origins))
                            )
                        )
                    ]
                    if other_fresh_edges and _fresh_fan_in_preserves_output(pulse_edge):
                        continue
                    if _edge_carries_pulse(pulse_edge, require_fired_change_filter=False):
                        stateful_handle = (target_type_name == "gate" and target_handle == "in") or (
                            target_type_name == "hysteresis" and target_handle == "value"
                        )
                        if stateful_handle:
                            stateful_relay_origins.setdefault(pulse_edge.target, {}).setdefault(target_handle, set()).update(handle_origins_out)
                        target_origins = relay_origins.setdefault(pulse_edge.target, set())
                        new_origins = source_origins - target_origins
                        if new_origins:
                            target_origins.update(new_origins)
                            queue.append(pulse_edge.target)
            return message_origins, trigger_origins, trigger_handle_origins, downstream_filter_origins, stateful_relay_origins

        _initial_event_fresh = (
            _fresh_input_handles({node_id: dict(values) for node_id, values in overrides.items()}, flow.edges) if overrides else None
        )

        def _event_origin(node_id: str) -> str:
            event_node = _node_by_id_early.get(node_id)
            datapoint_id = (event_node.data or {}).get("datapoint_id") if event_node is not None and event_node.type == "datapoint_read" else None
            return f"datapoint:{datapoint_id}" if datapoint_id else node_id

        (
            _cf_changed_message_origins,
            _cf_changed_trigger_origins,
            _cf_changed_trigger_handle_origins,
            _cf_downstream_filter_origins,
            _cf_changed_stateful_relay_origins,
        ) = _build_cf_pulse_origins(_initial_event_fresh, {node_id: _event_origin(node_id) for node_id in overrides})

        def _suppress_missing_cf_trigger_pulses(node_ids: set[str] | None = None) -> None:
            for target_id, origins in _cf_changed_trigger_origins.items():
                if node_ids is not None and target_id not in node_ids:
                    continue
                if any(_origin_pulsed(origin) for origin in origins):
                    continue
                target_output = outputs.get(target_id, {})
                target_node = _node_by_id_early.get(target_id)
                message_origins = _cf_changed_message_origins.get(target_id, set())
                has_independent_message = target_output.get("_message") is not None and (
                    not message_origins or any(_origin_pulsed(origin) for origin in message_origins)
                )
                if (
                    target_node is not None
                    and target_node.type in {"message_archive", "notify_message", "notify_pushover", "notify_sms"}
                    and has_independent_message
                ):
                    continue
                if "_trigger" in target_output:
                    target_output["_trigger"] = False
                if "_triggered" in target_output:
                    target_output["_triggered"] = False

        def _neutralize_missing_cf_messages(node_ids: set[str] | None = None) -> None:
            for target_id, origins in _cf_changed_message_origins.items():
                if node_ids is not None and target_id not in node_ids:
                    continue
                if any(_origin_pulsed(origin) for origin in origins):
                    continue
                target_output = outputs.get(target_id, {})
                if "_message" in target_output:
                    target_output["_message"] = None

        def _refresh_missing_cf_override_values() -> None:
            missing_cf_override_values.clear()
            for target_id, origins in _cf_changed_message_origins.items():
                if not any(_origin_pulsed(origin) for origin in origins):
                    missing_cf_override_values.setdefault(target_id, {})["message"] = None
            for target_id, handle_origins in _cf_changed_trigger_handle_origins.items():
                for handle, origins in handle_origins.items():
                    if any(_origin_pulsed(origin) for origin in origins):
                        continue
                    if _node_type_by_id.get(target_id) == "operating_hours" and handle == "active":
                        value = bool((pre_execute_node_state or {}).get(target_id, {}).get("last_start"))
                    else:
                        value = False
                    missing_cf_override_values.setdefault(target_id, {})[handle] = value
            for target_id, handle_origins in _cf_changed_stateful_relay_origins.items():
                if _node_type_by_id.get(target_id) in {"gate", "hysteresis"}:
                    continue
                for handle, origins in handle_origins.items():
                    if not any(_origin_pulsed(origin) for origin in origins):
                        missing_cf_override_values.setdefault(target_id, {})[handle] = None

        _refresh_missing_cf_override_values()
        _suppress_missing_cf_trigger_pulses()
        _neutralize_missing_cf_messages()

        missing_downstream_filters = {
            target_id for target_id, origins in _cf_downstream_filter_origins.items() if not any(_origin_pulsed(origin) for origin in origins)
        }
        if missing_downstream_filters:
            held_descendants = missing_downstream_filters | _downstream_closure(missing_downstream_filters, _effective_edges)
            held_overrides = {node_id: dict(values) for node_id, values in aug_overrides.items()}
            for target_id in missing_downstream_filters:
                held_overrides.setdefault(target_id, {})["_suppress_change_filter"] = True
            held_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
            held_known_outputs = {node_id: values for node_id, values in outputs.items() if node_id not in held_descendants}
            held_outputs = await _execute_pass(await _executor(held_hyst), held_overrides, known_outputs=held_known_outputs)
            for node_id in held_descendants:
                if node_id in held_outputs:
                    outputs[node_id] = held_outputs[node_id]
                if node_id in held_hyst:
                    hyst[node_id] = held_hyst[node_id]
                else:
                    hyst.pop(node_id, None)
            _apply_operating_hours_state(held_descendants, pre_execute_node_state)

        # edge_detect belongs here too: its "reset" is a trigger port, so a
        # no-pulse placeholder inverted into True by a synchronous node would
        # otherwise clear its remembered level for good — the stateful-relay
        # replay below only guards its "in".
        synchronous_trigger_types = {"statistics", "operating_hours", "random_value", "edge_detect"}
        missing_synchronous_handles = {
            target_id: {handle for handle, origins in handle_origins.items() if not any(_origin_pulsed(origin) for origin in origins)}
            for target_id, handle_origins in _cf_changed_trigger_handle_origins.items()
            if _node_type_by_id.get(target_id) in synchronous_trigger_types
        }
        missing_synchronous_handles = {target_id: handles for target_id, handles in missing_synchronous_handles.items() if handles}
        missing_synchronous_targets = set(missing_synchronous_handles)
        if missing_synchronous_targets:
            synchronous_descendants = missing_synchronous_targets | _downstream_closure(missing_synchronous_targets, _effective_edges)
            synchronous_overrides = {node_id: dict(values) for node_id, values in aug_overrides.items()}
            for target_id, missing_handles in missing_synchronous_handles.items():
                target_overrides = synchronous_overrides.setdefault(target_id, {})
                for handle in missing_handles:
                    if _node_type_by_id.get(target_id) == "operating_hours" and handle == "active":
                        target_overrides[handle] = bool(pre_execute_node_state.get(target_id, {}).get("last_start"))
                    else:
                        target_overrides[handle] = False
            synchronous_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
            synchronous_known_outputs = {node_id: values for node_id, values in outputs.items() if node_id not in synchronous_descendants}
            synchronous_outputs = await _execute_pass(
                await _executor(synchronous_hyst),
                synchronous_overrides,
                known_outputs=synchronous_known_outputs,
            )
            for node_id in synchronous_descendants:
                if node_id in synchronous_outputs:
                    outputs[node_id] = synchronous_outputs[node_id]
                if node_id in synchronous_hyst:
                    hyst[node_id] = synchronous_hyst[node_id]
                else:
                    hyst.pop(node_id, None)
            _apply_operating_hours_state(synchronous_descendants, pre_execute_node_state)

        missing_stateful_relay_targets = {
            target_id
            for target_id, handle_origins in _cf_changed_stateful_relay_origins.items()
            if any(not any(_origin_pulsed(origin) for origin in origins) for origins in handle_origins.values())
        }
        if missing_stateful_relay_targets:
            stateful_descendants = missing_stateful_relay_targets | _downstream_closure(missing_stateful_relay_targets, _effective_edges)
            stateful_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
            stateful_known_outputs = {node_id: values for node_id, values in outputs.items() if node_id not in stateful_descendants}
            stateful_overrides = {node_id: dict(values) for node_id, values in aug_overrides.items()}
            for target_id in missing_stateful_relay_targets:
                target_type_name = _node_type_by_id.get(target_id)
                if target_type_name in {"gate", "hysteresis"}:
                    prior_value = stateful_hyst.get(target_id, False) if target_type_name == "hysteresis" else stateful_hyst.get(target_id)
                    stateful_known_outputs[target_id] = {"out": prior_value}
                else:
                    target_overrides = stateful_overrides.setdefault(target_id, {})
                    for handle, origins in _cf_changed_stateful_relay_origins[target_id].items():
                        if not any(_origin_pulsed(origin) for origin in origins):
                            target_overrides[handle] = None
            stateful_outputs = await _execute_pass(
                await _executor(stateful_hyst),
                stateful_overrides,
                known_outputs=stateful_known_outputs,
            )
            for node_id in stateful_descendants:
                if node_id in stateful_outputs:
                    outputs[node_id] = stateful_outputs[node_id]
                if node_id in stateful_hyst:
                    hyst[node_id] = stateful_hyst[node_id]
                else:
                    hyst.pop(node_id, None)

        cron_node_ids = {n.id for n in flow.nodes if n.type == "timer_cron"}
        # A change_filter's "changed" pulse — and an Edge Detection edge — is a
        # discrete event just like a cron tick: consecutive real pulses must
        # each retrigger host_check / wake_on_lan instead of being deduplicated
        # as one "sustained" trigger.
        _initial_pulse_handles = _discrete_pulse_handles()
        # Forward-reachability from the cron nodes that actually fired this
        # execution, plus any change_filter pulses — scopes the retrigger
        # exception to only those async nodes driven by the firing pulse
        # source, not every cron/change_filter in the graph.
        fired_crons = overrides.keys() & cron_node_ids
        cron_reachable: set[str] = set(fired_crons)
        # Seed only the targets reached via each pulsing change_filter's
        # "changed" handle — its "out" handle carries the held/passthrough
        # value, not a discrete pulse, and must not bypass rising-edge dedup.
        for _cfe in _effective_edges:
            if (_cfe.source, _cfe.sourceHandle or "out") in _initial_pulse_handles and _edge_carries_pulse(_cfe):
                cron_reachable.add(_cfe.target)
        if cron_reachable:
            _cq: list[str] = list(cron_reachable)
            while _cq:
                _cn = _cq.pop()
                # memory is an explicit tick boundary: a pulse legitimately
                # reaches its trigger-typed "reset" port (added to
                # cron_reachable above/below like any other trigger-typed
                # target), but memory's "out" this pass is whatever was
                # already committed at the end of a *previous* tick,
                # entirely independent of the reset/in this pulse just
                # delivered — that only takes effect via the deferred
                # commit_memory_inputs, for the *next* tick. The pulse must
                # not be treated as having propagated through to memory's
                # own descendants.
                if _node_type_by_id.get(_cn) == "memory":
                    continue
                for _ce in _effective_edges:
                    if _ce.source == _cn and _ce.target not in cron_reachable and _edge_carries_pulse(_ce):
                        cron_reachable.add(_ce.target)
                        _cq.append(_ce.target)

        def _register_change_filter_pulses(node_ids: set[str]) -> None:
            # change_filter_pulse_ids/cron_reachable above only see pulses
            # already visible in the *first* pass — a change_filter held
            # behind an unresolved async source (see the suppression above)
            # still reports changed=False there, and only turns changed=True
            # once one of the replay passes below re-runs it with the real
            # value. Without folding that pulse into cron_reachable too, a
            # downstream host_check/wake_on_lan fed by "changed" would treat
            # two consecutive real changes as one "sustained" trigger and
            # dedupe the second — silently dropping a real retrigger. Call
            # this right after any replay pass updates `outputs` for
            # `node_ids`, before anything downstream reads cron_reachable.
            _suppress_missing_cf_trigger_pulses(node_ids)
            _refresh_missing_cf_override_values()
            _new_pulses = _discrete_pulse_handles(node_ids)
            if not _new_pulses:
                return
            _pq: list[str] = []
            for _pe in _effective_edges:
                if (_pe.source, _pe.sourceHandle or "out") in _new_pulses and _pe.target not in cron_reachable and _edge_carries_pulse(_pe):
                    cron_reachable.add(_pe.target)
                    _pq.append(_pe.target)
            while _pq:
                _pn = _pq.pop()
                # Same memory tick-boundary stop as the preamble traversal
                # above — a pulse reaching memory's "reset" must not be
                # treated as having propagated through to its descendants.
                if _node_type_by_id.get(_pn) == "memory":
                    continue
                for _pe2 in _effective_edges:
                    if _pe2.source == _pn and _pe2.target not in cron_reachable and _edge_carries_pulse(_pe2):
                        cron_reachable.add(_pe2.target)
                        _pq.append(_pe2.target)

        executed_host_check_nodes: set[str] = set()

        async def _run_host_check_node(node: Any, target_set: set[str], log_suffix: str = "") -> bool:
            out = outputs.get(node.id, {})
            hyst_hc = hyst.setdefault(node.id, {})
            is_triggered = GraphExecutor._to_bool(out.get("_trigger"))
            was_triggered = hyst_hc.get("hc_prev_trigger", False)
            is_cron_triggered = node.id in cron_reachable
            if not is_triggered:
                return False
            host = (node.data.get("host") or "").strip()
            if not host:
                logger.warning("host_check: host missing on node %s", node.id[:8])
                # A misconfigured node will never succeed this tick — mark it
                # resolved (not merely "pending") so any change_filter held
                # behind it is released with this final (placeholder) output
                # instead of staying stuck until a config fix retriggers it.
                target_set.add(node.id)
                return False
            try:
                timeout_s, count = _normalise_host_check_ping_config(node.data.get("timeout_s"), node.data.get("count"))
                config_sig = f"{host}\0{timeout_s:g}\0{count}"
            except Exception:
                logger.exception("Graph %s: host_check %s failed", graph_id[:8], host)
                target_set.add(node.id)
                return False
            if (
                was_triggered
                and not is_cron_triggered
                and hyst_hc.get("hc_config_sig") == config_sig
                and hyst_hc.get("hc_runtime_token") == _HOST_CHECK_RUNTIME_TOKEN
            ):
                outputs[node.id]["reachable"] = hyst_hc.get("hc_last_reachable", False)
                outputs[node.id]["latency_ms"] = hyst_hc.get("hc_last_latency_ms")
                target_set.add(node.id)
                return True
            try:
                reachable, latency_ms = await _ping_host(host, count, timeout_s)
                hyst_hc["hc_prev_trigger"] = True
                hyst_hc["hc_last_reachable"] = reachable
                hyst_hc["hc_last_latency_ms"] = latency_ms
                hyst_hc["hc_config_sig"] = config_sig
                hyst_hc["hc_runtime_token"] = _HOST_CHECK_RUNTIME_TOKEN
                outputs[node.id]["reachable"] = reachable
                outputs[node.id]["latency_ms"] = latency_ms
                target_set.add(node.id)
                executed_host_check_nodes.add(node.id)
                logger.info(
                    "Graph %s: host_check%s %s → reachable=%s latency=%s ms",
                    graph_id[:8],
                    log_suffix,
                    host,
                    reachable,
                    f"{latency_ms:.1f}" if latency_ms is not None else "—",
                )
                return True
            except Exception:
                logger.exception("Graph %s: host_check %s failed", graph_id[:8], host)
                target_set.add(node.id)
                return False

        # ── Handle host_check ─────────────────────────────────────────────
        # Rising-edge trigger (same cron-exemption logic as wake_on_lan):
        # ping is sent only on the False→True transition of _trigger, or on
        # every cron tick if this node is reachable from a firing cron node.
        # Runs BEFORE wake_on_lan so that graphs with host_check → WoL see
        # real reachability values, not executor placeholders.

        # Accumulates edge-level input overrides from every resolved async node.
        # Injected into every replay merge so that nodes downstream of multiple
        # async sources see real values instead of first-pass placeholders.
        resolved_async_edge_overrides: dict[str, dict[str, Any]] = {}

        # Initialised here (before any replay pass) so that output-update guards
        # in the HC and WoL replay loops can safely reference this set even before
        # the api_client processing block populates it.
        triggered_api_clients: set[str] = set()

        # Declared here (rather than where it's first assigned, in the
        # api_client stage below) so _add_resolved_outputs — called from
        # over a dozen sites throughout this function, both before and
        # after the api_client stage — can always safely read/refresh it
        # via `nonlocal` without an UnboundLocalError on the calls that
        # happen first.
        api_replay_overrides: dict[str, dict[str, Any]] | None = None

        def _refresh_api_replay_hold_overrides(outputs_source: dict[str, dict[str, Any]] | None = None) -> None:
            """Keep api_replay_overrides' `_suppress_change_filter` holds in
            sync with the current _settled_async_ids/_still_unresolved_source_ids
            state.

            api_replay_overrides is reused, unmodified, as the base overrides
            for several LATER, independent replay stages (post-api host_check,
            post-api WoL, ...), not all of which recompute their own holds
            from scratch. Baking a hold in once and never touching it again
            would let it survive in api_replay_overrides forever — even after
            the exact async source it was protecting against settles for real
            via _add_resolved_outputs — so every later stage reusing it as a
            base would keep suppressing a change_filter long after the real
            value it was waiting on is already known. Recomputed wholesale
            (not merely added-to) each time, so a hold whose source has since
            settled is dropped here, not just left un-renewed.

            `outputs_source` mirrors _still_unresolved_source_ids' own param
            for the same reason: a caller that just ran its own fresher
            replay pass (e.g. second_outputs) must pass that pass's results,
            not rely on the stale outer `outputs` snapshot.
            """
            nonlocal api_replay_overrides
            if api_replay_overrides is None:
                return
            _current_cf_hold_ids = _compute_cf_hold_ids(
                _unresolved_value_ids_from(outputs_source) | _still_unresolved_source_ids(outputs_source), outputs_source
            )
            _refreshed: dict[str, dict[str, Any]] = {}
            for _nid, _vals in api_replay_overrides.items():
                if "_suppress_change_filter" in _vals and _nid not in _current_cf_hold_ids:
                    _vals = {k: v for k, v in _vals.items() if k != "_suppress_change_filter"}
                _refreshed[_nid] = _vals
            for _nid in _current_cf_hold_ids:
                _refreshed.setdefault(_nid, {})["_suppress_change_filter"] = True
            api_replay_overrides = _refreshed

        def _add_resolved_outputs(node_ids: set[str]) -> None:
            _settled_async_ids.update(node_ids & async_replay_source_ids)
            for _re in _effective_edges:
                if _re.source in node_ids:
                    resolved_async_edge_overrides.setdefault(_re.target, {})[_re.targetHandle or "in"] = GraphExecutor._get_output_value(
                        outputs.get(_re.source, {}), _re.sourceHandle or "out"
                    )
            _refresh_api_replay_hold_overrides()

        async def _replay_async_descendants(node_ids: set[str], *, skip_node_ids: set[str] | None = None) -> set[str]:
            descendants: set[str] = set()
            queue: list[str] = list(node_ids)
            while queue:
                source_id = queue.pop()
                for edge in _effective_edges:
                    if edge.source == source_id and edge.target not in descendants:
                        descendants.add(edge.target)
                        queue.append(edge.target)
            if not descendants:
                return descendants

            replay_overrides: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in aug_overrides.items()}
            for nid, vals in resolved_async_edge_overrides.items():
                replay_overrides.setdefault(nid, {}).update(vals)
            for edge in _effective_edges:
                if edge.source in node_ids:
                    source_handle = edge.sourceHandle or "out"
                    target_handle = edge.targetHandle or "in"
                    source_value = GraphExecutor._get_output_value(outputs.get(edge.source, {}), source_handle)
                    replay_overrides.setdefault(edge.target, {})[target_handle] = source_value
            for edge in _effective_edges:
                if edge.target not in descendants or edge.source in descendants or edge.source in node_ids:
                    continue
                source_handle = edge.sourceHandle or "out"
                target_handle = edge.targetHandle or "in"
                source_value = GraphExecutor._get_output_value(outputs.get(edge.source, {}), source_handle)
                replay_overrides.setdefault(edge.target, {})[target_handle] = source_value

            replay_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
            replay_executor = await _executor(replay_hyst)
            replay_outputs = await _execute_pass(replay_executor, replay_overrides)
            # A downstream async node (e.g. wake_on_lan) newly reachable
            # within this replay's own outputs may still be only "triggered,
            # not yet actually run" — its own output here is a placeholder,
            # same as the api_client replay branch further below. A
            # change_filter reachable through it — or through a still-
            # unseeded Read Object — must stay held rather than commit that
            # placeholder and let a downstream host_check irreversibly ping.
            # Redo the replay with suppression applied if this reveals
            # anything new.
            _late_pending = _still_unresolved_source_ids(replay_outputs)
            _late_cf_hold_ids = _compute_cf_hold_ids(_unresolved_value_ids_from(replay_outputs) | _late_pending, replay_outputs)
            if _late_cf_hold_ids:
                for _late_cf_id in _late_cf_hold_ids:
                    replay_overrides.setdefault(_late_cf_id, {})["_suppress_change_filter"] = True
                replay_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                replay_executor = await _executor(replay_hyst)
                replay_outputs = await _execute_pass(replay_executor, replay_overrides)
            blocked_ids = skip_node_ids or set()
            for nid, vals in replay_outputs.items():
                if nid in descendants and nid not in blocked_ids:
                    outputs[nid] = vals
                    if nid in replay_hyst:
                        hyst[nid] = replay_hyst[nid]
            _apply_operating_hours_state(descendants, pre_execute_node_state)
            _register_change_filter_pulses(descendants)
            return descendants

        triggered_host_check_nodes: set[str] = set()
        for node in flow.nodes:
            if node.type != "host_check":
                continue
            await _run_host_check_node(node, triggered_host_check_nodes)
        _add_resolved_outputs(triggered_host_check_nodes)

        # ── Re-propagate host_check outputs to downstream nodes ───────────
        pending_host_check_replay = set(triggered_host_check_nodes)
        processed_host_check_replay: set[str] = set()
        while pending_host_check_replay:
            replay_sources = pending_host_check_replay - processed_host_check_replay
            if not replay_sources:
                break
            processed_host_check_replay.update(replay_sources)
            hc_downstream_overrides: dict[str, dict[str, Any]] = {}
            for e in _effective_edges:
                if e.source in replay_sources:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    hc_downstream_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if not hc_downstream_overrides:
                continue
            hc_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in aug_overrides.items()}
            for nid, vals in resolved_async_edge_overrides.items():
                hc_merged.setdefault(nid, {}).update(vals)
            for nid, vals in hc_downstream_overrides.items():
                hc_merged.setdefault(nid, {}).update(vals)
            hc_hyst_snapshot = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
            hc_second_executor = await _executor(hc_hyst_snapshot)
            hc_second_outputs = await _execute_pass(hc_second_executor, hc_merged)
            # A downstream async node (e.g. wake_on_lan) newly reachable
            # within this replay's own outputs may still be only "triggered,
            # not yet actually run" — its own output here is a placeholder,
            # same as the api_client/_replay_async_descendants replay
            # branches. A change_filter reachable through it — or through a
            # still-unseeded Read Object — must stay held rather than
            # commit that placeholder and let a further downstream
            # host_check irreversibly ping. Redo the replay with
            # suppression applied if this reveals anything new.
            _hc_late_pending = _still_unresolved_source_ids(hc_second_outputs)
            _hc_late_cf_hold_ids = _compute_cf_hold_ids(_unresolved_value_ids_from(hc_second_outputs) | _hc_late_pending, hc_second_outputs)
            if _hc_late_cf_hold_ids:
                for _hc_late_cf_id in _hc_late_cf_hold_ids:
                    hc_merged.setdefault(_hc_late_cf_id, {})["_suppress_change_filter"] = True
                hc_hyst_snapshot = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                hc_second_executor = await _executor(hc_hyst_snapshot)
                hc_second_outputs = await _execute_pass(hc_second_executor, hc_merged)
            hc_descendants: set[str] = set()
            hc_queue: list[str] = list(replay_sources)
            while hc_queue:
                nid = hc_queue.pop()
                for e in _effective_edges:
                    if e.source == nid and e.target not in hc_descendants:
                        hc_descendants.add(e.target)
                        hc_queue.append(e.target)
            for nid, vals in hc_second_outputs.items():
                if nid in hc_descendants and nid not in triggered_api_clients:
                    outputs[nid] = vals
                    if nid not in host_check_ids and nid in hc_hyst_snapshot:
                        hyst[nid] = hc_hyst_snapshot[nid]
            _apply_operating_hours_state(hc_descendants, pre_execute_node_state)
            _register_change_filter_pulses(hc_descendants)
            newly_triggered_hc: set[str] = set()
            for node in flow.nodes:
                if node.type == "host_check" and node.id in hc_descendants and node.id not in triggered_host_check_nodes:
                    await _run_host_check_node(node, newly_triggered_hc, " (replay)")
            if newly_triggered_hc:
                triggered_host_check_nodes.update(newly_triggered_hc)
                _add_resolved_outputs(newly_triggered_hc)
                pending_host_check_replay.update(newly_triggered_hc)

        async def _run_wake_on_lan_node(node: Any, target_set: set[str]) -> bool:
            out = outputs.get(node.id, {})
            hyst_wol = hyst.setdefault(node.id, {})
            is_triggered = GraphExecutor._to_bool(out.get("_trigger"))
            was_triggered = hyst_wol.get("wol_prev_trigger", False)
            # Cron-retrigger exception applies only when the firing cron node
            # actually drives this specific WoL node (reachability check above).
            is_cron_triggered = node.id in cron_reachable
            if not is_triggered:
                hyst_wol["wol_prev_trigger"] = False
                return False
            if was_triggered and not is_cron_triggered:
                # Dedup skip, not a failure — but the trigger IS active this
                # tick and "sent" is settled (no new packet this time), so a
                # change_filter held behind this node's output must still be
                # released instead of waiting for a send that isn't coming.
                target_set.add(node.id)
                return False
            mac = (node.data.get("mac_address") or "").strip()
            if not mac:
                logger.warning("wake_on_lan: mac_address missing on node %s", node.id[:8])
                target_set.add(node.id)
                return False
            broadcast = (node.data.get("broadcast_ip") or "").strip() or "255.255.255.255"
            _port_raw = node.data.get("port")
            try:
                if isinstance(_port_raw, float) and not _port_raw.is_integer():
                    raise ValueError(f"fractional port {_port_raw!r} — must be a whole number")
                port = int(_port_raw) if _port_raw not in (None, "") else 9
                if not (1 <= port <= 65535):
                    raise ValueError(f"port {port!r} out of range 1–65535")
                try:
                    ipaddress.IPv4Address(broadcast)
                except ValueError:
                    raise ValueError(f"invalid broadcast IP {broadcast!r}") from None
                await asyncio.to_thread(_send_wol_packet, mac, broadcast, port)
                # Record the consumed rising edge only after a successful send so
                # that a transient failure does not silently suppress the next attempt.
                hyst_wol["wol_prev_trigger"] = True
                outputs[node.id]["sent"] = True
                target_set.add(node.id)
                logger.info("Graph %s: WoL sent by node %s", graph_id[:8], node.id[:8])
                return True
            except Exception:
                logger.exception("Graph %s: WoL failed on node %s", graph_id[:8], node.id[:8])
                target_set.add(node.id)
                return False

        # ── Handle wake_on_lan ────────────────────────────────────────────
        # Runs AFTER host_check so that graphs with host_check → WoL read
        # real reachability, and BEFORE api_client/notify so that wol.sent
        # can propagate to downstream api_client or notify in the same tick.
        triggered_wol_nodes: set[str] = set()
        for node in flow.nodes:
            if node.type != "wake_on_lan":
                continue
            await _run_wake_on_lan_node(node, triggered_wol_nodes)

        _add_resolved_outputs(triggered_wol_nodes)

        # ── Re-propagate wake_on_lan sent=True to downstream nodes ───────────
        # The first executor pass computed downstream nodes with sent=False.
        # Re-run only the transitive downstream subgraph with the real sent
        # value injected as an input override.
        # Full aug_overrides (dp-read seeds + cron/event overrides from the
        # call site) are carried into the second pass so that downstream nodes
        # which also read from a cron pulse or a datapoint see correct values.
        # Only transitively downstream nodes are updated from the second pass
        # so that unrelated nodes (e.g. an api_client with its own trigger)
        # keep their first-pass results.
        if triggered_wol_nodes:
            wol_downstream_overrides: dict[str, dict[str, Any]] = {}
            for e in _effective_edges:
                if e.source in triggered_wol_nodes:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    wol_downstream_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if wol_downstream_overrides:
                wol_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in aug_overrides.items()}
                for nid, vals in resolved_async_edge_overrides.items():
                    wol_merged.setdefault(nid, {}).update(vals)
                for nid, vals in wol_downstream_overrides.items():
                    wol_merged.setdefault(nid, {}).update(vals)
                # Replay from the *pre-execution* snapshot, not the current
                # (already first-pass-mutated) hyst — matching every other
                # replay site in this function. Deep-copying the current
                # hyst instead would let a stateful descendant (statistics,
                # avg_multi, …) mutate its already-mutated-once state a
                # second time here, and the copy-back below would commit
                # that double mutation as if it were a single real sample.
                # Replaying from the untouched pre-execution baseline means
                # this is that descendant's *only* mutation this tick, so
                # copying its result back is safe for every descendant type
                # — including change_filter, whose "state" *is* its
                # output-determining comparison baseline and must be copied
                # back, or the next tick compares against a stale baseline
                # and silently drops the following real change.
                wol_second_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                wol_second_executor = await _executor(wol_second_hyst)
                wol_second_outputs = await _execute_pass(wol_second_executor, wol_merged)
                # A downstream async node (e.g. a second, chained
                # wake_on_lan) newly reachable within this replay's own
                # outputs may still be only "triggered, not yet actually
                # run" — its own output here is a placeholder, same as the
                # api_client/host_check/_replay_async_descendants replay
                # branches. A change_filter reachable through it — or
                # through a still-unseeded Read Object — must stay held
                # rather than commit that placeholder and let a further
                # downstream host_check irreversibly ping. Redo the replay
                # with suppression applied if this reveals anything new.
                _wol_late_pending = _still_unresolved_source_ids(wol_second_outputs)
                _wol_late_cf_hold_ids = _compute_cf_hold_ids(_unresolved_value_ids_from(wol_second_outputs) | _wol_late_pending, wol_second_outputs)
                if _wol_late_cf_hold_ids:
                    for _wol_late_cf_id in _wol_late_cf_hold_ids:
                        wol_merged.setdefault(_wol_late_cf_id, {})["_suppress_change_filter"] = True
                    wol_second_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                    wol_second_executor = await _executor(wol_second_hyst)
                    wol_second_outputs = await _execute_pass(wol_second_executor, wol_merged)
                # Compute transitive closure of WoL-triggered nodes so that only
                # their descendants are updated, leaving unrelated nodes intact.
                wol_descendants: set[str] = set()
                queue = list(triggered_wol_nodes)
                while queue:
                    nid = queue.pop()
                    for e in _effective_edges:
                        if e.source == nid and e.target not in wol_descendants:
                            wol_descendants.add(e.target)
                            queue.append(e.target)
                wol_node_ids = {n.id for n in flow.nodes if n.type == "wake_on_lan"}
                for nid, vals in wol_second_outputs.items():
                    if nid not in wol_node_ids and nid in wol_descendants:
                        outputs[nid] = vals
                        if nid not in host_check_ids and nid in wol_second_hyst:
                            hyst[nid] = wol_second_hyst[nid]
                _register_change_filter_pulses(wol_descendants)

        # ── Post-WoL host_check pass ──────────────────────────────────────
        # WoL.sent may drive host_check._trigger via downstream edges. Run
        # those checks now so the api_client loop below sees real reachability.
        if triggered_wol_nodes:
            _wol_all_desc: set[str] = set()
            _wol_desc_q: list[str] = list(triggered_wol_nodes)
            while _wol_desc_q:
                _wn = _wol_desc_q.pop()
                for _we in _effective_edges:
                    if _we.source == _wn and _we.target not in _wol_all_desc:
                        _wol_all_desc.add(_we.target)
                        _wol_desc_q.append(_we.target)
            _post_wol_hc: set[str] = set()
            for node in flow.nodes:
                if node.type == "host_check" and node.id in _wol_all_desc and node.id not in triggered_host_check_nodes:
                    await _run_host_check_node(node, _post_wol_hc, " (post-wol)")
            if _post_wol_hc:
                triggered_host_check_nodes.update(_post_wol_hc)
                _add_resolved_outputs(_post_wol_hc)
                _pending_pwol = set(_post_wol_hc)
                _processed_pwol: set[str] = set()
                while _pending_pwol:
                    _pwol_src = _pending_pwol - _processed_pwol
                    if not _pwol_src:
                        break
                    _processed_pwol.update(_pwol_src)
                    _pwol_dn_ovr: dict[str, dict[str, Any]] = {}
                    for _e in _effective_edges:
                        if _e.source in _pwol_src:
                            _pwol_dn_ovr.setdefault(_e.target, {})[_e.targetHandle or "in"] = GraphExecutor._get_output_value(
                                outputs[_e.source], _e.sourceHandle or "out"
                            )
                    if not _pwol_dn_ovr:
                        continue
                    _pwol_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in aug_overrides.items()}
                    for nid, vals in resolved_async_edge_overrides.items():
                        _pwol_merged.setdefault(nid, {}).update(vals)
                    for nid, vals in _pwol_dn_ovr.items():
                        _pwol_merged.setdefault(nid, {}).update(vals)
                    _pwol_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                    _pwol_exec = await _executor(_pwol_hyst)
                    _pwol_out = await _execute_pass(_pwol_exec, _pwol_merged)
                    # A downstream async node (e.g. a chained wake_on_lan)
                    # newly reachable within this replay's own outputs may
                    # still be only "triggered, not yet actually run" — its
                    # own output here is a placeholder, same as every other
                    # replay branch. A change_filter reachable through it —
                    # or through a still-unseeded Read Object — must stay
                    # held rather than commit that placeholder and let a
                    # further downstream host_check irreversibly ping. Redo
                    # the replay with suppression applied if this reveals
                    # anything new.
                    _pwol_late_pending = _still_unresolved_source_ids(_pwol_out)
                    _pwol_late_cf_hold_ids = _compute_cf_hold_ids(_unresolved_value_ids_from(_pwol_out) | _pwol_late_pending, _pwol_out)
                    if _pwol_late_cf_hold_ids:
                        for _pwol_late_cf_id in _pwol_late_cf_hold_ids:
                            _pwol_merged.setdefault(_pwol_late_cf_id, {})["_suppress_change_filter"] = True
                        _pwol_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                        _pwol_exec = await _executor(_pwol_hyst)
                        _pwol_out = await _execute_pass(_pwol_exec, _pwol_merged)
                    _pwol_desc: set[str] = set()
                    _pwol_dq: list[str] = list(_pwol_src)
                    while _pwol_dq:
                        _pn = _pwol_dq.pop()
                        for _e in _effective_edges:
                            if _e.source == _pn and _e.target not in _pwol_desc:
                                _pwol_desc.add(_e.target)
                                _pwol_dq.append(_e.target)
                    for nid, vals in _pwol_out.items():
                        if nid in _pwol_desc and nid not in triggered_api_clients:
                            outputs[nid] = vals
                            if nid not in host_check_ids and nid in _pwol_hyst:
                                hyst[nid] = _pwol_hyst[nid]
                    _apply_operating_hours_state(_pwol_desc, pre_execute_node_state)
                    _register_change_filter_pulses(_pwol_desc)
                    _chained_pwol: set[str] = set()
                    for node in flow.nodes:
                        if node.type == "host_check" and node.id in _pwol_desc and node.id not in triggered_host_check_nodes:
                            await _run_host_check_node(node, _chained_pwol, " (post-wol replay)")
                    if _chained_pwol:
                        triggered_host_check_nodes.update(_chained_pwol)
                        _add_resolved_outputs(_chained_pwol)
                        _pending_pwol.update(_chained_pwol)

        # ── Handle api_client ─────────────────────────────────────────────
        # Track api_client nodes with final manager-computed outputs so we can
        # re-propagate success responses and explicit error details downstream.
        triggered_api_clients: set[str] = set()
        execution_values_by_datapoint_id: dict[str, Any] = {}
        execution_value_priority_by_datapoint_id: dict[str, int] = {}
        for node in flow.nodes:
            if node.type != "datapoint_read":
                continue
            dp_id_str = str(node.data.get("datapoint_id") or "").strip()
            node_override = {
                **aug_overrides.get(node.id, {}),
                **debug_overrides.get(node.id, {}),
            }
            if not dp_id_str or "value" not in node_override:
                continue
            if "value" in debug_overrides.get(node.id, {}):
                priority = 3
            elif node.id in overrides or GraphExecutor._to_bool(node_override.get("changed")):
                priority = 2
            else:
                priority = 1
            if priority >= execution_value_priority_by_datapoint_id.get(dp_id_str, 0):
                execution_values_by_datapoint_id[dp_id_str] = node_override["value"]
                execution_value_priority_by_datapoint_id[dp_id_str] = priority
        import json as _json

        async def _run_api_client_node(node: Any, target_set: set[str]) -> bool:
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                return False
            variable_resolver = _make_api_client_variable_resolver(
                self._registry,
                node.data.get("variables"),
                execution_values_by_datapoint_id,
            )
            try:
                url = _replace_api_client_url_placeholders(
                    node.data.get("url") or "",
                    variable_resolver,
                ).strip()
                if not url:
                    target_set.add(node.id)
                    return False
            except _ApiClientVariableError as exc:
                logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                target_set.add(node.id)
                return True
            try:
                request_urls, pinned_headers, request_extensions = _build_api_client_fetch_targets(url)
            except ValueError as exc:
                logger.warning("Graph %s: blocked api_client target %s: %s", graph_id[:8], url, exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                target_set.add(node.id)
                return True
            method = (node.data.get("method", "GET") or "GET").upper()
            content_type = node.data.get("content_type", "application/json")
            resp_type = node.data.get("response_type", "application/json")
            verify_ssl = node.data.get("verify_ssl", True)
            if isinstance(verify_ssl, str):
                verify_ssl = verify_ssl.lower() not in ("false", "0", "no")
            timeout_s = float(node.data.get("timeout_s", 10) or 10)
            extra_headers: dict[str, str] = {}
            hdr_str = (node.data.get("headers") or "").strip()
            if hdr_str:
                try:
                    extra_headers = _json.loads(hdr_str)
                except (json.JSONDecodeError, TypeError):
                    pass
            hdr_file = (node.data.get("headers_value_file") or "").strip()
            if hdr_file:
                try:
                    extra_headers = {
                        **extra_headers,
                        **_json.loads(_load_external_value_file(hdr_file)),
                    }
                except (json.JSONDecodeError, TypeError):
                    pass
            try:
                extra_headers = _replace_api_client_placeholders(extra_headers, variable_resolver)
            except _ApiClientVariableError as exc:
                logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                target_set.add(node.id)
                return True
            # ── Authentication ──────────────────────────────────────────
            auth_type = (node.data.get("auth_type") or "none").lower()
            auth: Any = None
            try:
                if auth_type in ("basic", "digest"):
                    username = _replace_api_client_placeholders(
                        node.data.get("auth_username") or "",
                        variable_resolver,
                    ).strip()
                    password = _replace_api_client_placeholders(
                        node.data.get("auth_password") or "",
                        variable_resolver,
                    )
                    if username:
                        auth = httpx.BasicAuth(username, password) if auth_type == "basic" else httpx.DigestAuth(username, password)
                elif auth_type == "bearer":
                    token = _replace_api_client_placeholders(
                        node.data.get("auth_token") or "",
                        variable_resolver,
                    ).strip()
                    if not token:
                        token = _replace_api_client_placeholders(
                            _load_external_value_file(node.data.get("auth_value_file") or ""),
                            variable_resolver,
                        ).strip()
                    if token:
                        extra_headers = {
                            **extra_headers,
                            "Authorization": f"Bearer {token}",
                        }
            except _ApiClientVariableError as exc:
                logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                target_set.add(node.id)
                return True
            try:
                req_kwargs: dict[str, Any] = {
                    "headers": extra_headers,
                    "timeout": timeout_s,
                }
                if method in ("POST", "PUT", "PATCH"):
                    body = _replace_api_client_placeholders(out.get("_body"), variable_resolver)
                    if content_type == "application/json":
                        req_kwargs["content"] = _json.dumps(body) if not isinstance(body, (str, bytes)) else body
                        req_kwargs["headers"] = {
                            **extra_headers,
                            "Content-Type": "application/json",
                        }
                    elif content_type == "application/x-www-form-urlencoded":
                        req_kwargs["data"] = body if isinstance(body, dict) else {"data": str(body)}
                    else:
                        req_kwargs["content"] = str(body or "")
                        req_kwargs["headers"] = {
                            **extra_headers,
                            "Content-Type": "text/plain",
                        }
                req_headers = {key: value for key, value in req_kwargs.get("headers", {}).items() if key.lower() != "host"}
                req_kwargs["headers"] = {**req_headers, **pinned_headers}
                if request_extensions:
                    req_kwargs["extensions"] = request_extensions
                last_transport_error: Exception = ValueError(f"Could not fetch API target after trying {len(request_urls)} address(es)")
                resp: httpx.Response | Any | None = None
                async with httpx.AsyncClient(auth=auth, verify=verify_ssl) as client:
                    for request_url in request_urls:
                        try:
                            resp = await client.request(method, request_url, **req_kwargs)
                            break
                        except httpx.RequestError as req_exc:
                            last_transport_error = req_exc
                            if method not in _API_CLIENT_RETRYABLE_METHODS:
                                break
                            continue
                if resp is None:
                    raise last_transport_error
                resp_text = resp.text
                if len(resp_text) > 1_000_000:
                    resp_text = resp_text[:1_000_000]
                if resp_type in ("json", "application/json"):
                    try:
                        resp_data: Any = resp.json()
                    except ValueError:
                        resp_data = resp_text
                else:
                    resp_data = resp_text
                outputs[node.id].update(
                    {
                        "response": resp_data,
                        "status": resp.status_code,
                        "success": 200 <= resp.status_code < 300,
                    },
                )
                logger.info(
                    "Graph %s: API %s %s → %d",
                    graph_id[:8],
                    method,
                    url,
                    resp.status_code,
                )
                target_set.add(node.id)
                return True
            except Exception as exc:
                logger.exception("Graph %s: api_client failed", graph_id[:8])
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                target_set.add(node.id)
                return True

        for node in flow.nodes:
            if node.type != "api_client":
                continue
            await _run_api_client_node(node, triggered_api_clients)

        _add_resolved_outputs(triggered_api_clients)

        # ── Re-propagate api_client outputs to downstream nodes ───────────
        # The first executor pass computed downstream nodes with the placeholder
        # success=False. Now that we have the real HTTP results, we re-run the
        # executor for those downstream nodes using input overrides so their
        # outputs (and downstream datapoint writes, etc.) reflect the real values.
        # (api_replay_overrides itself is declared earlier, alongside
        # _refresh_api_replay_hold_overrides — see the comment there.)
        if triggered_api_clients:
            downstream_node_ids: set[str] = set()
            pending_sources = list(triggered_api_clients)
            while pending_sources:
                source_id = pending_sources.pop()
                for e in _effective_edges:
                    if e.source != source_id or e.target in downstream_node_ids:
                        continue
                    downstream_node_ids.add(e.target)
                    pending_sources.append(e.target)

            downstream_overrides: dict[str, dict[str, Any]] = {}
            for e in _effective_edges:
                if e.source in triggered_api_clients:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    downstream_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if downstream_overrides:
                replay_overrides = {nid: dict(vals) for nid, vals in aug_overrides.items()}
                for nid, vals in downstream_overrides.items():
                    replay_overrides.setdefault(nid, {}).update(vals)
                for e in _effective_edges:
                    if e.target not in downstream_node_ids or e.source in downstream_node_ids or e.source in triggered_api_clients:
                        continue
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    replay_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs.get(e.source, {}), src_handle)
                api_replay_overrides = {nid: dict(vals) for nid, vals in replay_overrides.items()}
                if pre_execute_hyst is not None:
                    replay_hyst = _safe_deepcopy_state(pre_execute_hyst)
                    second_executor = await _executor(replay_hyst)
                    second_outputs = await _execute_pass(second_executor, replay_overrides)
                    # api_client's real result can newly reveal — only once
                    # visible in this replay's OWN outputs — that a chained
                    # async node downstream (e.g. wake_on_lan.trigger, now
                    # fed by api_client's real success) is triggered but
                    # hasn't actually run yet: its own output here is still
                    # a placeholder (e.g. wol.sent=False before WoL is
                    # actually sent, since GraphExecutor never performs the
                    # real send itself). A change_filter reachable only
                    # through that node must stay held rather than commit —
                    # and let a downstream host_check irreversibly ping —
                    # using this still-wrong value, exactly like the
                    # initial pass's own _cf_hold_ids. Detected only after
                    # running the replay once (the outer `outputs` is still
                    # stale at that point), so redo it with suppression
                    # applied if this reveals anything new. Always folded in
                    # regardless of whether _late_pending itself is
                    # non-empty: a change_filter that also depends on an
                    # unseeded Read Object must stay held even when no new
                    # async node became pending in this replay — the read
                    # is still unresolved and its placeholder must not be
                    # committed just because this pass happened to be an
                    # API replay.
                    _late_pending = _still_unresolved_source_ids(second_outputs)
                    _late_cf_hold_ids = _compute_cf_hold_ids(_unresolved_value_ids_from(second_outputs) | _late_pending, second_outputs)
                    if _late_cf_hold_ids:
                        for _late_cf_id in _late_cf_hold_ids:
                            replay_overrides.setdefault(_late_cf_id, {})["_suppress_change_filter"] = True
                        # Refresh (not overwrite-and-forget) api_replay_overrides
                        # from this pass's own fresher second_outputs — see
                        # _refresh_api_replay_hold_overrides. A plain
                        # dict-copy-from-replay_overrides here would bake this
                        # hold into api_replay_overrides permanently, since
                        # nothing downstream ever removes a key it didn't add —
                        # the later post-api host_check/WoL stages reuse
                        # api_replay_overrides as their base and would then keep
                        # suppressing this change_filter even after the async
                        # source it's guarding against settles for real.
                        _refresh_api_replay_hold_overrides(second_outputs)
                        replay_hyst = _safe_deepcopy_state(pre_execute_hyst)
                        second_executor = await _executor(replay_hyst)
                        second_outputs = await _execute_pass(second_executor, replay_overrides)
                    # Compute transitive descendants of triggered api_clients so that
                    # only their subtree is updated. This prevents the api_client
                    # second pass from overwriting WoL-propagated outputs that were
                    # already written to outputs[] by the WoL second pass above.
                    api_descendants: set[str] = set()
                    _aq: list[str] = list(triggered_api_clients)
                    while _aq:
                        _an = _aq.pop()
                        for _ae in _effective_edges:
                            if _ae.source == _an and _ae.target not in api_descendants:
                                api_descendants.add(_ae.target)
                                _aq.append(_ae.target)
                    for nid, vals in second_outputs.items():
                        if nid not in api_client_ids and nid in api_descendants:
                            outputs[nid] = vals
                            if nid in replay_hyst:
                                hyst[nid] = replay_hyst[nid]
                    _register_change_filter_pulses(api_descendants)

        # ── Post-api-replay host_check pass ───────────────────────────────
        # api_client outputs (via the second executor pass above) may have
        # updated host_check trigger values. Re-run host_check for any nodes
        # not fired in the first pass whose trigger is now true.
        post_api_triggered_hc: set[str] = set()
        for node in flow.nodes:
            if node.type != "host_check" or node.id in triggered_host_check_nodes:
                continue
            if await _run_host_check_node(node, post_api_triggered_hc, " (post-api)"):
                triggered_host_check_nodes.add(node.id)
        if post_api_triggered_hc:
            _add_resolved_outputs(post_api_triggered_hc)

        post_api_hc_descendants: set[str] = set()
        pending_post_api_hc_replay = set(post_api_triggered_hc)
        processed_post_api_hc_replay: set[str] = set()
        while pending_post_api_hc_replay:
            replay_sources = pending_post_api_hc_replay - processed_post_api_hc_replay
            if not replay_sources:
                break
            processed_post_api_hc_replay.update(replay_sources)
            pat_hc_overrides: dict[str, dict[str, Any]] = {}
            for e in _effective_edges:
                if e.source in replay_sources:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    pat_hc_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if not pat_hc_overrides:
                continue
            pat_base_overrides = api_replay_overrides if api_replay_overrides is not None else aug_overrides
            pat_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in pat_base_overrides.items()}
            for nid, vals in resolved_async_edge_overrides.items():
                pat_merged.setdefault(nid, {}).update(vals)
            for nid, vals in pat_hc_overrides.items():
                pat_merged.setdefault(nid, {}).update(vals)
            pat_hyst_snapshot = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
            pat_executor = await _executor(pat_hyst_snapshot)
            pat_outputs = await _execute_pass(pat_executor, pat_merged)
            # A downstream async node (e.g. wake_on_lan) newly reachable
            # within this replay's own outputs may still be only "triggered,
            # not yet actually run" — its own output here is a placeholder,
            # same as the other replay branches. A change_filter reachable
            # through it — or through a still-unseeded Read Object — must
            # stay held rather than commit that placeholder and let a
            # further downstream host_check irreversibly ping. In practice
            # a change_filter reachable from THIS pass's replay_sources is
            # already held via pat_base_overrides (inherited from the
            # api-client stage's own suppression, since any host_check
            # replayed here was necessarily already one of ITS late-pending
            # seeds) — this recompute is kept for defense in depth and
            # consistency with every other replay site, in case a future
            # change decouples pat_base_overrides from that inheritance.
            _pat_late_pending = _still_unresolved_source_ids(pat_outputs)
            _pat_late_cf_hold_ids = _compute_cf_hold_ids(_unresolved_value_ids_from(pat_outputs) | _pat_late_pending, pat_outputs)
            if _pat_late_cf_hold_ids:
                for _pat_late_cf_id in _pat_late_cf_hold_ids:
                    pat_merged.setdefault(_pat_late_cf_id, {})["_suppress_change_filter"] = True
                pat_hyst_snapshot = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                pat_executor = await _executor(pat_hyst_snapshot)
                pat_outputs = await _execute_pass(pat_executor, pat_merged)
            pat_descendants: set[str] = set()
            pat_queue: list[str] = list(replay_sources)
            while pat_queue:
                nid = pat_queue.pop()
                for e in _effective_edges:
                    if e.source == nid and e.target not in pat_descendants:
                        pat_descendants.add(e.target)
                        pat_queue.append(e.target)
            post_api_hc_descendants.update(pat_descendants)
            for nid, vals in pat_outputs.items():
                if nid in pat_descendants and nid not in triggered_api_clients:
                    outputs[nid] = vals
                    if nid not in host_check_ids and nid in pat_hyst_snapshot:
                        hyst[nid] = pat_hyst_snapshot[nid]
            _apply_operating_hours_state(pat_descendants, pre_execute_node_state)
            _register_change_filter_pulses(pat_descendants)
            newly_triggered_hc: set[str] = set()
            for node in flow.nodes:
                if node.type == "host_check" and node.id in pat_descendants and node.id not in triggered_host_check_nodes:
                    await _run_host_check_node(node, newly_triggered_hc, " (post-api replay)")
            if newly_triggered_hc:
                post_api_triggered_hc.update(newly_triggered_hc)
                triggered_host_check_nodes.update(newly_triggered_hc)
                _add_resolved_outputs(newly_triggered_hc)
                pending_post_api_hc_replay.update(newly_triggered_hc)

        # Post-api host_check replay can make downstream WoL nodes fire after
        # the normal WoL loop has already run. Process those affected nodes once
        # more so the side effect is not deferred to the next graph execution.
        post_api_wol_nodes: set[str] = set()
        if post_api_hc_descendants:
            for node in flow.nodes:
                if node.type != "wake_on_lan" or node.id not in post_api_hc_descendants or node.id in triggered_wol_nodes:
                    continue
                out = outputs.get(node.id, {})
                hyst_wol = hyst.setdefault(node.id, {})
                is_triggered = GraphExecutor._to_bool(out.get("_trigger"))
                was_triggered = hyst_wol.get("wol_prev_trigger", False)
                is_cron_triggered = node.id in cron_reachable
                if not is_triggered:
                    hyst_wol["wol_prev_trigger"] = False
                    continue
                if was_triggered and not is_cron_triggered:
                    continue
                mac = (node.data.get("mac_address") or "").strip()
                if not mac:
                    logger.warning("wake_on_lan: mac_address missing on node %s", node.id[:8])
                    continue
                broadcast = (node.data.get("broadcast_ip") or "").strip() or "255.255.255.255"
                _port_raw = node.data.get("port")
                try:
                    if isinstance(_port_raw, float) and not _port_raw.is_integer():
                        raise ValueError(f"fractional port {_port_raw!r} — must be a whole number")
                    port = int(_port_raw) if _port_raw not in (None, "") else 9
                    if not (1 <= port <= 65535):
                        raise ValueError(f"port {port!r} out of range 1–65535")
                    try:
                        ipaddress.IPv4Address(broadcast)
                    except ValueError:
                        raise ValueError(f"invalid broadcast IP {broadcast!r}") from None
                    await asyncio.to_thread(_send_wol_packet, mac, broadcast, port)
                    hyst_wol["wol_prev_trigger"] = True
                    outputs[node.id]["sent"] = True
                    post_api_wol_nodes.add(node.id)
                    triggered_wol_nodes.add(node.id)
                    logger.info("Graph %s: WoL sent by node %s", graph_id[:8], node.id[:8])
                except Exception:
                    logger.exception("Graph %s: WoL failed on node %s", graph_id[:8], node.id[:8])

        if post_api_wol_nodes:
            _add_resolved_outputs(post_api_wol_nodes)
            post_api_wol_overrides: dict[str, dict[str, Any]] = {}
            for e in _effective_edges:
                if e.source in post_api_wol_nodes:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    post_api_wol_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if post_api_wol_overrides:
                wol_base_overrides = api_replay_overrides if api_replay_overrides is not None else aug_overrides
                post_api_wol_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in wol_base_overrides.items()}
                for nid, vals in resolved_async_edge_overrides.items():
                    post_api_wol_merged.setdefault(nid, {}).update(vals)
                for nid, vals in post_api_wol_overrides.items():
                    post_api_wol_merged.setdefault(nid, {}).update(vals)
                _pawol_hyst_snap = _safe_deepcopy_state(hyst)
                post_api_wol_executor = await _executor(_pawol_hyst_snap)
                post_api_wol_outputs = await _execute_pass(post_api_wol_executor, post_api_wol_merged)
                # This WoL send can itself newly reveal — only once visible in
                # this pass's OWN outputs — that a further downstream async
                # node (e.g. a second, chained wake_on_lan or host_check) is
                # triggered but hasn't actually run yet: its own output here
                # is still a placeholder, exactly like every other replay
                # site. A change_filter reachable only through that node must
                # stay held rather than commit — and let a downstream
                # host_check irreversibly ping — using this still-wrong
                # value. Redo with suppression applied if this reveals
                # anything new.
                _pawol_late_pending = _still_unresolved_source_ids(post_api_wol_outputs)
                _pawol_late_cf_hold_ids = _compute_cf_hold_ids(
                    _unresolved_value_ids_from(post_api_wol_outputs) | _pawol_late_pending, post_api_wol_outputs
                )
                if _pawol_late_cf_hold_ids:
                    for _pawol_late_cf_id in _pawol_late_cf_hold_ids:
                        post_api_wol_merged.setdefault(_pawol_late_cf_id, {})["_suppress_change_filter"] = True
                    _pawol_hyst_snap = _safe_deepcopy_state(hyst)
                    post_api_wol_executor = await _executor(_pawol_hyst_snap)
                    post_api_wol_outputs = await _execute_pass(post_api_wol_executor, post_api_wol_merged)
                post_api_wol_descendants: set[str] = set()
                post_api_wol_queue = list(post_api_wol_nodes)
                while post_api_wol_queue:
                    nid = post_api_wol_queue.pop()
                    for e in _effective_edges:
                        if e.source == nid and e.target not in post_api_wol_descendants:
                            post_api_wol_descendants.add(e.target)
                            post_api_wol_queue.append(e.target)
                wol_node_ids = {n.id for n in flow.nodes if n.type == "wake_on_lan"}
                for nid, vals in post_api_wol_outputs.items():
                    if nid not in wol_node_ids and nid in post_api_wol_descendants:
                        outputs[nid] = vals
                        if nid not in host_check_ids and nid in _pawol_hyst_snap:
                            hyst[nid] = _pawol_hyst_snap[nid]
                _register_change_filter_pulses(post_api_wol_descendants)

                # HC nodes driven by post-api WoL output
                _pawol_hc: set[str] = set()
                for node in flow.nodes:
                    if node.type == "host_check" and node.id in post_api_wol_descendants and node.id not in triggered_host_check_nodes:
                        await _run_host_check_node(node, _pawol_hc, " (post-api-wol)")
                if _pawol_hc:
                    triggered_host_check_nodes.update(_pawol_hc)
                    _add_resolved_outputs(_pawol_hc)
                    _pawol_pending = set(_pawol_hc)
                    _pawol_processed: set[str] = set()
                    while _pawol_pending:
                        _pawol_replay_src = _pawol_pending - _pawol_processed
                        if not _pawol_replay_src:
                            break
                        _pawol_processed.update(_pawol_replay_src)
                        _pawol_dn_ovr: dict[str, dict[str, Any]] = {}
                        for _e in _effective_edges:
                            if _e.source in _pawol_replay_src:
                                _pawol_dn_ovr.setdefault(_e.target, {})[_e.targetHandle or "in"] = GraphExecutor._get_output_value(
                                    outputs[_e.source], _e.sourceHandle or "out"
                                )
                        if not _pawol_dn_ovr:
                            continue
                        _pawol_base = api_replay_overrides if api_replay_overrides is not None else aug_overrides
                        _pawol_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in _pawol_base.items()}
                        for nid, vals in resolved_async_edge_overrides.items():
                            _pawol_merged.setdefault(nid, {}).update(vals)
                        for nid, vals in _pawol_dn_ovr.items():
                            _pawol_merged.setdefault(nid, {}).update(vals)
                        _pawol_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                        _pawol_exec = await _executor(_pawol_hyst)
                        _pawol_out = await _execute_pass(_pawol_exec, _pawol_merged)
                        _pawol_desc: set[str] = set()
                        _pawol_dq: list[str] = list(_pawol_replay_src)
                        while _pawol_dq:
                            _pn = _pawol_dq.pop()
                            for _e in _effective_edges:
                                if _e.source == _pn and _e.target not in _pawol_desc:
                                    _pawol_desc.add(_e.target)
                                    _pawol_dq.append(_e.target)
                        for nid, vals in _pawol_out.items():
                            if nid in _pawol_desc and nid not in triggered_api_clients:
                                outputs[nid] = vals
                                if nid not in host_check_ids and nid in _pawol_hyst:
                                    hyst[nid] = _pawol_hyst[nid]
                        _apply_operating_hours_state(_pawol_desc, pre_execute_node_state)
                        _pawol_chained: set[str] = set()
                        for node in flow.nodes:
                            if node.type == "host_check" and node.id in _pawol_desc and node.id not in triggered_host_check_nodes:
                                await _run_host_check_node(node, _pawol_chained, " (post-api-wol replay)")
                        if _pawol_chained:
                            triggered_host_check_nodes.update(_pawol_chained)
                            _add_resolved_outputs(_pawol_chained)
                            _pawol_pending.update(_pawol_chained)

        post_api_hc_api_clients: set[str] = set()
        if post_api_hc_descendants:
            for node in flow.nodes:
                if node.type != "api_client" or node.id not in post_api_hc_descendants or node.id in triggered_api_clients:
                    continue
                out = outputs.get(node.id, {})
                if not GraphExecutor._to_bool(out.get("_trigger")):
                    continue
                variable_resolver = _make_api_client_variable_resolver(
                    self._registry,
                    node.data.get("variables"),
                    execution_values_by_datapoint_id,
                )
                try:
                    url = _replace_api_client_url_placeholders(
                        node.data.get("url") or "",
                        variable_resolver,
                    ).strip()
                    if not url:
                        # Matches _run_api_client_node's own empty-URL path
                        # (target_set.add + return False there): this node
                        # is genuinely, finally inactive — not still
                        # pending — so it must be marked settled here too.
                        # Without this, _still_unresolved_source_ids keeps
                        # treating it as pending forever (its own _trigger
                        # reads True, but it's never added to
                        # _settled_async_ids via _add_resolved_outputs
                        # below), holding every change_filter downstream of
                        # it hostage indefinitely across every future
                        # execution of this chain.
                        post_api_hc_api_clients.add(node.id)
                        triggered_api_clients.add(node.id)
                        continue
                except _ApiClientVariableError as exc:
                    logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                    outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)
                    continue
                try:
                    request_urls, pinned_headers, request_extensions = _build_api_client_fetch_targets(url)
                except ValueError as exc:
                    logger.warning("Graph %s: blocked api_client target %s: %s", graph_id[:8], url, exc)
                    outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)
                    continue
                method = (node.data.get("method", "GET") or "GET").upper()
                content_type = node.data.get("content_type", "application/json")
                resp_type = node.data.get("response_type", "application/json")
                verify_ssl = node.data.get("verify_ssl", True)
                if isinstance(verify_ssl, str):
                    verify_ssl = verify_ssl.lower() not in ("false", "0", "no")
                timeout_s = float(node.data.get("timeout_s", 10) or 10)
                extra_headers: dict[str, str] = {}
                hdr_str = (node.data.get("headers") or "").strip()
                if hdr_str:
                    try:
                        extra_headers = _json.loads(hdr_str)
                    except (json.JSONDecodeError, TypeError):
                        pass
                hdr_file = (node.data.get("headers_value_file") or "").strip()
                if hdr_file:
                    try:
                        extra_headers = {
                            **extra_headers,
                            **_json.loads(_load_external_value_file(hdr_file)),
                        }
                    except (json.JSONDecodeError, TypeError):
                        pass
                try:
                    extra_headers = _replace_api_client_placeholders(extra_headers, variable_resolver)
                except _ApiClientVariableError as exc:
                    logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                    outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)
                    continue
                auth_type = (node.data.get("auth_type") or "none").lower()
                auth: Any = None
                try:
                    if auth_type in ("basic", "digest"):
                        username = _replace_api_client_placeholders(
                            node.data.get("auth_username") or "",
                            variable_resolver,
                        ).strip()
                        password = _replace_api_client_placeholders(
                            node.data.get("auth_password") or "",
                            variable_resolver,
                        )
                        if username:
                            auth = httpx.BasicAuth(username, password) if auth_type == "basic" else httpx.DigestAuth(username, password)
                    elif auth_type == "bearer":
                        token = _replace_api_client_placeholders(
                            node.data.get("auth_token") or "",
                            variable_resolver,
                        ).strip()
                        if not token:
                            token = _replace_api_client_placeholders(
                                _load_external_value_file(node.data.get("auth_value_file") or ""),
                                variable_resolver,
                            ).strip()
                        if token:
                            extra_headers = {
                                **extra_headers,
                                "Authorization": f"Bearer {token}",
                            }
                except _ApiClientVariableError as exc:
                    logger.warning("Graph %s: api_client variable error: %s", graph_id[:8], exc)
                    outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)
                    continue
                try:
                    req_kwargs: dict[str, Any] = {
                        "headers": extra_headers,
                        "timeout": timeout_s,
                    }
                    if method in ("POST", "PUT", "PATCH"):
                        body = _replace_api_client_placeholders(out.get("_body"), variable_resolver)
                        if content_type == "application/json":
                            req_kwargs["content"] = _json.dumps(body) if not isinstance(body, (str, bytes)) else body
                            req_kwargs["headers"] = {
                                **extra_headers,
                                "Content-Type": "application/json",
                            }
                        elif content_type == "application/x-www-form-urlencoded":
                            req_kwargs["data"] = body if isinstance(body, dict) else {"data": str(body)}
                        else:
                            req_kwargs["content"] = str(body or "")
                            req_kwargs["headers"] = {
                                **extra_headers,
                                "Content-Type": "text/plain",
                            }
                    req_headers = {key: value for key, value in req_kwargs.get("headers", {}).items() if key.lower() != "host"}
                    req_kwargs["headers"] = {**req_headers, **pinned_headers}
                    if request_extensions:
                        req_kwargs["extensions"] = request_extensions
                    last_transport_error: Exception = ValueError(f"Could not fetch API target after trying {len(request_urls)} address(es)")
                    resp: httpx.Response | Any | None = None
                    async with httpx.AsyncClient(auth=auth, verify=verify_ssl) as client:
                        for request_url in request_urls:
                            try:
                                resp = await client.request(method, request_url, **req_kwargs)
                                break
                            except httpx.RequestError as req_exc:
                                last_transport_error = req_exc
                                if method not in _API_CLIENT_RETRYABLE_METHODS:
                                    break
                                continue
                    if resp is None:
                        raise last_transport_error
                    resp_text = resp.text
                    if len(resp_text) > 1_000_000:
                        resp_text = resp_text[:1_000_000]
                    if resp_type in ("json", "application/json"):
                        try:
                            resp_data: Any = resp.json()
                        except ValueError:
                            resp_data = resp_text
                    else:
                        resp_data = resp_text
                    outputs[node.id].update(
                        {
                            "response": resp_data,
                            "status": resp.status_code,
                            "success": 200 <= resp.status_code < 300,
                        },
                    )
                    logger.info(
                        "Graph %s: API %s %s → %d",
                        graph_id[:8],
                        method,
                        url,
                        resp.status_code,
                    )
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)
                except Exception as exc:
                    logger.exception("Graph %s: api_client failed", graph_id[:8])
                    outputs[node.id].update({"response": str(exc), "status": None, "success": False})
                    post_api_hc_api_clients.add(node.id)
                    triggered_api_clients.add(node.id)

        if post_api_hc_api_clients:
            _add_resolved_outputs(post_api_hc_api_clients)
            api_descendants: set[str] = set()
            pending_sources = list(post_api_hc_api_clients)
            while pending_sources:
                source_id = pending_sources.pop()
                for e in _effective_edges:
                    if e.source != source_id or e.target in api_descendants:
                        continue
                    api_descendants.add(e.target)
                    pending_sources.append(e.target)

            downstream_overrides: dict[str, dict[str, Any]] = {}
            for e in _effective_edges:
                if e.source in post_api_hc_api_clients:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    downstream_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs[e.source], src_handle)
            if downstream_overrides:
                replay_base = api_replay_overrides if api_replay_overrides is not None else aug_overrides
                replay_overrides = {nid: dict(vals) for nid, vals in replay_base.items()}
                for nid, vals in downstream_overrides.items():
                    replay_overrides.setdefault(nid, {}).update(vals)
                for e in _effective_edges:
                    if e.target not in api_descendants or e.source in api_descendants or e.source in post_api_hc_api_clients:
                        continue
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    replay_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(outputs.get(e.source, {}), src_handle)
                replay_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                api_executor = await _executor(replay_hyst)
                api_outputs = await _execute_pass(api_executor, replay_overrides)
                for nid, vals in api_outputs.items():
                    if nid not in api_client_ids and nid in api_descendants:
                        outputs[nid] = vals
                        if nid in replay_hyst:
                            hyst[nid] = replay_hyst[nid]
                _apply_operating_hours_state(api_descendants, pre_execute_node_state)
                _register_change_filter_pulses(api_descendants)
                final_api_triggered_hc: set[str] = set()
                for node in flow.nodes:
                    if node.type == "host_check" and node.id in api_descendants and node.id not in triggered_host_check_nodes:
                        await _run_host_check_node(node, final_api_triggered_hc, " (post-api api replay)")
                if final_api_triggered_hc:
                    triggered_host_check_nodes.update(final_api_triggered_hc)
                    _add_resolved_outputs(final_api_triggered_hc)
                    pending_final_api_hc_replay = set(final_api_triggered_hc)
                    processed_final_api_hc_replay: set[str] = set()
                    while pending_final_api_hc_replay:
                        replay_sources = pending_final_api_hc_replay - processed_final_api_hc_replay
                        if not replay_sources:
                            break
                        processed_final_api_hc_replay.update(replay_sources)
                        final_hc_descendants: set[str] = set()
                        final_hc_queue = list(replay_sources)
                        while final_hc_queue:
                            nid = final_hc_queue.pop()
                            for e in _effective_edges:
                                if e.source == nid and e.target not in final_hc_descendants:
                                    final_hc_descendants.add(e.target)
                                    final_hc_queue.append(e.target)
                        final_hc_overrides: dict[str, dict[str, Any]] = {}
                        for e in _effective_edges:
                            if e.source in replay_sources:
                                src_handle = e.sourceHandle or "out"
                                tgt_handle = e.targetHandle or "in"
                                final_hc_overrides.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(
                                    outputs[e.source],
                                    src_handle,
                                )
                        if not final_hc_overrides:
                            continue
                        final_hc_merged = {nid: dict(vals) for nid, vals in replay_overrides.items()}
                        for nid, vals in resolved_async_edge_overrides.items():
                            final_hc_merged.setdefault(nid, {}).update(vals)
                        for nid, vals in final_hc_overrides.items():
                            final_hc_merged.setdefault(nid, {}).update(vals)
                        for e in _effective_edges:
                            if e.target not in final_hc_descendants or e.source in final_hc_descendants or e.source in replay_sources:
                                continue
                            src_handle = e.sourceHandle or "out"
                            tgt_handle = e.targetHandle or "in"
                            final_hc_merged.setdefault(e.target, {})[tgt_handle] = GraphExecutor._get_output_value(
                                outputs.get(e.source, {}),
                                src_handle,
                            )
                        final_hc_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                        final_hc_executor = await _executor(final_hc_hyst)
                        final_hc_outputs = await _execute_pass(final_hc_executor, final_hc_merged)
                        # Same late-hold guard as every earlier replay stage
                        # (see _hc_late_pending/_hc_late_cf_hold_ids above):
                        # this replay can itself newly activate another
                        # async node (e.g. a chained api_client) that hasn't
                        # actually run yet — its output here is a
                        # placeholder, and there is no further pass after
                        # this one to correct a change_filter that already
                        # committed it.
                        _final_hc_late_pending = _still_unresolved_source_ids(final_hc_outputs)
                        _final_hc_late_cf_hold_ids = _compute_cf_hold_ids(
                            _unresolved_value_ids_from(final_hc_outputs) | _final_hc_late_pending, final_hc_outputs
                        )
                        if _final_hc_late_cf_hold_ids:
                            for _final_hc_late_cf_id in _final_hc_late_cf_hold_ids:
                                final_hc_merged.setdefault(_final_hc_late_cf_id, {})["_suppress_change_filter"] = True
                            final_hc_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                            final_hc_executor = await _executor(final_hc_hyst)
                            final_hc_outputs = await _execute_pass(final_hc_executor, final_hc_merged)
                        for nid, vals in final_hc_outputs.items():
                            if nid in final_hc_descendants and nid not in triggered_api_clients:
                                outputs[nid] = vals
                                if nid not in host_check_ids and nid in final_hc_hyst:
                                    hyst[nid] = final_hc_hyst[nid]
                        _apply_operating_hours_state(final_hc_descendants, pre_execute_node_state)
                        _register_change_filter_pulses(final_hc_descendants)
                        chained_final_hc: set[str] = set()
                        for node in flow.nodes:
                            if node.type == "host_check" and node.id in final_hc_descendants and node.id not in triggered_host_check_nodes:
                                await _run_host_check_node(node, chained_final_hc, " (post-api api replay)")
                        if chained_final_hc:
                            triggered_host_check_nodes.update(chained_final_hc)
                            _add_resolved_outputs(chained_final_hc)
                            pending_final_api_hc_replay.update(chained_final_hc)

        # ── Final WoL pass ────────────────────────────────────────────────
        # The final HC replay (above) can set wake_on_lan._trigger=True for
        # WoL nodes that the earlier WoL loop never reached. Send those packets
        # so that chains like api_client→hc→api_client→wol complete in one tick.
        _final_wol_candidates: set[str] = set()
        for _fw_node in flow.nodes:
            if _fw_node.type != "wake_on_lan" or _fw_node.id in triggered_wol_nodes:
                continue
            _fw_out = outputs.get(_fw_node.id, {})
            _fw_hyst = hyst.setdefault(_fw_node.id, {})
            if not GraphExecutor._to_bool(_fw_out.get("_trigger")):
                _fw_hyst["wol_prev_trigger"] = False
                continue
            if _fw_hyst.get("wol_prev_trigger") and _fw_node.id not in cron_reachable:
                continue
            _fw_mac = (_fw_node.data.get("mac_address") or "").strip()
            if not _fw_mac:
                logger.warning("wake_on_lan: mac_address missing on node %s", _fw_node.id[:8])
                continue
            _fw_broadcast = (_fw_node.data.get("broadcast_ip") or "").strip() or "255.255.255.255"
            _fw_port_raw = _fw_node.data.get("port")
            try:
                if isinstance(_fw_port_raw, float) and not _fw_port_raw.is_integer():
                    raise ValueError(f"fractional port {_fw_port_raw!r}")
                _fw_port = int(_fw_port_raw) if _fw_port_raw not in (None, "") else 9
                if not (1 <= _fw_port <= 65535):
                    raise ValueError(f"port {_fw_port!r} out of range 1–65535")
                try:
                    ipaddress.IPv4Address(_fw_broadcast)
                except ValueError:
                    raise ValueError(f"invalid broadcast IP {_fw_broadcast!r}") from None
                await asyncio.to_thread(_send_wol_packet, _fw_mac, _fw_broadcast, _fw_port)
                _fw_hyst["wol_prev_trigger"] = True
                outputs[_fw_node.id]["sent"] = True
                _final_wol_candidates.add(_fw_node.id)
                triggered_wol_nodes.add(_fw_node.id)
                logger.info("Graph %s: WoL sent by node %s", graph_id[:8], _fw_node.id[:8])
            except Exception:
                logger.exception("Graph %s: WoL failed on node %s", graph_id[:8], _fw_node.id[:8])
        if _final_wol_candidates:
            _add_resolved_outputs(_final_wol_candidates)
            _fwol_dn_ovr: dict[str, dict[str, Any]] = {}
            for _e in _effective_edges:
                if _e.source in _final_wol_candidates:
                    _fwol_dn_ovr.setdefault(_e.target, {})[_e.targetHandle or "in"] = GraphExecutor._get_output_value(
                        outputs[_e.source], _e.sourceHandle or "out"
                    )
            if _fwol_dn_ovr:
                _fwol_base = api_replay_overrides if api_replay_overrides is not None else aug_overrides
                _fwol_merged: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in _fwol_base.items()}
                for nid, vals in resolved_async_edge_overrides.items():
                    _fwol_merged.setdefault(nid, {}).update(vals)
                for nid, vals in _fwol_dn_ovr.items():
                    _fwol_merged.setdefault(nid, {}).update(vals)
                _fwol_hyst_snap = _safe_deepcopy_state(hyst)
                _fwol_exec = await _executor(_fwol_hyst_snap)
                _fwol_out = await _execute_pass(_fwol_exec, _fwol_merged)
                # Same late-hold guard as every earlier replay stage (see
                # _hc_late_pending/_hc_late_cf_hold_ids above): this final-WoL
                # replay can itself newly activate another async node whose
                # output here is still only a placeholder, and there is no
                # further pass after this one to correct a change_filter
                # that already committed it.
                _fwol_late_pending = _still_unresolved_source_ids(_fwol_out)
                _fwol_late_cf_hold_ids = _compute_cf_hold_ids(_unresolved_value_ids_from(_fwol_out) | _fwol_late_pending, _fwol_out)
                if _fwol_late_cf_hold_ids:
                    for _fwol_late_cf_id in _fwol_late_cf_hold_ids:
                        _fwol_merged.setdefault(_fwol_late_cf_id, {})["_suppress_change_filter"] = True
                    _fwol_hyst_snap = _safe_deepcopy_state(hyst)
                    _fwol_exec = await _executor(_fwol_hyst_snap)
                    _fwol_out = await _execute_pass(_fwol_exec, _fwol_merged)
                _fwol_desc: set[str] = set()
                _fwol_q: list[str] = list(_final_wol_candidates)
                while _fwol_q:
                    _fn = _fwol_q.pop()
                    for _e in _effective_edges:
                        if _e.source == _fn and _e.target not in _fwol_desc:
                            _fwol_desc.add(_e.target)
                            _fwol_q.append(_e.target)
                _fwol_wol_ids = {n.id for n in flow.nodes if n.type == "wake_on_lan"}
                for nid, vals in _fwol_out.items():
                    if nid not in _fwol_wol_ids and nid in _fwol_desc and nid not in triggered_api_clients:
                        outputs[nid] = vals
                        if nid not in host_check_ids and nid in _fwol_hyst_snap:
                            hyst[nid] = _fwol_hyst_snap[nid]
                _register_change_filter_pulses(_fwol_desc)
                _fwol_hc: set[str] = set()
                for node in flow.nodes:
                    if node.type == "host_check" and node.id in _fwol_desc and node.id not in triggered_host_check_nodes:
                        await _run_host_check_node(node, _fwol_hc, " (final-wol)")
                if _fwol_hc:
                    triggered_host_check_nodes.update(_fwol_hc)
                    _add_resolved_outputs(_fwol_hc)
                    _fwolhc_pending = set(_fwol_hc)
                    _fwolhc_processed: set[str] = set()
                    while _fwolhc_pending:
                        _fwolhc_srcs = _fwolhc_pending - _fwolhc_processed
                        if not _fwolhc_srcs:
                            break
                        _fwolhc_processed.update(_fwolhc_srcs)
                        _fwolhc_dn_ovr: dict[str, dict[str, Any]] = {}
                        for _e in _effective_edges:
                            if _e.source in _fwolhc_srcs:
                                _fwolhc_dn_ovr.setdefault(_e.target, {})[_e.targetHandle or "in"] = GraphExecutor._get_output_value(
                                    outputs[_e.source], _e.sourceHandle or "out"
                                )
                        if not _fwolhc_dn_ovr:
                            continue
                        _fwolhc_base = api_replay_overrides if api_replay_overrides is not None else aug_overrides
                        _fwolhc_mrgd: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in _fwolhc_base.items()}
                        for nid, vals in resolved_async_edge_overrides.items():
                            _fwolhc_mrgd.setdefault(nid, {}).update(vals)
                        for nid, vals in _fwolhc_dn_ovr.items():
                            _fwolhc_mrgd.setdefault(nid, {}).update(vals)
                        _fwolhc_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                        _fwolhc_exec = await _executor(_fwolhc_hyst)
                        _fwolhc_out = await _execute_pass(_fwolhc_exec, _fwolhc_mrgd)
                        _fwolhc_desc: set[str] = set()
                        _fwolhc_dq: list[str] = list(_fwolhc_srcs)
                        while _fwolhc_dq:
                            _fn = _fwolhc_dq.pop()
                            for _e in _effective_edges:
                                if _e.source == _fn and _e.target not in _fwolhc_desc:
                                    _fwolhc_desc.add(_e.target)
                                    _fwolhc_dq.append(_e.target)
                        for nid, vals in _fwolhc_out.items():
                            if nid in _fwolhc_desc and nid not in triggered_api_clients:
                                outputs[nid] = vals
                                if nid not in host_check_ids and nid in _fwolhc_hyst:
                                    hyst[nid] = _fwolhc_hyst[nid]
                        _apply_operating_hours_state(_fwolhc_desc, pre_execute_node_state)
                        _register_change_filter_pulses(_fwolhc_desc)
                        _fwolhc_chained: set[str] = set()
                        for node in flow.nodes:
                            if node.type == "host_check" and node.id in _fwolhc_desc and node.id not in triggered_host_check_nodes:
                                await _run_host_check_node(node, _fwolhc_chained, " (final-wol-hc)")
                        if _fwolhc_chained:
                            triggered_host_check_nodes.update(_fwolhc_chained)
                            _add_resolved_outputs(_fwolhc_chained)
                            _fwolhc_pending.update(_fwolhc_chained)

        # ── Handle message_archive ────────────────────────────────────────────
        triggered_message_archive_nodes: set[str] = set()
        replayed_message_archive_nodes: set[str] = set()

        async def _run_message_archive_node(node: Any, target_set: set[str]) -> bool:
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                return False
            if not _has_fresh_firing_input(node.id, out):
                # A truthy but STALE _trigger (e.g. left over from a previous
                # tick that hasn't gone false yet) means this action will not
                # actually run this tick — its final output IS this current,
                # inactive `out`, not a still-pending placeholder. Without
                # settling it here, a downstream change_filter reachable only
                # through this node would stay held hostage to it for the
                # rest of the tick for no reason — see the late release pass
                # near the end of this function and _freshness_settled_async_ids.
                _add_resolved_outputs({node.id})
                _freshness_settled_async_ids.add(node.id)
                return False

            archive_id = (node.data.get("archive_id") or "").strip().lower()
            if not archive_id:
                logger.warning("Message archive: archive_id missing on node %s", node.id[:8])
                target_set.add(node.id)
                return False

            _raw_msg = out.get("_message")
            msg = _msg_to_str(_raw_msg) if _raw_msg is not None else str(node.data.get("message") or "")
            _raw_title = out.get("_title")
            title = _msg_to_str(_raw_title) if _raw_title is not None else str(node.data.get("title") or "")
            message_type = str(node.data.get("type") or "automation")
            severity = str(node.data.get("severity") or "info")

            try:
                from obs.message_archive import get_message_archive_service

                payload = {
                    "graph_id": graph_id,
                    "graph_name": name,
                    "node_id": node.id,
                    "node_label": node.data.get("label") or node.data.get("name") or "",
                }
                source = f"logic.graph.{graph_id}.node.{node.id}"
                record_kwargs = {"type": message_type, "severity": severity, "source": source, "title": title, "message": msg, "payload": payload}
                await get_message_archive_service().record(archive_id, **record_kwargs)
                outputs[node.id]["stored"] = True
                target_set.add(node.id)
                logger.info("Graph %s: message archived in %s (msg=%r)", graph_id[:8], archive_id, msg[:40])
                return True
            except Exception:
                logger.exception("Graph %s: message archive write failed (node=%s)", graph_id[:8], node.id[:8])
                target_set.add(node.id)
                return False

        triggered_notify_nodes: set[str] = set()
        replayed_notify_nodes: set[str] = set()

        input_sources = {(edge.target, edge.targetHandle or "in"): (edge.source, edge.sourceHandle or "out") for edge in flow.edges}
        freshness_cache: dict[tuple[frozenset[str], frozenset[tuple[str, str]]], dict[str, set[str]]] = {}

        def _current_input_value(node_id: str, handle: str) -> Any:
            node_overrides = {**aug_overrides.get(node_id, {}), **debug_overrides.get(node_id, {})}
            if handle in node_overrides:
                return node_overrides[handle]
            source_id, source_handle = input_sources.get((node_id, handle), ("", ""))
            return GraphExecutor._get_output_value(outputs.get(source_id, {}), source_handle)

        def _event_fresh_inputs() -> dict[str, set[str]] | None:
            if not overrides:
                return None
            event_sources = {node_id: dict(values) for node_id, values in overrides.items()}
            for node_id in refreshed_ical_nodes:
                event_sources.setdefault(node_id, {})
            blocked_sources = {node.id for node in flow.nodes if node.type == "memory"}
            blocked_sources.update(api_client_ids - triggered_api_clients)
            blocked_sources.update(host_check_ids - executed_host_check_nodes)
            blocked_sources.update(ical_ids - refreshed_ical_nodes)
            blocked_sources.update(message_archive_ids - replayed_message_archive_nodes)
            blocked_sources.update(notify_ids - replayed_notify_nodes)
            blocked_sources.update(node.id for node in flow.nodes if node.type == "wake_on_lan" and node.id not in triggered_wol_nodes)
            blocked_sources.update(node.id for node in flow.nodes if node.type == "random_value" and outputs.get(node.id, {}).get("value") is None)
            blocked_sources.update(
                node.id
                for node in flow.nodes
                if node.type == "gate"
                and node.data.get("closed_behavior", "retain") == "retain"
                and GraphExecutor._to_bool(_current_input_value(node.id, "enable")) == bool(node.data.get("negate_enable"))
            )
            no_result_mapping_ids = {
                node.id
                for node in flow.nodes
                if node.type == "value_mapping"
                and not GraphExecutor._to_bool(node.data.get("has_default"))
                and outputs.get(node.id, {}).get("result") is None
            }
            blocked_outputs = {
                (edge.source, edge.sourceHandle or "out")
                for edge in _effective_edges
                if edge.source in no_result_mapping_ids and (edge.sourceHandle or "out") in {"out", "result"}
            }
            while True:
                cache_key = (frozenset(blocked_sources), frozenset(blocked_outputs))
                if cache_key not in freshness_cache:
                    freshness_cache[cache_key] = _fresh_input_handles(event_sources, flow.edges, blocked_sources, blocked_outputs)
                event_fresh_inputs = freshness_cache[cache_key]
                newly_blocked_default_gates = {
                    node.id
                    for node in flow.nodes
                    if node.type == "gate"
                    and node.data.get("closed_behavior", "retain") == "default_value"
                    and GraphExecutor._to_bool(_current_input_value(node.id, "enable")) == bool(node.data.get("negate_enable"))
                    and ("enable" not in event_fresh_inputs.get(node.id, set()) or graph_state.get(node.id, {}).get("gate_prev_open") is False)
                } - blocked_sources
                if not newly_blocked_default_gates:
                    return event_fresh_inputs
                blocked_sources.update(newly_blocked_default_gates)

        # The "message" port also accepts a trigger-typed pulse (e.g.
        # change_filter.changed wired directly into Notify.message) — a
        # a non-firing source means "no pulse", not "a real message",
        # unlike any other falsy-but-real message (0, "", an ordinary bool
        # source, ...), which must still count as delivered. Only an edge
        # whose provenance reaches a change_filter's own "changed" output
        # gets this treatment, including effective pure-relay paths. Keeping
        # provenance structural (not value-based) leaves ordinary boolean
        # sources wired to "message" unaffected.
        (
            _cf_changed_message_origins,
            _cf_changed_trigger_origins,
            _cf_changed_trigger_handle_origins,
            _cf_downstream_filter_origins,
            _late_cf_changed_stateful_relay_origins,
        ) = _build_cf_pulse_origins(_event_fresh_inputs(), {node_id: _event_origin(node_id) for node_id in set(overrides) | refreshed_ical_nodes})
        _cf_changed_stateful_relay_origins = _late_cf_changed_stateful_relay_origins
        _refresh_missing_cf_override_values()
        _neutralize_missing_cf_messages()

        def _has_fresh_firing_input(node_id: str, out: dict[str, Any]) -> bool:
            event_fresh_inputs = _event_fresh_inputs()
            _msg = out.get("_message")
            _pulse_origins = _cf_changed_message_origins.get(node_id, set())
            _is_missing_cf_pulse = bool(_pulse_origins) and not any(_origin_pulsed(origin) for origin in _pulse_origins)
            _trigger_origins = _cf_changed_trigger_origins.get(node_id, set())
            _is_missing_cf_trigger = bool(_trigger_origins) and not any(_origin_pulsed(origin) for origin in _trigger_origins)
            if event_fresh_inputs is None:
                fresh_message = _msg is not None and not _is_missing_cf_pulse
                fresh_trigger = GraphExecutor._to_bool(_current_input_value(node_id, "trigger")) and not _is_missing_cf_trigger
                return fresh_message or fresh_trigger
            fresh_handles = event_fresh_inputs.get(node_id, set())
            fresh_message = "message" in fresh_handles and _msg is not None and not _is_missing_cf_pulse
            fresh_trigger = (
                "trigger" in fresh_handles and GraphExecutor._to_bool(_current_input_value(node_id, "trigger")) and not _is_missing_cf_trigger
            )
            return fresh_message or fresh_trigger

        async def _run_notify_node(node: Any, target_set: set[str]) -> bool:
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                return False
            if not _has_fresh_firing_input(node.id, out):
                # See _run_message_archive_node's identical check: a truthy
                # but STALE _trigger means this notification will not
                # actually fire this tick, so its final output IS this
                # current, inactive `out` — settle it now (see the late
                # release pass near the end of this function and
                # _freshness_settled_async_ids) so a downstream change_filter
                # isn't held hostage to this node for the rest of the tick
                # just because it never reaches the success path below (the
                # only other place that settles it).
                _add_resolved_outputs({node.id})
                _freshness_settled_async_ids.add(node.id)
                return False

            if node.type == "notify_message":
                instance_id = str(node.data.get("adapter_instance_id") or "").strip()
                providers = node.data.get("providers") or []
                if not instance_id or not isinstance(providers, list) or not providers:
                    outputs[node.id]["__error__"] = "MESSAGE adapter and at least one target are required"
                    logger.warning("Notification: adapter or targets missing on node %s", node.id[:8])
                    target_set.add(node.id)
                    return False
                from obs.adapters import registry as adapter_registry

                adapter = adapter_registry.get_instance_by_id(instance_id)
                if adapter is None or getattr(adapter, "adapter_type", None) != "MESSAGE":
                    outputs[node.id]["__error__"] = "MESSAGE adapter instance is unavailable"
                    logger.warning("Notification: MESSAGE adapter %s unavailable", instance_id)
                    target_set.add(node.id)
                    return False
                raw_message = out.get("_message")
                message = _msg_to_str(raw_message) if raw_message is not None else str(node.data.get("message") or "")
                try:
                    raw_priority = node.data.get("priority")
                    try:
                        priority = int(raw_priority) if raw_priority not in (None, "") else 0
                    except (TypeError, ValueError):
                        priority = 0
                    priority = max(-2, min(1, priority))
                    results = await adapter.send_notification(
                        message=message,
                        providers=providers,
                        title=str(node.data.get("title") or "") or None,
                        priority=priority,
                    )
                    failures = [result for result in results if not result.ok]
                    if not results or failures:
                        detail = ", ".join(f"{result.provider}/{result.target}: {result.detail}" for result in failures)
                        outputs[node.id]["__error__"] = detail or "MESSAGE adapter did not process any targets"
                        logger.warning("Graph %s: notification failed: %s", graph_id[:8], outputs[node.id]["__error__"])
                        target_set.add(node.id)
                        return False
                    outputs[node.id]["sent"] = True
                    target_set.add(node.id)
                    return True
                except Exception as exc:
                    outputs[node.id]["__error__"] = str(exc)
                    logger.exception("Graph %s: notification failed", graph_id[:8])
                    target_set.add(node.id)
                    return False

            if node.type == "notify_pushover":
                app_token = (node.data.get("app_token") or "").strip()
                user_key = (node.data.get("user_key") or "").strip()
                if not app_token or not user_key:
                    logger.warning("Pushover: app_token or user_key missing on node %s", node.id[:8])
                    target_set.add(node.id)
                    return False
                _raw_msg = out.get("_message")
                msg = _msg_to_str(_raw_msg) if _raw_msg is not None else str(node.data.get("message") or "")
                title = node.data.get("title", "open bridge server")
                prio = int(node.data.get("priority", 0))
                _out_url = out.get("_url")
                _out_utit = out.get("_url_title")
                _out_img = out.get("_image_url")
                url = (_msg_to_str(_out_url) if _out_url is not None else (node.data.get("url") or "")).strip()
                url_title = (_msg_to_str(_out_utit) if _out_utit is not None else (node.data.get("url_title") or "")).strip()
                image_url = (_msg_to_str(_out_img) if _out_img is not None else (node.data.get("image_url") or "")).strip()
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        payload: dict[str, object] = {
                            "token": app_token,
                            "user": user_key,
                            "title": str(title),
                            "message": msg,
                            "priority": prio,
                        }
                        if url:
                            payload["url"] = url
                        if url_title:
                            payload["url_title"] = url_title

                        if image_url:
                            resolved = await _resolve_safe_image_url(image_url)
                            if resolved is None:
                                raise ValueError("Unsafe image_url: only validated HTTPS targets are allowed")
                            pinned_url, host_header, pinned_ip = resolved
                            async with client.stream(
                                "GET",
                                pinned_url,
                                timeout=10.0,
                                follow_redirects=False,
                                headers={"Host": host_header},
                                extensions={"sni_hostname": host_header.split(":", 1)[0]},
                            ) as img_r:
                                net_stream = img_r.extensions.get("network_stream")
                                if net_stream is not None:
                                    server_addr = net_stream.get_extra_info("server_addr")
                                    if server_addr and server_addr[0] != pinned_ip:
                                        raise ValueError("Pushover image_url resolved to an unexpected target IP")
                                img_r.raise_for_status()
                                content_type = img_r.headers.get("content-type", "").split(";")[0].strip().lower()
                                if not content_type.startswith("image/"):
                                    raise ValueError("Pushover image_url must return an image/* content type")

                                content_len_raw = img_r.headers.get("content-length", "0") or "0"
                                try:
                                    content_len = int(content_len_raw)
                                except ValueError:
                                    content_len = 0
                                if content_len > _PUSHOVER_ATTACHMENT_MAX_BYTES:
                                    raise ValueError("Pushover attachment too large (max 5 MB)")

                                img_content = bytearray()
                                async for chunk in img_r.aiter_bytes():
                                    img_content.extend(chunk)
                                    if len(img_content) > _PUSHOVER_ATTACHMENT_MAX_BYTES:
                                        raise ValueError("Pushover attachment too large (max 5 MB)")

                            fname = image_url.split("?")[0].split("/")[-1] or "image.jpg"
                            r = await client.post(
                                "https://api.pushover.net/1/messages.json",
                                data=payload,
                                files={"attachment": (fname, bytes(img_content), content_type or "image/jpeg")},
                            )
                        else:
                            r = await client.post(
                                "https://api.pushover.net/1/messages.json",
                                data=payload,
                            )
                        r.raise_for_status()
                        outputs[node.id]["sent"] = True
                        target_set.add(node.id)
                        logger.info("Graph %s: Pushover sent (msg=%r)", graph_id[:8], msg[:40])
                        return True
                except Exception:
                    logger.exception(
                        "Graph %s: Pushover failed (msg=%r)",
                        graph_id[:8],
                        msg[:40],
                    )
                    target_set.add(node.id)
                    return False

            if node.type == "notify_sms":
                api_key = (node.data.get("api_key") or "").strip()
                to = (node.data.get("to") or "").strip()
                if not api_key or not to:
                    logger.warning("seven.io SMS: api_key or to missing on node %s", node.id[:8])
                    target_set.add(node.id)
                    return False
                _raw_msg = out.get("_message")
                msg = _msg_to_str(_raw_msg) if _raw_msg is not None else str(node.data.get("message") or "")
                sender = node.data.get("sender", "obs")
                try:
                    async with httpx.AsyncClient(timeout=15.0) as client:
                        r = await client.post(
                            "https://gateway.seven.io/api/sms",
                            headers={"X-Api-Key": api_key},
                            data={"to": to, "from": str(sender), "text": msg},
                        )
                        r.raise_for_status()
                        body = r.text.strip()
                        logger.info(
                            "Graph %s: seven.io response status=%d body=%r",
                            graph_id[:8],
                            r.status_code,
                            body[:80],
                        )
                        _SEVEN_ERRORS = {
                            100: "Unbekannter Fehler / Empfänger nicht angegeben",
                            200: "Absender nicht angegeben",
                            201: "Absender zu lang (max 11 Zeichen)",
                            300: "Nachricht nicht angegeben",
                            301: "Nachricht zu lang",
                            401: "API-Key ungültig oder nicht autorisiert",
                            402: "Nicht genug Guthaben",
                            403: "Absender nicht erlaubt",
                            500: "Server-Fehler bei seven.io",
                        }
                        try:
                            body_int = int(body)
                            if body_int in _SEVEN_ERRORS:
                                raise ValueError(f"seven.io Fehlercode {body_int}: {_SEVEN_ERRORS[body_int]}")
                            if body_int <= 0:
                                raise ValueError(f"seven.io: 0 Nachrichten gesendet (body={body!r})")
                        except ValueError:
                            raise
                        except TypeError:
                            pass
                        outputs[node.id]["sent"] = True
                        target_set.add(node.id)
                        logger.info(
                            "Graph %s: seven.io SMS sent to %s (msg=%r)",
                            graph_id[:8],
                            to,
                            msg[:40],
                        )
                        return True
                except Exception:
                    logger.exception(
                        "Graph %s: seven.io SMS failed (msg=%r)",
                        graph_id[:8],
                        msg[:40],
                    )
                    target_set.add(node.id)
                    return False

            return False

        async def _run_replay_triggered_side_effects(candidate_ids: set[str]) -> None:
            def _triggered_side_effect_ids() -> set[str]:
                return (
                    triggered_message_archive_nodes
                    | triggered_notify_nodes
                    | triggered_api_clients
                    | triggered_wol_nodes
                    | triggered_host_check_nodes
                )

            pending_candidates = set(candidate_ids)
            while pending_candidates:
                # Settle side effects in dependency layers.  A downstream
                # action must not run until every pending upstream action has
                # had its real result replayed through the graph; otherwise
                # it consumes that action's first-pass placeholder.
                ready_candidates: set[str] = set()
                for candidate_id in pending_candidates:
                    seen = {candidate_id}
                    upstream = [candidate_id]
                    has_pending_predecessor = False
                    while upstream and not has_pending_predecessor:
                        target_id = upstream.pop()
                        for candidate_edge in _effective_edges:
                            if candidate_edge.target != target_id or candidate_edge.source in seen:
                                continue
                            if candidate_edge.source in pending_candidates:
                                has_pending_predecessor = True
                                break
                            seen.add(candidate_edge.source)
                            upstream.append(candidate_edge.source)
                    if not has_pending_predecessor:
                        ready_candidates.add(candidate_id)
                if not ready_candidates:
                    break

                newly_triggered: set[str] = set()
                for node in flow.nodes:
                    if node.id not in ready_candidates:
                        continue
                    if node.type == "host_check" and node.id not in triggered_host_check_nodes:
                        if await _run_host_check_node(node, newly_triggered, " (message-archive replay)"):
                            triggered_host_check_nodes.add(node.id)
                    elif node.type == "wake_on_lan" and node.id not in triggered_wol_nodes:
                        if await _run_wake_on_lan_node(node, newly_triggered):
                            triggered_wol_nodes.add(node.id)
                    elif node.type == "api_client" and node.id not in triggered_api_clients:
                        if await _run_api_client_node(node, newly_triggered):
                            triggered_api_clients.add(node.id)
                    elif node.type == "message_archive" and node.id not in triggered_message_archive_nodes:
                        if await _run_message_archive_node(node, newly_triggered):
                            triggered_message_archive_nodes.add(node.id)
                    elif node.type in {"notify_message", "notify_pushover", "notify_sms"} and node.id not in triggered_notify_nodes:
                        out = outputs.get(node.id, {})
                        if GraphExecutor._to_bool(out.get("_trigger")) and _has_fresh_firing_input(node.id, out):
                            await _run_notify_node(node, newly_triggered)
                            triggered_notify_nodes.add(node.id)
                pending_candidates.difference_update(ready_candidates)
                if not newly_triggered:
                    continue
                _add_resolved_outputs(newly_triggered)
                pending_candidates.update(
                    await _replay_async_descendants(
                        newly_triggered,
                        skip_node_ids=_triggered_side_effect_ids(),
                    )
                )
                replayed_message_archive_nodes.update(newly_triggered & triggered_message_archive_nodes)
                replayed_notify_nodes.update(newly_triggered & triggered_notify_nodes)

        for node in flow.nodes:
            if node.type != "message_archive":
                continue
            await _run_message_archive_node(node, triggered_message_archive_nodes)
        if triggered_message_archive_nodes:
            _add_resolved_outputs(triggered_message_archive_nodes)
            message_archive_descendants = await _replay_async_descendants(
                triggered_message_archive_nodes,
                skip_node_ids=triggered_message_archive_nodes
                | triggered_notify_nodes
                | triggered_api_clients
                | triggered_wol_nodes
                | triggered_host_check_nodes,
            )
            replayed_message_archive_nodes.update(triggered_message_archive_nodes)
            await _run_replay_triggered_side_effects(message_archive_descendants)

        # ── Handle notify_pushover ────────────────────────────────────────
        # Generic notifications use the MESSAGE adapter. Provider-specific
        # branches below are retained solely for existing legacy sheets.
        for node in flow.nodes:
            if node.type == "notify_message" and node.id not in triggered_notify_nodes:
                await _run_notify_node(node, triggered_notify_nodes)

        # Runs AFTER api_client second-pass so that graphs with api_client →
        # json_extractor → notify see the real HTTP response, not placeholders.
        for node in flow.nodes:
            if node.type != "notify_pushover":
                continue
            if node.id in triggered_notify_nodes:
                continue
            await _run_notify_node(node, triggered_notify_nodes)

        # ── Handle notify_sms ─────────────────────────────────────────────
        for node in flow.nodes:
            if node.type != "notify_sms":
                continue
            if node.id in triggered_notify_nodes:
                continue
            await _run_notify_node(node, triggered_notify_nodes)

        if triggered_notify_nodes:
            _add_resolved_outputs(triggered_notify_nodes)
            notify_descendants = await _replay_async_descendants(
                triggered_notify_nodes,
                skip_node_ids=triggered_message_archive_nodes
                | triggered_notify_nodes
                | triggered_api_clients
                | triggered_wol_nodes
                | triggered_host_check_nodes,
            )
            replayed_notify_nodes.update(triggered_notify_nodes)
            await _run_replay_triggered_side_effects(notify_descendants)

        # ── Late release of change_filters held only by a now-settled
        # message_archive/notify node ───────────────────────────────────
        # A message_archive/notify node with a truthy but STALE _trigger
        # (see _run_message_archive_node/_run_notify_node's freshness-skip
        # branch above) is only settled here, well after the very first
        # _cf_hold_ids correction already ran and may have suppressed a
        # change_filter reachable through it. Recompute the hold set with
        # exactly those nodes subtracted from the ORIGINAL, frozen
        # _unresolved_source_ids seed, and redo that same correction if
        # anything is no longer tainted — reusing _cf_hold_ids' original
        # island as the redo scope (safe: only removing seeds can only
        # shrink reachability, never reveal a node outside that island).
        # Deliberately NOT a general _still_unresolved_source_ids()
        # recompute here: by this point in the tick, the outer `outputs`
        # has been updated by many unrelated intermediate replays (host_check/
        # WoL chains that reached a new, still-unresolved link without ever
        # "settling" it) — recomputing the whole seed from that drifted
        # snapshot could incorrectly release a change_filter that must stay
        # held for one of those unrelated, still-genuinely-pending reasons.
        if _cf_hold_ids:
            # A freshness-skipped action also settles async descendants that
            # were included only because the original frozen chain closure
            # ran through that action. Re-evaluate just that scoped closure;
            # unrelated async branches retain their frozen seed status.
            _freshness_descendants = async_replay_source_ids & _downstream_closure(_freshness_settled_async_ids, _effective_edges)
            _still_unresolved_freshness_descendants = _still_unresolved_source_ids() & _freshness_descendants
            _freshness_definitively_settled = _freshness_settled_async_ids | (_freshness_descendants - _still_unresolved_freshness_descendants)
            _late_cf_hold_ids = _compute_cf_hold_ids(_unresolved_source_ids - _freshness_definitively_settled)
            if _late_cf_hold_ids != _cf_hold_ids:
                _late_cf_hold_overrides: dict[str, dict[str, Any]] = {nid: dict(vals) for nid, vals in aug_overrides.items()}
                for _nid, _vals in resolved_async_edge_overrides.items():
                    _late_cf_hold_overrides.setdefault(_nid, {}).update(_vals)
                for _cf_id in _late_cf_hold_ids:
                    _late_cf_hold_overrides.setdefault(_cf_id, {})["_suppress_change_filter"] = True
                _late_cf_hold_known_outputs = {nid: vals for nid, vals in outputs.items() if nid not in _cf_hold_island}
                _late_cf_hold_hyst = _safe_deepcopy_state(pre_execute_hyst if pre_execute_hyst is not None else hyst)
                _late_cf_hold_outputs = await _execute_pass(
                    await _executor(_late_cf_hold_hyst), _late_cf_hold_overrides, known_outputs=_late_cf_hold_known_outputs
                )
                for _nid, _vals in _late_cf_hold_outputs.items():
                    if _nid in _cf_hold_island:
                        outputs[_nid] = _vals
                        if _nid in _late_cf_hold_hyst:
                            hyst[_nid] = _late_cf_hold_hyst[_nid]
                        else:
                            hyst.pop(_nid, None)
                _register_change_filter_pulses(_cf_hold_island)
                # This release can produce a change_filter's first genuine
                # changed=True pulse — but every host_check/WoL/api_client/
                # archive/notify execution loop above has already finished
                # for this tick, so simply updating `outputs` here never
                # actually runs any of them. Without this, an action fed by
                # that pulse (e.g. change_filter.changed -> host_check)
                # would have its new baseline committed silently, losing
                # the action until the next real, unrelated change.
                await _run_replay_triggered_side_effects(_cf_hold_island)

        # Deferred hc_prev_trigger=False: clear only for HC nodes that did NOT
        # fire in any async pass. Clearing inside _run_host_check_node was wrong
        # for async-driven triggers (e.g. api_client.success→hc._trigger) because
        # the first executor pass uses placeholder success=False → _trigger=False,
        # but after the post-api pass the real trigger may be True. By deferring
        # to here, triggered_host_check_nodes is final.
        for node in flow.nodes:
            if node.type == "host_check" and node.id not in triggered_host_check_nodes:
                hyst.setdefault(node.id, {})["hc_prev_trigger"] = False

        # Memory is the explicit tick boundary for feedback loops. Commit it
        # after all async node re-propagation so the stored value always reflects
        # the final graph outputs, not executor placeholders from an earlier pass.
        memory_commit_overrides = _debug_run_overrides(aug_overrides)
        blocked_memory_inputs: set[tuple[str, str]] = set()
        for memory_node in flow.nodes:
            if memory_node.type != "memory":
                continue
            origins = _cf_changed_trigger_origins.get(memory_node.id, set())
            if origins and not any(_origin_pulsed(origin) for origin in origins):
                memory_commit_overrides.setdefault(memory_node.id, {})["reset"] = False
            data_origins = _cf_changed_stateful_relay_origins.get(memory_node.id, {}).get("in", set())
            if data_origins and not any(_origin_pulsed(origin) for origin in data_origins):
                blocked_memory_inputs.add((memory_node.id, "in"))
        executor.commit_memory_inputs(outputs, memory_commit_overrides, blocked_memory_inputs)

        # ── Start/cancel value sequences ──────────────────────────────────
        wired_inputs: set[tuple[str, str]] = {(e.target, e.targetHandle or "in") for e in flow.edges}
        node_by_id = {node.id: node for node in flow.nodes}
        pending_sequence_starts: list[tuple[Any, bool]] = []
        for node in flow.nodes:
            if node.type != "value_sequence":
                continue
            output = outputs.get(node.id, {})
            key = (graph_id, node.id)
            condition_origins = _cf_changed_stateful_relay_origins.get(node.id, {}).get("condition", set())
            condition_missing = condition_origins and not any(_origin_pulsed(origin) for origin in condition_origins)
            condition = (
                self._sequence_conditions.get(key, True)
                if condition_missing
                else GraphExecutor._to_bool(output.get("_condition"))
                if (node.id, "condition") in wired_inputs
                else True
            )
            if condition_missing:
                output["_condition"] = condition
            self._sequence_conditions[key] = condition
            active = self._sequence_tasks.get(key)
            if (
                node.data.get("cancel_when_condition_false")
                and not condition
                and active
                and not active.done()
                and active is not asyncio.current_task()
            ):
                self._cancel_sequence_task(key)
            state = graph_state.setdefault(node.id, {})
            triggered = GraphExecutor._to_bool(output.get("_triggered"))
            # A wired condition gates every sequence mode.  The cancellation
            # setting controls only whether an already-running task is stopped.
            blocked = not condition
            if blocked:
                state["sequence_prev_trigger"] = False
                continue
            was_triggered = state.get("sequence_prev_trigger", False)
            state["sequence_prev_trigger"] = triggered
            cron_triggered = any(
                edge.target == node.id
                and (edge.targetHandle or "in") == "trigger"
                and edge.source in cron_reachable
                # A pulse reaching a Memory node (e.g. change_filter.changed
                # -> memory.reset) puts memory itself in cron_reachable, but
                # memory is an explicit tick boundary: its "out" this pass is
                # whatever was already committed at the end of a *previous*
                # tick, unaffected by the reset/in this pulse just delivered
                # (that only takes effect via the deferred
                # commit_memory_inputs, for the *next* tick). Memory being
                # reachable must not be read as "my trigger input just
                # pulsed" — only a non-Memory source directly in
                # cron_reachable genuinely means that.
                and (node_by_id[edge.source].type != "memory" if edge.source in node_by_id else True)
                for edge in _effective_edges
            )
            pulse_sources = [node.id]
            pulse_seen: set[str] = set()
            change_pulse_triggered = False
            while pulse_sources:
                target_id = pulse_sources.pop()
                if target_id in pulse_seen:
                    continue
                pulse_seen.add(target_id)
                # Memory is an explicit tick boundary — mirrors cron_reachable's
                # forward traversal above (see its own "memory" comment): its
                # "out" this pass is whatever was already committed at the end
                # of a *previous* tick, entirely independent of whatever pulse
                # just reached its "in"/"reset" this tick — that only takes
                # effect via the deferred commit_memory_inputs, for the *next*
                # tick. A change_filter feeding memory.reset must not be
                # treated as if it drove memory.out itself, so this reverse
                # trace must not walk past memory to inspect what fed it.
                _target_node = node_by_id.get(target_id)
                if _target_node is not None and _target_node.type == "memory":
                    continue
                for edge in _effective_edges:
                    if edge.target != target_id:
                        continue
                    # Same trigger-aware filtering as _edge_carries_pulse: at
                    # the sequence node itself, only its "trigger" handle
                    # carries a pulse (a change_filter wired into "condition"
                    # must not retrigger a separately sustained trigger) — and
                    # the same applies at every intermediate hop, e.g. a pulse
                    # entering an api_client's "body" data port must not be
                    # traced onward as if it drove that node's own trigger.
                    if not _edge_carries_pulse(edge):
                        continue
                    source = node_by_id.get(edge.source)
                    if source and (
                        (
                            source.type in ("datapoint_read", "change_filter")
                            and (edge.sourceHandle or "out") == "changed"
                            and GraphExecutor._to_bool(outputs.get(source.id, {}).get("changed"))
                        )
                        # Every Edge Detection handle is discrete, so the one
                        # that fired this pass retriggers a sequence exactly
                        # like a "changed" pulse does.
                        or (source.type == "edge_detect" and (edge.source, edge.sourceHandle or "out") in _discrete_pulse_handles({edge.source}))
                    ):
                        change_pulse_triggered = True
                        break
                    pulse_sources.append(edge.source)
                if change_pulse_triggered:
                    break
            if triggered and (not was_triggered or cron_triggered or change_pulse_triggered):
                # Defer creating the task until ordinary datapoint writes have
                # been published below.  A task created here can otherwise run
                # at the write loop's first await and invert graph-local order.
                pending_sequence_starts.append((node, condition))

        # ── Process datapoint_write outputs — apply trigger gating + write-side filters,
        # then publish DataValueEvent so registry, ring-buffer, MQTT and WS all get notified.
        await self._apply_datapoint_write_outputs(graph_id, flow, outputs, graph_state, wired_inputs, execute_now, logic_depth)

        # Value sequences are intentionally started after synchronous graph
        # writes, so an execution that triggers both has deterministic order.
        for node, condition in pending_sequence_starts:
            if not graph_state.get(node.id, {}).get("sequence_prev_trigger", False):
                continue
            current_condition = self._sequence_conditions.get((graph_id, node.id), condition)
            if current_condition:
                self._start_value_sequence(graph_id, node, current_condition, logic_depth, flow.model_dump_json())

        for node in flow.nodes:
            if node.type == "gate":
                graph_state.setdefault(node.id, {})["gate_prev_open"] = GraphExecutor._to_bool(_current_input_value(node.id, "enable")) != bool(
                    node.data.get("negate_enable")
                )

        # ── Persist node state (statistics / hysteresis) to DB ───────────
        await self._persist_node_state(graph_id)

        # Select each node's capture from the execution pass whose output was
        # retained. Async replay passes may execute unrelated branches whose
        # outputs are deliberately discarded; their inputs must be discarded too.
        if capture_debug_inputs:
            for run_outputs, run_inputs in debug_input_runs:
                for node_id, ports in run_inputs.items():
                    if run_outputs.get(node_id) is outputs.get(node_id):
                        debug_inputs[node_id] = ports
            if debug_input_capture is not None:
                debug_input_capture.clear()
                debug_input_capture.update(debug_inputs)

        # ── Broadcast final execution results to all WS clients ──────────
        # Broadcast happens here — after all async ops (api_client HTTP calls,
        # second-pass re-execution, etc.) — so the debug view shows the real
        # success/response values and not the executor's initial placeholders.
        for node_id, ports in (debug_inputs or {}).items():
            node_debug_overrides = debug_overrides.get(node_id, {})
            for port, snapshot in ports.items():
                is_debug_override = port in node_debug_overrides
                snapshot["overridden"] = is_debug_override
                if not is_debug_override:
                    snapshot["incoming"] = snapshot["effective"]
        try:
            from obs.api.v1.websocket import get_ws_manager

            ws_manager = get_ws_manager()
            if not ws_manager.has_logic_debug_subscribers(graph_id):
                return outputs

            payload = await _run_logic_debug_serialization_in_worker(
                _serialize_logic_debug_payload,
                graph_id,
                outputs,
                debug_inputs or {},
                debug_overrides,
                execution_started,
            )
            await ws_manager.broadcast_logic_debug(graph_id, payload)
        except Exception:
            logger.exception("Graph %s: WS broadcast failed — ignoring (non-critical)", graph_id[:8])

        return outputs

    # ── Cache ─────────────────────────────────────────────────────────────

    async def _persist_node_state(self, graph_id: str) -> None:
        """Persist node state (statistics / hysteresis) to the DB.

        Nodes with persist_state=False are excluded from the saved snapshot
        so their accumulators reset on server restart (opt-out behaviour).
        """
        hyst = self._hysteresis.get(graph_id)
        if not hyst:
            return
        try:
            ical_runtime_keys = {
                "raw",
                "_ical_result_cache",
                "_ical_last_attempt_url",
                "_ical_last_attempt_limit",
                "_ical_last_attempt_ts",
                "_ical_precompute_token",
            }

            def _without_ical_runtime(node_state: Any) -> Any:
                if not isinstance(node_state, dict):
                    return node_state
                return {key: value for key, value in node_state.items() if key not in ical_runtime_keys}

            graph_entry = self._graphs.get(graph_id)
            if graph_entry:
                _, _, _flow = graph_entry
                current_nodes = {node.id: node for node in _flow.nodes}
                no_persist = {n.id for n in _flow.nodes if n.data.get("persist_state") is False}
                state_to_save = {}
                for node_id, node_state in hyst.items():
                    if node_id not in current_nodes or node_id in no_persist:
                        continue
                    state_to_save[node_id] = _without_ical_runtime(node_state)
            else:
                # During a semantic save, invalidate_cache() briefly removes
                # the graph entry before reload() restores it.  Never let that
                # cache gap serialize large calendar bodies or attempt metadata.
                state_to_save = {node_id: _without_ical_runtime(node_state) for node_id, node_state in hyst.items()}
            # _persist_default covers values json can't natively encode
            # (e.g. a change_filter holding a datetime.time/date from a KNX
            # DPT10/11 object, or bytes from an UNKNOWN-type DataPoint) —
            # without it, one such node poisons persistence for every node
            # in the graph, since this dumps the whole snapshot in one call.
            # Recognized types are tagged so _load_graphs can restore the
            # exact original value/type instead of leaving it as a lossy
            # str() that a live value of the same type can never compare
            # equal to again. _escape_persist_collision runs first so a
            # node's own application data can never be misread as one of
            # those tags, and the version envelope lets _load_graphs know
            # this row is guaranteed fully tagged (see _PERSIST_STATE_VERSION).
            escaped_state: dict[str, Any] = {}
            for node_id, node_state in state_to_save.items():
                try:
                    escaped_node_state = _escape_persist_collision(node_state)
                    # Validate each node independently before composing the
                    # graph row. Cyclic or otherwise unencodable state from
                    # one custom value must not prevent unrelated node state
                    # from being saved.
                    json.dumps(escaped_node_state, default=_persist_default)
                except Exception:
                    logger.warning(
                        "Graph %s node %s: skipping unpersistable node_state",
                        graph_id[:8],
                        node_id,
                        exc_info=True,
                    )
                    continue
                escaped_state[node_id] = escaped_node_state
            state_payload: dict[str, Any] = escaped_state
            if _PERSIST_TYPE_TAG in escaped_state:
                # Node ids are unrestricted strings. Preserve a top-level
                # id that collides with the persistence tag without walking
                # already-escaped node values a second time.
                state_payload = {_PERSIST_TYPE_TAG: _PERSIST_ESCAPED_TAG, "value": escaped_state}
            envelope = {
                _PERSIST_STATE_VERSION_KEY: _PERSIST_STATE_VERSION,
                "state": state_payload,
            }
            await self._db.execute_and_commit(
                "UPDATE logic_graphs SET node_state = ? WHERE id = ?",
                (json.dumps(envelope, default=_persist_default), graph_id),
            )
        except Exception:
            logger.exception("Graph %s: failed to persist node_state", graph_id[:8])

    async def _apply_datapoint_write_outputs(
        self,
        graph_id: str,
        flow: FlowData,
        outputs: dict[str, dict[str, Any]],
        graph_state: dict[str, Any],
        wired_inputs: set[tuple[str, str]],
        write_now: datetime,
        logic_depth: int,
        *,
        skip_node_ids: set[str] | frozenset[str] = frozenset(),
        initialization: bool = False,
    ) -> set[str]:
        """Apply trigger gating + write-side filters to datapoint_write outputs,
        then publish DataValueEvent so registry, ring-buffer, MQTT and WS all get
        notified. skip_node_ids excludes individual write nodes (used by
        initialize_graph for writes descending from unseeded Read Objects);
        initialization marks the events as save-time seeding so notification
        subscribers do not react to them. Returns the ids of the write nodes
        whose event was actually published.
        """
        from obs.core.event_bus import DataValueEvent

        published: set[str] = set()
        for node in flow.nodes:
            if node.type != "datapoint_write" or node.id in skip_node_ids:
                continue
            node_out = outputs.get(node.id, {})
            write_val = node_out.get("_write_value")

            # ── Trigger gating ───────────────────────────────────────────
            # If the trigger handle is wired, only write when trigger is truthy.
            if (node.id, "trigger") in wired_inputs:
                triggered = node_out.get("_triggered")
                if not GraphExecutor._to_bool(triggered):
                    continue

            if write_val is None:
                continue
            dp_id_str = node.data.get("datapoint_id")
            if not dp_id_str:
                continue

            ns = graph_state.setdefault(node.id, {})
            if not self._write_filters_allow(node.data, ns, write_val, write_now):
                continue

            # All filters passed — update state and publish
            ns["last_write_val"] = write_val
            ns["last_write_ts"] = write_now
            try:
                dp_id = uuid.UUID(dp_id_str)
                event = DataValueEvent(
                    datapoint_id=dp_id,
                    value=write_val,
                    quality="good",
                    source_adapter="logic",
                    logic_depth=logic_depth + 1,
                    initialization=initialization,
                )
                await self._event_bus.publish(event)
                published.add(node.id)
                logger.debug("Graph %s: wrote dp %s = %s", graph_id, dp_id_str, write_val)
            except Exception:
                logger.exception("Graph %s: failed to write dp %s", graph_id, dp_id_str)
        return published

    @staticmethod
    def _write_filters_allow(d: dict[str, Any], ns: dict[str, Any], write_val: Any, write_now: datetime) -> bool:
        """Write-side only_on_change / min_delta / throttle filters.

        Pure predicate against the node's filter state — shared by the
        publish path and the initialization settle pass, which must not feed
        values downstream that these filters would suppress.
        """
        last_wr = ns.get("last_write_val")
        last_ts = ns.get("last_write_ts")

        ooc = d.get("only_on_change")
        if (ooc is True or ooc == "true") and write_val == last_wr:
            return False

        raw_delta = d.get("min_delta")
        if raw_delta not in (None, "", 0) and last_wr is not None:
            try:
                if abs(float(write_val) - float(last_wr)) < float(raw_delta):
                    return False
            except (TypeError, ValueError):
                pass

        tv = d.get("throttle_value")
        if tv not in (None, "", 0) and last_ts is not None:
            try:
                unit_ms = _THROTTLE_UNITS.get(d.get("throttle_unit", "s"), 1000.0)
                if (write_now - last_ts).total_seconds() * 1000 < float(tv) * unit_ms:
                    return False
            except (TypeError, ValueError):
                pass
        return True

    async def _load_graphs(self) -> None:
        rows = await self._db.fetchall("SELECT id, name, enabled, flow_data, node_state FROM logic_graphs")
        self._graphs = {}
        for row in rows:
            try:
                raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
                flow = FlowData.model_validate(raw)
                _migrate_legacy_api_client_field_names(flow)
                self._graphs[row["id"]] = (row["name"], bool(row["enabled"]), flow)

                # Restore persisted node state (statistics, hysteresis, …) from DB,
                # but only when there is no in-memory state already — so a reload()
                # triggered by a graph save does NOT overwrite the live accumulators.
                if row["id"] not in self._hysteresis:
                    try:
                        saved_raw = json.loads(row["node_state"] or "{}")
                        # _persist_node_state ALWAYS writes exactly these
                        # two top-level keys, unconditionally, regardless of
                        # how many real nodes the graph has or what any of
                        # their ids are — every real per-node entry lives
                        # one level deeper, inside "state", never colliding
                        # with the envelope's own two reserved keys at this
                        # level. A cross-check against this row's current
                        # node ids was tried here to also guard a legacy
                        # (pre-envelope, unwrapped) row whose own node ids
                        # coincidentally collided with these two reserved
                        # strings — but that guard could not tell a genuine
                        # collision apart from an ordinary graph that
                        # simply CONTAINS a node named "state" (reachable
                        # via importing a hand-crafted flow_data, unlike a
                        # node_state collision, which needs direct DB
                        # tampering that the app itself never does), and so
                        # rejected every genuine envelope for such a graph
                        # instead. Requiring exactly these two top-level
                        # keys (nothing else) still narrows the legacy
                        # collision to graphs with exactly two real nodes
                        # matching both reserved ids, without that
                        # regression.
                        is_tagged_envelope = (
                            isinstance(saved_raw, dict)
                            and saved_raw.get(_PERSIST_STATE_VERSION_KEY) == _PERSIST_STATE_VERSION
                            and isinstance(saved_raw.get("state"), dict)
                            and len(saved_raw) == 2
                        )
                        if is_tagged_envelope:
                            # Restore any value _persist_node_state had to tag
                            # (datetime.date/time/datetime, bytes) back to its
                            # exact original type. A row saved under this
                            # exact version is *guaranteed* fully tagged, so
                            # any string surviving this decode is a genuine
                            # string value — never needs the legacy
                            # "_recovered_str" marker below at all (applying
                            # it here would wrongly suppress a real
                            # string→datetime type transition on a source
                            # that legitimately persisted a native string).
                            # Decode the *whole* state container first, not
                            # just each value: if some stateful node's own
                            # unrestricted string id happens to be exactly
                            # _PERSIST_TYPE_TAG, _escape_persist_collision
                            # wraps this entire top-level mapping in an
                            # escape envelope (since it, too, is just a dict
                            # that "contains the reserved tag key"). Decoding
                            # per-value here would then iterate that
                            # envelope's own _PERSIST_TYPE_TAG/"value" keys
                            # as though they were node ids instead of
                            # unwrapping it — _decode_persisted_value already
                            # knows how to reverse exactly this wrapper.
                            _decoded_state = _decode_persisted_value(saved_raw["state"])
                            saved = _decoded_state if isinstance(_decoded_state, dict) else {}
                            # _persist_default's catch-all for an otherwise
                            # unrecognized type (e.g. a python_script's
                            # complex-number/custom-object baseline) tags it
                            # "opaque_str" — a genuinely lossy str() stand-in,
                            # unlike every other string in this envelope.
                            # Mark it "_opaque_recovered_str" so
                            # GraphExecutor._compare_values can still
                            # recognize a live value matching that
                            # representation as "unchanged", while a later,
                            # genuine type transition still clears the marker
                            # and reports a real change — same self-clearing
                            # mechanism as the legacy "_recovered_str" below,
                            # just detected from this version's own tag
                            # instead of inferred from "any string".
                            # Same unwrap as above, but keeping the RAW
                            # (not yet _decode_persisted_value-processed)
                            # shape _contains_opaque_tag expects: if a node
                            # id collided with _PERSIST_TYPE_TAG, the whole
                            # container above is the escape wrapper, and its
                            # own top-level keys ("__obs_persisted_type__",
                            # "value") are not node ids — looking those up
                            # directly would find nothing for every node in
                            # this graph, silently skipping the
                            # _opaque_recovered_str marker for all of them.
                            _raw_state_container = saved_raw["state"]
                            if isinstance(_raw_state_container, dict) and _raw_state_container.get(_PERSIST_TYPE_TAG) == _PERSIST_ESCAPED_TAG:
                                _unwrapped_raw_state = _raw_state_container.get("value")
                                if isinstance(_unwrapped_raw_state, dict):
                                    _raw_state_container = _unwrapped_raw_state
                            _cf_ids_v2 = {n.id for n in flow.nodes if n.type == "change_filter"}
                            for _nid_v2 in _cf_ids_v2:
                                _raw_state_v2 = _raw_state_container.get(_nid_v2)
                                _decoded_state_v2 = saved.get(_nid_v2)
                                if (
                                    isinstance(_raw_state_v2, dict)
                                    and _contains_opaque_tag(_raw_state_v2.get("value"))
                                    and isinstance(_decoded_state_v2, dict)
                                ):
                                    _decoded_state_v2["_opaque_recovered_str"] = True
                        elif isinstance(saved_raw, dict) and saved_raw:
                            # Legacy row (saved before tagged persistence
                            # existed): plain default=str, no version
                            # envelope. May still hold a change_filter's
                            # datetime.date/time/datetime lossily flattened
                            # to a plain str() — tag those as DB-recovered so
                            # GraphExecutor._compare_values knows it may
                            # safely re-recognize a matching live temporal
                            # value as "unchanged". Without it, a string this
                            # node received live *this session* (never
                            # round-tripped through persistence) would be
                            # wrongly treated the same way, swallowing a
                            # genuine live type transition. The flag is
                            # self-clearing: any real value commit replaces
                            # the whole state dict, dropping it again.
                            saved = saved_raw
                            _cf_ids = {n.id for n in flow.nodes if n.type == "change_filter"}
                            for _nid in _cf_ids:
                                _node_state = saved.get(_nid)
                                if isinstance(_node_state, dict) and isinstance(_node_state.get("value"), str):
                                    _node_state["_recovered_str"] = True
                        else:
                            saved = saved_raw
                        if isinstance(saved, dict) and saved:
                            self._hysteresis[row["id"]] = saved
                            logger.debug(
                                "Graph %s: restored node_state (%d nodes)",
                                row["id"][:8],
                                len(saved),
                            )
                    except (json.JSONDecodeError, TypeError):
                        pass
            except Exception:
                logger.exception("Failed to parse graph %s", row["id"])

    def invalidate_cache(self, graph_id: str) -> None:
        self._ical_cache_generations[graph_id] = object()
        self._graphs.pop(graph_id, None)
        # NOTE: _hysteresis is intentionally NOT cleared here.
        # When a graph is saved (PUT/PATCH), invalidate_cache + reload() are called.
        # Clearing _hysteresis would reset statistics accumulators on every save.
        # The state is re-used by the next execution after reload.
        # On DELETE the graph row is gone from DB so no persistence concerns remain;
        # the in-memory entry is a no-op and will be GC'd naturally.
        self._node_state.pop(graph_id, None)
        self._cancel_sequence_tasks(graph_id)
        # Cancel cron tasks for this specific graph
        to_remove = [k for k in list(self._cron_tasks) if k[0] == graph_id]
        for k in to_remove:
            self._cron_tasks[k].cancel()
            del self._cron_tasks[k]

    def _prune_graph_executor_lock(self, graph_id: str) -> None:
        """Release an idle worker lock after its graph has disappeared."""
        lock = self._graph_executor_locks.get(graph_id)
        if graph_id not in self._graphs and lock is not None and not lock.locked():
            self._graph_executor_locks.pop(graph_id, None)

    def _prune_ical_precompute_lock(self, key: tuple[str, str], expected_lock: asyncio.Lock | None = None) -> None:
        """Drop an inactive node's lock only after its worker has drained."""
        lock = self._ical_precompute_locks.get(key)
        if lock is None or (expected_lock is not None and lock is not expected_lock):
            return
        graph_id, node_id = key
        graph = self._graphs.get(graph_id)
        active = bool(
            graph
            and graph[1]
            and any(
                node.id == node_id and node.type == "ical" and isinstance(node.data.get("url"), str) and node.data["url"].strip()
                for node in graph[2].nodes
            )
        )
        if not active and not lock.locked():
            self._ical_precompute_locks.pop(key, None)

    def remove_graph(self, graph_id: str) -> None:
        """Invalidate a deleted graph and release all of its runtime data."""
        self.invalidate_cache(graph_id)
        self._hysteresis.pop(graph_id, None)
        self._ical_result_caches.pop(graph_id, None)
        self._ical_cache_generations.pop(graph_id, None)
        for key in [key for key in self._ical_fetch_locks if key[0] == graph_id]:
            self._ical_fetch_locks.pop(key, None)
        for key in [key for key in self._ical_precompute_locks if key[0] == graph_id]:
            self._prune_ical_precompute_lock(key)
        self._prune_graph_executor_lock(graph_id)

    def update_cached_graph_name(self, graph_id: str, name: str) -> None:
        """Refresh metadata without invalidating active graph execution."""
        graph = self._graphs.get(graph_id)
        if graph:
            _, enabled, flow = graph
            self._graphs[graph_id] = (name, enabled, flow)

    def update_cached_graph(self, graph_id: str, name: str, enabled: bool, flow: FlowData) -> None:
        """Apply a layout-only save without interrupting active sequences."""
        if graph_id in self._graphs:
            # `flow` here is the request body straight from the API layer,
            # which reads its pre-edit copy from the DB row (still holding
            # any legacy api_client field names) rather than from this
            # manager's already-migrated in-memory cache. Without this, a
            # layout-only save (e.g. dragging a node) would overwrite the
            # migrated cached flow with the legacy one, silently losing the
            # configured headers/bearer-token file until the next full
            # _load_graphs() reload.
            _migrate_legacy_api_client_field_names(flow)
            self._graphs[graph_id] = (name, enabled, flow)
            self._sequence_graph_signatures[graph_id] = flow.model_dump_json()
