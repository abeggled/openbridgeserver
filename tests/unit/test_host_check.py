"""Unit tests for the host_check logic node.

Covers:
  - _ping_host: reachable/unreachable/latency parsing/timeout/exception
  - Executor: trigger pass-through, placeholder outputs
  - Manager: ping called on trigger, skipped without trigger/host, outputs propagated
  - Manager: rising-edge semantics (sustained trigger, re-arm after False, cron)
  - Manager: downstream re-propagation for both reachable and unreachable results
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.logic.manager import LogicManager, _ping_host
from obs.logic.models import FlowData
from tests.unit.conftest import edge, make_executor, node


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


class _FakeProcess:
    """Minimal asyncio.subprocess.Process stand-in."""

    def __init__(self, returncode: int, stdout: bytes = b""):
        self.returncode = returncode
        self._stdout = stdout

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, b""

    def kill(self) -> None:
        pass

    async def wait(self) -> None:
        pass


def _patch_subprocess(returncode: int, stdout: bytes = b""):
    proc = _FakeProcess(returncode, stdout)
    return patch(
        "obs.logic.manager.asyncio.create_subprocess_exec",
        new_callable=AsyncMock,
        return_value=proc,
    )


# ===========================================================================
# _ping_host — helper function
# ===========================================================================


class TestPingHost:
    def test_reachable_host_returns_true(self):
        stdout = b"PING 192.168.1.1: 56 data bytes\n64 bytes: icmp_seq=0 time=5.2 ms\n"
        with _patch_subprocess(0, stdout):
            reachable, latency_ms = asyncio.run(_ping_host("192.168.1.1", count=1, timeout_s=1))
        assert reachable is True
        assert latency_ms == pytest.approx(5.2)

    def test_unreachable_host_returns_false(self):
        with _patch_subprocess(1, b"Request timeout for icmp_seq 0\n"):
            reachable, latency_ms = asyncio.run(_ping_host("192.168.1.99", count=1, timeout_s=1))
        assert reachable is False
        assert latency_ms is None

    def test_latency_parsed_without_space_before_ms(self):
        stdout = b"64 bytes: icmp_seq=0 time=12.3ms\n"
        with _patch_subprocess(0, stdout):
            _, latency_ms = asyncio.run(_ping_host("host", count=1, timeout_s=1))
        assert latency_ms == pytest.approx(12.3)

    def test_latency_parsed_with_equals_sign(self):
        stdout = b"64 bytes from 1.1.1.1: icmp_seq=1 ttl=55 time=23.456 ms\n"
        with _patch_subprocess(0, stdout):
            _, latency_ms = asyncio.run(_ping_host("1.1.1.1", count=1, timeout_s=1))
        assert latency_ms == pytest.approx(23.456)

    def test_latency_none_when_no_time_in_output(self):
        with _patch_subprocess(0, b"PING ok\n"):
            _, latency_ms = asyncio.run(_ping_host("host", count=1, timeout_s=1))
        assert latency_ms is None

    def test_subprocess_exception_returns_false_none(self):
        with patch(
            "obs.logic.manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=OSError("no ping binary"),
        ):
            reachable, latency_ms = asyncio.run(_ping_host("host", count=1, timeout_s=1))
        assert reachable is False
        assert latency_ms is None

    def test_timeout_returns_false_none(self):
        async def _slow_communicate():
            await asyncio.sleep(10)
            return b"", b""

        proc = _FakeProcess(returncode=0)
        proc.communicate = _slow_communicate  # type: ignore[method-assign]

        with patch(
            "obs.logic.manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            return_value=proc,
        ):
            reachable, latency_ms = asyncio.run(_ping_host("host", count=1, timeout_s=0.05))
        assert reachable is False
        assert latency_ms is None

    def test_count_clamped_to_minimum_one(self):
        with _patch_subprocess(0, b"time=1.0 ms\n") as mock_exec:
            asyncio.run(_ping_host("host", count=0, timeout_s=1))
        cmd = mock_exec.call_args.args
        assert "-c" in cmd
        c_idx = cmd.index("-c")
        assert cmd[c_idx + 1] == "1"


# ===========================================================================
# Executor: host_check node
# ===========================================================================


class TestHostCheckExecutor:
    def test_placeholder_outputs_when_triggered(self):
        n = node("hc", "host_check", {"host": "192.168.1.1"})
        exc = make_executor([n])
        out = exc.execute({"hc": {"trigger": True}})["hc"]
        assert out["_trigger"] is True
        assert out["reachable"] is False
        assert out["latency_ms"] is None

    def test_placeholder_outputs_when_not_triggered(self):
        n = node("hc", "host_check", {"host": "192.168.1.1"})
        exc = make_executor([n])
        out = exc.execute({"hc": {"trigger": False}})["hc"]
        assert out["_trigger"] is False
        assert out["reachable"] is False
        assert out["latency_ms"] is None


# ===========================================================================
# Manager: host_check dispatch
# ===========================================================================


def _run_manager(host: str, trigger: bool, ping_return: tuple = (True, 5.0)):
    manager = _make_manager()
    flow = _flow([node("hc", "host_check", {"host": host, "timeout_s": 1, "count": 1})])
    graph_id = "g"
    manager._graphs[graph_id] = ("test", True, flow)
    manager._node_state[graph_id] = {}

    with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
        with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=ping_return) as mock_ping:
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": trigger}}))
    return outputs, mock_ping


class TestHostCheckManager:
    def test_ping_called_when_triggered(self):
        _, mock_ping = _run_manager("192.168.1.1", trigger=True)
        mock_ping.assert_awaited_once()

    def test_ping_not_called_when_not_triggered(self):
        _, mock_ping = _run_manager("192.168.1.1", trigger=False)
        mock_ping.assert_not_awaited()

    def test_reachable_true_set_in_output(self):
        outputs, _ = _run_manager("192.168.1.1", trigger=True, ping_return=(True, 5.0))
        assert outputs["hc"]["reachable"] is True

    def test_latency_ms_set_in_output(self):
        outputs, _ = _run_manager("192.168.1.1", trigger=True, ping_return=(True, 5.0))
        assert outputs["hc"]["latency_ms"] == pytest.approx(5.0)

    def test_reachable_false_set_in_output(self):
        outputs, _ = _run_manager("192.168.1.1", trigger=True, ping_return=(False, None))
        assert outputs["hc"]["reachable"] is False
        assert outputs["hc"]["latency_ms"] is None

    def test_missing_host_skips_ping(self):
        _, mock_ping = _run_manager("", trigger=True)
        mock_ping.assert_not_awaited()

    def test_whitespace_only_host_skips_ping(self):
        _, mock_ping = _run_manager("   ", trigger=True)
        mock_ping.assert_not_awaited()

    def test_ping_exception_does_not_raise(self):
        manager = _make_manager()
        flow = _flow([node("hc", "host_check", {"host": "192.168.1.1"})])
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, side_effect=OSError("fail")):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))

        assert outputs["hc"]["reachable"] is False

    def test_ping_called_with_correct_host(self):
        _, mock_ping = _run_manager("myhost.local", trigger=True)
        call_args = mock_ping.call_args
        assert call_args.args[0] == "myhost.local"

    def test_ping_called_with_timeout_and_count(self):
        manager = _make_manager()
        flow = _flow([node("hc", "host_check", {"host": "h", "timeout_s": 3, "count": 2})])
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping:
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))

        call_args = mock_ping.call_args
        assert call_args.args[1] == 2  # count
        assert call_args.args[2] == 3.0  # timeout_s


# ===========================================================================
# Manager: rising-edge trigger semantics
# ===========================================================================


class TestHostCheckRisingEdge:
    def _make_flow(self) -> FlowData:
        return _flow([node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1})])

    def _exec(self, manager, flow, trigger: bool, mock_ping):
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            return asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": trigger}}))

    def test_sustained_trigger_pings_only_once(self):
        manager = _make_manager()
        flow = self._make_flow()
        with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping:
            self._exec(manager, flow, True, mock_ping)
            self._exec(manager, flow, True, mock_ping)
            self._exec(manager, flow, True, mock_ping)
        assert mock_ping.await_count == 1

    def test_pings_again_after_dropping_to_false(self):
        manager = _make_manager()
        flow = self._make_flow()
        with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping:
            self._exec(manager, flow, True, mock_ping)  # rising edge → ping
            self._exec(manager, flow, False, mock_ping)  # falling → no ping
            self._exec(manager, flow, True, mock_ping)  # rising again → ping
        assert mock_ping.await_count == 2

    def test_initial_false_then_true_fires(self):
        manager = _make_manager()
        flow = self._make_flow()
        with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping:
            self._exec(manager, flow, False, mock_ping)  # no ping
            self._exec(manager, flow, True, mock_ping)  # rising edge → ping
        assert mock_ping.await_count == 1

    def test_cron_retriggers_on_each_tick(self):
        nodes = [
            node("cron", "timer_cron", {"cron": "* * * * *"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(nodes, [edge("cron", "hc", "trigger", "trigger")])

        manager = _make_manager()
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        cron_overrides = {"cron": {"trigger": True}}
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping:
                asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))

        assert mock_ping.await_count == 3


# ===========================================================================
# Manager: downstream re-propagation
# ===========================================================================


class TestHostCheckDownstreamPropagation:
    def test_downstream_receives_reachable_true(self):
        nodes = [
            node("hc", "host_check", {"host": "192.168.1.1"}),
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("gate", "and", {"input_count": 2}),
        ]
        edges_list = [
            edge("hc", "gate", "reachable", "in1"),
            edge("cv", "gate", "value", "in2"),
        ]
        flow = _flow(nodes, edges_list)

        manager = _make_manager()
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))

        assert outputs["hc"]["reachable"] is True
        assert outputs["gate"]["out"] is True

    def test_downstream_receives_reachable_false(self):
        """Unreachable result must ALSO re-propagate so downstream sees False."""
        nodes = [
            node("hc", "host_check", {"host": "192.168.1.1"}),
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("gate", "and", {"input_count": 2}),
        ]
        edges_list = [
            edge("hc", "gate", "reachable", "in1"),
            edge("cv", "gate", "value", "in2"),
        ]
        flow = _flow(nodes, edges_list)

        manager = _make_manager()
        graph_id = "g2"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(False, None)):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))

        assert outputs["hc"]["reachable"] is False
        assert outputs["gate"]["out"] is False

    def test_unrelated_node_not_overwritten(self):
        nodes = [
            node("cv_true", "const_value", {"value": "true", "data_type": "bool"}),
            node("cv_false", "const_value", {"value": "false", "data_type": "bool"}),
            node("hc", "host_check", {"host": "192.168.1.1"}),
            node("unrelated", "and", {"input_count": 2}),
        ]
        edges_list = [
            edge("cv_true", "hc", "value", "trigger"),
            edge("cv_false", "unrelated", "value", "in1"),
            edge("cv_true", "unrelated", "value", "in2"),
        ]
        flow = _flow(nodes, edges_list)

        manager = _make_manager()
        graph_id = "g3"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))

        assert outputs["hc"]["reachable"] is True
        assert outputs["unrelated"]["out"] is False
