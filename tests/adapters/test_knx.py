"""Unit tests for the KNX adapter.

Tests that require xknx (Telegram objects) are skipped automatically if
xknx is not installed — so the test suite stays green on environments
without the optional dependency.
"""

from __future__ import annotations
from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import Telegram
from xknx.telegram.address import GroupAddress
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from obs.adapters.knx.adapter import KnxAdapter, KnxAdapterConfig, KnxBindingConfig, _telegram_to_bytes
from obs.adapters.knx.dpt_registry import DPTRegistry


import pytest

from tests.adapters.conftest import make_binding

# ---------------------------------------------------------------------------
# Helpers — skip markers
# ---------------------------------------------------------------------------

xknx = pytest.importorskip("xknx", reason="xknx not installed")

# ---------------------------------------------------------------------------
# KnxAdapterConfig validation
# ---------------------------------------------------------------------------


class TestKnxAdapterConfig:
    def test_defaults(self):
        cfg = KnxAdapterConfig()
        assert cfg.connection_type == "tunneling"
        assert cfg.host == "192.168.1.100"
        assert cfg.port == 3671
        assert cfg.individual_address == "1.1.255"
        assert cfg.local_ip is None
        assert cfg.user_id == 2
        assert cfg.user_password is None
        assert cfg.device_authentication_password is None
        assert cfg.backbone_key is None

    def test_tunneling_secure_fields(self):
        cfg = KnxAdapterConfig(
            connection_type="tunneling_secure",
            host="192.168.1.50",
            user_id=3,
            user_password="secret",
            device_authentication_password="devauth",
        )
        assert cfg.connection_type == "tunneling_secure"
        assert cfg.user_id == 3
        assert cfg.user_password == "secret"
        assert cfg.device_authentication_password == "devauth"

    def test_routing_secure_fields(self):
        cfg = KnxAdapterConfig(
            connection_type="routing_secure",
            backbone_key="0102030405060708090a0b0c0d0e0f10",
        )
        assert cfg.connection_type == "routing_secure"
        assert cfg.backbone_key == "0102030405060708090a0b0c0d0e0f10"

    def test_user_id_bounds(self):
        import pydantic
        with pytest.raises(pydantic.ValidationError):
            KnxAdapterConfig(user_id=0)
        with pytest.raises(pydantic.ValidationError):
            KnxAdapterConfig(user_id=128)

    def test_password_fields_in_json_schema(self):
        """Passwort-Felder müssen format=password im JSON-Schema haben."""
        schema = KnxAdapterConfig.model_json_schema()
        props = schema["properties"]
        for field_name in ("user_password", "device_authentication_password", "backbone_key"):
            assert props[field_name].get("format") == "password", (
                f"{field_name} muss format=password im Schema haben"
            )

    def test_individual_address_default(self):
        cfg = KnxAdapterConfig()
        assert cfg.individual_address == "1.1.255"

    def test_individual_address_custom(self):
        cfg = KnxAdapterConfig(individual_address="2.3.10")
        assert cfg.individual_address == "2.3.10"

    def test_local_ip_for_routing(self):
        cfg = KnxAdapterConfig(connection_type="routing", local_ip="192.168.1.5")
        assert cfg.local_ip == "192.168.1.5"


# ---------------------------------------------------------------------------
# _do_connect — SecureConfig Aufbau (ohne echte Netzwerkverbindung)
# ---------------------------------------------------------------------------


class TestDoConnectSecure:
    @pytest.mark.asyncio
    async def test_routing_secure_missing_backbone_key_publishes_error(self, mock_bus):
        """routing_secure ohne backbone_key → Status-Fehler, kein Absturz."""
        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={"connection_type": "routing_secure", "host": "239.0.0.1"},
        )
        await adapter._do_connect()

        assert mock_bus.publish.called
        event = mock_bus.publish.call_args[0][0]
        assert event.connected is False
        assert "backbone_key" in event.message.lower() or "backbone" in event.message.lower()

    @pytest.mark.asyncio
    async def test_routing_secure_invalid_backbone_key_publishes_error(self, mock_bus):
        """routing_secure mit ungültigem Hex → Status-Fehler, kein Absturz."""
        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={
                "connection_type": "routing_secure",
                "host": "239.0.0.1",
                "backbone_key": "KEIN-HEX",
            },
        )
        await adapter._do_connect()

        assert mock_bus.publish.called
        event = mock_bus.publish.call_args[0][0]
        assert event.connected is False


# ---------------------------------------------------------------------------
# Config validation
# ---------------------------------------------------------------------------


class TestKnxBindingConfig:
    def test_defaults(self):
        bc = KnxBindingConfig(group_address="1/2/3")
        assert bc.dpt_id == "DPT1.001"
        assert bc.state_group_address is None
        assert bc.respond_to_read is False

    def test_custom_values(self):
        bc = KnxBindingConfig(
            group_address="5/6/7",
            dpt_id="DPT9.001",
            state_group_address="5/6/8",
            respond_to_read=True,
        )
        assert bc.group_address == "5/6/7"
        assert bc.dpt_id == "DPT9.001"
        assert bc.state_group_address == "5/6/8"
        assert bc.respond_to_read is True


# ---------------------------------------------------------------------------
# _telegram_to_bytes
# ---------------------------------------------------------------------------


class TestTelegramToBytes:
    def _make_telegram(self, ga: str, raw_bytes: bytes) -> Telegram:
        return Telegram(
            destination_address=GroupAddress(ga),
            payload=GroupValueWrite(DPTArray(list(raw_bytes))),
        )

    def _make_bool_telegram(self, ga: str, bit: int) -> Telegram:
        return Telegram(
            destination_address=GroupAddress(ga),
            payload=GroupValueWrite(DPTBinary(bit)),
        )

    def test_dpt_array_two_bytes(self):
        t = self._make_telegram("1/2/3", b"\x0c\x7a")
        result = _telegram_to_bytes(t)
        assert isinstance(result, bytes)
        assert result == b"\x0c\x7a"

    def test_dpt_array_single_byte(self):
        t = self._make_telegram("1/2/3", b"\xff")
        result = _telegram_to_bytes(t)
        assert result == b"\xff"

    def test_dpt_binary_true(self):
        t = self._make_bool_telegram("0/0/1", 1)
        result = _telegram_to_bytes(t)
        assert isinstance(result, bytes)
        assert len(result) == 1

    def test_dpt_binary_false(self):
        t = self._make_bool_telegram("0/0/1", 0)
        result = _telegram_to_bytes(t)
        assert isinstance(result, bytes)
        assert result == b"\x00"


# ---------------------------------------------------------------------------
# _on_telegram — DataValueEvent dispatch
# ---------------------------------------------------------------------------


class TestOnTelegram:
    def _make_adapter(self, mock_bus) -> KnxAdapter:
        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        return adapter

    def _make_telegram(self, ga: str, raw_bytes: bytes) -> Telegram:
        return Telegram(
            destination_address=GroupAddress(ga),
            payload=GroupValueWrite(DPTArray(list(raw_bytes))),
        )

    @pytest.mark.asyncio
    async def test_known_ga_fires_data_value_event(self, mock_bus):
        adapter = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")  # Temperature, 2-byte float
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})

        adapter._ga_source_map["1/2/3"] = [(binding, dpt)]

        # Encode 21.5 °C
        raw = dpt.encoder(21.5)
        telegram = self._make_telegram("1/2/3", raw)

        await adapter._on_telegram(telegram)

        assert mock_bus.publish.called
        event = mock_bus.publish.call_args[0][0]
        assert event.datapoint_id == binding.datapoint_id
        assert abs(event.value - 21.5) < 0.1
        assert event.quality == "good"
        assert event.source_adapter == "KNX"

    @pytest.mark.asyncio
    async def test_unknown_ga_does_not_fire_event(self, mock_bus):
        adapter = self._make_adapter(mock_bus)
        # _ga_source_map is empty → GA unknown
        # KNX middle group is 0-7, use valid address 2/7/255
        telegram = self._make_telegram("2/7/255", b"\x00\x00")

        await adapter._on_telegram(telegram)

        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_value_read_does_not_fire_value_event(self, mock_bus):
        """GroupValueRead triggers _handle_read_request, never DataValueEvent."""
        adapter = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_source_map["1/2/3"] = [(binding, dpt)]

        telegram = Telegram(
            destination_address=GroupAddress("1/2/3"),
            payload=GroupValueRead(),
        )

        await adapter._on_telegram(telegram)

        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_group_value_response_fires_event(self, mock_bus):
        """GroupValueResponse is treated the same as GroupValueWrite."""
        adapter = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_source_map["1/2/3"] = [(binding, dpt)]

        raw = dpt.encoder(10.0)
        telegram = Telegram(
            destination_address=GroupAddress("1/2/3"),
            payload=GroupValueResponse(DPTArray(list(raw))),
        )

        await adapter._on_telegram(telegram)

        assert mock_bus.publish.called

    @pytest.mark.asyncio
    async def test_boolean_dpt1_decoded_correctly(self, mock_bus):
        adapter = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT1.001")  # Switch
        binding = make_binding({"group_address": "0/0/1", "dpt_id": "DPT1.001"})
        adapter._ga_source_map["0/0/1"] = [(binding, dpt)]

        raw = dpt.encoder(True)
        telegram = Telegram(
            destination_address=GroupAddress("0/0/1"),
            payload=GroupValueWrite(DPTBinary(raw[0])),
        )

        await adapter._on_telegram(telegram)

        event = mock_bus.publish.call_args[0][0]
        assert event.value is True
        assert event.quality == "good"

    @pytest.mark.asyncio
    async def test_multiple_bindings_on_same_ga_all_get_event(self, mock_bus):
        adapter = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")
        b1 = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        b2 = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_source_map["1/2/3"] = [(b1, dpt), (b2, dpt)]

        raw = dpt.encoder(5.0)
        telegram = self._make_telegram("1/2/3", raw)
        await adapter._on_telegram(telegram)

        assert mock_bus.publish.call_count == 2


# ---------------------------------------------------------------------------
# DPTRegistry
# ---------------------------------------------------------------------------


class TestDPTRegistry:
    def test_get_known_dpt(self):
        dpt = DPTRegistry.get("DPT9.001")
        assert dpt.dpt_id == "DPT9.001"
        assert dpt.data_type == "FLOAT"

    def test_get_unknown_returns_fallback(self):
        dpt = DPTRegistry.get("NONEXISTENT.999")
        assert dpt.dpt_id == "UNKNOWN"

    def test_dpt1_encoder_decoder_round_trip(self):
        dpt = DPTRegistry.get("DPT1.001")
        for val in (True, False):
            assert dpt.decoder(dpt.encoder(val)) == val

    def test_dpt9_encoder_decoder_round_trip(self):
        dpt = DPTRegistry.get("DPT9.001")
        val = 21.5
        assert abs(dpt.decoder(dpt.encoder(val)) - val) < 0.1
