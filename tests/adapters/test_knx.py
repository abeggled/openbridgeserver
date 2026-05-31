"""Unit tests for the KNX adapter.

Tests that require xknx (Telegram objects) are skipped automatically if
xknx is not installed — so the test suite stays green on environments
without the optional dependency.
"""

from __future__ import annotations
import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from xknx.core.connection_state import XknxConnectionState
from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import Telegram
from xknx.telegram.address import GroupAddress
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite

from obs.adapters.knx.adapter import (
    KnxAdapter,
    KnxAdapterConfig,
    KnxBindingConfig,
    _knx_connect_error_detail,
    _secure_config_from_keyfile,
    _telegram_to_bytes,
)
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
# _knx_connect_error_detail — Docker bridge / interface resolution hint
# Issue #393
# ---------------------------------------------------------------------------


class TestKnxConnectErrorDetail:
    def test_gateway_unreachable_adds_docker_hint(self):
        """'Could not fetch gateway info' gets the Docker bridge hint appended."""
        exc = Exception("Could not fetch gateway info from 192.168.1.152:3671")
        detail = _knx_connect_error_detail(exc)
        assert "192.168.1.152" in detail
        assert "Docker" in detail or "docker" in detail
        assert "network_mode" in detail

    def test_did_not_respond_adds_docker_hint(self):
        """'did not respond in time' also triggers the Docker hint."""
        exc = Exception("KNX bus did not respond in time (2 secs) to request of type 'DescriptionQuery'")
        detail = _knx_connect_error_detail(exc)
        assert "Docker" in detail or "docker" in detail
        assert "network_mode" in detail

    def test_description_query_case_insensitive(self):
        exc = Exception("DescriptionQuery timeout for 192.168.1.100")
        detail = _knx_connect_error_detail(exc)
        assert "network_mode" in detail

    def test_no_more_connections_adds_hint(self):
        """E_NO_MORE_CONNECTIONS gets the 'all tunnel slots occupied' hint."""
        inner = Exception("ConnectRequest failed. Status code: ErrorCode.E_NO_MORE_CONNECTIONS")
        exc = Exception("Tunnel connection could not be established")
        exc.__cause__ = inner
        detail = _knx_connect_error_detail(exc)
        assert "E_NO_MORE_CONNECTIONS" in detail
        assert "Tunnel-Verbindungsplätze" in detail or "belegt" in detail

    def test_unrelated_error_unchanged(self):
        """Errors not matching any known pattern are returned unchanged."""
        exc = Exception("Connection refused by host")
        detail = _knx_connect_error_detail(exc)
        assert detail == "Connection refused by host"
        assert "Docker" not in detail

    def test_detail_starts_with_original_message(self):
        """The original xknx message must appear verbatim at the start."""
        original = "Could not fetch gateway info from 10.0.0.1:3671"
        exc = Exception(original)
        detail = _knx_connect_error_detail(exc)
        assert detail.startswith(original)

    @pytest.mark.asyncio
    async def test_do_connect_publishes_docker_hint_on_gateway_timeout(self, mock_bus, monkeypatch):
        """_do_connect() emits the Docker hint in the status detail on gateway timeout."""
        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={"connection_type": "tunneling_secure", "host": "192.168.1.152"},
        )

        class _FakeXKNX:
            async def start(self):
                raise Exception("Could not fetch gateway info from 192.168.1.152:3671")

            async def stop(self):
                pass

        import xknx as _xknx_mod

        monkeypatch.setattr(_xknx_mod, "XKNX", lambda **kw: _FakeXKNX())

        await adapter._do_connect()

        events = [c.args[0] for c in mock_bus.publish.call_args_list]
        error_events = [e for e in events if hasattr(e, "connected") and not e.connected]
        assert error_events, "expected at least one error status event"
        assert "network_mode" in error_events[-1].detail


# ---------------------------------------------------------------------------
# _secure_config_from_keyfile — bypasses UDP DescriptionRequest (Issue #393)
# ---------------------------------------------------------------------------


class TestSecureConfigFromKeyfile:
    """_secure_config_from_keyfile() must extract credentials from a .knxkeys file
    so that xknx receives explicit user_id/user_password/device_auth and skips
    the internal UDP DescriptionRequest that fails in Docker bridge networks."""

    def _make_keyring_mock(self, *, user_id: int = 2, individual_address: str = "1.1.100") -> Any:
        from unittest.mock import MagicMock
        from xknx.secure.keyring import InterfaceType

        iface = MagicMock()
        iface.type = InterfaceType.TUNNELING
        iface.individual_address = MagicMock()
        iface.individual_address.__str__ = lambda self, ia=individual_address: ia
        iface.individual_address.__eq__ = lambda self, other: str(self) == str(other)
        iface.user_id = user_id
        iface.decrypted_password = "tunnelpass"
        iface.decrypted_authentication = "devauth"

        keyring = MagicMock()
        keyring.interfaces = [iface]
        keyring.get_tunnel_interface_by_individual_address = lambda ia: iface if str(ia) == individual_address else None
        keyring.backbone = None
        return keyring, iface

    def _make_backbone_keyring_mock(self) -> Any:
        from unittest.mock import MagicMock

        backbone = MagicMock()
        backbone.decrypted_key = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")

        keyring = MagicMock()
        keyring.interfaces = []
        keyring.backbone = backbone
        return keyring

    def test_tunneling_secure_returns_explicit_credentials(self, monkeypatch):
        """For tunneling_secure the result must carry user_id, user_password, device_auth
        AND the keyring object (for data_secure_init) but NOT a keyfile path."""
        from xknx.io import SecureConfig

        keyring, iface = self._make_keyring_mock()
        monkeypatch.setattr(
            "obs.adapters.knx.adapter.sync_load_keyring",
            lambda path, pw: keyring,
        )

        result = _secure_config_from_keyfile(
            "/data/knxkeys/test.knxkeys",
            "secret",
            "tunneling_secure",
            "1.1.100",
        )

        assert isinstance(result, SecureConfig)
        assert result.user_id == 2
        assert result.user_password == "tunnelpass"
        assert result.device_authentication_password == "devauth"
        # keyring must be present so xknx can do data_secure_init
        assert result.keyring is keyring
        # but no file path — that would re-trigger UDP DescriptionRequest
        assert result.knxkeys_file_path is None

    def test_tunneling_secure_no_keyfile_path_in_result(self, monkeypatch):
        """The returned SecureConfig must NOT contain the keyfile path — that would
        re-trigger xknx's UDP DescriptionRequest on the first connection attempt."""
        keyring, _ = self._make_keyring_mock()
        monkeypatch.setattr(
            "obs.adapters.knx.adapter.sync_load_keyring",
            lambda path, pw: keyring,
        )

        result = _secure_config_from_keyfile("/data/knxkeys/test.knxkeys", "pw", "tunneling_secure", "1.1.100")
        assert result is not None
        assert result.knxkeys_file_path is None

    def test_tunneling_secure_fallback_when_ia_not_found(self, monkeypatch):
        """If the individual_address isn't in the keyfile, fall back to the first tunnel."""
        keyring, iface = self._make_keyring_mock(individual_address="1.1.100")
        monkeypatch.setattr(
            "obs.adapters.knx.adapter.sync_load_keyring",
            lambda path, pw: keyring,
        )

        result = _secure_config_from_keyfile("/data/knxkeys/test.knxkeys", "pw", "tunneling_secure", "9.9.9")
        # Should still return a SecureConfig using the first available interface
        assert result is not None
        assert result.user_id == iface.user_id

    def test_tunneling_secure_returns_none_when_no_tunnels(self, monkeypatch):
        """Returns None when keyfile has no tunneling interfaces."""
        from unittest.mock import MagicMock

        keyring = MagicMock()
        keyring.interfaces = []
        keyring.backbone = None
        keyring.get_tunnel_interface_by_individual_address.return_value = None
        monkeypatch.setattr(
            "obs.adapters.knx.adapter.sync_load_keyring",
            lambda path, pw: keyring,
        )

        result = _secure_config_from_keyfile("/data/knxkeys/test.knxkeys", "pw", "tunneling_secure", "1.1.100")
        assert result is None

    def test_routing_secure_extracts_backbone_key(self, monkeypatch):
        """For routing_secure the result carries a hex backbone_key."""
        from xknx.io import SecureConfig

        keyring = self._make_backbone_keyring_mock()
        monkeypatch.setattr(
            "obs.adapters.knx.adapter.sync_load_keyring",
            lambda path, pw: keyring,
        )

        result = _secure_config_from_keyfile("/data/knxkeys/test.knxkeys", "pw", "routing_secure", "1.1.255")
        assert isinstance(result, SecureConfig)
        # SecureConfig.backbone_key stores bytes internally (converts from hex)
        expected_bytes = bytes.fromhex("0102030405060708090a0b0c0d0e0f10")
        assert result.backbone_key == expected_bytes
        # keyring must be passed for data_secure_init
        assert result.keyring is keyring

    def test_routing_secure_returns_none_when_no_backbone(self, monkeypatch):
        """Returns None when keyfile has no backbone."""
        from unittest.mock import MagicMock

        keyring = MagicMock()
        keyring.interfaces = []
        keyring.backbone = None
        monkeypatch.setattr(
            "obs.adapters.knx.adapter.sync_load_keyring",
            lambda path, pw: keyring,
        )

        result = _secure_config_from_keyfile("/data/knxkeys/test.knxkeys", "pw", "routing_secure", "1.1.255")
        assert result is None

    @pytest.mark.asyncio
    async def test_do_connect_tunneling_secure_uses_explicit_credentials(self, mock_bus, monkeypatch):
        """_do_connect() with keyfile calls _secure_config_from_keyfile — no keyfile
        path reaches xknx, so no UDP DescriptionRequest is triggered."""
        from unittest.mock import MagicMock
        from xknx.io import SecureConfig

        # Return a SecureConfig with explicit credentials (no keyfile path)
        explicit_sc = SecureConfig(
            device_authentication_password="devauth",
            user_id=2,
            user_password="tunnelpass",
        )
        monkeypatch.setattr(
            "obs.adapters.knx.adapter._secure_config_from_keyfile",
            lambda *a, **kw: explicit_sc,
        )

        captured_conn_cfg = {}

        class _FakeXKNX:
            def __init__(self, **kw):
                captured_conn_cfg.update(kw)

            async def start(self):
                pass

            async def stop(self):
                pass

            devices = MagicMock()
            devices.__iter__ = lambda self: iter([])
            devices.__len__ = lambda self: 0
            telegrams = MagicMock()

        import xknx as _xknx_mod

        monkeypatch.setattr(_xknx_mod, "XKNX", lambda **kw: _FakeXKNX(**kw))

        adapter = KnxAdapter(
            event_bus=mock_bus,
            config={
                "connection_type": "tunneling_secure",
                "host": "192.168.1.152",
                "knxkeys_file_path": "/data/knxkeys/test.knxkeys",
                "knxkeys_password": "secret",
                "individual_address": "1.1.100",
            },
        )
        await adapter._do_connect()

        # Verify the connection_config passed to xknx has NO keyfile path
        conn_cfg = captured_conn_cfg.get("connection_config")
        assert conn_cfg is not None
        sc = conn_cfg.secure_config
        assert sc is not None
        assert sc.knxkeys_file_path is None
        assert sc.user_id == 2
