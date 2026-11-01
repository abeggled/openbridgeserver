"""A list/dict written by a Write Object to a STRING object is published as JSON (#999).

The iCalendar filter's Array output is a Python list. Published unchanged to a
STRING object, the WriteRouter refused it as a type mismatch on every refresh.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.core.write_router import WriteRouter
from obs.logic.manager import LogicManager
from obs.logic.models import FlowData

_ROWS = [["2026-11-01", "10:00", "11:00", "Termin", "Bern", "Grüße"]]


def _manager(data_types: dict[uuid.UUID, str]) -> LogicManager:
    event_bus = MagicMock()
    event_bus.publish = AsyncMock()
    registry = MagicMock()
    registry.get = MagicMock(side_effect=lambda dp_id: SimpleNamespace(data_type=data_types[dp_id], name="calendar") if dp_id in data_types else None)
    return LogicManager(MagicMock(), event_bus, registry)


def _flow(dst_id: str, data: dict | None = None) -> FlowData:
    return FlowData.model_validate(
        {
            "nodes": [{"id": "w1", "type": "datapoint_write", "position": {"x": 0, "y": 0}, "data": {"datapoint_id": dst_id, **(data or {})}}],
            "edges": [],
        }
    )


async def _apply(mgr: LogicManager, dst_id: str, value, graph_state: dict | None = None, data: dict | None = None) -> dict:
    graph_state = graph_state if graph_state is not None else {}
    await mgr._apply_datapoint_write_outputs("g1", _flow(dst_id, data), {"w1": {"_write_value": value}}, graph_state, set(), datetime.now(UTC), 0)
    return graph_state


def _published_value(mgr: LogicManager):
    mgr._event_bus.publish.assert_awaited_once()
    return mgr._event_bus.publish.await_args.args[0].value


@pytest.mark.asyncio
async def test_list_to_string_object_is_published_as_json():
    dst = uuid.uuid4()
    mgr = _manager({dst: "STRING"})

    await _apply(mgr, str(dst), _ROWS)

    value = _published_value(mgr)
    assert isinstance(value, str)
    assert json.loads(value) == _ROWS
    assert "Grüße" in value  # non-ASCII stays readable, not \u-escaped


@pytest.mark.asyncio
async def test_dict_to_string_object_is_published_as_json():
    dst = uuid.uuid4()
    mgr = _manager({dst: "STRING"})

    await _apply(mgr, str(dst), {"a": 1})

    assert _published_value(mgr) == '{"a": 1}'


@pytest.mark.asyncio
async def test_scalar_to_string_object_is_unchanged():
    dst = uuid.uuid4()
    mgr = _manager({dst: "STRING"})

    await _apply(mgr, str(dst), "text")

    assert _published_value(mgr) == "text"


@pytest.mark.asyncio
async def test_list_to_non_string_object_is_unchanged():
    dst = uuid.uuid4()
    mgr = _manager({dst: "UNKNOWN"})

    await _apply(mgr, str(dst), _ROWS)

    assert _published_value(mgr) == _ROWS


@pytest.mark.asyncio
async def test_list_to_unknown_datapoint_is_unchanged():
    mgr = _manager({})

    await _apply(mgr, str(uuid.uuid4()), _ROWS)

    assert _published_value(mgr) == _ROWS


def test_invalid_datapoint_id_leaves_value_unchanged():
    mgr = _manager({})

    assert mgr._serialize_structured_for_target("not-a-uuid", _ROWS) is _ROWS
    mgr._registry.get.assert_not_called()


@pytest.mark.asyncio
async def test_only_on_change_skips_identical_calendar_refresh():
    dst = uuid.uuid4()
    mgr = _manager({dst: "STRING"})
    graph_state: dict = {}

    await _apply(mgr, str(dst), _ROWS, graph_state, {"only_on_change": True})
    await _apply(mgr, str(dst), [list(row) for row in _ROWS], graph_state, {"only_on_change": True})

    mgr._event_bus.publish.assert_awaited_once()
    assert graph_state["w1"]["last_write_val"] == json.dumps(_ROWS, ensure_ascii=False)


@pytest.mark.asyncio
async def test_published_calendar_passes_write_router_type_gate():
    """The documented Array → STRING wiring reaches DEST bindings without a mismatch."""
    dst = uuid.uuid4()
    mgr = _manager({dst: "STRING"})
    await _apply(mgr, str(dst), _ROWS)
    event = mgr._event_bus.publish.await_args.args[0]

    router_registry = SimpleNamespace(
        get=lambda _dp_id: SimpleNamespace(data_type="STRING", name="calendar"),
        report_type_mismatch=AsyncMock(),
        clear_diagnostic=AsyncMock(),
    )
    router = WriteRouter(db=None, registry=router_registry)
    router._write_to_dest_bindings = AsyncMock()

    await router.handle_value_event(event)

    router_registry.report_type_mismatch.assert_not_awaited()
    router._write_to_dest_bindings.assert_awaited_once()
    assert json.loads(router._write_to_dest_bindings.await_args.args[1]) == _ROWS
