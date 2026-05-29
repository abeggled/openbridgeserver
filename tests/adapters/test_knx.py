"""Unit tests for the KNX adapter.

Tests that require xknx (Telegram objects) are skipped automatically if
xknx is not installed — so the test suite stays green on environments
without the optional dependency.
"""

from __future__ import annotations
import asyncio
from datetime import UTC, datetime, timedelta

from xknx.core.connection_state import XknxConnectionState
from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import Telegram
from xknx.telegram.address import GroupAddress
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from obs.adapters.knx.adapter import KnxAdapter, KnxAdapterConfig, KnxBindingConfig, _telegram_to_bytes
from obs.adapters.knx.dpt_registry import DPTRegistry
from obs.core.event_bus import AdapterStatusEvent


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
        assert cfg.multicast_group == "224.0.23.12"
        assert cfg.multicast_port == 3671
        assert cfg.user_id == 2
        assert cfg.user_password is None
        assert cfg.device_authentication_password is None
        assert cfg.backbone_key is None
        assert cfg.knxkeys_file_path is None
        assert cfg.knxkeys_password is None

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

    def test_keyfile_fields(self):
        cfg = KnxAdapterConfig(
            connection_type="tunneling_secure",
            host="192.168.1.50",
            knxkeys_file_path="/data/knxkeys/abc.knxkeys",
            knxkeys_password="geheim",
            individual_address="1.1.100",
        )
        assert cfg.knxkeys_file_path == "/data/knxkeys/abc.knxkeys"
        assert cfg.knxkeys_password == "geheim"
        assert cfg.individual_address == "1.1.100"

    def test_password_fields_in_json_schema(self):
        """Passwort-Felder müssen format=password im JSON-Schema haben."""
        schema = KnxAdapterConfig.model_json_schema()
        props = schema["properties"]
        for field_name in (
            "user_password",
            "device_authentication_password",
            "backbone_key",
            "knxkeys_password",
        ):
            assert props[field_name].get("format") == "password", f"{field_name} muss format=password im Schema haben"

    def test_individual_address_default(self):
        cfg = KnxAdapterConfig()
        assert cfg.individual_address == "1.1.255"

    def test_individual_address_custom(self):
        cfg = KnxAdapterConfig(individual_address="2.3.10")
        assert cfg.individual_address == "2.3.10"

    def test_local_ip_for_routing(self):
        cfg = KnxAdapterConfig(connection_type="routing", local_ip="192.168.1.5")
        assert cfg.local_ip == "192.168.1.5"

    def test_routing_multicast_defaults(self):
        cfg = KnxAdapterConfig(connection_type="routing")
        assert cfg.multicast_group == "224.0.23.12"
        assert cfg.multicast_port == 3671

    def test_routing_custom_multicast(self):
        cfg = KnxAdapterConfig(connection_type="routing_secure", multicast_group="239.0.0.1")
        assert cfg.multicast_group == "239.0.0.1"

    def test_tunneling_tcp(self):
        cfg = KnxAdapterConfig(connection_type="tunneling_tcp", host="10.0.0.1", port=3671)
        assert cfg.connection_type == "tunneling_tcp"
        assert cfg.host == "10.0.0.1"


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
        assert "backbone_key" in event.detail.lower() or "backbone" in event.detail.lower()

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


# ---------------------------------------------------------------------------
# Tunnel-Pool overload detection — issue #466
# ---------------------------------------------------------------------------


def _warning_events(mock_bus) -> list[AdapterStatusEvent]:
    return [c.args[0] for c in mock_bus.publish.call_args_list if isinstance(c.args[0], AdapterStatusEvent) and c.args[0].severity == "warning"]


def _ok_status_events(mock_bus) -> list[AdapterStatusEvent]:
    return [c.args[0] for c in mock_bus.publish.call_args_list if isinstance(c.args[0], AdapterStatusEvent) and c.args[0].severity == "ok"]


class TestTunnelOverloadDetection:
    """Issue #466: detect repeated KNX/IP tunnel disconnects (pool overload)
    and surface them as a visible AdapterStatusEvent with severity=warning."""

    def _make_adapter(self, mock_bus, *, threshold: int = 3, window_s: int = 300) -> KnxAdapter:
        return KnxAdapter(
            event_bus=mock_bus,
            config={
                "host": "127.0.0.1",
                "tunnel_overload_threshold": threshold,
                "tunnel_overload_window_s": window_s,
            },
        )

    def _pin_now(self, monkeypatch, adapter: KnxAdapter, when: datetime) -> None:
        monkeypatch.setattr(adapter, "_now", lambda: when)

    # ------------------------------------------------------------------
    # Config schema
    # ------------------------------------------------------------------

    def test_config_defaults_match_issue_spec(self):
        cfg = KnxAdapterConfig()
        assert cfg.tunnel_overload_threshold == 3
        assert cfg.tunnel_overload_window_s == 300

    def test_config_accepts_custom_values(self):
        cfg = KnxAdapterConfig(tunnel_overload_threshold=5, tunnel_overload_window_s=600)
        assert cfg.tunnel_overload_threshold == 5
        assert cfg.tunnel_overload_window_s == 600

    # ------------------------------------------------------------------
    # Sliding window
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_disconnect_older_than_window_is_pruned(self, mock_bus, monkeypatch):
        adapter = self._make_adapter(mock_bus, threshold=3, window_s=300)
        t0 = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)

        self._pin_now(monkeypatch, adapter, t0)
        await adapter._record_disconnect()
        self._pin_now(monkeypatch, adapter, t0 + timedelta(seconds=301))
        await adapter._record_disconnect()

        # The first event aged out — only the most recent one remains.
        assert len(adapter._disconnect_times) == 1
        assert adapter._disconnect_times[0] == t0 + timedelta(seconds=301)

    # ------------------------------------------------------------------
    # Warning publication
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_below_threshold_publishes_no_warning(self, mock_bus, monkeypatch):
        adapter = self._make_adapter(mock_bus, threshold=3, window_s=300)
        t0 = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)

        for offset in (0, 30):
            self._pin_now(monkeypatch, adapter, t0 + timedelta(seconds=offset))
            await adapter._record_disconnect()

        assert _warning_events(mock_bus) == []

    @pytest.mark.asyncio
    async def test_threshold_reached_publishes_warning_status(self, mock_bus, monkeypatch):
        adapter = self._make_adapter(mock_bus, threshold=3, window_s=300)
        t0 = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)

        for offset in (0, 30, 60):
            self._pin_now(monkeypatch, adapter, t0 + timedelta(seconds=offset))
            await adapter._record_disconnect()

        warnings = _warning_events(mock_bus)
        assert len(warnings) == 1
        evt = warnings[0]
        assert evt.adapter_type == "KNX"
        assert evt.severity == "warning"
        # Issue #466 wording — the GUI/admin uses detail verbatim.
        assert "Tunnel" in evt.detail
        assert "anderem Client" in evt.detail or "anderer Client" in evt.detail

    @pytest.mark.asyncio
    async def test_further_disconnects_do_not_republish_warning(self, mock_bus, monkeypatch):
        adapter = self._make_adapter(mock_bus, threshold=3, window_s=300)
        t0 = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)

        for offset in (0, 30, 60, 90, 120):
            self._pin_now(monkeypatch, adapter, t0 + timedelta(seconds=offset))
            await adapter._record_disconnect()

        # Exactly one warning event — no spam while overload persists.
        assert len(_warning_events(mock_bus)) == 1

    # ------------------------------------------------------------------
    # Recovery
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_connected_after_quiet_period_clears_warning(self, mock_bus, monkeypatch):
        adapter = self._make_adapter(mock_bus, threshold=3, window_s=300)
        t0 = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)

        for offset in (0, 30, 60):
            self._pin_now(monkeypatch, adapter, t0 + timedelta(seconds=offset))
            await adapter._record_disconnect()
        assert len(_warning_events(mock_bus)) == 1

        # Window passes without further disconnects, then xknx reports CONNECTED.
        self._pin_now(monkeypatch, adapter, t0 + timedelta(seconds=400))
        await adapter._record_reconnect()

        # A status event with severity="ok" must be published exactly once on recovery.
        ok_events = _ok_status_events(mock_bus)
        assert len(ok_events) >= 1
        assert ok_events[-1].severity == "ok"
        assert adapter._warning_active is False

    @pytest.mark.asyncio
    async def test_reconnect_during_active_window_does_not_clear_warning(self, mock_bus, monkeypatch):
        adapter = self._make_adapter(mock_bus, threshold=3, window_s=300)
        t0 = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)

        for offset in (0, 30, 60):
            self._pin_now(monkeypatch, adapter, t0 + timedelta(seconds=offset))
            await adapter._record_disconnect()
        ok_before = len(_ok_status_events(mock_bus))

        # Ping-pong reconnect still inside the window — must NOT publish an ok event.
        self._pin_now(monkeypatch, adapter, t0 + timedelta(seconds=90))
        await adapter._record_reconnect()

        assert adapter._warning_active is True
        assert len(_ok_status_events(mock_bus)) == ok_before

    # ------------------------------------------------------------------
    # xknx callback wiring
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_xknx_disconnected_callback_records_event(self, mock_bus, monkeypatch):
        adapter = self._make_adapter(mock_bus, threshold=3, window_s=300)
        t0 = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
        self._pin_now(monkeypatch, adapter, t0)

        # The xknx ConnectionManager calls the sync callback in the main event loop.
        adapter._on_xknx_connection_state(XknxConnectionState.DISCONNECTED)
        # Allow the scheduled async record task to run.
        await asyncio.sleep(0)

        assert len(adapter._disconnect_times) == 1

    @pytest.mark.asyncio
    async def test_xknx_connected_callback_runs_reconnect_handler(self, mock_bus, monkeypatch):
        adapter = self._make_adapter(mock_bus, threshold=3, window_s=300)
        t0 = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)

        # First trip the warning, then advance time past the window.
        for offset in (0, 30, 60):
            self._pin_now(monkeypatch, adapter, t0 + timedelta(seconds=offset))
            await adapter._record_disconnect()

        self._pin_now(monkeypatch, adapter, t0 + timedelta(seconds=400))
        adapter._on_xknx_connection_state(XknxConnectionState.CONNECTED)
        await asyncio.sleep(0)

        assert adapter._warning_active is False
        assert any(e.severity == "ok" for e in _ok_status_events(mock_bus))

    @pytest.mark.asyncio
    async def test_xknx_connecting_callback_is_ignored(self, mock_bus, monkeypatch):
        adapter = self._make_adapter(mock_bus, threshold=3, window_s=300)
        t0 = datetime(2026, 5, 15, 12, 0, 0, tzinfo=UTC)
        self._pin_now(monkeypatch, adapter, t0)

        adapter._on_xknx_connection_state(XknxConnectionState.CONNECTING)
        await asyncio.sleep(0)

        assert len(adapter._disconnect_times) == 0
        assert _warning_events(mock_bus) == []


# ---------------------------------------------------------------------------
# _now() and set_value_getter()
# ---------------------------------------------------------------------------


class TestKnxAdapterMiscSetters:
    def test_now_returns_utc_datetime(self):
        from datetime import UTC

        result = KnxAdapter._now()
        assert result.tzinfo is UTC

    def test_set_value_getter(self, mock_bus):
        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        getter = lambda dp_id: None  # noqa: E731
        adapter.set_value_getter(getter)
        assert adapter._value_getter is getter


# ---------------------------------------------------------------------------
# _prune_disconnects — TypeError/ValueError fallback
# ---------------------------------------------------------------------------


class TestPruneDisconnectsEdgeCases:
    @pytest.mark.asyncio
    async def test_invalid_window_type_falls_back_to_300(self, mock_bus, monkeypatch):
        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={"host": "127.0.0.1", "tunnel_overload_window_s": "not-a-number"},
        )
        from datetime import UTC, datetime

        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(adapter, "_now", lambda: now)
        # Should not raise — TypeError path uses 300 s default
        await adapter._record_disconnect()
        assert len(adapter._disconnect_times) == 1

    @pytest.mark.asyncio
    async def test_invalid_threshold_type_falls_back_to_3(self, mock_bus, monkeypatch):
        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={"host": "127.0.0.1", "tunnel_overload_threshold": None},
        )
        from datetime import UTC, datetime

        now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(adapter, "_now", lambda: now)
        await adapter._record_disconnect()
        assert len(adapter._disconnect_times) == 1


# ---------------------------------------------------------------------------
# connect() / disconnect() lifecycle (mocked xknx)
# ---------------------------------------------------------------------------


class TestKnxAdapterConnectDisconnect:
    def _make_mock_xknx(self):
        from unittest.mock import AsyncMock, MagicMock

        mock = MagicMock()
        mock.start = AsyncMock()
        mock.stop = AsyncMock()
        mock.telegrams = MagicMock()
        mock.telegrams.put = AsyncMock()
        mock.devices = MagicMock()
        mock.devices.__iter__ = MagicMock(return_value=iter([]))
        mock.devices.async_add = MagicMock()
        mock.devices.async_remove = MagicMock()
        return mock

    @pytest.mark.asyncio
    async def test_connect_tunneling_publishes_connected(self, mock_bus, monkeypatch):
        from unittest.mock import patch

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1", "port": 3671})
        mock_xknx_instance = self._make_mock_xknx()

        with patch("xknx.XKNX", return_value=mock_xknx_instance):
            await adapter._do_connect()

        assert mock_xknx_instance.start.called
        events = [c.args[0] for c in mock_bus.publish.call_args_list]
        connected = [e for e in events if hasattr(e, "connected") and e.connected]
        assert connected

    @pytest.mark.asyncio
    async def test_connect_routing_publishes_connected(self, mock_bus):
        from unittest.mock import patch

        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={
                "connection_type": "routing",
                "multicast_group": "224.0.23.12",
                "multicast_port": 3671,
                "individual_address": "1.1.1",
            },
        )
        mock_xknx_instance = self._make_mock_xknx()

        with patch("xknx.XKNX", return_value=mock_xknx_instance):
            await adapter._do_connect()

        assert mock_xknx_instance.start.called
        events = [c.args[0] for c in mock_bus.publish.call_args_list]
        connected = [e for e in events if hasattr(e, "connected") and e.connected]
        assert connected

    @pytest.mark.asyncio
    async def test_connect_failure_publishes_error(self, mock_bus):
        from unittest.mock import AsyncMock, MagicMock, patch

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        failing_mock = MagicMock()
        failing_mock.start = AsyncMock(side_effect=ConnectionRefusedError("refused"))
        failing_mock.stop = AsyncMock()
        failing_mock.devices = MagicMock()
        failing_mock.devices.__iter__ = MagicMock(return_value=iter([]))

        with patch("xknx.XKNX", return_value=failing_mock):
            await adapter._do_connect()

        events = [c.args[0] for c in mock_bus.publish.call_args_list]
        error_events = [e for e in events if hasattr(e, "connected") and not e.connected]
        assert error_events

    @pytest.mark.asyncio
    async def test_do_connect_tunneling_secure_keyfile(self, mock_bus):
        from unittest.mock import MagicMock, patch

        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={
                "connection_type": "tunneling_secure",
                "host": "192.168.1.50",
                "knxkeys_file_path": "/tmp/test.knxkeys",
                "knxkeys_password": "secret",
            },
        )
        mock_xknx_instance = self._make_mock_xknx()
        mock_secure_config = MagicMock()

        with patch("xknx.XKNX", return_value=mock_xknx_instance), patch("xknx.io.SecureConfig", return_value=mock_secure_config):
            await adapter._do_connect()

        assert mock_xknx_instance.start.called

    @pytest.mark.asyncio
    async def test_do_connect_tunneling_secure_manual(self, mock_bus):
        from unittest.mock import MagicMock, patch

        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={
                "connection_type": "tunneling_secure",
                "host": "192.168.1.50",
                "user_id": 3,
                "user_password": "userpass",
                "device_authentication_password": "devauth",
            },
        )
        mock_xknx_instance = self._make_mock_xknx()
        mock_secure_config = MagicMock()

        with patch("xknx.XKNX", return_value=mock_xknx_instance), patch("xknx.io.SecureConfig", return_value=mock_secure_config):
            await adapter._do_connect()

        assert mock_xknx_instance.start.called

    @pytest.mark.asyncio
    async def test_do_connect_routing_secure_valid_backbone(self, mock_bus):
        from unittest.mock import MagicMock, patch

        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={
                "connection_type": "routing_secure",
                "backbone_key": "0102030405060708090a0b0c0d0e0f10",
            },
        )
        mock_xknx_instance = self._make_mock_xknx()
        mock_secure_config = MagicMock()

        with patch("xknx.XKNX", return_value=mock_xknx_instance), patch("xknx.io.SecureConfig", return_value=mock_secure_config):
            await adapter._do_connect()

        assert mock_xknx_instance.start.called

    @pytest.mark.asyncio
    async def test_connect_cleans_up_previous_xknx(self, mock_bus):
        from unittest.mock import patch

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        old_xknx = self._make_mock_xknx()
        adapter._xknx = old_xknx

        new_xknx = self._make_mock_xknx()
        with patch("xknx.XKNX", return_value=new_xknx):
            await adapter._do_connect()

        old_xknx.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_disconnect_stops_xknx(self, mock_bus):
        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        mock_xknx_instance = self._make_mock_xknx()
        adapter._xknx = mock_xknx_instance

        await adapter.disconnect()

        mock_xknx_instance.stop.assert_called_once()
        assert adapter._xknx is None
        assert adapter._stopped is True

    @pytest.mark.asyncio
    async def test_disconnect_cancels_reconnect_task(self, mock_bus):
        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        mock_xknx_instance = self._make_mock_xknx()
        adapter._xknx = mock_xknx_instance

        adapter._reconnect_task = asyncio.ensure_future(asyncio.sleep(1000))
        await adapter.disconnect()

        assert adapter._reconnect_task is None

    @pytest.mark.asyncio
    async def test_disconnect_without_xknx_is_safe(self, mock_bus):
        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        # _xknx is None — should not raise
        await adapter.disconnect()
        assert adapter._stopped is True

    @pytest.mark.asyncio
    async def test_connect_starts_reconnect_loop(self, mock_bus):
        from unittest.mock import patch

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        mock_xknx_instance = self._make_mock_xknx()

        with patch("xknx.XKNX", return_value=mock_xknx_instance):
            await adapter.connect()

        assert adapter._reconnect_task is not None
        assert not adapter._reconnect_task.done()
        # cleanup
        adapter._reconnect_task.cancel()
        try:
            await adapter._reconnect_task
        except asyncio.CancelledError:
            pass


# ---------------------------------------------------------------------------
# _on_bindings_reloaded — GA map building
# ---------------------------------------------------------------------------


class TestOnBindingsReloaded:
    def _make_adapter(self, mock_bus, bindings=None):
        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        adapter._bindings = bindings or []
        return adapter

    @pytest.mark.asyncio
    async def test_no_xknx_builds_map_only(self, mock_bus):
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"}, direction="SOURCE")
        adapter = self._make_adapter(mock_bus, [binding])
        # _xknx is None → builds map but returns early
        await adapter._on_bindings_reloaded()
        assert "1/2/3" in adapter._ga_source_map

    @pytest.mark.asyncio
    async def test_source_binding_added_to_source_map(self, mock_bus):
        binding = make_binding({"group_address": "2/3/4", "dpt_id": "DPT1.001"}, direction="SOURCE")
        adapter = self._make_adapter(mock_bus, [binding])
        await adapter._on_bindings_reloaded()
        assert "2/3/4" in adapter._ga_source_map

    @pytest.mark.asyncio
    async def test_dest_only_binding_not_in_source_map(self, mock_bus):
        binding = make_binding({"group_address": "3/4/5", "dpt_id": "DPT1.001"}, direction="DEST")
        adapter = self._make_adapter(mock_bus, [binding])
        await adapter._on_bindings_reloaded()
        assert "3/4/5" not in adapter._ga_source_map

    @pytest.mark.asyncio
    async def test_both_direction_binding_in_source_map(self, mock_bus):
        binding = make_binding({"group_address": "4/5/6", "dpt_id": "DPT9.001"}, direction="BOTH")
        adapter = self._make_adapter(mock_bus, [binding])
        await adapter._on_bindings_reloaded()
        assert "4/5/6" in adapter._ga_source_map

    @pytest.mark.asyncio
    async def test_state_group_address_added_to_source_map(self, mock_bus):
        binding = make_binding(
            {"group_address": "1/2/3", "dpt_id": "DPT9.001", "state_group_address": "1/2/4"},
            direction="SOURCE",
        )
        adapter = self._make_adapter(mock_bus, [binding])
        await adapter._on_bindings_reloaded()
        assert "1/2/3" in adapter._ga_source_map
        assert "1/2/4" in adapter._ga_source_map

    @pytest.mark.asyncio
    async def test_respond_to_read_adds_to_respond_map(self, mock_bus):
        binding = make_binding(
            {"group_address": "5/6/7", "dpt_id": "DPT1.001", "respond_to_read": True},
            direction="SOURCE",
        )
        adapter = self._make_adapter(mock_bus, [binding])
        await adapter._on_bindings_reloaded()
        assert "5/6/7" in adapter._ga_respond_map

    @pytest.mark.asyncio
    async def test_invalid_binding_config_is_skipped(self, mock_bus):
        binding = make_binding({"bad_field": "no_group_address"}, direction="SOURCE")
        adapter = self._make_adapter(mock_bus, [binding])
        await adapter._on_bindings_reloaded()
        # No crash, map remains empty
        assert len(adapter._ga_source_map) == 0

    @pytest.mark.asyncio
    async def test_with_xknx_and_empty_ga_map_returns_early(self, mock_bus):
        from unittest.mock import MagicMock

        adapter = self._make_adapter(mock_bus, [])
        mock_xknx = MagicMock()
        mock_xknx.devices.__iter__ = MagicMock(return_value=iter([]))
        mock_xknx.devices.async_add = MagicMock()
        mock_xknx.devices.async_remove = MagicMock()
        adapter._xknx = mock_xknx
        # _ga_source_map will be empty → should return early without building sniffer
        await adapter._on_bindings_reloaded()
        mock_xknx.devices.async_add.assert_not_called()

    @pytest.mark.asyncio
    async def test_with_xknx_builds_and_registers_sniffer(self, mock_bus):
        from unittest.mock import MagicMock, patch

        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"}, direction="SOURCE")
        adapter = self._make_adapter(mock_bus, [binding])

        mock_xknx = MagicMock()
        mock_xknx.devices.__iter__ = MagicMock(return_value=iter([]))
        mock_xknx.devices.async_add = MagicMock()
        mock_xknx.devices.async_remove = MagicMock()
        adapter._xknx = mock_xknx

        with patch("obs.adapters.knx.adapter._build_sniffer") as mock_build:
            mock_sniffer = MagicMock()
            mock_build.return_value = mock_sniffer
            # Simulate device count unchanged → explicit async_add call
            mock_xknx.devices.__len__ = MagicMock(return_value=0)
            await adapter._on_bindings_reloaded()

        assert mock_build.called

    @pytest.mark.asyncio
    async def test_old_sniffer_removed_on_reload(self, mock_bus):
        from unittest.mock import MagicMock, patch

        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"}, direction="SOURCE")
        adapter = self._make_adapter(mock_bus, [binding])

        mock_xknx = MagicMock()
        mock_xknx.devices.__iter__ = MagicMock(return_value=iter([]))
        mock_xknx.devices.async_add = MagicMock()
        mock_xknx.devices.async_remove = MagicMock()
        adapter._xknx = mock_xknx

        old_sniffer = MagicMock()
        adapter._sniffer = old_sniffer

        with patch("obs.adapters.knx.adapter._build_sniffer", return_value=MagicMock()):
            await adapter._on_bindings_reloaded()

        mock_xknx.devices.async_remove.assert_called_once_with(old_sniffer)


# ---------------------------------------------------------------------------
# read() and write() — mocked xknx.telegrams
# ---------------------------------------------------------------------------


class TestKnxReadWrite:
    def _make_adapter_with_xknx(self, mock_bus):
        from unittest.mock import AsyncMock, MagicMock

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        mock_xknx = MagicMock()
        mock_xknx.telegrams = MagicMock()
        mock_xknx.telegrams.put = AsyncMock()
        adapter._xknx = mock_xknx
        return adapter, mock_xknx

    @pytest.mark.asyncio
    async def test_read_puts_telegram_on_queue(self, mock_bus):
        adapter, mock_xknx = self._make_adapter_with_xknx(mock_bus)
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        result = await adapter.read(binding)
        assert mock_xknx.telegrams.put.called
        assert result is None

    @pytest.mark.asyncio
    async def test_read_uses_state_group_address_when_set(self, mock_bus):

        adapter, mock_xknx = self._make_adapter_with_xknx(mock_bus)
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001", "state_group_address": "1/2/4"})
        await adapter.read(binding)
        telegram = mock_xknx.telegrams.put.call_args[0][0]
        assert str(telegram.destination_address) == "1/2/4"

    @pytest.mark.asyncio
    async def test_read_without_xknx_returns_none(self, mock_bus):
        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        result = await adapter.read(binding)
        assert result is None

    @pytest.mark.asyncio
    async def test_write_boolean_puts_dpt_binary(self, mock_bus):
        from xknx.dpt import DPTBinary

        adapter, mock_xknx = self._make_adapter_with_xknx(mock_bus)
        binding = make_binding({"group_address": "0/0/1", "dpt_id": "DPT1.001"})
        await adapter.write(binding, True)
        assert mock_xknx.telegrams.put.called
        telegram = mock_xknx.telegrams.put.call_args[0][0]
        assert isinstance(telegram.payload.value, DPTBinary)

    @pytest.mark.asyncio
    async def test_write_float_puts_dpt_array(self, mock_bus):
        from xknx.dpt import DPTArray

        adapter, mock_xknx = self._make_adapter_with_xknx(mock_bus)
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        await adapter.write(binding, 21.5)
        assert mock_xknx.telegrams.put.called
        telegram = mock_xknx.telegrams.put.call_args[0][0]
        assert isinstance(telegram.payload.value, DPTArray)

    @pytest.mark.asyncio
    async def test_write_without_xknx_is_noop(self, mock_bus):
        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        # Should not raise
        await adapter.write(binding, 10.0)

    @pytest.mark.asyncio
    async def test_read_exception_is_swallowed(self, mock_bus):
        from unittest.mock import AsyncMock, MagicMock

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        mock_xknx = MagicMock()
        mock_xknx.telegrams.put = AsyncMock(side_effect=RuntimeError("oops"))
        adapter._xknx = mock_xknx
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        # Should not raise
        await adapter.read(binding)

    @pytest.mark.asyncio
    async def test_write_exception_is_swallowed(self, mock_bus):
        from unittest.mock import AsyncMock, MagicMock

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        mock_xknx = MagicMock()
        mock_xknx.telegrams.put = AsyncMock(side_effect=RuntimeError("oops"))
        adapter._xknx = mock_xknx
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        await adapter.write(binding, 10.0)


# ---------------------------------------------------------------------------
# _handle_read_request
# ---------------------------------------------------------------------------


class TestHandleReadRequest:
    def _make_adapter(self, mock_bus):
        from unittest.mock import AsyncMock, MagicMock

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        mock_xknx = MagicMock()
        mock_xknx.telegrams = MagicMock()
        mock_xknx.telegrams.put = AsyncMock()
        adapter._xknx = mock_xknx
        return adapter, mock_xknx

    @pytest.mark.asyncio
    async def test_no_entries_in_respond_map_does_nothing(self, mock_bus):
        adapter, mock_xknx = self._make_adapter(mock_bus)
        await adapter._handle_read_request("1/2/3")
        mock_xknx.telegrams.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_value_getter_does_nothing(self, mock_bus):
        adapter, mock_xknx = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_respond_map["1/2/3"] = [(binding, dpt)]
        await adapter._handle_read_request("1/2/3")
        mock_xknx.telegrams.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_poor_quality_value_not_responded(self, mock_bus):
        from unittest.mock import MagicMock

        adapter, mock_xknx = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_respond_map["1/2/3"] = [(binding, dpt)]

        state = MagicMock()
        state.quality = "uncertain"
        state.value = 20.0
        adapter.set_value_getter(lambda _: state)

        await adapter._handle_read_request("1/2/3")
        mock_xknx.telegrams.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_none_state_not_responded(self, mock_bus):
        adapter, mock_xknx = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_respond_map["1/2/3"] = [(binding, dpt)]
        adapter.set_value_getter(lambda _: None)

        await adapter._handle_read_request("1/2/3")
        mock_xknx.telegrams.put.assert_not_called()

    @pytest.mark.asyncio
    async def test_good_float_value_sends_response_telegram(self, mock_bus):
        from unittest.mock import MagicMock

        adapter, mock_xknx = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_respond_map["1/2/3"] = [(binding, dpt)]

        state = MagicMock()
        state.quality = "good"
        state.value = 21.5
        adapter.set_value_getter(lambda _: state)

        await adapter._handle_read_request("1/2/3")
        mock_xknx.telegrams.put.assert_called_once()
        telegram = mock_xknx.telegrams.put.call_args[0][0]
        from xknx.telegram.apci import GroupValueResponse

        assert isinstance(telegram.payload, GroupValueResponse)

    @pytest.mark.asyncio
    async def test_good_boolean_value_sends_dpt_binary_response(self, mock_bus):
        from unittest.mock import MagicMock
        from xknx.dpt import DPTBinary

        adapter, mock_xknx = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT1.001")
        binding = make_binding({"group_address": "0/0/1", "dpt_id": "DPT1.001"})
        adapter._ga_respond_map["0/0/1"] = [(binding, dpt)]

        state = MagicMock()
        state.quality = "good"
        state.value = True
        adapter.set_value_getter(lambda _: state)

        await adapter._handle_read_request("0/0/1")
        mock_xknx.telegrams.put.assert_called_once()
        telegram = mock_xknx.telegrams.put.call_args[0][0]
        assert isinstance(telegram.payload.value, DPTBinary)

    @pytest.mark.asyncio
    async def test_no_xknx_skips_response(self, mock_bus):
        from unittest.mock import MagicMock

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        dpt = DPTRegistry.get("DPT9.001")
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_respond_map["1/2/3"] = [(binding, dpt)]

        state = MagicMock()
        state.quality = "good"
        state.value = 20.0
        adapter.set_value_getter(lambda _: state)

        # _xknx is None → should silently skip
        await adapter._handle_read_request("1/2/3")
        assert mock_bus.publish.call_count == 0


# ---------------------------------------------------------------------------
# _on_telegram — edge cases: decode error, value_formula, value_map
# ---------------------------------------------------------------------------


class TestOnTelegramEdgeCases:
    def _make_adapter(self, mock_bus):
        return KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})

    @pytest.mark.asyncio
    async def test_decode_error_publishes_uncertain_event(self, mock_bus):
        from unittest.mock import MagicMock

        adapter = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")

        # Patch decoder to raise
        bad_dpt = MagicMock()
        bad_dpt.dpt_id = "DPT9.001"
        bad_dpt.decoder = MagicMock(side_effect=ValueError("bad bytes"))

        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_source_map["1/2/3"] = [(binding, bad_dpt)]

        raw = dpt.encoder(21.5)
        telegram = Telegram(
            destination_address=GroupAddress("1/2/3"),
            payload=GroupValueWrite(DPTArray(list(raw))),
        )
        await adapter._on_telegram(telegram)

        assert mock_bus.publish.called
        event = mock_bus.publish.call_args[0][0]
        assert event.quality == "uncertain"

    @pytest.mark.asyncio
    async def test_value_formula_is_applied(self, mock_bus):
        adapter = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")
        # apply_formula uses 'x' as the variable name
        binding = make_binding(
            {"group_address": "1/2/3", "dpt_id": "DPT9.001"},
            value_formula="x * 2",
        )
        adapter._ga_source_map["1/2/3"] = [(binding, dpt)]

        raw = dpt.encoder(10.0)
        telegram = Telegram(
            destination_address=GroupAddress("1/2/3"),
            payload=GroupValueWrite(DPTArray(list(raw))),
        )
        await adapter._on_telegram(telegram)

        event = mock_bus.publish.call_args[0][0]
        assert abs(event.value - 20.0) < 0.5

    @pytest.mark.asyncio
    async def test_value_map_is_applied(self, mock_bus):
        adapter = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT1.001")
        # apply_value_map uses string keys; booleans normalise to "true"/"false"
        binding = make_binding(
            {"group_address": "0/0/1", "dpt_id": "DPT1.001"},
            value_map={"true": "ON", "false": "OFF"},
        )
        adapter._ga_source_map["0/0/1"] = [(binding, dpt)]

        telegram = Telegram(
            destination_address=GroupAddress("0/0/1"),
            payload=GroupValueWrite(DPTBinary(1)),
        )
        await adapter._on_telegram(telegram)

        event = mock_bus.publish.call_args[0][0]
        assert event.value == "ON"

    @pytest.mark.asyncio
    async def test_group_value_read_triggers_handle_read_request(self, mock_bus):
        from unittest.mock import AsyncMock, patch

        adapter = self._make_adapter(mock_bus)
        dpt = DPTRegistry.get("DPT9.001")
        binding = make_binding(
            {"group_address": "1/2/3", "dpt_id": "DPT9.001", "respond_to_read": True},
            direction="SOURCE",
        )
        adapter._ga_respond_map["1/2/3"] = [(binding, dpt)]

        telegram = Telegram(
            destination_address=GroupAddress("1/2/3"),
            payload=GroupValueRead(),
        )
        with patch.object(adapter, "_handle_read_request", new_callable=AsyncMock) as mock_hrr:
            await adapter._on_telegram(telegram)

        mock_hrr.assert_called_once_with("1/2/3")


# ---------------------------------------------------------------------------
# _build_sniffer — creates and registers a device
# ---------------------------------------------------------------------------


class TestBuildSniffer:
    def test_sniffer_is_xknx_device(self):
        from unittest.mock import MagicMock
        from xknx.devices import Device as XknxDevice

        from obs.adapters.knx.adapter import _build_sniffer

        mock_xknx = MagicMock()
        mock_xknx.devices = MagicMock()
        mock_xknx.devices.async_add = MagicMock()

        adapter = MagicMock()
        ga_map = {"1/2/3": []}

        sniffer = _build_sniffer(mock_xknx, ga_map, adapter)
        assert isinstance(sniffer, XknxDevice)

    @pytest.mark.asyncio
    async def test_sniffer_process_schedules_on_telegram(self):
        from unittest.mock import AsyncMock, MagicMock

        from obs.adapters.knx.adapter import _build_sniffer

        mock_xknx = MagicMock()
        mock_xknx.devices = MagicMock()
        mock_xknx.devices.async_add = MagicMock()

        adapter = MagicMock()
        adapter._on_telegram = AsyncMock()
        ga_map = {"1/2/3": []}

        sniffer = _build_sniffer(mock_xknx, ga_map, adapter)

        telegram = Telegram(
            destination_address=GroupAddress("1/2/3"),
            payload=GroupValueWrite(DPTBinary(1)),
        )
        result = sniffer.process(telegram)
        assert result is True
        await asyncio.sleep(0)  # let ensure_future task run

    def test_sniffer_iter_remote_values_returns_all_gas(self):
        from unittest.mock import MagicMock

        from obs.adapters.knx.adapter import _build_sniffer

        mock_xknx = MagicMock()
        mock_xknx.devices = MagicMock()
        mock_xknx.devices.async_add = MagicMock()

        adapter = MagicMock()
        ga_map = {"1/2/3": [], "4/5/6": []}

        sniffer = _build_sniffer(mock_xknx, ga_map, adapter)
        remote_values = list(sniffer._iter_remote_values())
        assert len(remote_values) == 2

    def test_passthrough_rv_from_knx_with_data(self):
        from unittest.mock import MagicMock

        from obs.adapters.knx.adapter import _build_sniffer

        mock_xknx = MagicMock()
        mock_xknx.devices = MagicMock()
        mock_xknx.devices.async_add = MagicMock()
        adapter = MagicMock()
        sniffer = _build_sniffer(mock_xknx, {"1/2/3": []}, adapter)
        rv = list(sniffer._iter_remote_values())[0]
        assert rv.from_knx([0xAB, 0xCD]) == bytes([0xAB, 0xCD])

    def test_passthrough_rv_from_knx_empty(self):
        from unittest.mock import MagicMock

        from obs.adapters.knx.adapter import _build_sniffer

        mock_xknx = MagicMock()
        mock_xknx.devices = MagicMock()
        mock_xknx.devices.async_add = MagicMock()
        adapter = MagicMock()
        sniffer = _build_sniffer(mock_xknx, {"1/2/3": []}, adapter)
        rv = list(sniffer._iter_remote_values())[0]
        assert rv.from_knx(None) == b""
        assert rv.from_knx([]) == b""

    def test_passthrough_rv_to_knx_returns_empty_list(self):
        from unittest.mock import MagicMock

        from obs.adapters.knx.adapter import _build_sniffer

        mock_xknx = MagicMock()
        mock_xknx.devices = MagicMock()
        mock_xknx.devices.async_add = MagicMock()
        adapter = MagicMock()
        sniffer = _build_sniffer(mock_xknx, {"1/2/3": []}, adapter)
        rv = list(sniffer._iter_remote_values())[0]
        assert rv.to_knx("anything") == []

    def test_passthrough_rv_unit_of_measurement_is_none(self):
        from unittest.mock import MagicMock

        from obs.adapters.knx.adapter import _build_sniffer

        mock_xknx = MagicMock()
        mock_xknx.devices = MagicMock()
        mock_xknx.devices.async_add = MagicMock()
        adapter = MagicMock()
        sniffer = _build_sniffer(mock_xknx, {"1/2/3": []}, adapter)
        rv = list(sniffer._iter_remote_values())[0]
        assert rv.unit_of_measurement is None


# ---------------------------------------------------------------------------
# _telegram_to_bytes — error path
# ---------------------------------------------------------------------------


class TestTelegramToBytesEdgeCases:
    def test_list_payload_value(self):
        from unittest.mock import MagicMock

        telegram = MagicMock()
        telegram.payload.value = [0xAB, 0xCD]
        result = _telegram_to_bytes(telegram)
        assert result == bytes([0xAB, 0xCD])

    def test_integer_payload_value(self):
        from unittest.mock import MagicMock

        telegram = MagicMock()
        # Remove the .value attribute on the inner value object so
        # the code falls through to the int branch
        inner = 0x3F
        payload_val = MagicMock(spec=[])  # no 'value' attr, no list/tuple
        payload_val.__class__ = int.__class__
        telegram.payload.value = inner
        result = _telegram_to_bytes(telegram)
        assert isinstance(result, bytes)

    def test_exception_returns_null_byte(self):
        from unittest.mock import MagicMock, PropertyMock

        telegram = MagicMock()
        type(telegram.payload).value = PropertyMock(side_effect=RuntimeError("boom"))
        result = _telegram_to_bytes(telegram)
        assert result == b"\x00"

    def test_none_value_returns_null_byte(self):
        from unittest.mock import MagicMock

        telegram = MagicMock()
        telegram.payload.value = None
        result = _telegram_to_bytes(telegram)
        assert result == b"\x00"


# ---------------------------------------------------------------------------
# Exception-path coverage for _do_connect and disconnect
# ---------------------------------------------------------------------------


class TestKnxAdapterExceptionPaths:
    @pytest.mark.asyncio
    async def test_do_connect_old_xknx_stop_exception_is_swallowed(self, mock_bus):
        from unittest.mock import AsyncMock, MagicMock, patch

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        old_xknx = MagicMock()
        old_xknx.stop = AsyncMock(side_effect=RuntimeError("stop failed"))
        adapter._xknx = old_xknx

        new_xknx = MagicMock()
        new_xknx.start = AsyncMock()
        new_xknx.stop = AsyncMock()
        new_xknx.devices = MagicMock()
        new_xknx.devices.__iter__ = MagicMock(return_value=iter([]))
        new_xknx.devices.async_add = MagicMock()

        with patch("xknx.XKNX", return_value=new_xknx):
            await adapter._do_connect()  # must not raise

        old_xknx.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_do_connect_secure_generic_exception_publishes_error(self, mock_bus):
        from unittest.mock import patch

        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={
                "connection_type": "tunneling_secure",
                "host": "192.168.1.50",
                "knxkeys_file_path": "/tmp/test.knxkeys",
                "knxkeys_password": "secret",
            },
        )
        with patch("xknx.io.SecureConfig", side_effect=RuntimeError("unexpected")):
            await adapter._do_connect()

        events = [c.args[0] for c in mock_bus.publish.call_args_list]
        error_events = [e for e in events if hasattr(e, "connected") and not e.connected]
        assert error_events

    @pytest.mark.asyncio
    async def test_disconnect_xknx_stop_exception_is_logged(self, mock_bus):
        from unittest.mock import AsyncMock, MagicMock

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        mock_xknx = MagicMock()
        mock_xknx.stop = AsyncMock(side_effect=RuntimeError("disconnect boom"))
        adapter._xknx = mock_xknx

        await adapter.disconnect()  # must not raise
        assert adapter._xknx is None


# ---------------------------------------------------------------------------
# _on_bindings_reloaded — sniffer remove/build exception paths
# ---------------------------------------------------------------------------


class TestOnBindingsReloadedExceptionPaths:
    @pytest.mark.asyncio
    async def test_sniffer_remove_exception_is_swallowed(self, mock_bus):
        from unittest.mock import MagicMock, patch

        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"}, direction="SOURCE")
        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        adapter._bindings = [binding]

        mock_xknx = MagicMock()
        mock_xknx.devices.__iter__ = MagicMock(return_value=iter([]))
        mock_xknx.devices.async_add = MagicMock()
        mock_xknx.devices.async_remove = MagicMock(side_effect=RuntimeError("remove failed"))
        adapter._xknx = mock_xknx

        old_sniffer = MagicMock()
        adapter._sniffer = old_sniffer

        with patch("obs.adapters.knx.adapter._build_sniffer", return_value=MagicMock()):
            await adapter._on_bindings_reloaded()  # must not raise

    @pytest.mark.asyncio
    async def test_sniffer_build_exception_is_logged(self, mock_bus):
        from unittest.mock import MagicMock, patch

        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"}, direction="SOURCE")
        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        adapter._bindings = [binding]

        mock_xknx = MagicMock()
        mock_xknx.devices.__iter__ = MagicMock(return_value=iter([]))
        mock_xknx.devices.async_add = MagicMock()
        mock_xknx.devices.async_remove = MagicMock()
        adapter._xknx = mock_xknx

        with patch("obs.adapters.knx.adapter._build_sniffer", side_effect=RuntimeError("build failed")):
            await adapter._on_bindings_reloaded()  # must not raise


# ---------------------------------------------------------------------------
# _on_telegram — unknown payload type and outer exception
# ---------------------------------------------------------------------------


class TestOnTelegramExceptionPaths:
    @pytest.mark.asyncio
    async def test_unknown_payload_type_returns_without_event(self, mock_bus):
        from unittest.mock import MagicMock

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        dpt = DPTRegistry.get("DPT9.001")
        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_source_map["1/2/3"] = [(binding, dpt)]

        # Payload is neither GroupValueRead, GroupValueWrite nor GroupValueResponse
        telegram = MagicMock()
        telegram.destination_address = GroupAddress("1/2/3")
        telegram.payload = MagicMock(spec=[])  # no isinstance match for any APCI type

        await adapter._on_telegram(telegram)
        mock_bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_outer_exception_in_on_telegram_is_swallowed(self, mock_bus):
        from unittest.mock import MagicMock, PropertyMock

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})

        telegram = MagicMock()
        # Make destination_address raise to trigger the outer except
        type(telegram).destination_address = PropertyMock(side_effect=RuntimeError("oops"))

        await adapter._on_telegram(telegram)  # must not raise


# ---------------------------------------------------------------------------
# _handle_read_request — exception in per-binding loop
# ---------------------------------------------------------------------------


class TestHandleReadRequestExceptionPath:
    @pytest.mark.asyncio
    async def test_exception_in_binding_loop_is_swallowed(self, mock_bus):
        from unittest.mock import AsyncMock, MagicMock

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        mock_xknx = MagicMock()
        mock_xknx.telegrams = MagicMock()
        mock_xknx.telegrams.put = AsyncMock()
        adapter._xknx = mock_xknx

        bad_dpt = MagicMock()
        bad_dpt.data_type = "FLOAT"
        bad_dpt.encoder = MagicMock(side_effect=RuntimeError("encode exploded"))

        binding = make_binding({"group_address": "1/2/3", "dpt_id": "DPT9.001"})
        adapter._ga_respond_map["1/2/3"] = [(binding, bad_dpt)]

        state = MagicMock()
        state.quality = "good"
        state.value = 21.5
        adapter.set_value_getter(lambda _: state)

        await adapter._handle_read_request("1/2/3")  # must not raise
        mock_xknx.telegrams.put.assert_not_called()


# ---------------------------------------------------------------------------
# _reconnect_loop — runs one iteration when not connected, then stops
# ---------------------------------------------------------------------------


class TestReconnectLoop:
    @pytest.mark.asyncio
    async def test_reconnect_loop_attempts_reconnect_when_not_connected(self, mock_bus):
        from unittest.mock import patch

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        adapter._connected = False
        adapter._stopped = False

        reconnect_called = []

        async def fake_do_connect():
            reconnect_called.append(1)
            adapter._stopped = True  # stop after first reconnect attempt

        async def fake_sleep(_):
            pass  # skip the 30 s wait

        with patch.object(adapter, "_do_connect", side_effect=fake_do_connect), patch("asyncio.sleep", side_effect=fake_sleep):
            await adapter._reconnect_loop()

        assert reconnect_called

    @pytest.mark.asyncio
    async def test_reconnect_loop_skips_reconnect_when_connected(self, mock_bus):
        from unittest.mock import patch

        adapter = KnxAdapter(event_bus=mock_bus, config={"host": "127.0.0.1"})
        adapter._connected = True
        adapter._stopped = False

        reconnect_called = []

        async def fake_do_connect():
            reconnect_called.append(1)  # pragma: no cover

        call_count = 0

        async def fake_sleep(_):
            nonlocal call_count
            call_count += 1
            adapter._stopped = True  # exit loop after first sleep

        with patch.object(adapter, "_do_connect", side_effect=fake_do_connect), patch("asyncio.sleep", side_effect=fake_sleep):
            await adapter._reconnect_loop()

        assert not reconnect_called  # connected → no reconnect
