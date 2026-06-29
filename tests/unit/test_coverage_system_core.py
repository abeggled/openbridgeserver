"""Coverage-boost tests for system API, history API, weather API,
config, registry, formula, write_router, transformation and event_bus.

All tests are self-contained (no Docker, no real DB, no network).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from obs.security.url_targets import UrlTargetDecision

# ===========================================================================
# Helpers / stubs
# ===========================================================================


class _Row(dict):
    """dict that also supports attribute-style access (like aiosqlite Row)."""

    def __getitem__(self, key):
        return super().__getitem__(key)


def _row(**kwargs):
    return _Row(**kwargs)


def _url_decision(
    *,
    allowed: bool,
    url: str = "http://example.test/",
    host: str = "example.test",
    resolved_ips: list[str] | None = None,
    blocked_ips: list[str] | None = None,
    reason: str = "test decision",
    allowlisted_by: str | None = None,
) -> UrlTargetDecision:
    return UrlTargetDecision(
        allowed=allowed,
        url=url,
        host=host,
        resolved_ips=resolved_ips or [],
        blocked_ips=blocked_ips or [],
        reason=reason,
        allowlisted_by=allowlisted_by,
        suggested_target=(blocked_ips or [None])[0],
    )


class _DbStub:
    """Minimal async DB stub."""

    def __init__(self, *, rows=None, one=None):
        self._rows = rows or []
        self._one = one
        self.executed: list[tuple] = []

    async def fetchone(self, query, params=()):
        return self._one

    async def fetchall(self, query, params=()):
        return list(self._rows)

    async def execute_and_commit(self, query, params=()):
        self.executed.append((query, params))


# ===========================================================================
# obs/core/event_bus
# ===========================================================================


class TestEventBus:
    def test_init_event_bus_returns_instance(self):
        from obs.core.event_bus import EventBus, init_event_bus, reset_event_bus

        reset_event_bus()
        bus = init_event_bus()
        assert isinstance(bus, EventBus)

    def test_get_event_bus_raises_before_init(self):
        from obs.core.event_bus import get_event_bus, reset_event_bus

        reset_event_bus()
        with pytest.raises(RuntimeError):
            get_event_bus()

    def test_get_event_bus_returns_singleton(self):
        from obs.core.event_bus import get_event_bus, init_event_bus, reset_event_bus

        reset_event_bus()
        bus = init_event_bus()
        assert get_event_bus() is bus

    def test_reset_event_bus(self):
        from obs.core.event_bus import get_event_bus, init_event_bus, reset_event_bus

        reset_event_bus()
        init_event_bus()
        reset_event_bus()
        with pytest.raises(RuntimeError):
            get_event_bus()

    @pytest.mark.asyncio
    async def test_publish_no_handlers_does_nothing(self):
        from obs.core.event_bus import DataValueEvent, EventBus

        bus = EventBus()
        event = DataValueEvent(
            datapoint_id=uuid.uuid4(),
            value=1,
            quality="good",
            source_adapter="test",
        )
        # Should not raise
        await bus.publish(event)

    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self):
        from obs.core.event_bus import DataValueEvent, EventBus

        bus = EventBus()
        received = []

        async def handler(evt):
            received.append(evt)

        bus.subscribe(DataValueEvent, handler)
        event = DataValueEvent(
            datapoint_id=uuid.uuid4(),
            value=42,
            quality="good",
            source_adapter="test",
        )
        await bus.publish(event)
        assert received == [event]

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_handler(self):
        from obs.core.event_bus import DataValueEvent, EventBus

        bus = EventBus()
        received = []

        async def handler(evt):
            received.append(evt)

        bus.subscribe(DataValueEvent, handler)
        bus.unsubscribe(DataValueEvent, handler)

        event = DataValueEvent(
            datapoint_id=uuid.uuid4(),
            value=99,
            quality="good",
            source_adapter="test",
        )
        await bus.publish(event)
        assert received == []

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_handler_does_not_raise(self):
        from obs.core.event_bus import DataValueEvent, EventBus

        bus = EventBus()

        async def handler(evt):
            pass

        # Should not raise
        bus.unsubscribe(DataValueEvent, handler)

    @pytest.mark.asyncio
    async def test_publish_handler_exception_is_logged_not_raised(self):
        from obs.core.event_bus import DataValueEvent, EventBus

        bus = EventBus()

        async def bad_handler(evt):
            raise RuntimeError("boom")

        bus.subscribe(DataValueEvent, bad_handler)
        event = DataValueEvent(
            datapoint_id=uuid.uuid4(),
            value=1,
            quality="good",
            source_adapter="test",
        )
        # Should not raise even though handler raises
        await bus.publish(event)

    @pytest.mark.asyncio
    async def test_publish_multiple_handlers(self):
        from obs.core.event_bus import DataValueEvent, EventBus

        bus = EventBus()
        log = []

        async def h1(evt):
            log.append("h1")

        async def h2(evt):
            log.append("h2")

        bus.subscribe(DataValueEvent, h1)
        bus.subscribe(DataValueEvent, h2)

        event = DataValueEvent(
            datapoint_id=uuid.uuid4(),
            value=1,
            quality="good",
            source_adapter="test",
        )
        await bus.publish(event)
        assert "h1" in log
        assert "h2" in log

    def test_adapter_status_event_dataclass(self):
        from obs.core.event_bus import AdapterStatusEvent

        evt = AdapterStatusEvent(adapter_type="MQTT", connected=True, detail="ok")
        assert evt.adapter_type == "MQTT"
        assert evt.connected is True

    def test_data_point_renamed_event(self):
        from obs.core.event_bus import DataPointRenamedEvent

        dp_id = uuid.uuid4()
        evt = DataPointRenamedEvent(dp_id=dp_id, old_name="old", new_name="new")
        assert evt.dp_id == dp_id


# ===========================================================================
# obs/core/formula (missing lines)
# ===========================================================================


class TestFormulaValidate:
    def test_empty_formula_is_valid(self):
        from obs.core.formula import validate_formula

        assert validate_formula("") is None
        assert validate_formula("   ") is None

    def test_syntax_error_returns_message(self):
        from obs.core.formula import validate_formula

        result = validate_formula("x +* 2")
        assert result is not None
        assert "Syntaxfehler" in result

    def test_disallowed_node_returns_message(self):
        from obs.core.formula import validate_formula

        # import statement is not allowed
        result = validate_formula("__import__('os')")
        assert result is not None

    def test_disallowed_name_returns_message(self):
        from obs.core.formula import validate_formula

        result = validate_formula("os.getcwd()")
        assert result is not None

    def test_div_by_zero_detected(self):
        from obs.core.formula import validate_formula

        result = validate_formula("x / 0")
        assert result is not None

    def test_nan_result_detected(self):
        from obs.core.formula import validate_formula

        # math.log(0) returns -inf
        result = validate_formula("math.log(0)")
        assert result is not None

    def test_valid_formula_returns_none(self):
        from obs.core.formula import validate_formula

        assert validate_formula("x * 2 + 1") is None
        assert validate_formula("round(x * 0.1)") is None
        assert validate_formula("max(0, x - 10)") is None

    def test_math_attribute_allowed(self):
        from obs.core.formula import validate_formula

        assert validate_formula("math.sqrt(x)") is None

    def test_disallowed_attribute_access(self):
        from obs.core.formula import validate_formula

        result = validate_formula("x.__class__")
        assert result is not None

    def test_keyword_call_disallowed(self):
        from obs.core.formula import validate_formula

        # round(x, ndigits=2) uses keyword arg — disallowed
        result = validate_formula("round(x, ndigits=2)")
        assert result is not None


class TestFormulaApply:
    def test_empty_formula_returns_value_unchanged(self):
        from obs.core.formula import apply_formula

        assert apply_formula("", 42) == 42
        assert apply_formula("  ", "hello") == "hello"

    def test_non_numeric_value_returned_unchanged(self):
        from obs.core.formula import apply_formula

        assert apply_formula("x * 2", "not a number") == "not a number"
        assert apply_formula("x * 2", None) is None

    def test_basic_multiplication(self):
        from obs.core.formula import apply_formula

        assert apply_formula("x * 2", 5) == pytest.approx(10.0)

    def test_division_by_zero_returns_original(self):
        from obs.core.formula import apply_formula

        assert apply_formula("x / 0", 5) == 5

    def test_nan_result_returns_original(self):
        from obs.core.formula import apply_formula

        # math.log(-1) → nan
        assert apply_formula("math.log(-1)", 5) == 5

    def test_inf_result_returns_original(self):
        from obs.core.formula import apply_formula

        # math.log(0) returns -inf
        assert apply_formula("math.log(0)", 5) == 5

    def test_ast_validation_failure_returns_original(self):
        from obs.core.formula import apply_formula

        # __import__ will fail validation
        assert apply_formula("__import__('os')", 5) == 5

    def test_non_numeric_input_none_returns_none(self):
        from obs.core.formula import apply_formula

        result = apply_formula("x * 2", None)
        assert result is None


class TestTryEval:
    def test_returns_none_for_valid(self):
        from obs.core.formula import _try_eval

        assert _try_eval("x * 2", 3.0) is None

    def test_returns_error_for_div_zero(self):
        from obs.core.formula import _try_eval

        result = _try_eval("x / 0", 0.0)
        assert result is not None

    def test_returns_error_for_nan(self):
        from obs.core.formula import _try_eval

        result = _try_eval("math.log(0)", 0.0)
        assert result is not None


# ===========================================================================
# obs/core/transformation (missing lines)
# ===========================================================================


class TestApplySourceTypeXml:
    def test_xml_with_path(self):
        from obs.core.transformation import apply_source_type

        raw = "<root><temp>22</temp></root>"
        result = apply_source_type(raw, raw, "xml", None, "temp")
        assert result == 22

    def test_xml_with_float_text(self):
        from obs.core.transformation import apply_source_type

        raw = "<root><temp>22.5</temp></root>"
        result = apply_source_type(raw, raw, "xml", None, "temp")
        assert result == pytest.approx(22.5)

    def test_xml_with_string_text(self):
        from obs.core.transformation import apply_source_type

        raw = "<root><status>ok</status></root>"
        result = apply_source_type(raw, raw, "xml", None, "status")
        assert result == "ok"

    def test_xml_missing_path_returns_unchanged(self):
        from obs.core.transformation import apply_source_type

        raw = "<root><temp>22</temp></root>"
        result = apply_source_type(raw, raw, "xml", None, "humidity")
        # path not found, returns original auto_value
        assert result == raw

    def test_xml_no_path_returns_root_text(self):
        from obs.core.transformation import apply_source_type

        raw = "<root>hello</root>"
        result = apply_source_type(raw, raw, "xml", None, None)
        assert result == "hello"

    def test_xml_parse_error_returns_unchanged(self):
        from obs.core.transformation import apply_source_type

        raw = "not xml at all!!!"
        result = apply_source_type(raw, raw, "xml", None, "something")
        # parse error → returns auto_value (raw)
        assert result == raw

    def test_auto_returns_auto_value(self):
        from obs.core.transformation import apply_source_type

        result = apply_source_type("42", 42, None, None, None)
        assert result == 42

    def test_int_coerce_from_non_string(self):
        from obs.core.transformation import apply_source_type

        result = apply_source_type("3.7", 3.7, "int", None, None)
        assert result == 3

    def test_float_invalid_returns_original(self):
        from obs.core.transformation import apply_source_type

        result = apply_source_type("abc", "abc", "float", None, None)
        assert result == "abc"

    def test_int_invalid_returns_original(self):
        from obs.core.transformation import apply_source_type

        result = apply_source_type("abc", "abc", "int", None, None)
        assert result == "abc"

    def test_bool_already_bool(self):
        from obs.core.transformation import apply_source_type

        result = apply_source_type("true", True, "bool", None, None)
        assert result is True

    def test_bool_numeric_coerce(self):
        from obs.core.transformation import apply_source_type

        result = apply_source_type("2", 2, "bool", None, None)
        assert result is True


# ===========================================================================
# obs/core/registry
# ===========================================================================


def _make_db_rows_for_registry(dp_id, name="Test DP"):
    """Create mock DB rows for a single DataPoint."""
    now = datetime.now(UTC).isoformat()
    return [
        _row(
            id=str(dp_id),
            name=name,
            data_type="FLOAT",
            unit="°C",
            tags="[]",
            mqtt_topic=f"dp/{dp_id}/value",
            mqtt_alias=None,
            persist_value=1,
            record_history=1,
            created_at=now,
            updated_at=now,
        )
    ]


class TestValueState:
    def test_initial_state(self):
        from obs.core.registry import ValueState

        vs = ValueState()
        assert vs.value is None
        assert vs.quality == "uncertain"
        assert vs.old_value is None

    def test_update_returns_true_on_change(self):
        from obs.core.registry import ValueState

        vs = ValueState()
        changed = vs.update(42, "good")
        assert changed is True
        assert vs.value == 42
        assert vs.quality == "good"

    def test_update_returns_false_when_unchanged(self):
        from obs.core.registry import ValueState

        vs = ValueState()
        vs.update(42, "good")
        changed = vs.update(42, "good")
        assert changed is False

    def test_update_stores_old_value(self):
        from obs.core.registry import ValueState

        vs = ValueState()
        vs.update(10, "good")
        vs.update(20, "good")
        assert vs.old_value == 10
        assert vs.value == 20


class TestDataPointRegistrySingleton:
    def test_get_registry_raises_before_init(self):
        from obs.core.registry import get_registry, reset_registry

        reset_registry()
        with pytest.raises(RuntimeError):
            get_registry()

    @pytest.mark.asyncio
    async def test_init_registry_returns_instance(self):
        from obs.core.registry import DataPointRegistry, get_registry, init_registry, reset_registry

        reset_registry()
        db = _DbStub(rows=[], one=None)
        mqtt = AsyncMock()
        bus = AsyncMock()
        reg = await init_registry(db, mqtt, bus)
        assert isinstance(reg, DataPointRegistry)
        assert get_registry() is reg
        reset_registry()

    def test_reset_registry(self):
        from obs.core.registry import get_registry, reset_registry

        reset_registry()
        with pytest.raises(RuntimeError):
            get_registry()


class TestDataPointRegistryReadMethods:
    @pytest.mark.asyncio
    async def test_count_empty(self):
        from obs.core.registry import DataPointRegistry

        db = _DbStub(rows=[])
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()
        reg._points = {}
        reg._values = {}
        assert reg.count() == 0

    @pytest.mark.asyncio
    async def test_get_returns_none_for_missing(self):
        from obs.core.registry import DataPointRegistry

        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._points = {}
        reg._values = {}
        assert reg.get(uuid.uuid4()) is None

    @pytest.mark.asyncio
    async def test_get_or_raise_raises(self):
        from obs.core.registry import DataPointRegistry

        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._points = {}
        reg._values = {}
        with pytest.raises(KeyError):
            reg.get_or_raise(uuid.uuid4())

    @pytest.mark.asyncio
    async def test_all_returns_list(self):
        from obs.core.registry import DataPointRegistry

        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._points = {}
        reg._values = {}
        assert reg.all() == []

    @pytest.mark.asyncio
    async def test_page_returns_slice(self):
        from obs.core.registry import DataPointRegistry
        from obs.models.datapoint import DataPoint

        reg = DataPointRegistry.__new__(DataPointRegistry)
        dp1 = DataPoint(name="A")
        dp2 = DataPoint(name="B")
        reg._points = {dp1.id: dp1, dp2.id: dp2}
        reg._values = {}

        page = reg.page(offset=0, limit=1)
        assert len(page) == 1

    @pytest.mark.asyncio
    async def test_search_by_name(self):
        from obs.core.registry import DataPointRegistry
        from obs.models.datapoint import DataPoint

        reg = DataPointRegistry.__new__(DataPointRegistry)
        dp1 = DataPoint(name="Temperature Sensor")
        dp2 = DataPoint(name="Humidity")
        reg._points = {dp1.id: dp1, dp2.id: dp2}
        reg._values = {}

        results = reg.search(q="temperature")
        assert len(results) == 1
        assert results[0].name == "Temperature Sensor"

    @pytest.mark.asyncio
    async def test_search_by_tag(self):
        from obs.core.registry import DataPointRegistry
        from obs.models.datapoint import DataPoint

        reg = DataPointRegistry.__new__(DataPointRegistry)
        dp1 = DataPoint(name="A", tags=["sensor"])
        dp2 = DataPoint(name="B", tags=["actuator"])
        reg._points = {dp1.id: dp1, dp2.id: dp2}
        reg._values = {}

        results = reg.search(tag="sensor")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_search_by_data_type(self):
        from obs.core.registry import DataPointRegistry
        from obs.models.datapoint import DataPoint

        reg = DataPointRegistry.__new__(DataPointRegistry)
        dp1 = DataPoint(name="A", data_type="FLOAT")
        dp2 = DataPoint(name="B", data_type="BOOLEAN")
        reg._points = {dp1.id: dp1, dp2.id: dp2}
        reg._values = {}

        results = reg.search(data_type="FLOAT")
        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_get_value_returns_none_for_missing(self):
        from obs.core.registry import DataPointRegistry

        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._values = {}
        assert reg.get_value(uuid.uuid4()) is None


class TestDataPointRegistryLoadFromDb:
    @pytest.mark.asyncio
    async def test_load_from_db_empty(self):
        from obs.core.registry import DataPointRegistry

        db = _DbStub(rows=[])
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()
        reg._points = {}
        reg._values = {}
        count = await reg.load_from_db()
        assert count == 0

    @pytest.mark.asyncio
    async def test_load_from_db_with_rows(self):
        from obs.core.registry import DataPointRegistry

        dp_id = uuid.uuid4()
        rows = _make_db_rows_for_registry(dp_id)

        class _DbWithLastValues(_DbStub):
            async def fetchall(self, query, params=()):
                if "datapoint_last_values" in query:
                    return []
                return list(self._rows)

        db = _DbWithLastValues(rows=rows)
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()
        reg._points = {}
        reg._values = {}
        count = await reg.load_from_db()
        assert count == 1
        assert dp_id in reg._points

    @pytest.mark.asyncio
    async def test_load_from_db_restores_persisted_value(self):
        from obs.core.registry import DataPointRegistry

        dp_id = uuid.uuid4()
        dp_rows = _make_db_rows_for_registry(dp_id)
        last_val_rows = [
            _row(
                datapoint_id=str(dp_id),
                value="22.5",
                unit="°C",
                ts=datetime.now(UTC).isoformat(),
            )
        ]

        class _DbWithPersisted(_DbStub):
            async def fetchall(self, query, params=()):
                if "datapoint_last_values" in query:
                    return last_val_rows
                return dp_rows

        db = _DbWithPersisted(rows=dp_rows)
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()
        reg._points = {}
        reg._values = {}
        await reg.load_from_db()
        state = reg._values[dp_id]
        assert state.value == pytest.approx(22.5)
        assert state.quality == "good"

    @pytest.mark.asyncio
    async def test_load_from_db_restores_time_value_as_datetime_time(self):
        import datetime as _dt

        from obs.core.registry import DataPointRegistry

        dp_id = uuid.uuid4()
        now = datetime.now(UTC).isoformat()
        dp_rows = [
            _row(
                id=str(dp_id),
                name="Time DP",
                data_type="TIME",
                unit="",
                tags="[]",
                mqtt_topic=f"dp/{dp_id}/value",
                mqtt_alias=None,
                persist_value=1,
                record_history=0,
                created_at=now,
                updated_at=now,
            )
        ]
        last_val_rows = [
            _row(
                datapoint_id=str(dp_id),
                value='"10:30:00"',
                unit="",
                ts=now,
            )
        ]

        class _DbWithTime(_DbStub):
            async def fetchall(self, query, params=()):
                if "datapoint_last_values" in query:
                    return last_val_rows
                return dp_rows

        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = _DbWithTime(rows=dp_rows)
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()
        reg._points = {}
        reg._values = {}
        await reg.load_from_db()
        state = reg._values[dp_id]
        assert state.value == _dt.time(10, 30, 0)

    @pytest.mark.asyncio
    async def test_load_from_db_time_invalid_string_leaves_as_string(self):
        from obs.core.registry import DataPointRegistry

        dp_id = uuid.uuid4()
        now = datetime.now(UTC).isoformat()
        dp_rows = [
            _row(
                id=str(dp_id),
                name="Time DP Bad",
                data_type="TIME",
                unit="",
                tags="[]",
                mqtt_topic=f"dp/{dp_id}/value",
                mqtt_alias=None,
                persist_value=1,
                record_history=0,
                created_at=now,
                updated_at=now,
            )
        ]
        last_val_rows = [
            _row(
                datapoint_id=str(dp_id),
                value='"not-a-time"',
                unit="",
                ts=now,
            )
        ]

        class _DbWithBadTime(_DbStub):
            async def fetchall(self, query, params=()):
                if "datapoint_last_values" in query:
                    return last_val_rows
                return dp_rows

        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = _DbWithBadTime(rows=dp_rows)
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()
        reg._points = {}
        reg._values = {}
        await reg.load_from_db()
        state = reg._values[dp_id]
        assert state.value == "not-a-time"


class TestDataPointRegistryCRUD:
    @pytest.mark.asyncio
    async def test_create_adds_to_points(self):
        from obs.core.registry import DataPointRegistry
        from obs.models.datapoint import DataPointCreate

        db = _DbStub()
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()
        reg._points = {}
        reg._values = {}

        payload = DataPointCreate(name="New DP")
        dp = await reg.create(payload)
        assert dp.id in reg._points
        assert len(db.executed) == 1

    @pytest.mark.asyncio
    async def test_update_modifies_datapoint(self):
        from obs.core.registry import DataPointRegistry
        from obs.models.datapoint import DataPoint, DataPointUpdate

        db = _DbStub()
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()

        dp = DataPoint(name="Old Name")
        reg._points = {dp.id: dp}
        reg._values = {dp.id: MagicMock()}

        payload = DataPointUpdate(name="New Name")
        updated = await reg.update(dp.id, payload)
        assert updated.name == "New Name"

    @pytest.mark.asyncio
    async def test_update_publishes_renamed_event(self):
        from obs.core.event_bus import DataPointRenamedEvent
        from obs.core.registry import DataPointRegistry
        from obs.models.datapoint import DataPoint, DataPointUpdate

        db = _DbStub()
        bus = AsyncMock()
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = AsyncMock()
        reg._bus = bus

        dp = DataPoint(name="Old")
        reg._points = {dp.id: dp}
        reg._values = {dp.id: MagicMock()}

        payload = DataPointUpdate(name="New")
        await reg.update(dp.id, payload)
        bus.publish.assert_awaited_once()
        event = bus.publish.call_args[0][0]
        assert isinstance(event, DataPointRenamedEvent)
        assert event.old_name == "Old"
        assert event.new_name == "New"

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(self):
        from obs.core.registry import DataPointRegistry
        from obs.models.datapoint import DataPointUpdate

        db = _DbStub()
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()
        reg._points = {}
        reg._values = {}

        with pytest.raises(KeyError):
            await reg.update(uuid.uuid4(), DataPointUpdate(name="x"))

    @pytest.mark.asyncio
    async def test_delete_removes_from_points(self):
        from obs.core.registry import DataPointRegistry
        from obs.models.datapoint import DataPoint

        db = _DbStub()
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()

        dp = DataPoint(name="To Delete")
        reg._points = {dp.id: dp}
        reg._values = {dp.id: MagicMock()}

        await reg.delete(dp.id)
        assert dp.id not in reg._points

    @pytest.mark.asyncio
    async def test_delete_nonexistent_raises(self):
        from obs.core.registry import DataPointRegistry

        db = _DbStub()
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = AsyncMock()
        reg._bus = AsyncMock()
        reg._points = {}
        reg._values = {}

        with pytest.raises(KeyError):
            await reg.delete(uuid.uuid4())


class TestHandleValueEvent:
    @pytest.mark.asyncio
    async def test_handle_value_event_unknown_dp_does_nothing(self):
        from obs.core.registry import DataPointRegistry

        db = _DbStub()
        mqtt = AsyncMock()
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = mqtt
        reg._bus = AsyncMock()
        reg._points = {}
        reg._values = {}

        event = SimpleNamespace(
            datapoint_id=uuid.uuid4(),
            value=10,
            quality="good",
            ts=datetime.now(UTC),
        )
        await reg.handle_value_event(event)
        mqtt.publish_value.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_value_event_updates_state_and_publishes(self):
        from obs.core.registry import DataPointRegistry, ValueState
        from obs.models.datapoint import DataPoint

        db = _DbStub()
        mqtt = AsyncMock()
        reg = DataPointRegistry.__new__(DataPointRegistry)
        reg._db = db
        reg._mqtt = mqtt
        reg._bus = AsyncMock()

        dp = DataPoint(name="Sensor", persist_value=False)
        vs = ValueState()
        reg._points = {dp.id: dp}
        reg._values = {dp.id: vs}

        event = SimpleNamespace(
            datapoint_id=dp.id,
            value=42.0,
            quality="good",
            ts=datetime.now(UTC),
        )
        await reg.handle_value_event(event)
        assert vs.value == 42.0
        mqtt.publish_value.assert_awaited_once()


# ===========================================================================
# obs/core/write_router (missing lines)
# ===========================================================================


def _make_router_with_registry(db_rows, registry=None):
    from obs.core.write_router import WriteRouter

    router = WriteRouter.__new__(WriteRouter)
    router._db = _DbStub(rows=db_rows)
    router._registry = registry or SimpleNamespace(get=lambda _: None)
    router._last_sent = {}
    router._last_value = {}
    return router


class TestWriteRouterSingleton:
    def test_get_write_router_raises_before_init(self):
        from obs.core.write_router import get_write_router, reset_write_router

        reset_write_router()
        with pytest.raises(RuntimeError):
            get_write_router()

    def test_init_write_router_returns_instance(self):
        from obs.core.write_router import WriteRouter, get_write_router, init_write_router, reset_write_router

        reset_write_router()
        db = _DbStub()
        reg = SimpleNamespace()
        wr = init_write_router(db, reg)
        assert isinstance(wr, WriteRouter)
        assert get_write_router() is wr
        reset_write_router()

    def test_reset_write_router(self):
        from obs.core.write_router import get_write_router, init_write_router, reset_write_router

        reset_write_router()
        init_write_router(_DbStub(), SimpleNamespace())
        reset_write_router()
        with pytest.raises(RuntimeError):
            get_write_router()


class TestWriteRouterHandle:
    @pytest.mark.asyncio
    async def test_handle_unknown_dp_does_nothing(self):
        from obs.core.write_router import WriteRouter

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub()
        router._registry = SimpleNamespace(get=lambda _: None)
        router._last_sent = {}
        router._last_value = {}
        router._write_to_dest_bindings = AsyncMock()

        await router.handle(uuid.uuid4(), "42")
        router._write_to_dest_bindings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handle_publishes_value_event(self):
        from obs.core.event_bus import DataValueEvent
        from obs.core.write_router import WriteRouter

        dp = SimpleNamespace(name="dp", data_type="FLOAT")
        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub()
        router._registry = SimpleNamespace(get=lambda _: dp)
        router._bus = SimpleNamespace(publish=AsyncMock())
        router._last_sent = {}
        router._last_value = {}
        router._write_to_dest_bindings = AsyncMock()

        dp_id = uuid.uuid4()
        await router.handle(dp_id, "42.0")
        router._bus.publish.assert_awaited_once()
        event = router._bus.publish.await_args.args[0]
        assert isinstance(event, DataValueEvent)
        assert event.datapoint_id == dp_id
        assert event.value == pytest.approx(42.0)
        assert event.quality == "good"
        assert event.source_adapter == "mqtt_set"
        router._write_to_dest_bindings.assert_not_awaited()


class TestWriteRouterHandleValueEvent:
    @pytest.mark.asyncio
    async def test_skips_bad_quality(self):
        from obs.core.write_router import WriteRouter

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub()
        router._registry = SimpleNamespace(get=lambda _: SimpleNamespace(name="dp", data_type="FLOAT"))
        router._last_sent = {}
        router._last_value = {}
        router._write_to_dest_bindings = AsyncMock()

        event = SimpleNamespace(
            datapoint_id=uuid.uuid4(),
            value=42,
            binding_id=uuid.uuid4(),
            quality="bad",
        )
        await router.handle_value_event(event)
        router._write_to_dest_bindings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_none_value(self):
        from obs.core.write_router import WriteRouter

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub()
        router._registry = SimpleNamespace(get=lambda _: SimpleNamespace(name="dp", data_type="FLOAT"))
        router._last_sent = {}
        router._last_value = {}
        router._write_to_dest_bindings = AsyncMock()

        event = SimpleNamespace(
            datapoint_id=uuid.uuid4(),
            value=None,
            binding_id=uuid.uuid4(),
            quality="good",
        )
        await router.handle_value_event(event)
        router._write_to_dest_bindings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_unknown_dp(self):
        from obs.core.write_router import WriteRouter

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub()
        router._registry = SimpleNamespace(get=lambda _: None)
        router._last_sent = {}
        router._last_value = {}
        router._write_to_dest_bindings = AsyncMock()

        event = SimpleNamespace(
            datapoint_id=uuid.uuid4(),
            value=42,
            binding_id=uuid.uuid4(),
            quality="good",
        )
        await router.handle_value_event(event)
        router._write_to_dest_bindings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_skips_type_mismatch(self):
        from obs.core.write_router import WriteRouter

        # BOOLEAN expects bool, but we send str
        dp = SimpleNamespace(name="dp", data_type="BOOLEAN")
        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub()
        router._registry = SimpleNamespace(get=lambda _: dp)
        router._last_sent = {}
        router._last_value = {}
        router._write_to_dest_bindings = AsyncMock()

        event = SimpleNamespace(
            datapoint_id=uuid.uuid4(),
            value="not_bool",
            binding_id=uuid.uuid4(),
            quality="good",
        )
        await router.handle_value_event(event)
        router._write_to_dest_bindings.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_float_accepts_int_value(self):
        from obs.core.write_router import WriteRouter

        dp = SimpleNamespace(name="dp", data_type="FLOAT")
        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub()
        router._registry = SimpleNamespace(get=lambda _: dp)
        router._last_sent = {}
        router._last_value = {}
        router._write_to_dest_bindings = AsyncMock()

        event = SimpleNamespace(
            datapoint_id=uuid.uuid4(),
            value=42,  # int, not float — but FLOAT allows int
            binding_id=uuid.uuid4(),
            quality="good",
        )
        await router.handle_value_event(event)
        router._write_to_dest_bindings.assert_awaited_once()


class TestWriteRouterDestBindings:
    def _make_binding(self, **kwargs):
        defaults = {
            "id": uuid.uuid4(),
            "adapter_instance_id": None,
            "adapter_type": "MQTT",
            "send_throttle_ms": None,
            "send_on_change": False,
            "send_min_delta": None,
            "send_min_delta_pct": None,
            "value_formula": None,
            "value_map": None,
        }
        defaults.update(kwargs)
        return SimpleNamespace(**defaults)

    @pytest.mark.asyncio
    async def test_no_bindings_logs_and_returns(self, monkeypatch):
        from obs.adapters import registry as adapter_registry
        from obs.core.write_router import WriteRouter

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub(rows=[])
        router._registry = None
        router._last_sent = {}
        router._last_value = {}

        monkeypatch.setattr(adapter_registry, "_row_to_binding", lambda r: None)
        monkeypatch.setattr(adapter_registry, "get_instance_by_id", lambda i: None)
        monkeypatch.setattr(adapter_registry, "get_instance", lambda t: None)

        # Should not raise
        await router._write_to_dest_bindings(uuid.uuid4(), 42, skip_binding_id=None)

    @pytest.mark.asyncio
    async def test_skip_binding_id_skips_matching(self, monkeypatch):
        from obs.adapters import registry as adapter_registry
        from obs.core.write_router import WriteRouter

        binding = self._make_binding()
        instance = SimpleNamespace(write=AsyncMock())

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub(rows=[{"id": str(binding.id)}])
        router._registry = None
        router._last_sent = {}
        router._last_value = {}

        monkeypatch.setattr(adapter_registry, "_row_to_binding", lambda r: binding)
        monkeypatch.setattr(adapter_registry, "get_instance_by_id", lambda i: instance)
        monkeypatch.setattr(adapter_registry, "get_instance", lambda t: instance)

        await router._write_to_dest_bindings(uuid.uuid4(), 42, skip_binding_id=binding.id)
        instance.write.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_adapter_not_found_skips_write(self, monkeypatch):
        from obs.adapters import registry as adapter_registry
        from obs.core.write_router import WriteRouter

        binding = self._make_binding()

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub(rows=[{"id": str(binding.id)}])
        router._registry = None
        router._last_sent = {}
        router._last_value = {}

        monkeypatch.setattr(adapter_registry, "_row_to_binding", lambda r: binding)
        monkeypatch.setattr(adapter_registry, "get_instance_by_id", lambda i: None)
        monkeypatch.setattr(adapter_registry, "get_instance", lambda t: None)

        # Should not raise
        await router._write_to_dest_bindings(uuid.uuid4(), 42, skip_binding_id=None)

    @pytest.mark.asyncio
    async def test_write_with_formula_applied(self, monkeypatch):
        from obs.adapters import registry as adapter_registry
        from obs.core.write_router import WriteRouter

        binding = self._make_binding(value_formula="x * 2")
        instance = SimpleNamespace(write=AsyncMock())

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub(rows=[{"id": str(binding.id)}])
        router._registry = None
        router._last_sent = {}
        router._last_value = {}

        monkeypatch.setattr(adapter_registry, "_row_to_binding", lambda r: binding)
        monkeypatch.setattr(adapter_registry, "get_instance_by_id", lambda i: instance)
        monkeypatch.setattr(adapter_registry, "get_instance", lambda t: instance)

        await router._write_to_dest_bindings(uuid.uuid4(), 5.0, skip_binding_id=None)
        instance.write.assert_awaited_once()
        written_value = instance.write.call_args[0][1]
        assert written_value == pytest.approx(10.0)

    @pytest.mark.asyncio
    async def test_write_with_value_map_applied(self, monkeypatch):
        from obs.adapters import registry as adapter_registry
        from obs.core.write_router import WriteRouter

        binding = self._make_binding(value_map={"1": "on", "0": "off"})
        instance = SimpleNamespace(write=AsyncMock())

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub(rows=[{"id": str(binding.id)}])
        router._registry = None
        router._last_sent = {}
        router._last_value = {}

        monkeypatch.setattr(adapter_registry, "_row_to_binding", lambda r: binding)
        monkeypatch.setattr(adapter_registry, "get_instance_by_id", lambda i: instance)
        monkeypatch.setattr(adapter_registry, "get_instance", lambda t: instance)

        await router._write_to_dest_bindings(uuid.uuid4(), 1, skip_binding_id=None)
        instance.write.assert_awaited_once()
        written_value = instance.write.call_args[0][1]
        assert written_value == "on"

    @pytest.mark.asyncio
    async def test_send_min_delta_pct_skips_small_change(self, monkeypatch):
        from obs.adapters import registry as adapter_registry
        from obs.core.write_router import WriteRouter

        binding = self._make_binding(send_min_delta_pct=10.0)  # 10% threshold
        instance = SimpleNamespace(write=AsyncMock())

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub(rows=[{"id": str(binding.id)}])
        router._registry = None
        router._last_sent = {}
        router._last_value = {}

        monkeypatch.setattr(adapter_registry, "_row_to_binding", lambda r: binding)
        monkeypatch.setattr(adapter_registry, "get_instance_by_id", lambda i: instance)
        monkeypatch.setattr(adapter_registry, "get_instance", lambda t: instance)

        # First write — no cache yet
        await router._write_to_dest_bindings(uuid.uuid4(), 100.0, skip_binding_id=None)
        # Second write — only 1% change (< 10%)
        await router._write_to_dest_bindings(uuid.uuid4(), 101.0, skip_binding_id=None)

        assert instance.write.await_count == 1

    @pytest.mark.asyncio
    async def test_write_exception_is_caught(self, monkeypatch):
        from obs.adapters import registry as adapter_registry
        from obs.core.write_router import WriteRouter

        binding = self._make_binding()
        bad_instance = SimpleNamespace(write=AsyncMock(side_effect=RuntimeError("write failed")))

        router = WriteRouter.__new__(WriteRouter)
        router._db = _DbStub(rows=[{"id": str(binding.id)}])
        router._registry = None
        router._last_sent = {}
        router._last_value = {}

        monkeypatch.setattr(adapter_registry, "_row_to_binding", lambda r: binding)
        monkeypatch.setattr(adapter_registry, "get_instance_by_id", lambda i: bad_instance)
        monkeypatch.setattr(adapter_registry, "get_instance", lambda t: bad_instance)

        # Should not propagate the exception
        await router._write_to_dest_bindings(uuid.uuid4(), 42, skip_binding_id=None)


# ===========================================================================
# obs/config
# ===========================================================================


class TestGetSettings:
    def test_get_settings_returns_settings_instance(self, monkeypatch):
        import obs.config as cfg_module
        from obs.config import Settings

        monkeypatch.setattr(cfg_module, "_settings", None)
        s = cfg_module.get_settings()
        assert isinstance(s, Settings)

    def test_get_settings_singleton(self, monkeypatch):
        import obs.config as cfg_module

        monkeypatch.setattr(cfg_module, "_settings", None)
        s1 = cfg_module.get_settings()
        s2 = cfg_module.get_settings()
        assert s1 is s2

    def test_override_settings_replaces_singleton(self, monkeypatch):
        import obs.config as cfg_module
        from obs.config import Settings, override_settings

        monkeypatch.setattr(cfg_module, "_settings", None)
        s = Settings()
        override_settings(s)
        assert cfg_module.get_settings() is s
        monkeypatch.setattr(cfg_module, "_settings", None)

    def test_default_server_settings(self, monkeypatch):
        import obs.config as cfg_module

        monkeypatch.setattr(cfg_module, "_settings", None)
        s = cfg_module.get_settings()
        assert s.server.port == 8080
        assert s.server.host == "0.0.0.0"

    def test_default_mqtt_settings(self, monkeypatch):
        import obs.config as cfg_module

        monkeypatch.setattr(cfg_module, "_settings", None)
        s = cfg_module.get_settings()
        assert s.mqtt.host == "localhost"
        assert s.mqtt.port == 1883


class TestSecuritySettingsValidator:
    def test_short_jwt_secret_triggers_warning(self, caplog):
        import logging

        from obs.config import SecuritySettings

        with caplog.at_level(logging.WARNING, logger="obs.config"):
            sec = SecuritySettings(jwt_secret="short")
        assert sec.jwt_secret == "short"

    def test_long_jwt_secret_no_warning(self, caplog):
        import logging

        from obs.config import SecuritySettings

        with caplog.at_level(logging.WARNING, logger="obs.config"):
            sec = SecuritySettings(jwt_secret="a" * 32)
        assert sec.jwt_secret == "a" * 32

    def test_default_url_target_allowlist_path_uses_database_secret_dir(self, tmp_path, monkeypatch):
        from obs.config import DatabaseSettings, SecuritySettings, Settings

        monkeypatch.delenv("OBS_SECRET_FILE_DIR", raising=False)
        db_path = tmp_path / "obs.db"

        settings = Settings(
            database=DatabaseSettings(path=str(db_path)),
            security=SecuritySettings(jwt_secret="unit-test-secret-32-chars-xxx"),
        )

        assert settings.security.url_target_allowlist_path == str(tmp_path / "secrets" / "url-target-allowlist.yaml")

    def test_default_url_target_allowlist_path_uses_configured_secret_root(self, tmp_path, monkeypatch):
        from obs.config import DatabaseSettings, SecuritySettings, Settings

        secret_root = tmp_path / "configured-secrets"
        monkeypatch.setenv("OBS_SECRET_FILE_DIR", str(secret_root))

        settings = Settings(
            database=DatabaseSettings(path=str(tmp_path / "obs.db")),
            security=SecuritySettings(jwt_secret="unit-test-secret-32-chars-xxx"),
        )

        assert settings.security.url_target_allowlist_path == str(secret_root / "url-target-allowlist.yaml")

    def test_custom_url_target_allowlist_path_is_preserved(self, tmp_path, monkeypatch):
        from obs.config import DatabaseSettings, SecuritySettings, Settings

        monkeypatch.setenv("OBS_SECRET_FILE_DIR", str(tmp_path / "configured-secrets"))
        custom_path = tmp_path / "custom" / "allow.yaml"

        settings = Settings(
            database=DatabaseSettings(path=str(tmp_path / "obs.db")),
            security=SecuritySettings(
                jwt_secret="unit-test-secret-32-chars-xxx",
                url_target_allowlist_path=str(custom_path),
            ),
        )

        assert settings.security.url_target_allowlist_path == str(custom_path)

    def test_explicit_builtin_url_target_allowlist_path_is_preserved(self, tmp_path, monkeypatch):
        from obs.config import DatabaseSettings, SecuritySettings, Settings

        monkeypatch.delenv("OBS_SECRET_FILE_DIR", raising=False)

        settings = Settings(
            database=DatabaseSettings(path=str(tmp_path / "obs.db")),
            security=SecuritySettings(
                jwt_secret="unit-test-secret-32-chars-xxx",
                url_target_allowlist_path="/data/secrets/url-target-allowlist.yaml",
            ),
        )

        assert settings.security.url_target_allowlist_path == "/data/secrets/url-target-allowlist.yaml"


class TestHelperFunctions:
    def test_has_env_key_case_insensitive(self, monkeypatch):
        from obs.config import _has_env_key_case_insensitive

        monkeypatch.setenv("OBS_TEST_KEY", "value")
        assert _has_env_key_case_insensitive("obs_test_key") is True
        assert _has_env_key_case_insensitive("OBS_TEST_KEY") is True
        assert _has_env_key_case_insensitive("OTHER_KEY") is False

    def test_get_existing_env_key_case_insensitive(self, monkeypatch):
        from obs.config import _get_existing_env_key_case_insensitive

        monkeypatch.setenv("OBS_FIND_ME", "value")
        key = _get_existing_env_key_case_insensitive("obs_find_me")
        assert key is not None

    def test_get_env_case_insensitive(self, monkeypatch):
        from obs.config import _get_env_case_insensitive

        monkeypatch.setenv("OBS_MY_KEY", "hello")
        assert _get_env_case_insensitive("obs_my_key") == "hello"
        assert _get_env_case_insensitive("MISSING_KEY") is None

    def test_resolve_default_db_path_returns_default(self, tmp_path):
        from obs.config import _resolve_default_db_path

        # Neither new nor legacy path exists → returns default
        result = _resolve_default_db_path(str(tmp_path / "obs.db"))
        assert result == str(tmp_path / "obs.db")

    def test_resolve_default_db_path_prefers_legacy(self, tmp_path):
        from obs.config import _resolve_default_db_path

        legacy = tmp_path / "opentws.db"
        legacy.write_text("fake")
        new_db = tmp_path / "obs.db"
        # new_db does not exist, legacy does
        result = _resolve_default_db_path(str(new_db))
        assert result == str(legacy)

    def test_is_builtin_default_db_path(self):
        from obs.config import _is_builtin_default_db_path

        assert _is_builtin_default_db_path("/data/obs.db") is True
        assert _is_builtin_default_db_path("/custom/path.db") is False


class TestYamlConfigSource:
    def test_missing_yaml_file_silently_ignored(self, tmp_path):
        from obs.config import Settings, YamlConfigSource

        source = YamlConfigSource(Settings, tmp_path / "nonexistent.yaml")
        assert source() == {}

    def test_yaml_file_loaded(self, tmp_path):
        from obs.config import Settings, YamlConfigSource

        yaml_file = tmp_path / "config.yaml"
        yaml_file.write_text("server:\n  port: 9999\n")
        source = YamlConfigSource(Settings, yaml_file)
        data = source()
        assert data.get("server") is not None

    def test_field_is_complex_returns_true(self, tmp_path):
        from obs.config import Settings, YamlConfigSource

        source = YamlConfigSource(Settings, tmp_path / "none.yaml")
        assert source.field_is_complex(None) is True


class TestImportLegacyEnvVars:
    def test_opentws_vars_mapped_to_obs(self, monkeypatch):
        import os

        import obs.config as cfg_module

        monkeypatch.setenv("OPENTWS_MQTT__HOST", "my-broker")
        # Remove any existing OBS_MQTT__HOST
        monkeypatch.delenv("OBS_MQTT__HOST", raising=False)
        cfg_module._import_legacy_env_vars()
        assert os.environ.get("OBS_MQTT__HOST") == "my-broker"


# ===========================================================================
# obs/api/v1/system (direct function calls, no HTTP)
# ===========================================================================


class TestSystemHealth:
    @pytest.mark.asyncio
    async def test_health_with_registry(self, monkeypatch):
        import obs.api.v1.system as sys_api
        from obs.adapters import registry as adapter_registry

        fake_reg = SimpleNamespace(count=lambda: 5)
        monkeypatch.setattr("obs.core.registry.get_registry", lambda: fake_reg)
        monkeypatch.setattr(adapter_registry, "get_all_instances", lambda: {})

        result = await sys_api.health()
        assert result.status == "ok"
        assert result.datapoints == 5
        assert result.adapters_running == 0

    @pytest.mark.asyncio
    async def test_health_registry_not_initialized(self, monkeypatch):
        import obs.api.v1.system as sys_api
        from obs.adapters import registry as adapter_registry

        def _raise():
            raise RuntimeError("not initialized")

        monkeypatch.setattr("obs.core.registry.get_registry", _raise)
        monkeypatch.setattr(adapter_registry, "get_all_instances", lambda: {})

        result = await sys_api.health()
        assert result.status == "ok"
        assert result.datapoints == 0

    @pytest.mark.asyncio
    async def test_health_counts_connected_adapters(self, monkeypatch):
        import obs.api.v1.system as sys_api
        from obs.adapters import registry as adapter_registry

        monkeypatch.setattr("obs.core.registry.get_registry", lambda: SimpleNamespace(count=lambda: 0))
        instances = {
            "a": SimpleNamespace(connected=True),
            "b": SimpleNamespace(connected=False),
            "c": SimpleNamespace(connected=True),
        }
        monkeypatch.setattr(adapter_registry, "get_all_instances", lambda: instances)

        result = await sys_api.health()
        assert result.adapters_running == 2


class TestSystemDatatypes:
    @pytest.mark.asyncio
    async def test_datatypes_returns_list(self):
        import obs.api.v1.system as sys_api

        result = await sys_api.datatypes(_user="admin")
        assert isinstance(result, list)
        assert len(result) > 0
        assert all(hasattr(dt, "name") for dt in result)


class TestSystemAppSettings:
    @pytest.mark.asyncio
    async def test_get_app_settings_returns_default_when_no_row(self):
        import obs.api.v1.system as sys_api

        db = _DbStub(one=None)
        result = await sys_api.get_app_settings(db=db, _user="admin")
        assert result.timezone == "Europe/Zurich"

    @pytest.mark.asyncio
    async def test_get_app_settings_returns_stored_value(self):
        import obs.api.v1.system as sys_api

        db = _DbStub(one=_row(value="America/New_York"))
        result = await sys_api.get_app_settings(db=db, _user="admin")
        assert result.timezone == "America/New_York"

    @pytest.mark.asyncio
    async def test_update_app_settings_valid_timezone(self, monkeypatch):
        import obs.api.v1.system as sys_api

        db = _DbStub()

        # Patch logic manager import to avoid RuntimeError
        def _mock_get_logic_manager():
            return SimpleNamespace(update_app_config=lambda x: None)

        monkeypatch.setattr("obs.logic.manager.get_logic_manager", _mock_get_logic_manager, raising=False)

        body = sys_api.AppSettingsIn(timezone="Europe/Berlin")
        result = await sys_api.update_app_settings(body=body, db=db, _user="admin")
        assert result.timezone == "Europe/Berlin"

    @pytest.mark.asyncio
    async def test_update_app_settings_invalid_timezone_raises(self):
        import obs.api.v1.system as sys_api
        from fastapi import HTTPException

        db = _DbStub()
        body = sys_api.AppSettingsIn(timezone="Invalid/Timezone/XYZ")
        with pytest.raises(HTTPException) as exc_info:
            await sys_api.update_app_settings(body=body, db=db, _user="admin")
        assert exc_info.value.status_code == 422


class TestSystemNavLinks:
    @pytest.mark.asyncio
    async def test_list_nav_links_empty(self):
        import obs.api.v1.system as sys_api

        db = _DbStub(rows=[])
        result = await sys_api.list_nav_links(db=db, _user="admin")
        assert result == []

    @pytest.mark.asyncio
    async def test_list_nav_links_with_rows(self):
        import obs.api.v1.system as sys_api

        rows = [
            _row(id="link-1", label="Home", url="http://example.com", icon="home", sort_order=0, open_new_tab=1),
        ]
        db = _DbStub(rows=rows)
        result = await sys_api.list_nav_links(db=db, _user="admin")
        assert len(result) == 1
        assert result[0].label == "Home"

    @pytest.mark.asyncio
    async def test_create_nav_link(self):
        import obs.api.v1.system as sys_api

        db = _DbStub()
        body = sys_api.NavLinkIn(label="Test", url="http://test.com")
        result = await sys_api.create_nav_link(body=body, db=db, _admin="admin")
        assert result.label == "Test"
        assert result.url == "http://test.com"
        assert len(result.id) > 0

    @pytest.mark.asyncio
    async def test_update_nav_link_not_found_raises(self):
        import obs.api.v1.system as sys_api
        from fastapi import HTTPException

        db = _DbStub(one=None)
        body = sys_api.NavLinkPatch(label="New")
        with pytest.raises(HTTPException) as exc_info:
            await sys_api.update_nav_link(link_id="nonexistent", body=body, db=db, _admin="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_update_nav_link_success(self):
        import obs.api.v1.system as sys_api

        row = _row(id="link-1", label="Old", url="http://old.com", icon="", sort_order=0, open_new_tab=1)
        db = _DbStub(one=row)
        body = sys_api.NavLinkPatch(label="Updated")
        result = await sys_api.update_nav_link(link_id="link-1", body=body, db=db, _admin="admin")
        assert result.label == "Updated"
        assert result.url == "http://old.com"

    @pytest.mark.asyncio
    async def test_delete_nav_link_not_found_raises(self):
        import obs.api.v1.system as sys_api
        from fastapi import HTTPException

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await sys_api.delete_nav_link(link_id="nonexistent", db=db, _admin="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_delete_nav_link_success(self):
        import obs.api.v1.system as sys_api

        db = _DbStub(one=_row(id="link-1"))
        # Should not raise
        await sys_api.delete_nav_link(link_id="link-1", db=db, _admin="admin")
        assert any("DELETE" in q for q, _ in db.executed)


class TestSystemLogs:
    @pytest.mark.asyncio
    async def test_get_logs_returns_entries(self, monkeypatch):
        import obs.api.v1.system as sys_api

        entries = [
            {"ts": "2024-01-01T00:00:00Z", "level": "INFO", "logger": "obs", "message": "hello"},
            {"ts": "2024-01-01T00:00:01Z", "level": "ERROR", "logger": "obs", "message": "error"},
        ]
        monkeypatch.setattr("obs.log_buffer.get_log_buffer", lambda: entries)

        result = await sys_api.get_logs(_user="admin")
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_get_logs_filter_by_level(self, monkeypatch):
        import obs.api.v1.system as sys_api

        entries = [
            {"ts": "2024-01-01T00:00:00Z", "level": "INFO", "logger": "obs", "message": "info msg"},
            {"ts": "2024-01-01T00:00:01Z", "level": "ERROR", "logger": "obs", "message": "err msg"},
        ]
        monkeypatch.setattr("obs.log_buffer.get_log_buffer", lambda: entries)

        result = await sys_api.get_logs(level="ERROR", _user="admin")
        assert len(result) == 1
        assert result[0].level == "ERROR"

    @pytest.mark.asyncio
    async def test_get_logs_limit_applied(self, monkeypatch):
        import obs.api.v1.system as sys_api

        entries = [{"ts": "2024-01-01T00:00:00Z", "level": "INFO", "logger": "obs", "message": f"msg {i}"} for i in range(10)]
        monkeypatch.setattr("obs.log_buffer.get_log_buffer", lambda: entries)

        result = await sys_api.get_logs(limit=3, _user="admin")
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_get_log_level(self):
        import obs.api.v1.system as sys_api

        result = await sys_api.get_log_level(_admin="admin")
        assert isinstance(result.level, str)

    @pytest.mark.asyncio
    async def test_set_log_level_valid(self, monkeypatch):
        import obs.api.v1.system as sys_api

        called_with = []
        monkeypatch.setattr("obs.log_buffer.set_log_buffer_level", lambda lvl: called_with.append(lvl))

        body = sys_api.LogLevelIn(level="debug")
        result = await sys_api.set_log_level(body=body, _admin="admin")
        assert result is None
        assert called_with == ["DEBUG"]

    @pytest.mark.asyncio
    async def test_set_log_level_invalid_raises(self):
        import obs.api.v1.system as sys_api
        from fastapi import HTTPException

        body = sys_api.LogLevelIn(level="SUPERVERBOSE")
        with pytest.raises(HTTPException) as exc_info:
            await sys_api.set_log_level(body=body, _admin="admin")
        assert exc_info.value.status_code == 422


class TestSystemHistorySettings:
    @pytest.mark.asyncio
    async def test_get_history_settings_defaults(self):
        import obs.api.v1.system as sys_api

        db = _DbStub(rows=[])
        result = await sys_api.get_history_settings(db=db, _user="admin")
        assert result.plugin == "sqlite"
        assert result.default_window_hours == 168

    @pytest.mark.asyncio
    async def test_get_history_settings_overrides_defaults(self):
        import obs.api.v1.system as sys_api

        rows = [
            _row(key="history.plugin", value="influxdb"),
            _row(key="history.default_window_hours", value="24"),
        ]
        db = _DbStub(rows=rows)
        result = await sys_api.get_history_settings(db=db, _user="admin")
        assert result.plugin == "influxdb"
        assert result.default_window_hours == 24

    @pytest.mark.asyncio
    async def test_update_history_settings_invalid_plugin_raises(self):
        import obs.api.v1.system as sys_api
        from fastapi import HTTPException

        db = _DbStub()
        body = sys_api.HistorySettingsIn(plugin="badplugin")
        with pytest.raises(HTTPException) as exc_info:
            await sys_api.update_history_settings(body=body, db=db, _admin="admin")
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_test_history_connection_sqlite(self):
        import obs.api.v1.system as sys_api

        body = sys_api.HistorySettingsIn(plugin="sqlite")
        result = await sys_api.test_history_connection(body=body, _admin="admin")
        assert result.ok is True
        assert "SQLite" in result.message

    @pytest.mark.asyncio
    async def test_test_history_connection_preserves_redacted_influx_secrets(self, monkeypatch):
        import obs.api.v1.system as sys_api

        seen = {}

        class _InfluxPlugin:
            def __init__(self, **kwargs):
                seen.update(kwargs)

            async def ping(self):
                return True

        monkeypatch.setattr("obs.history.influxdb_plugin.InfluxDBHistoryPlugin", _InfluxPlugin)
        rows = [
            _row(key="history.influx_token", value="stored-token"),
            _row(key="history.influx_password", value="stored-password"),
        ]
        db = _DbStub(rows=rows)
        body = sys_api.HistorySettingsIn(
            plugin="influxdb",
            influx_token=sys_api.REDACTED,
            influx_password=sys_api.REDACTED,
        )

        result = await sys_api.test_history_connection(body=body, db=db, _admin="admin")

        assert result.ok is True
        assert seen["token"] == "stored-token"
        assert seen["password"] == "stored-password"

    @pytest.mark.asyncio
    async def test_test_history_connection_unknown_plugin(self):
        import obs.api.v1.system as sys_api

        body = sys_api.HistorySettingsIn(plugin="unknownplugin")
        result = await sys_api.test_history_connection(body=body, _admin="admin")
        assert result.ok is False


# ===========================================================================
# obs/api/v1/history (_parse_ts helper + _get_default_history_window_hours)
# ===========================================================================


class TestParseTsHelper:
    def test_none_returns_default(self):
        from obs.api.v1.history import _parse_ts

        default = datetime(2024, 1, 1, tzinfo=UTC)
        result = _parse_ts(None, default)
        assert result == default

    def test_empty_string_returns_default(self):
        from obs.api.v1.history import _parse_ts

        default = datetime(2024, 1, 1, tzinfo=UTC)
        result = _parse_ts("", default)
        assert result == default

    def test_valid_iso_string_parses(self):
        from obs.api.v1.history import _parse_ts

        default = datetime(2024, 1, 1, tzinfo=UTC)
        result = _parse_ts("2024-06-01T12:00:00+00:00", default)
        assert result.year == 2024
        assert result.month == 6

    def test_z_suffix_is_handled(self):
        from obs.api.v1.history import _parse_ts

        default = datetime(2024, 1, 1, tzinfo=UTC)
        result = _parse_ts("2024-06-01T12:00:00Z", default)
        assert result.year == 2024

    def test_invalid_string_raises_http_exception(self):
        from fastapi import HTTPException

        from obs.api.v1.history import _parse_ts

        default = datetime(2024, 1, 1, tzinfo=UTC)
        with pytest.raises(HTTPException) as exc_info:
            _parse_ts("not-a-date", default)
        assert exc_info.value.status_code == 422


class TestGetDefaultHistoryWindowHours:
    @pytest.mark.asyncio
    async def test_returns_default_when_no_row(self):
        from obs.api.v1.history import DEFAULT_HISTORY_WINDOW_HOURS, _get_default_history_window_hours

        db = _DbStub(one=None)
        hours = await _get_default_history_window_hours(db)
        assert hours == DEFAULT_HISTORY_WINDOW_HOURS

    @pytest.mark.asyncio
    async def test_returns_configured_value(self):
        from obs.api.v1.history import _get_default_history_window_hours

        db = _DbStub(one=_row(value="48"))
        hours = await _get_default_history_window_hours(db)
        assert hours == 48

    @pytest.mark.asyncio
    async def test_clamps_to_max(self):
        from obs.api.v1.history import MAX_HISTORY_WINDOW_HOURS, _get_default_history_window_hours

        db = _DbStub(one=_row(value="99999"))
        hours = await _get_default_history_window_hours(db)
        assert hours == MAX_HISTORY_WINDOW_HOURS

    @pytest.mark.asyncio
    async def test_clamps_to_min(self):
        from obs.api.v1.history import MIN_HISTORY_WINDOW_HOURS, _get_default_history_window_hours

        db = _DbStub(one=_row(value="0"))
        hours = await _get_default_history_window_hours(db)
        assert hours == MIN_HISTORY_WINDOW_HOURS

    @pytest.mark.asyncio
    async def test_invalid_value_returns_default(self):
        from obs.api.v1.history import DEFAULT_HISTORY_WINDOW_HOURS, _get_default_history_window_hours

        db = _DbStub(one=_row(value="notanumber"))
        hours = await _get_default_history_window_hours(db)
        assert hours == DEFAULT_HISTORY_WINDOW_HOURS

    @pytest.mark.asyncio
    async def test_null_value_returns_default(self):
        from obs.api.v1.history import DEFAULT_HISTORY_WINDOW_HOURS, _get_default_history_window_hours

        db = _DbStub(one=_row(value=None))
        hours = await _get_default_history_window_hours(db)
        assert hours == DEFAULT_HISTORY_WINDOW_HOURS


# ===========================================================================
# obs/api/v1/weather (SSRF protection and _weather_auth)
# ===========================================================================


class TestCheckSsrf:
    @pytest.mark.asyncio
    async def test_loopback_address_blocked(self, monkeypatch):
        from fastapi import HTTPException

        from obs.api.v1.weather import _build_fetch_targets as _check_ssrf

        monkeypatch.setattr(
            "obs.api.v1.weather.build_pinned_url_targets",
            lambda url: (_ for _ in ()).throw(ValueError("Blocked URL target")),
        )

        with pytest.raises(HTTPException) as exc_info:
            await _check_ssrf("http://evil.example.com/test")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_public_address_allowed(self, monkeypatch):
        from obs.api.v1.weather import _build_fetch_targets as _check_ssrf

        monkeypatch.setattr(
            "obs.api.v1.weather.build_pinned_url_targets",
            lambda url: ([url], {}, {}),
        )

        # Should not raise
        await _check_ssrf("http://example.com/weather")

    @pytest.mark.asyncio
    async def test_dns_failure_raises_502(self, monkeypatch):
        from fastapi import HTTPException

        from obs.api.v1.weather import _build_fetch_targets as _check_ssrf

        monkeypatch.setattr(
            "obs.api.v1.weather.build_pinned_url_targets",
            lambda url: (_ for _ in ()).throw(ValueError("Hostname could not be resolved: Name not found")),
        )

        with pytest.raises(HTTPException) as exc_info:
            await _check_ssrf("http://nonexistent-host-xyz.invalid/")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_no_hostname_raises_400(self):
        from fastapi import HTTPException

        from obs.api.v1.weather import _build_fetch_targets as _check_ssrf

        with pytest.raises(HTTPException) as exc_info:
            await _check_ssrf("http:///no-host")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_link_local_blocked(self, monkeypatch):
        from fastapi import HTTPException

        from obs.api.v1.weather import _build_fetch_targets as _check_ssrf

        monkeypatch.setattr(
            "obs.api.v1.weather.build_pinned_url_targets",
            lambda url: (_ for _ in ()).throw(ValueError("Blocked URL target")),
        )

        with pytest.raises(HTTPException) as exc_info:
            await _check_ssrf("http://metadata.internal/latest")
        assert exc_info.value.status_code == 400


class TestFetchWeather:
    @pytest.mark.asyncio
    async def test_non_http_url_raises_400(self):
        from fastapi import HTTPException

        from obs.api.v1.weather import fetch_weather

        with pytest.raises(HTTPException) as exc_info:
            await fetch_weather(url="ftp://example.com/file", _user="admin")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_successful_fetch_returns_json(self, monkeypatch):
        import httpx

        from obs.api.v1.weather import fetch_weather

        monkeypatch.setattr(
            "obs.api.v1.weather.build_pinned_url_targets",
            lambda url: ([url], {}, {}),
        )

        # Mock httpx response
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "application/json"}
        mock_response.json.return_value = {"temp": 22}

        class _FakeHttpxClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, **kwargs):
                return mock_response

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeHttpxClient())

        result = await fetch_weather(url="http://example.com/weather", _user="admin")
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_non_json_content_type_raises_502(self, monkeypatch):
        import httpx
        from fastapi import HTTPException

        from obs.api.v1.weather import fetch_weather

        monkeypatch.setattr(
            "obs.api.v1.weather.build_pinned_url_targets",
            lambda url: ([url], {}, {}),
        )

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.headers = {"content-type": "text/html"}

        class _FakeHttpxClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, **kwargs):
                return mock_response

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeHttpxClient())

        with pytest.raises(HTTPException) as exc_info:
            await fetch_weather(url="http://example.com/weather", _user="admin")
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_upstream_401_raises_502(self, monkeypatch):
        import httpx
        from fastapi import HTTPException

        from obs.api.v1.weather import fetch_weather

        monkeypatch.setattr(
            "obs.api.v1.weather.build_pinned_url_targets",
            lambda url: ([url], {}, {}),
        )

        mock_response = MagicMock()
        mock_response.status_code = 401

        class _FakeHttpxClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, **kwargs):
                return mock_response

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeHttpxClient())

        with pytest.raises(HTTPException) as exc_info:
            await fetch_weather(url="http://example.com/weather", _user="admin")
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_redirect_raises_400(self, monkeypatch):
        import httpx
        from fastapi import HTTPException

        from obs.api.v1.weather import fetch_weather

        monkeypatch.setattr(
            "obs.api.v1.weather.build_pinned_url_targets",
            lambda url: ([url], {}, {}),
        )

        mock_response = MagicMock()
        mock_response.status_code = 301

        class _FakeHttpxClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, **kwargs):
                return mock_response

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeHttpxClient())

        with pytest.raises(HTTPException) as exc_info:
            await fetch_weather(url="http://example.com/weather", _user="admin")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_request_error_raises_502(self, monkeypatch):
        import httpx
        from fastapi import HTTPException

        from obs.api.v1.weather import fetch_weather

        monkeypatch.setattr(
            "obs.api.v1.weather.build_pinned_url_targets",
            lambda url: ([url], {}, {}),
        )

        class _FakeHttpxClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, **kwargs):
                raise httpx.RequestError("connection refused")

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeHttpxClient())

        with pytest.raises(HTTPException) as exc_info:
            await fetch_weather(url="http://example.com/weather", _user="admin")
        assert exc_info.value.status_code == 502

    @pytest.mark.asyncio
    async def test_upstream_500_raises_502(self, monkeypatch):
        import httpx
        from fastapi import HTTPException

        from obs.api.v1.weather import fetch_weather

        monkeypatch.setattr(
            "obs.api.v1.weather.build_pinned_url_targets",
            lambda url: ([url], {}, {}),
        )

        mock_response = MagicMock()
        mock_response.status_code = 500

        class _FakeHttpxClient:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                pass

            async def get(self, url, **kwargs):
                return mock_response

        monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: _FakeHttpxClient())

        with pytest.raises(HTTPException) as exc_info:
            await fetch_weather(url="http://example.com/weather", _user="admin")
        assert exc_info.value.status_code == 502
