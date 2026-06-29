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


class _MockResponse:
    def __init__(self, status_code: int = 200, json_data: object | None = None, text: str = ""):
        self.status_code = status_code
        self._json_data = json_data if json_data is not None else {"ok": True}
        self.text = text or '{"ok": true}'

    def json(self):
        return self._json_data


def _patch_api_success():
    patcher = patch("obs.logic.manager.httpx.AsyncClient")
    mock_client_cls = patcher.start()
    mock_client = AsyncMock()
    mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
    mock_client.request = AsyncMock(return_value=_MockResponse(200))
    return patcher


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

    def test_count_and_timeout_clamped_to_maximums(self):
        with _patch_subprocess(0, b"time=1.0 ms\n") as mock_exec:
            asyncio.run(_ping_host("host", count=999, timeout_s=999))
        cmd = mock_exec.call_args.args
        assert cmd[cmd.index("-c") + 1] == "10"
        assert cmd[cmd.index("-W") + 1] == "30"

    def test_macos_uses_per_packet_wait_flag(self):
        with patch("sys.platform", "darwin"):
            with _patch_subprocess(0, b"time=1.0 ms\n") as mock_exec:
                asyncio.run(_ping_host("host", count=2, timeout_s=2))
        cmd = mock_exec.call_args.args
        assert "-W" in cmd
        assert "-t" not in cmd
        assert cmd[cmd.index("-W") + 1] == "2000"


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


# ===========================================================================
# _ping_host: asyncio watchdog scales with count
# ===========================================================================


class TestPingHostTimeoutScaling:
    def test_asyncio_timeout_includes_count_factor(self):
        """With count=3 and timeout_s=2.0, asyncio.wait_for gets timeout=8.0 (2*3+2)."""
        captured: list[float] = []
        real_wait_for = asyncio.wait_for

        async def _fake_wait_for(coro, timeout):
            captured.append(timeout)
            return await real_wait_for(coro, timeout=30)

        proc = _FakeProcess(0, b"time=1.0 ms\n")
        with (
            patch("obs.logic.manager.asyncio.create_subprocess_exec", new_callable=AsyncMock, return_value=proc),
            patch("obs.logic.manager.asyncio.wait_for", side_effect=_fake_wait_for),
        ):
            asyncio.run(_ping_host("host", count=3, timeout_s=2.0))

        assert captured[0] == pytest.approx(8.0)  # 2.0 * 3 + 2


# ===========================================================================
# Manager: sustained trigger restores last result (Fix 6)
# ===========================================================================


class TestHostCheckSustainedTrigger:
    def _make_flow(self) -> FlowData:
        return _flow([node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1})])

    def _exec(self, manager, flow, trigger: bool):
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            return asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": trigger}}))

    def test_sustained_trigger_returns_last_real_result(self):
        manager = _make_manager()
        flow = self._make_flow()
        with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 7.5)):
            self._exec(manager, flow, True)  # rising edge: real ping → reachable=True, latency=7.5
            out2 = self._exec(manager, flow, True)  # sustained: no new ping but last result restored
        assert out2["hc"]["reachable"] is True
        assert out2["hc"]["latency_ms"] == pytest.approx(7.5)

    def test_sustained_trigger_unreachable_restores_false(self):
        manager = _make_manager()
        flow = self._make_flow()
        with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(False, None)):
            self._exec(manager, flow, True)  # rising edge: unreachable
            out2 = self._exec(manager, flow, True)  # sustained: should still show False, not placeholder
        assert out2["hc"]["reachable"] is False
        assert out2["hc"]["latency_ms"] is None

    def test_sustained_trigger_replays_restored_result_downstream(self):
        nodes = [
            node("cv_trig", "const_value", {"value": "true", "data_type": "bool"}),
            node("cv_true", "const_value", {"value": "true", "data_type": "bool"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("gate", "and", {"input_count": 2}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv_trig", "hc", "value", "trigger"),
                edge("hc", "gate", "reachable", "in1"),
                edge("cv_true", "gate", "value", "in2"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-sustained-downstream"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 7.5)) as mock_ping:
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                out2 = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert mock_ping.await_count == 1
        assert out2["hc"]["reachable"] is True
        assert out2["gate"]["out"] is True

    def test_sustained_trigger_rechecks_after_config_change(self):
        manager = _make_manager()
        flow = self._make_flow()
        with patch(
            "obs.logic.manager._ping_host",
            new_callable=AsyncMock,
            side_effect=[(True, 7.5), (False, None)],
        ) as mock_ping:
            self._exec(manager, flow, True)
            flow.nodes[0].data["host"] = "192.168.1.2"
            out2 = self._exec(manager, flow, True)
        assert mock_ping.await_count == 2
        assert mock_ping.await_args.args[0] == "192.168.1.2"
        assert out2["hc"]["reachable"] is False

    def test_sustained_trigger_rechecks_after_process_token_change(self):
        manager = _make_manager()
        flow = self._make_flow()
        with patch(
            "obs.logic.manager._ping_host",
            new_callable=AsyncMock,
            side_effect=[(True, 7.5), (False, None)],
        ) as mock_ping:
            self._exec(manager, flow, True)
            manager._hysteresis["g"]["hc"]["hc_runtime_token"] = "previous-process"
            out2 = self._exec(manager, flow, True)
        assert mock_ping.await_count == 2
        assert out2["hc"]["reachable"] is False


# ===========================================================================
# Manager: non-numeric config values do not crash the graph (Fix 5)
# ===========================================================================


class TestHostCheckConfigGuard:
    def test_nonnumeric_timeout_does_not_crash(self):
        manager = _make_manager()
        flow = _flow([node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": "bad", "count": 1})])
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))
        assert "hc" in outputs

    def test_nonnumeric_count_does_not_crash(self):
        manager = _make_manager()
        flow = _flow([node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": "bad"})])
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))
        assert "hc" in outputs

    def test_ping_config_is_clamped_before_dispatch(self):
        manager = _make_manager()
        flow = _flow([node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 999, "count": 999})])
        graph_id = "g-clamp"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping:
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))
        assert mock_ping.await_args.args[1] == 10
        assert mock_ping.await_args.args[2] == pytest.approx(30.0)


# ===========================================================================
# Manager: host_check replay state and post-api interactions
# ===========================================================================


class TestHostCheckReplayState:
    def test_stateful_descendant_counts_real_result_once(self):
        nodes = [
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("stats", "statistics", {}),
        ]
        flow = _flow(nodes, [edge("hc", "stats", "reachable", "value")])
        manager = _make_manager()
        graph_id = "g-stats"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))

        assert outputs["stats"]["count"] == 1
        assert outputs["stats"]["avg"] == pytest.approx(1.0)
        assert manager._hysteresis[graph_id]["stats"]["s_count"] == 1

    def test_post_api_replay_preserves_api_outputs(self):
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34", "method": "GET"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("gate", "and", {"input_count": 2}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc", "success", "trigger"),
                edge("ac", "gate", "success", "in1"),
                edge("hc", "gate", "reachable", "in2"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-post-api-preserve"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["ac"]["success"] is True
        assert outputs["hc"]["reachable"] is True
        assert outputs["gate"]["out"] is True

    def test_post_api_host_check_runs_downstream_wol(self):
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34", "method": "GET"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc", "success", "trigger"),
                edge("hc", "wol", "reachable", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-post-api-wol"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        mock_to_thread.assert_awaited_once()
        assert outputs["wol"]["sent"] is True

    def test_host_check_replay_updates_operating_hours_state(self):
        nodes = [
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("hours", "operating_hours", {}),
        ]
        flow = _flow(nodes, [edge("hc", "hours", "reachable", "active")])
        manager = _make_manager()
        graph_id = "g-hours"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))

        assert outputs["hours"]["_active"] is True
        assert manager._node_state[graph_id]["hours"]["last_start"] is not None

    def test_replay_triggers_chained_host_check(self):
        nodes = [
            node("hc_a", "host_check", {"host": "a.local", "timeout_s": 1, "count": 1}),
            node("hc_b", "host_check", {"host": "b.local", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(nodes, [edge("hc_a", "hc_b", "reachable", "trigger")])
        manager = _make_manager()
        graph_id = "g-hc-chain"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, side_effect=[(True, 1.0), (True, 2.0)]) as mock_ping:
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc_a": {"trigger": True}}))

        assert mock_ping.await_count == 2
        assert outputs["hc_a"]["reachable"] is True
        assert outputs["hc_b"]["reachable"] is True
        assert outputs["hc_b"]["latency_ms"] == pytest.approx(2.0)

    def test_post_api_host_check_runs_downstream_api_client(self):
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc", "success", "trigger"),
                edge("hc", "ac2", "reachable", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-post-api-api"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_MockResponse(200))
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert mock_client.request.await_count == 2
        assert outputs["ac1"]["success"] is True
        assert outputs["hc"]["reachable"] is True
        assert outputs["ac2"]["success"] is True

    def test_final_api_replay_triggers_downstream_host_check(self):
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "one.local", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("hc2", "host_check", {"host": "two.local", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "hc2", "success", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-post-api-api-hc"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_MockResponse(200))
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ) as mock_ping:
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert mock_client.request.await_count == 2
        assert mock_ping.await_count == 2
        assert outputs["hc1"]["reachable"] is True
        assert outputs["hc2"]["reachable"] is True
        assert outputs["hc2"]["latency_ms"] == pytest.approx(2.0)
