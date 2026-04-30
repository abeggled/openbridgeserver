"""Unit-Tests für den Home Assistant Adapter.

Keine echte HA-Instanz erforderlich — HTTP-Client und WebSocket werden
gemockt. Direkte Tests von _on_state_changed() und write().
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.adapters.homeassistant.adapter import (
    HaBindingConfig,
    HomeAssistantAdapter,
    _coerce_state,
    _domain_from_entity,
)
from tests.adapters.conftest import make_binding

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def adapter(mock_bus):
    a = HomeAssistantAdapter(
        event_bus=mock_bus,
        config={
            "host": "192.168.1.100",
            "port": 8123,
            "token": "test-token-abc",
            "ssl": False,
        },
    )
    # Simulate connected state with a mock HTTP client
    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()
    a._http_client = mock_client
    a._cfg = a.config_schema(**a._config)
    return a


def _add_entity_binding(adapter: HomeAssistantAdapter, entity_id: str, **kwargs) -> object:
    """Helper: add a SOURCE binding to entity_map."""
    binding = make_binding({"entity_id": entity_id, **kwargs})
    adapter._entity_map.setdefault(entity_id, []).append(binding)
    return binding


# ---------------------------------------------------------------------------
# _coerce_state — pure function tests
# ---------------------------------------------------------------------------


class TestCoerceState:
    def test_on_returns_true(self):
        assert _coerce_state("on") is True

    def test_off_returns_false(self):
        assert _coerce_state("off") is False

    def test_true_string_returns_true(self):
        assert _coerce_state("true") is True

    def test_false_string_returns_false(self):
        assert _coerce_state("false") is False

    def test_integer_string(self):
        result = _coerce_state("42")
        assert result == 42
        assert isinstance(result, int)

    def test_float_string(self):
        result = _coerce_state("22.5")
        assert result == pytest.approx(22.5)
        assert isinstance(result, float)

    def test_plain_string_passthrough(self):
        assert _coerce_state("home") == "home"
        assert _coerce_state("idle") == "idle"


# ---------------------------------------------------------------------------
# _domain_from_entity — pure function tests
# ---------------------------------------------------------------------------


class TestDomainFromEntity:
    def test_light(self):
        assert _domain_from_entity("light.living_room") == "light"

    def test_switch(self):
        assert _domain_from_entity("switch.fan") == "switch"

    def test_input_number(self):
        assert _domain_from_entity("input_number.brightness") == "input_number"

    def test_sensor(self):
        assert _domain_from_entity("sensor.temperature") == "sensor"


# ---------------------------------------------------------------------------
# _on_state_changed — state field
# ---------------------------------------------------------------------------


class TestOnStateChangedStateField:
    @pytest.mark.asyncio
    async def test_numeric_state_published(self, adapter, mock_bus):
        binding = _add_entity_binding(adapter, "sensor.temperature")
        await adapter._on_state_changed(
            {
                "entity_id": "sensor.temperature",
                "new_state": {"state": "21.5", "attributes": {}},
            },
        )

        assert mock_bus.publish.called
        event = mock_bus.publish.call_args[0][0]
        assert event.value == pytest.approx(21.5)
        assert event.quality == "good"
        assert event.datapoint_id == binding.datapoint_id
        assert event.source_adapter == "HOME_ASSISTANT"

    @pytest.mark.asyncio
    async def test_boolean_on_state(self, adapter, mock_bus):
        _add_entity_binding(adapter, "switch.fan")
        await adapter._on_state_changed(
            {
                "entity_id": "switch.fan",
                "new_state": {"state": "on", "attributes": {}},
            },
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.value is True

    @pytest.mark.asyncio
    async def test_boolean_off_state(self, adapter, mock_bus):
        _add_entity_binding(adapter, "switch.fan")
        await adapter._on_state_changed(
            {
                "entity_id": "switch.fan",
                "new_state": {"state": "off", "attributes": {}},
            },
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.value is False

    @pytest.mark.asyncio
    async def test_string_state_passthrough(self, adapter, mock_bus):
        _add_entity_binding(adapter, "media_player.living_room")
        await adapter._on_state_changed(
            {
                "entity_id": "media_player.living_room",
                "new_state": {"state": "playing", "attributes": {}},
            },
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.value == "playing"

    @pytest.mark.asyncio
    async def test_unavailable_state_publishes_none(self, adapter, mock_bus):
        _add_entity_binding(adapter, "sensor.broken")
        await adapter._on_state_changed(
            {
                "entity_id": "sensor.broken",
                "new_state": {"state": "unavailable", "attributes": {}},
            },
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.value is None

    @pytest.mark.asyncio
    async def test_unknown_state_publishes_none(self, adapter, mock_bus):
        _add_entity_binding(adapter, "sensor.broken")
        await adapter._on_state_changed(
            {
                "entity_id": "sensor.broken",
                "new_state": {"state": "unknown", "attributes": {}},
            },
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.value is None

    @pytest.mark.asyncio
    async def test_new_state_none_skipped(self, adapter, mock_bus):
        """Entity removed (new_state is None) → no event published."""
        _add_entity_binding(adapter, "sensor.gone")
        await adapter._on_state_changed(
            {
                "entity_id": "sensor.gone",
                "new_state": None,
            },
        )

        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_entity_ignored(self, adapter, mock_bus):
        """state_changed for an entity with no binding → no event."""
        await adapter._on_state_changed(
            {
                "entity_id": "sensor.not_bound",
                "new_state": {"state": "42", "attributes": {}},
            },
        )

        mock_bus.publish.assert_not_called()


# ---------------------------------------------------------------------------
# _on_state_changed — attribute field
# ---------------------------------------------------------------------------


class TestOnStateChangedAttribute:
    @pytest.mark.asyncio
    async def test_attribute_extracted(self, adapter, mock_bus):
        _add_entity_binding(adapter, "light.bedroom", attribute="brightness")
        await adapter._on_state_changed(
            {
                "entity_id": "light.bedroom",
                "new_state": {
                    "state": "on",
                    "attributes": {"brightness": 128, "color_temp": 370},
                },
            },
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.value == 128

    @pytest.mark.asyncio
    async def test_missing_attribute_returns_none(self, adapter, mock_bus):
        _add_entity_binding(adapter, "light.bedroom", attribute="color_temp")
        await adapter._on_state_changed(
            {
                "entity_id": "light.bedroom",
                "new_state": {
                    "state": "on",
                    "attributes": {},  # attribute missing
                },
            },
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.value is None


# ---------------------------------------------------------------------------
# _on_state_changed — value_map
# ---------------------------------------------------------------------------


class TestOnStateChangedValueMap:
    @pytest.mark.asyncio
    async def test_value_map_applied_to_state(self, adapter, mock_bus):
        binding = make_binding(
            {"entity_id": "input_select.mode"},
            value_map={"home": "1", "away": "0"},
        )
        adapter._entity_map["input_select.mode"] = [binding]
        await adapter._on_state_changed(
            {
                "entity_id": "input_select.mode",
                "new_state": {"state": "home", "attributes": {}},
            },
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.value == "1"

    @pytest.mark.asyncio
    async def test_value_map_no_match_passthrough(self, adapter, mock_bus):
        binding = make_binding(
            {"entity_id": "input_select.mode"},
            value_map={"home": "1"},
        )
        adapter._entity_map["input_select.mode"] = [binding]
        await adapter._on_state_changed(
            {
                "entity_id": "input_select.mode",
                "new_state": {"state": "vacation", "attributes": {}},
            },
        )

        event = mock_bus.publish.call_args[0][0]
        assert event.value == "vacation"


# ---------------------------------------------------------------------------
# write() — service call logic
# ---------------------------------------------------------------------------


class TestWrite:
    @pytest.mark.asyncio
    async def test_write_bool_true_calls_turn_on(self, adapter):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        adapter._http_client.post = AsyncMock(return_value=mock_response)

        binding = make_binding({"entity_id": "switch.fan"})
        await adapter.write(binding, True)

        adapter._http_client.post.assert_called_once()
        call_args = adapter._http_client.post.call_args
        assert call_args[0][0] == "/api/services/switch/turn_on"
        assert call_args[1]["json"]["entity_id"] == "switch.fan"

    @pytest.mark.asyncio
    async def test_write_bool_false_calls_turn_off(self, adapter):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        adapter._http_client.post = AsyncMock(return_value=mock_response)

        binding = make_binding({"entity_id": "light.living_room"})
        await adapter.write(binding, False)

        call_args = adapter._http_client.post.call_args
        assert call_args[0][0] == "/api/services/light/turn_off"

    @pytest.mark.asyncio
    async def test_write_numeric_with_service_data_key(self, adapter):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        adapter._http_client.post = AsyncMock(return_value=mock_response)

        binding = make_binding(
            {
                "entity_id": "input_number.heating",
                "service_data_key": "value",
            },
        )
        await adapter.write(binding, 21.5)

        call_args = adapter._http_client.post.call_args
        assert call_args[0][0] == "/api/services/input_number/set_value"
        payload = call_args[1]["json"]
        assert payload["entity_id"] == "input_number.heating"
        assert payload["value"] == 21.5

    @pytest.mark.asyncio
    async def test_write_with_explicit_service_name(self, adapter):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        adapter._http_client.post = AsyncMock(return_value=mock_response)

        binding = make_binding(
            {
                "entity_id": "cover.blind",
                "service_name": "set_cover_position",
                "service_data_key": "position",
            },
        )
        await adapter.write(binding, 75)

        call_args = adapter._http_client.post.call_args
        assert call_args[0][0] == "/api/services/cover/set_cover_position"
        payload = call_args[1]["json"]
        assert payload["position"] == 75

    @pytest.mark.asyncio
    async def test_write_with_explicit_service_domain(self, adapter):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        adapter._http_client.post = AsyncMock(return_value=mock_response)

        binding = make_binding(
            {
                "entity_id": "scene.night_mode",
                "service_domain": "homeassistant",
                "service_name": "turn_on",
            },
        )
        await adapter.write(binding, True)

        call_args = adapter._http_client.post.call_args
        assert call_args[0][0] == "/api/services/homeassistant/turn_on"

    @pytest.mark.asyncio
    async def test_write_passes_through_pretransformed_value(self, adapter):
        # write_router applies value_map before calling adapter.write();
        # the adapter receives an already-transformed bool and must not re-map it.
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        adapter._http_client.post = AsyncMock(return_value=mock_response)

        binding = make_binding({"entity_id": "switch.pump"})
        await adapter.write(binding, True)

        call_args = adapter._http_client.post.call_args
        assert "/turn_on" in call_args[0][0]

    @pytest.mark.asyncio
    async def test_write_no_http_client_does_not_raise(self, adapter, mock_bus):
        """write() without initialized HTTP client logs warning but does not raise."""
        adapter._http_client = None
        binding = make_binding({"entity_id": "switch.test"})
        # Should not raise
        await adapter.write(binding, True)

    @pytest.mark.asyncio
    async def test_write_http_error_does_not_raise(self, adapter):
        """HTTP error in write() is caught and logged, not raised."""
        adapter._http_client.post = AsyncMock(side_effect=Exception("connection refused"))

        binding = make_binding({"entity_id": "switch.test"})
        # Should not raise
        await adapter.write(binding, True)


# ---------------------------------------------------------------------------
# read()
# ---------------------------------------------------------------------------


class TestRead:
    @pytest.mark.asyncio
    async def test_read_state_field(self, adapter):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={
                "entity_id": "sensor.temperature",
                "state": "22.3",
                "attributes": {},
            },
        )
        adapter._http_client.get = AsyncMock(return_value=mock_response)

        binding = make_binding({"entity_id": "sensor.temperature"})
        result = await adapter.read(binding)
        assert result == pytest.approx(22.3)

    @pytest.mark.asyncio
    async def test_read_attribute_field(self, adapter):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={
                "entity_id": "light.kitchen",
                "state": "on",
                "attributes": {"brightness": 200, "color_temp": 350},
            },
        )
        adapter._http_client.get = AsyncMock(return_value=mock_response)

        binding = make_binding({"entity_id": "light.kitchen", "attribute": "brightness"})
        result = await adapter.read(binding)
        assert result == 200

    @pytest.mark.asyncio
    async def test_read_unavailable_returns_none(self, adapter):
        mock_response = MagicMock()
        mock_response.raise_for_status = MagicMock()
        mock_response.json = MagicMock(
            return_value={
                "entity_id": "sensor.broken",
                "state": "unavailable",
                "attributes": {},
            },
        )
        adapter._http_client.get = AsyncMock(return_value=mock_response)

        binding = make_binding({"entity_id": "sensor.broken"})
        result = await adapter.read(binding)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_http_error_returns_none(self, adapter):
        adapter._http_client.get = AsyncMock(side_effect=Exception("timeout"))
        binding = make_binding({"entity_id": "sensor.unreachable"})
        result = await adapter.read(binding)
        assert result is None

    @pytest.mark.asyncio
    async def test_read_no_client_returns_none(self, adapter):
        adapter._http_client = None
        binding = make_binding({"entity_id": "sensor.test"})
        result = await adapter.read(binding)
        assert result is None


# ---------------------------------------------------------------------------
# connect() — config validation
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_without_token_publishes_error_status(self, mock_bus):
        adapter = HomeAssistantAdapter(
            event_bus=mock_bus,
            config={"host": "ha.local", "port": 8123, "token": "", "ssl": False},
        )
        with patch("obs.adapters.homeassistant.adapter.httpx.AsyncClient"):
            await adapter.connect()

        # Should publish status with connected=False
        assert mock_bus.publish.called
        event = mock_bus.publish.call_args[0][0]
        from obs.core.event_bus import AdapterStatusEvent

        assert isinstance(event, AdapterStatusEvent)
        assert event.connected is False

    @pytest.mark.asyncio
    async def test_connect_sets_cfg(self, mock_bus):
        adapter = HomeAssistantAdapter(
            event_bus=mock_bus,
            config={
                "host": "192.168.1.10",
                "port": 8123,
                "token": "mytoken",
                "ssl": False,
            },
        )
        with patch("obs.adapters.homeassistant.adapter.httpx.AsyncClient") as mock_client_cls:
            mock_client_cls.return_value = MagicMock(aclose=AsyncMock())
            await adapter.connect()

        assert adapter._cfg is not None
        assert adapter._cfg.host == "192.168.1.10"
        assert adapter._cfg.token == "mytoken"


# ---------------------------------------------------------------------------
# HaBindingConfig — Pydantic schema validation
# ---------------------------------------------------------------------------


class TestHaBindingConfig:
    def test_minimal_config(self):
        bc = HaBindingConfig(entity_id="sensor.temp")
        assert bc.entity_id == "sensor.temp"
        assert bc.attribute is None
        assert bc.service_domain is None
        assert bc.service_name is None
        assert bc.service_data_key is None

    def test_full_config(self):
        bc = HaBindingConfig(
            entity_id="light.kitchen",
            attribute="brightness",
            service_domain="light",
            service_name="turn_on",
            service_data_key="brightness",
        )
        assert bc.attribute == "brightness"
        assert bc.service_domain == "light"
        assert bc.service_name == "turn_on"
        assert bc.service_data_key == "brightness"
