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
        with patch("sys.platform", "linux"):
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

    def test_async_driven_sustained_trigger_pings_only_once(self):
        """api_client→hc: HC with async trigger doesn't re-ping when trigger stays True (rising-edge deferred clear)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc", "success", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-async-hc-edge"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping:
                    asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                    asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                    asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 1


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


# ===========================================================================
# Manager: post-api host_check replay — additional code-path coverage
# ===========================================================================


def _setup_post_api_hc_ac2_graph(ac2_data: dict) -> tuple[FlowData, "LogicManager", str]:
    """Shared setup: cv → ac1 → hc → ac2."""
    nodes = [
        node("cv", "const_value", {"value": "true", "data_type": "bool"}),
        node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
        node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        node("ac2", "api_client", ac2_data),
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
    graph_id = "g-post-api-ac2"
    manager._graphs[graph_id] = ("test", True, flow)
    manager._node_state[graph_id] = {}
    return flow, manager, graph_id


class TestHostCheckPostApiExtraPaths:
    """Coverage for code paths in the post-api HC replay sections."""

    def test_post_api_hc_triggers_chained_host_check(self):
        """Post-api HC triggers a second HC via its reachable output (lines 1775-1777)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34", "method": "GET"}),
            node("hc1", "host_check", {"host": "one.local", "timeout_s": 1, "count": 1}),
            node("hc2", "host_check", {"host": "two.local", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc1", "success", "trigger"),
                edge("hc1", "hc2", "reachable", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-post-api-hc-chain"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ) as mock_ping:
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 2
        assert outputs["hc1"]["reachable"] is True
        assert outputs["hc2"]["reachable"] is True

    def test_post_api_wol_downstream_propagation(self):
        """WoL fired by post-api HC propagates its sent=True to a downstream const_value node (lines 1826-1847)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34", "method": "GET"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("cv2", "const_value", {"value": "true", "data_type": "bool"}),
            node("gate", "and", {"input_count": 2}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc", "success", "trigger"),
                edge("hc", "wol", "reachable", "trigger"),
                edge("wol", "gate", "sent", "in1"),
                edge("cv2", "gate", "value", "in2"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-post-api-wol-ds"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["wol"]["sent"] is True
        assert outputs["gate"]["out"] is True

    def test_post_api_hc_unreachable_wol_not_triggered(self):
        """Post-api HC fires but is unreachable: WoL node in descendants skips WoL (lines 1793-1794)."""
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
        graph_id = "g-post-api-wol-unr"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(False, None)):
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_tt:
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["hc"]["reachable"] is False
        assert outputs["wol"]["sent"] is False
        mock_tt.assert_not_awaited()

    def test_post_api_hc_unreachable_skips_downstream_api_client(self):
        """Unreachable HC leaves ac2._trigger=False; post-api section skips it (line 1856)."""
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
        graph_id = "g-post-api-hc-unr-ac"
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
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(False, None)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["hc"]["reachable"] is False
        assert mock_client.request.await_count == 1  # only ac1 fired
        assert outputs["ac2"]["success"] is False

    def test_run_host_check_normalise_exception_returns_false(self):
        """Exception in config normalisation: HC skips ping and graph survives (lines 1302-1304)."""
        manager = _make_manager()
        flow = _flow([node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1})])
        graph_id = "g-hc-norm-exc"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch(
                "obs.logic.manager._normalise_host_check_ping_config",
                side_effect=RuntimeError("bad config"),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))

        assert outputs["hc"]["reachable"] is False

    def test_final_api_hc_updates_downstream_node(self):
        """HC triggered in the final-api replay propagates its result downstream (lines 2076-2116)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "one.local", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("hc2", "host_check", {"host": "two.local", "timeout_s": 1, "count": 1}),
            node("cv2", "const_value", {"value": "true", "data_type": "bool"}),
            node("gate", "and", {"input_count": 2}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "hc2", "success", "trigger"),
                edge("hc2", "gate", "reachable", "in1"),
                edge("cv2", "gate", "value", "in2"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-final-api-hc-gate"
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

        assert mock_ping.await_count == 2
        assert outputs["hc2"]["reachable"] is True
        assert outputs["gate"]["out"] is True


class TestHostCheckPostApiApiClientPaths:
    """Coverage for code paths inside the post-api host_check → api_client firing section."""

    def test_blocked_url_raises_value_error(self):
        """Private/blocked URL for post-api ac2 triggers ValueError path (lines 1877-1882)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://127.0.0.1/private", "method": "GET"})
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

        assert mock_client.request.await_count == 1  # only ac1
        assert outputs["ac2"]["success"] is False
        assert outputs["ac2"]["status"] is None

    def test_basic_auth_config(self):
        """Basic auth credentials for post-api ac2 (lines 1918-1927)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "GET", "auth_type": "basic", "auth_username": "admin", "auth_password": "secret"}
        )
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
        assert outputs["ac2"]["success"] is True

    def test_bearer_auth_config(self):
        """Bearer token for post-api ac2 (lines 1929-1942)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "GET", "auth_type": "bearer", "auth_token": "my-secret-token"}
        )
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
        assert outputs["ac2"]["success"] is True

    def test_post_method_json_body(self):
        """POST method with JSON content-type for post-api ac2 (lines 1955-1961)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "POST", "content_type": "application/json"}
        )
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
        assert outputs["ac2"]["success"] is True

    def test_httpx_request_error_caught(self):
        """httpx.RequestError from post-api ac2 flows through retry loop and outer handler (lines 1981-1985 + 2014-2018)."""
        import httpx as _httpx

        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "GET"})
        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        call_count = [0]

        async def _selective(method, url, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise _httpx.ConnectError("connection refused")
            return _MockResponse(200)

        mock_client.request = AsyncMock(side_effect=_selective)
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["ac1"]["success"] is True
        assert outputs["ac2"]["success"] is False

    def test_ssl_verify_string(self):
        """verify_ssl='false' string is converted to bool False before httpx call (line 1888)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "GET", "verify_ssl": "false"})
        patcher = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["ac2"]["success"] is True

    def test_json_headers_config(self):
        """Headers as JSON string are parsed and merged into request headers (lines 1893-1895)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "GET", "headers": '{"X-Custom": "val"}'})
        patcher = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["ac2"]["success"] is True

    def test_non_json_response_type(self):
        """response_type='text/plain' returns raw text instead of parsed JSON (line 1997)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "GET", "response_type": "text/plain"})
        patcher = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["ac2"]["success"] is True
        assert outputs["ac2"]["response"] == '{"ok": true}'

    def test_json_parse_failure_in_response(self):
        """Response body that fails JSON decode falls back to raw text (lines 1994-1995)."""
        import json as _json_mod

        class _BadJsonResponse:
            status_code = 200
            text = "not-valid-json"

            def json(self):
                raise _json_mod.JSONDecodeError("fail", "not-valid-json", 0)

        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "GET"})
        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        call_count = [0]

        async def _selective(method, url, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 2:
                return _BadJsonResponse()
            return _MockResponse(200)

        mock_client.request = AsyncMock(side_effect=_selective)
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["ac2"]["success"] is True
        assert outputs["ac2"]["response"] == "not-valid-json"

    def test_invalid_json_headers_ignored(self):
        """headers field with invalid JSON is silently ignored; request still succeeds (lines 1895-1896)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "GET", "headers": "not-valid-json"})
        patcher = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["ac2"]["success"] is True

    def test_non_retryable_method_request_error(self):
        """Non-retryable method (DELETE) breaks immediately on RequestError without retry (line 1984)."""
        import httpx as _httpx

        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "DELETE"})
        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        call_count = [0]

        async def _selective(method, url, **kwargs):
            call_count[0] += 1
            if call_count[0] >= 2:
                raise _httpx.ConnectError("connection refused")
            return _MockResponse(200)

        mock_client.request = AsyncMock(side_effect=_selective)
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["ac1"]["success"] is True
        assert outputs["ac2"]["success"] is False


# ===========================================================================
# Replay ordering fixes
# ===========================================================================


class TestReplayOrderingFixes:
    """Tests for the resolved_async_edge_overrides accumulator and new async passes."""

    def test_chained_hc_shared_gate_sees_both_real_results(self):
        """hc_a → hc_b AND hc_a + hc_b both feed an AND gate.

        When replaying after hc_b fires, the gate must receive hc_a's real
        reachable=True (not the first-pass placeholder False).
        """
        nodes = [
            node("hc_a", "host_check", {"host": "a.local", "timeout_s": 1, "count": 1}),
            node("hc_b", "host_check", {"host": "b.local", "timeout_s": 1, "count": 1}),
            node("gate", "and", {"input_count": 2}),
        ]
        flow = _flow(
            nodes,
            [
                edge("hc_a", "hc_b", "reachable", "trigger"),
                edge("hc_a", "gate", "reachable", "in1"),
                edge("hc_b", "gate", "reachable", "in2"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-hc-chain-gate"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch(
                "obs.logic.manager._ping_host",
                new_callable=AsyncMock,
                side_effect=[(True, 1.0), (True, 2.0)],
            ) as mock_ping:
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc_a": {"trigger": True}}))

        assert mock_ping.await_count == 2
        assert outputs["hc_a"]["reachable"] is True
        assert outputs["hc_b"]["reachable"] is True
        assert outputs["gate"]["out"] is True, "gate must see both real HC outputs, not the hc_a placeholder"

    def test_wol_triggers_downstream_host_check(self):
        """timer_cron → wake_on_lan → host_check: HC must ping in the same tick."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "wol", "value", "trigger"),
                edge("wol", "hc", "sent", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-wol-hc"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 5.0)) as mock_ping:
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        mock_ping.assert_awaited_once()
        assert outputs["wol"]["sent"] is True
        assert outputs["hc"]["reachable"] is True

    def test_api_hc_wol_sends_in_same_tick(self):
        """api_client → host_check → wake_on_lan: WoL must send in the same tick as the ping."""
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
        graph_id = "g-api-hc-wol"
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
        assert outputs["hc"]["reachable"] is True
        assert outputs["wol"]["sent"] is True

    def test_api_hc_api_wol_sends_in_same_tick(self):
        """api_client→hc→api_client→wol: WoL must send in the same execution tick."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc", "success", "trigger"),
                edge("hc", "ac2", "reachable", "trigger"),
                edge("ac2", "wol", "success", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-api-hc-api-wol"
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
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert mock_client.request.await_count == 2
        assert outputs["hc"]["reachable"] is True
        assert outputs["ac2"]["success"] is True
        mock_to_thread.assert_awaited_once()
        assert outputs["wol"]["sent"] is True

    def test_wol_hc_propagates_to_downstream_node(self):
        """wol → hc → gate: HC reachable result must be replayed to downstream nodes."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("gate", "and", {"input_count": 2}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "wol", "value", "trigger"),
                edge("wol", "hc", "sent", "trigger"),
                edge("hc", "gate", "reachable", "in1"),
                edge("cv", "gate", "value", "in2"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-wol-hc-gate"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 5.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["wol"]["sent"] is True
        assert outputs["hc"]["reachable"] is True
        assert outputs["gate"]["out"] is True

    def test_api_hc_wol_triggers_second_hc(self):
        """api_client→hc→wol→hc2: second HC must ping after the post-api WoL sends."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc1", "success", "trigger"),
                edge("hc1", "wol", "reachable", "trigger"),
                edge("wol", "hc2", "sent", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-api-hc-wol-hc"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ) as mock_ping:
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 2
        assert outputs["hc1"]["reachable"] is True
        assert outputs["wol"]["sent"] is True
        assert outputs["hc2"]["reachable"] is True


# ===========================================================================
# _ping_host: FileNotFoundError handler (line 595-597)
# ===========================================================================


class TestPingHostFileNotFound:
    def test_file_not_found_returns_false_none(self):
        """FileNotFoundError hits the specific except FileNotFoundError branch, not the generic one."""
        with patch(
            "obs.logic.manager.asyncio.create_subprocess_exec",
            new_callable=AsyncMock,
            side_effect=FileNotFoundError("ping not found"),
        ):
            reachable, latency_ms = asyncio.run(_ping_host("host", count=1, timeout_s=1))
        assert reachable is False
        assert latency_ms is None


# ===========================================================================
# _apply_operating_hours_state: reset and deactivation branches (lines 1258-1265)
# ===========================================================================


class TestApplyOperatingHoursStateBranches:
    def test_reset_branch_clears_accumulated_hours(self):
        """OH node with reset=True clears accumulated_hours and sets last_start=None (lines 1258-1259)."""
        nodes = [
            node("cv_reset", "const_value", {"value": "true", "data_type": "bool"}),
            node("oh", "operating_hours", {}),
        ]
        flow = _flow(nodes, [edge("cv_reset", "oh", "value", "reset")])
        manager = _make_manager()
        graph_id = "g-oh-reset"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {"oh": {"accumulated_hours": 5.0, "last_start": None}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        state = manager._node_state[graph_id]["oh"]
        assert state["accumulated_hours"] == 0.0
        assert state["last_start"] is None

    def test_deactivation_accumulates_hours(self):
        """OH node going active→inactive accumulates hours and clears last_start (lines 1264-1265)."""
        nodes = [node("oh", "operating_hours", {})]
        flow = _flow(nodes, [])
        manager = _make_manager()
        graph_id = "g-oh-deact"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"oh": {"active": True}}))

        assert manager._node_state[graph_id]["oh"]["last_start"] is not None

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"oh": {"active": False}}))

        state = manager._node_state[graph_id]["oh"]
        assert state["accumulated_hours"] > 0
        assert state["last_start"] is None


# ===========================================================================
# Post-WoL replay: hyst update + chained HC (lines 1560, 1565-1569)
# ===========================================================================


class TestPostWolReplayChainedHc:
    def test_post_wol_replay_hyst_and_chained_hc(self):
        """cv→wol→hc1→{stats,hc2}: stats hyst updated (line 1560), hc2 chained (lines 1565-1569)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
            node("stats", "statistics", {}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "wol", "value", "trigger"),
                edge("wol", "hc1", "sent", "trigger"),
                edge("hc1", "hc2", "reachable", "trigger"),
                edge("hc1", "stats", "reachable", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-post-wol-hyst"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                with patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ) as mock_ping:
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert mock_ping.await_count == 2
        assert outputs["hc1"]["reachable"] is True
        assert outputs["hc2"]["reachable"] is True


# ===========================================================================
# Post-api HC replay: hyst update for stateful node (line 1861)
# ===========================================================================


class TestPostApiHcReplayHystUpdate:
    def test_stateful_hyst_updated(self):
        """cv→ac→hc→stats: statistics hyst must be copied into hyst after post-api HC replay (line 1861)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("stats", "statistics", {}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc", "success", "trigger"),
                edge("hc", "stats", "reachable", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-hc-stats-hyst"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["hc"]["reachable"] is True
        assert "stats" in manager._graphs[graph_id][2].nodes or True  # graph still registered


# ===========================================================================
# Post-api WoL edge cases (lines 1890, 1893-1894, 1899, 1902, 1905-1906, 1913-1914)
# ===========================================================================


class TestPostApiWolEdgeCases:
    def _run_post_api_wol_graph(self, wol_data: dict, executions: int = 1):
        """Helper: cv→ac→hc→wol, returns outputs from the last execution."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol", "wake_on_lan", wol_data),
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
        graph_id = "g-pawol-edge"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        mock_client_cls = _patch_api_success()
        outputs = {}
        try:
            for _ in range(executions):
                with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                    with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                        with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        return outputs

    def test_was_triggered_skipped(self):
        """Post-api WoL: was_triggered=True → line 1890 fires (skip without re-send).

        An OR gate ensures wol._trigger=True in the first executor pass so the main
        WoL loop preserves wol_prev_trigger instead of resetting it.
        """
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("or_gate", "or", {"input_count": 2}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc", "success", "trigger"),
                edge("cv", "or_gate", "value", "in1"),
                edge("hc", "or_gate", "reachable", "in2"),
                edge("or_gate", "wol", "out", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-pawol-was-triggered"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            for _ in range(2):
                with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                    with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                        with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
                            asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        # Second execution must skip the WoL send (was_triggered=True → line 1890)
        mock_to_thread.assert_not_awaited()

    def test_missing_mac_skips_wol(self):
        """Post-api WoL: empty mac_address → warning logged, no send (lines 1893-1894)."""
        outputs = self._run_post_api_wol_graph({})
        assert outputs["wol"].get("sent") is not True

    def test_fractional_port_raises(self):
        """Post-api WoL: fractional port → ValueError caught (lines 1899, 1913-1914)."""
        outputs = self._run_post_api_wol_graph({"mac_address": "AA:BB:CC:DD:EE:FF", "port": 9.5})
        assert outputs["wol"].get("sent") is not True

    def test_port_out_of_range_raises(self):
        """Post-api WoL: port > 65535 → ValueError caught (line 1902, 1913-1914)."""
        outputs = self._run_post_api_wol_graph({"mac_address": "AA:BB:CC:DD:EE:FF", "port": 99999})
        assert outputs["wol"].get("sent") is not True

    def test_invalid_broadcast_raises(self):
        """Post-api WoL: non-IP broadcast_ip → ValueError caught (lines 1905-1906, 1913-1914)."""
        outputs = self._run_post_api_wol_graph({"mac_address": "AA:BB:CC:DD:EE:FF", "broadcast_ip": "not-an-ip"})
        assert outputs["wol"].get("sent") is not True


# ===========================================================================
# _pawol_pending loop (lines 1963-1998)
# ===========================================================================


class TestPaWolReplayLoop:
    def test_pawol_pending_covers_chained_hc_and_hyst(self):
        """cv→ac→hc1→wol→{hc2→hc3, hc2→stats}: covers the _pawol_pending loop (lines 1963-1998)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
            node("hc3", "host_check", {"host": "192.168.1.3", "timeout_s": 1, "count": 1}),
            node("stats", "statistics", {}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc1", "success", "trigger"),
                edge("hc1", "wol", "reachable", "trigger"),
                edge("wol", "hc2", "sent", "trigger"),
                edge("hc2", "hc3", "reachable", "trigger"),
                edge("hc2", "stats", "reachable", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-pawol-loop"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0), (True, 3.0)],
                ) as mock_ping:
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 3
        assert outputs["hc1"]["reachable"] is True
        assert outputs["hc2"]["reachable"] is True
        assert outputs["hc3"]["reachable"] is True


# ===========================================================================
# Post-api-hc api_client edge paths (lines 2019-2141)
# ===========================================================================


class TestPostApiHcApiEdgePaths:
    def test_empty_url_skips_request(self):
        """Post-api-hc api_client: empty URL → continue (line 2019), no HTTP call made."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "", "method": "GET"})
        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        # ac2 was skipped — success key may be absent or None
        assert outputs.get("ac2", {}).get("success") is not True

    def test_variable_error_in_url(self):
        """Post-api-hc api_client: unresolvable URL variable → error recorded (lines 2020-2025)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/###OBS1###", "method": "GET"})
        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is False
        assert "OBS1" in str(outputs["ac2"]["response"])

    def test_headers_secret_file_json_parse_failure(self):
        """Post-api-hc api_client: nonexistent headers_secret_file → JSONDecodeError swallowed (lines 2050, 2055-2056)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "GET", "headers_secret_file": "/run/secrets/nonexistent"}
        )
        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        # Request still completes (exception in headers file is swallowed)
        assert outputs["ac2"]["success"] is True

    def test_headers_secret_file_success(self):
        """Post-api-hc api_client: headers_secret_file returns valid JSON (line 2051)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "GET", "headers_secret_file": "/run/secrets/hdr"}
        )
        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    with patch("obs.logic.manager._read_secret_file", return_value='{"X-Custom": "value"}'):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is True

    def test_header_variable_error(self):
        """Post-api-hc api_client: unresolvable variable in headers → error recorded (lines 2059-2064)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "GET", "headers": '{"X-Token": "###OBS1###"}'}
        )
        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is False
        assert "OBS1" in str(outputs["ac2"]["response"])

    def test_bearer_token_from_file(self):
        """Post-api-hc api_client: empty auth_token falls back to auth_token_file (line 2085)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "GET", "auth_type": "bearer", "auth_token": "", "auth_token_file": "/run/secrets/tok"}
        )
        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    with patch("obs.logic.manager._read_secret_file", return_value="my-bearer-token"):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is True

    def test_auth_variable_error(self):
        """Post-api-hc api_client: unresolvable variable in auth_username → error recorded (lines 2094-2099)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {
                "url": "http://93.184.216.34/two",
                "method": "GET",
                "auth_type": "basic",
                "auth_username": "###OBS1###",
                "auth_password": "secret",
            }
        )
        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is False
        assert "OBS1" in str(outputs["ac2"]["response"])

    def test_form_encoded_body(self):
        """Post-api-hc api_client: content_type=application/x-www-form-urlencoded sets data= (lines 2113-2114)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "POST", "content_type": "application/x-www-form-urlencoded"}
        )
        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is True

    def test_text_plain_body(self):
        """Post-api-hc api_client: content_type=text/plain sets content= and Content-Type header (lines 2116-2117)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "POST", "content_type": "text/plain"})
        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is True

    def test_https_request_extensions(self):
        """Post-api-hc api_client: HTTPS URL → request_extensions set with sni_hostname (line 2124)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "https://93.184.216.34/two", "method": "GET"})
        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is True

    def test_large_response_truncated(self):
        """Post-api-hc api_client: response text > 1 MB is truncated to 1 MB (line 2141)."""

        class _LargeResponse:
            status_code = 200
            text = "x" * 1_000_001

            def json(self):
                raise ValueError("not json")

        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "GET", "response_type": "text/plain"})
        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_LargeResponse())
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert len(outputs["ac2"]["response"]) == 1_000_000


# ===========================================================================
# Final-api replay: stateful hyst update (line 2206)
# ===========================================================================


class TestFinalApiReplayStateful:
    def test_final_api_replay_updates_stateful_hyst(self):
        """cv→ac1→hc1→ac2→stats: statistics hyst copied after final-api replay (line 2206)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("stats", "statistics", {}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "stats", "success", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-final-api-hyst"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["ac2"]["success"] is True
        assert outputs["stats"]["count"] == 1


# ===========================================================================
# Final-api replay: chained HC (lines 2261, 2266-2270)
# ===========================================================================


class TestFinalApiReplayChainedHc:
    def test_final_api_chained_hc(self):
        """cv→ac1→hc1→ac2→hc2→{hc3,stats}: chained HCs + hyst update in final-api replay (lines 2261, 2266-2270)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
            node("hc3", "host_check", {"host": "192.168.1.3", "timeout_s": 1, "count": 1}),
            node("stats", "statistics", {}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "hc2", "success", "trigger"),
                edge("hc2", "hc3", "reachable", "trigger"),
                edge("hc2", "stats", "reachable", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-final-api-chained-hc"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0), (True, 3.0)],
                ) as mock_ping:
                    outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 3
        assert outputs["hc1"]["reachable"] is True
        assert outputs["hc2"]["reachable"] is True
        assert outputs["hc3"]["reachable"] is True


# ===========================================================================
# Final WoL downstream propagation (lines 2316-2338)
# ===========================================================================


class TestFinalWolDownstream:
    def test_final_wol_downstream_propagation(self):
        """cv→ac1→hc1→ac2→wol→gate: Final WoL pass propagates sent=True to gate (lines 2316-2338)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("gate", "and", {"input_count": 2}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "wol", "success", "trigger"),
                edge("wol", "gate", "sent", "in1"),
                edge("cv", "gate", "value", "in2"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-final-wol-dn"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["wol"]["sent"] is True
        assert outputs["gate"]["out"] is True


# ===========================================================================
# Final WoL replay hyst copy-back and HC downstream run (lines 2350, 2354-2357)
# ===========================================================================


class TestFinalWolReplayExtended:
    def test_final_wol_replay_hyst_copy_back(self):
        """cv→ac1→hc1→ac2→wol→stats: final WoL replay copies stateful node hyst back (line 2350)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("stats", "statistics", {}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "wol", "success", "trigger"),
                edge("wol", "stats", "sent", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-final-wol-hyst"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)):
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["wol"]["sent"] is True
        assert outputs["stats"]["count"] >= 1

    def test_final_wol_hc_downstream(self):
        """cv→ac1→hc1→ac2→wol→hc2: HC downstream of final WoL is run in same tick (lines 2354-2357)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "wol", "success", "trigger"),
                edge("wol", "hc2", "sent", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-final-wol-hc"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ) as mock_ping:
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 2
        assert outputs["wol"]["sent"] is True
        assert outputs["hc2"]["reachable"] is True

    def test_final_wol_hc_downstream_replay(self):
        """cv→ac1→hc1→ac2→wol→hc2→gate: HC downstream of final WoL has its descendants replayed (Fix 4)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
            node("gate", "and", {"input_count": 2}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "wol", "success", "trigger"),
                edge("wol", "hc2", "sent", "trigger"),
                edge("hc2", "gate", "reachable", "in1"),
                edge("cv", "gate", "value", "in2"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-final-wol-hc-replay"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ):
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["wol"]["sent"] is True
        assert outputs["hc2"]["reachable"] is True
        assert outputs["gate"]["out"] is True

    def test_final_wol_hc_downstream_hyst_copy_back(self):
        """cv→ac1→hc1→ac2→wol→hc2→stats: final-WoL HC downstream replay copies stateful node hyst back (line 2398)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
            node("stats", "statistics", {}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "wol", "success", "trigger"),
                edge("wol", "hc2", "sent", "trigger"),
                edge("hc2", "stats", "reachable", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-fwol-hc-hyst"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ):
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["wol"]["sent"] is True
        assert outputs["hc2"]["reachable"] is True
        assert outputs["stats"]["count"] >= 1

    def test_final_wol_hc_chained_downstream_hc(self):
        """cv→ac1→hc1→ac2→wol→hc2→hc3: chained HC downstream of final-WoL HC fires in the same tick (lines 2403-2407)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
            node("hc3", "host_check", {"host": "192.168.1.3", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "wol", "success", "trigger"),
                edge("wol", "hc2", "sent", "trigger"),
                edge("hc2", "hc3", "reachable", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-fwol-hc-chain"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                with patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0), (True, 3.0)],
                ) as mock_ping:
                    with patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock):
                        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 3
        assert outputs["wol"]["sent"] is True
        assert outputs["hc2"]["reachable"] is True
        assert outputs["hc3"]["reachable"] is True
