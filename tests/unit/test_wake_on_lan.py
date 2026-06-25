"""Unit tests for the wake_on_lan logic node.

Covers:
  - _send_wol_packet: magic packet construction, MAC normalization, invalid MAC rejection
  - Executor: trigger pass-through, no-trigger suppression
  - Manager: packet sent on trigger, skipped without trigger, skipped without MAC
"""

from __future__ import annotations

import asyncio
import socket
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.logic.manager import LogicManager, _send_wol_packet
from obs.logic.models import FlowData
from tests.unit.conftest import make_executor, node


def _flow(nodes: list[dict], edges: list[dict] | None = None) -> FlowData:
    return FlowData.model_validate({"nodes": nodes, "edges": edges or []})


def _make_manager() -> LogicManager:
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.execute_and_commit = AsyncMock()
    event_bus = AsyncMock()
    registry = MagicMock()
    registry.get_value.return_value = None
    return LogicManager(db, event_bus, registry)


# ===========================================================================
# _send_wol_packet — magic packet construction
# ===========================================================================


class TestSendWolPacket:
    def _capture_packet(self, mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> bytes:
        """Call _send_wol_packet and capture what was sent via a mocked socket."""
        sent = []

        class FakeSock:
            def setsockopt(self, *a):
                pass

            def sendto(self, data, addr):
                sent.append((data, addr))

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        with patch("obs.logic.manager.socket.socket", return_value=FakeSock()):
            _send_wol_packet(mac, broadcast, port)

        assert len(sent) == 1
        return sent[0][0]

    def test_magic_packet_starts_with_six_ff_bytes(self):
        pkt = self._capture_packet("AA:BB:CC:DD:EE:FF")
        assert pkt[:6] == b"\xff" * 6

    def test_magic_packet_repeats_mac_16_times(self):
        pkt = self._capture_packet("AA:BB:CC:DD:EE:FF")
        mac_bytes = bytes.fromhex("AABBCCDDEEFF")
        assert pkt[6:] == mac_bytes * 16

    def test_total_packet_length(self):
        pkt = self._capture_packet("AA:BB:CC:DD:EE:FF")
        assert len(pkt) == 6 + 6 * 16  # 102 bytes

    def test_mac_with_dashes_normalized(self):
        pkt = self._capture_packet("AA-BB-CC-DD-EE-FF")
        assert pkt[6:12] == bytes.fromhex("AABBCCDDEEFF")

    def test_mac_with_dots_normalized(self):
        pkt = self._capture_packet("AABB.CCDD.EEFF")
        assert pkt[6:12] == bytes.fromhex("AABBCCDDEEFF")

    def test_mac_lowercase_normalized(self):
        pkt = self._capture_packet("aa:bb:cc:dd:ee:ff")
        assert pkt[6:12] == bytes.fromhex("AABBCCDDEEFF")

    def test_broadcast_address_used(self):
        sent = []

        class FakeSock:
            def setsockopt(self, *a):
                pass

            def sendto(self, data, addr):
                sent.append(addr)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        with patch("obs.logic.manager.socket.socket", return_value=FakeSock()):
            _send_wol_packet("AA:BB:CC:DD:EE:FF", "192.168.1.255", 7)

        assert sent[0] == ("192.168.1.255", 7)

    def test_invalid_mac_too_short_raises(self):
        with pytest.raises(ValueError, match="Invalid MAC address"):
            _send_wol_packet("AA:BB:CC:DD:EE", "255.255.255.255", 9)

    def test_invalid_mac_non_hex_raises(self):
        with pytest.raises(ValueError, match="Invalid MAC address"):
            _send_wol_packet("ZZ:BB:CC:DD:EE:FF", "255.255.255.255", 9)

    def test_empty_mac_raises(self):
        with pytest.raises(ValueError, match="Invalid MAC address"):
            _send_wol_packet("", "255.255.255.255", 9)

    def test_socket_uses_broadcast_sockopt(self):
        sockopt_calls = []

        class FakeSock:
            def setsockopt(self, level, optname, value):
                sockopt_calls.append((level, optname, value))

            def sendto(self, data, addr):
                pass

            def __enter__(self):
                return self

            def __exit__(self, *a):
                pass

        with patch("obs.logic.manager.socket.socket", return_value=FakeSock()):
            _send_wol_packet("AA:BB:CC:DD:EE:FF", "255.255.255.255", 9)

        assert any(optname == socket.SO_BROADCAST for _, optname, _ in sockopt_calls)


# ===========================================================================
# Executor: wake_on_lan node
# ===========================================================================


class TestWakeOnLanExecutor:
    def test_trigger_true_passes_through(self):
        n = node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"})
        exc = make_executor([n])
        out = exc.execute({"wol": {"trigger": True}})["wol"]
        assert out["_trigger"] is True
        assert out["sent"] is False

    def test_trigger_false_suppresses(self):
        n = node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"})
        exc = make_executor([n])
        out = exc.execute({"wol": {"trigger": False}})["wol"]
        assert out["_trigger"] is False

    def test_no_trigger_input_defaults_to_false(self):
        n = node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"})
        exc = make_executor([n])
        out = exc.execute({})["wol"]
        assert out["_trigger"] is False

    def test_truthy_non_bool_trigger(self):
        n = node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"})
        exc = make_executor([n])
        out = exc.execute({"wol": {"trigger": 1}})["wol"]
        assert out["_trigger"] is True

    def test_sent_is_false_in_executor(self):
        n = node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"})
        exc = make_executor([n])
        out = exc.execute({"wol": {"trigger": True}})["wol"]
        assert out["sent"] is False


# ===========================================================================
# Manager: wake_on_lan packet dispatch
# ===========================================================================


class TestWakeOnLanManager:
    def _run_manager(self, mac: str, trigger: bool, broadcast: str = "255.255.255.255", port: int = 9):
        manager = _make_manager()
        flow = _flow(
            [
                node(
                    "wol",
                    "wake_on_lan",
                    {"mac_address": mac, "broadcast_ip": broadcast, "port": port},
                ),
            ],
        )
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                outputs = asyncio.run(
                    manager._execute_graph(
                        graph_id,
                        "test",
                        flow,
                        {"wol": {"trigger": trigger}},
                    ),
                )
        return outputs, mock_to_thread

    def test_packet_sent_when_triggered(self):
        outputs, mock_to_thread = self._run_manager("AA:BB:CC:DD:EE:FF", trigger=True)
        mock_to_thread.assert_awaited_once()
        assert outputs["wol"]["sent"] is True

    def test_packet_not_sent_when_not_triggered(self):
        outputs, mock_to_thread = self._run_manager("AA:BB:CC:DD:EE:FF", trigger=False)
        mock_to_thread.assert_not_awaited()
        assert outputs["wol"]["sent"] is False

    def test_custom_broadcast_and_port_passed_to_send(self):
        _, mock_to_thread = self._run_manager(
            "AA:BB:CC:DD:EE:FF",
            trigger=True,
            broadcast="192.168.1.255",
            port=7,
        )
        mock_to_thread.assert_awaited_once()
        call_args = mock_to_thread.call_args
        assert call_args.args[1] == "AA:BB:CC:DD:EE:FF"
        assert call_args.args[2] == "192.168.1.255"
        assert call_args.args[3] == 7

    def test_missing_mac_skips_send(self):
        manager = _make_manager()
        flow = _flow([node("wol", "wake_on_lan", {"mac_address": ""})])
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                asyncio.run(
                    manager._execute_graph(
                        graph_id,
                        "test",
                        flow,
                        {"wol": {"trigger": True}},
                    ),
                )

        mock_to_thread.assert_not_awaited()

    def test_send_failure_does_not_raise(self):
        manager = _make_manager()
        flow = _flow([node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"})])
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock, side_effect=OSError("network error")):
                outputs = asyncio.run(
                    manager._execute_graph(
                        graph_id,
                        "test",
                        flow,
                        {"wol": {"trigger": True}},
                    ),
                )

        assert outputs["wol"]["sent"] is False

    def _run_manager_raw(self, data: dict, trigger: bool = True):
        """Run manager with raw node data dict (for edge-case config testing)."""
        manager = _make_manager()
        flow = _flow([node("wol", "wake_on_lan", data)])
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                outputs = asyncio.run(
                    manager._execute_graph(
                        graph_id,
                        "test",
                        flow,
                        {"wol": {"trigger": trigger}},
                    ),
                )
        return outputs, mock_to_thread

    def test_whitespace_only_broadcast_defaults_to_global(self):
        _, mock_to_thread = self._run_manager_raw({"mac_address": "AA:BB:CC:DD:EE:FF", "broadcast_ip": "   "})
        call_args = mock_to_thread.call_args
        assert call_args.args[2] == "255.255.255.255"

    def test_port_zero_invalid_falls_back_to_nine(self):
        _, mock_to_thread = self._run_manager_raw({"mac_address": "AA:BB:CC:DD:EE:FF", "port": 0})
        mock_to_thread.assert_not_awaited()

    def test_port_out_of_range_falls_back_to_nine(self):
        _, mock_to_thread = self._run_manager_raw({"mac_address": "AA:BB:CC:DD:EE:FF", "port": 99999})
        mock_to_thread.assert_not_awaited()

    def test_non_numeric_port_does_not_raise(self):
        outputs, mock_to_thread = self._run_manager_raw({"mac_address": "AA:BB:CC:DD:EE:FF", "port": "abc"})
        mock_to_thread.assert_not_awaited()
        assert outputs["wol"]["sent"] is False

    def test_fractional_port_is_rejected(self):
        _, mock_to_thread = self._run_manager_raw({"mac_address": "AA:BB:CC:DD:EE:FF", "port": 9.8})
        mock_to_thread.assert_not_awaited()

    def test_integer_float_port_is_accepted(self):
        _, mock_to_thread = self._run_manager_raw({"mac_address": "AA:BB:CC:DD:EE:FF", "port": 9.0})
        mock_to_thread.assert_awaited_once()

    def test_invalid_broadcast_ip_is_rejected(self):
        _, mock_to_thread = self._run_manager_raw({"mac_address": "AA:BB:CC:DD:EE:FF", "broadcast_ip": "not-an-ip"})
        mock_to_thread.assert_not_awaited()


# ===========================================================================
# Manager: wake_on_lan rising-edge trigger semantics
# ===========================================================================


class TestWakeOnLanRisingEdge:
    """Packet must only be sent on the False→True edge, not on sustained True."""

    def _make_flow(self) -> "FlowData":
        return _flow([node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"})])

    def _exec(self, manager, flow, trigger: bool, mock_to_thread):
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            return asyncio.run(manager._execute_graph(graph_id, "test", flow, {"wol": {"trigger": trigger}}))

    def test_sustained_trigger_sends_only_once(self):
        manager = _make_manager()
        flow = self._make_flow()
        with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            self._exec(manager, flow, True, mock_to_thread)
            self._exec(manager, flow, True, mock_to_thread)
            self._exec(manager, flow, True, mock_to_thread)
        assert mock_to_thread.await_count == 1

    def test_trigger_fires_again_after_dropping_to_false(self):
        manager = _make_manager()
        flow = self._make_flow()
        with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            self._exec(manager, flow, True, mock_to_thread)  # rising edge → send
            self._exec(manager, flow, False, mock_to_thread)  # falling edge → no send
            self._exec(manager, flow, True, mock_to_thread)  # rising edge again → send
        assert mock_to_thread.await_count == 2

    def test_initial_false_then_true_fires(self):
        manager = _make_manager()
        flow = self._make_flow()
        with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
            self._exec(manager, flow, False, mock_to_thread)  # no send
            self._exec(manager, flow, True, mock_to_thread)  # rising edge → send
        assert mock_to_thread.await_count == 1

    def test_cron_execution_retrigggers_on_each_tick(self):
        """Each timer_cron tick must send a packet even though trigger stays True."""
        from tests.unit.conftest import edge

        nodes = [
            node("cron", "timer_cron", {"cron": "0 7 * * *"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
        ]
        edges_list = [edge("cron", "wol", "trigger", "trigger")]
        flow = _flow(nodes, edges_list)

        manager = _make_manager()
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        # Simulate three cron ticks — overrides use the cron node id as the key,
        # exactly as _cron_loop does: overrides = {node_id: {"trigger": True}}
        cron_overrides = {"cron": {"trigger": True}}
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))

        assert mock_to_thread.await_count == 3


# ===========================================================================
# Manager: wake_on_lan downstream re-propagation
# ===========================================================================


class TestWakeOnLanDownstreamPropagation:
    """sent=True must propagate to downstream nodes after the WoL packet is sent."""

    def test_downstream_and_gate_receives_sent_true(self):
        """Graph: wol.sent → gate.in1, const_value(True) → gate.in2
        After the second-pass fix, gate.out must be True when WoL succeeds.
        """
        from tests.unit.conftest import edge

        nodes = [
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("gate", "and", {"input_count": 2}),
        ]
        edges = [
            edge("wol", "gate", "sent", "in1"),
            edge("cv", "gate", "value", "in2"),
        ]
        flow = _flow(nodes, edges)

        manager = _make_manager()
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                outputs = asyncio.run(
                    manager._execute_graph(
                        graph_id,
                        "test",
                        flow,
                        {"wol": {"trigger": True}},
                    ),
                )

        assert outputs["wol"]["sent"] is True
        assert outputs["gate"]["out"] is True

    def test_downstream_gate_stays_false_on_wol_failure(self):
        """When WoL send fails, sent=False must NOT re-propagate (gate stays False)."""
        from tests.unit.conftest import edge

        nodes = [
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("gate", "and", {"input_count": 2}),
        ]
        edges = [
            edge("wol", "gate", "sent", "in1"),
            edge("cv", "gate", "value", "in2"),
        ]
        flow = _flow(nodes, edges)

        manager = _make_manager()
        graph_id = "g2"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock, side_effect=OSError("net err")):
                outputs = asyncio.run(
                    manager._execute_graph(
                        graph_id,
                        "test",
                        flow,
                        {"wol": {"trigger": True}},
                    ),
                )

        assert outputs["wol"]["sent"] is False
        assert outputs["gate"]["out"] is False

    def test_unrelated_node_output_not_overwritten_by_second_pass(self):
        """A node not downstream of WoL must keep its first-pass output value.

        Graph: cv_true → wol.trigger (wol has no outgoing edges)
               cv_false → unrelated_gate.in1, cv_true → unrelated_gate.in2
        The unrelated_gate is independent of WoL; its first-pass out=False
        must not be replaced by a second-pass placeholder.
        """
        from tests.unit.conftest import edge

        nodes = [
            node("cv_true", "const_value", {"value": "true", "data_type": "bool"}),
            node("cv_false", "const_value", {"value": "false", "data_type": "bool"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("unrelated_gate", "and", {"input_count": 2}),
        ]
        edges_list = [
            edge("cv_true", "wol", "value", "trigger"),
            edge("cv_false", "unrelated_gate", "value", "in1"),
            edge("cv_true", "unrelated_gate", "value", "in2"),
        ]
        flow = _flow(nodes, edges_list)

        manager = _make_manager()
        graph_id = "g3"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"wol": {"trigger": True}}))

        # WoL fired but has no outgoing edges; unrelated_gate must keep its
        # first-pass value (False AND True = False), not be overwritten.
        assert outputs["wol"]["sent"] is True
        assert outputs["unrelated_gate"]["out"] is False
