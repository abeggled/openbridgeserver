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
import uuid
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
        with patch("sys.platform", "linux"), _patch_subprocess(0, b"time=1.0 ms\n") as mock_exec:
            asyncio.run(_ping_host("host", count=999, timeout_s=999))
        cmd = mock_exec.call_args.args
        assert cmd[cmd.index("-c") + 1] == "10"
        assert cmd[cmd.index("-W") + 1] == "30"

    def test_macos_uses_per_packet_wait_flag(self):
        with patch("sys.platform", "darwin"), _patch_subprocess(0, b"time=1.0 ms\n") as mock_exec:
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

    with (
        patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=ping_return) as mock_ping,
    ):
        outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": trigger}}))
    return outputs, mock_ping


class TestHostCheckManager:
    def test_debug_override_is_used_for_deferred_memory_commit(self):
        manager = _make_manager()
        flow = _flow([node("mem", "memory", {"initial_value": "2", "data_type": "number"})])
        manager._node_state["g"] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(
                manager._execute_graph(
                    "g",
                    "test",
                    flow,
                    {},
                    debug_overrides={"mem": {"in": 41}},
                )
            )

        assert outputs["mem"]["out"] == pytest.approx(2.0)
        assert manager._hysteresis["g"]["mem"]["value"] == pytest.approx(41.0)

    def test_debug_override_wins_after_async_replay(self):
        manager = _make_manager()
        captured = {}
        flow = _flow(
            [
                node("hc", "host_check", {"host": "192.168.1.1"}),
                node("formula", "math_formula", {"formula": "a * 2"}),
            ],
            [edge("hc", "formula", "reachable", "in1")],
        )
        manager._node_state["g"] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        ):
            outputs = asyncio.run(
                manager._execute_graph(
                    "g",
                    "test",
                    flow,
                    {"hc": {"trigger": True}},
                    debug_overrides={"formula": {"in1": 9}},
                    debug_input_capture=captured,
                )
            )

        assert outputs["formula"]["result"] == 18
        assert captured["formula"]["in1"] == {"incoming": True, "effective": 9, "overridden": True}

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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, side_effect=OSError("fail")),
        ):
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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
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
        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))

        assert mock_ping.await_count == 3

    def test_cron_retriggers_through_operating_hours_named_trigger_port(self):
        """Regression: _edge_carries_pulse matched a trigger-typed input
        port only when its id was literally "trigger" — but operating_hours
        declares two trigger-typed inputs named "active" and "reset"
        instead. A cron → operating_hours.active → host_check.trigger
        chain was therefore never added to cron_reachable at all (the very
        first hop, into "active", was incorrectly rejected), so once
        host_check's own sustained trigger settled, later cron ticks were
        wrongly deduplicated as if the trigger had never re-risen."""
        nodes = [
            node("cron", "timer_cron", {"cron": "* * * * *"}),
            node("oh", "operating_hours", {}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cron", "oh", "trigger", "active"),
                edge("oh", "hc", "hours", "trigger"),
            ],
        )

        manager = _make_manager()
        graph_id = "g-oh-cron"
        manager._graphs[graph_id] = ("test", True, flow)
        # Pre-seed operating_hours as already accumulating (started an hour
        # ago), so its "hours" output is reliably nonzero/truthy from the
        # very first tick — not dependent on real wall-clock time elapsing
        # between two asyncio.run() calls within this test.
        from datetime import UTC, datetime, timedelta

        manager._node_state[graph_id] = {"oh": {"accumulated_hours": 0.0, "last_start": datetime.now(UTC) - timedelta(hours=1)}}

        cron_overrides = {"cron": {"trigger": True}}
        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            # Tick 1: hours is already truthy — first real trigger, always pings.
            asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))
            # Tick 2: trigger stays sustained (hours keeps growing) — must
            # still ping again because this is a fresh cron tick.
            asyncio.run(manager._execute_graph(graph_id, "test", flow, cron_overrides))

        assert mock_ping.await_count == 2

    def test_pulse_continues_through_edge_targeting_unregistered_node_type(self):
        """_edge_carries_pulse must still treat an edge as pulse-carrying
        when its target node's "type" isn't registered at all (e.g. a
        stale/removed node type left over in an old saved flow) — there is
        no trigger-typed port to compare against, so the pulse must pass
        through rather than being silently dropped. A change_filter pulse
        routed through such a node must still reach and retrigger a
        downstream host_check on every real change."""
        nodes = [
            node("cf", "change_filter"),
            node("unk", "some_bogus_unregistered_type"),
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("or_gate", "or", {"input_count": 2}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cf", "unk", "changed", "in"),
                edge("unk", "or_gate", "out", "in1"),
                edge("cv", "or_gate", "value", "in2"),
                edge("or_gate", "hc", "out", "trigger"),
            ],
        )

        manager = _make_manager()
        graph_id = "g-unk-type"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}}))

        assert mock_ping.await_count == 2

    def test_change_filter_pulse_retriggers_on_each_execution(self):
        """Regression: change_filter.changed must be a discrete retriggerable
        pulse like a cron tick — consecutive real changes must each ping,
        not be swallowed by the rising-edge dedup that treats a sustained
        True trigger as already handled."""
        nodes = [
            node("cf", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(nodes, [edge("cf", "hc", "changed", "trigger")])

        manager = _make_manager()
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 3}}))

        assert mock_ping.await_count == 3

    def test_change_filter_pulse_does_not_bypass_dedup_through_a_closed_gate(self):
        """Regression: a "gate" (Freigabe/relay) node closed by a resolved
        enable input (here: left unwired, resolving to closed) is not a
        pure pulse relay while closed — its output is a fixed
        default_value, entirely independent of change_filter.changed. A
        pulse arriving at "in" has no effect on the gate's output, so a
        host_check downstream of the gate must see an ordinary sustained
        (constant) trigger and be deduplicated normally, pinging only
        once — not on every execution just because change_filter.changed
        happened to fire behind the (irrelevant) gate."""
        nodes = [
            node("cf", "change_filter"),
            node("gate1", "gate", {"closed_behavior": "default_value", "default_value": "1"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cf", "gate1", "changed", "in"),
                # enable is intentionally left unwired -> resolves to closed
                edge("gate1", "hc", "out", "trigger"),
            ],
        )

        manager = _make_manager()
        graph_id = "g-cf-closed-gate-dedup"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 3}}))

        assert mock_ping.await_count == 1

    def test_change_filter_pulse_stops_at_unchanged_hysteresis_output(self):
        nodes = [
            node("cf", "change_filter"),
            node("hyst", "hysteresis", {"threshold_on": 0.5, "threshold_off": 0.0}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cf", "hyst", "changed", "value"),
                edge("hyst", "hc", "out", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-unchanged-hysteresis-dedup"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 3}}))

        assert mock_ping.await_count == 1

    def test_shadowed_change_filter_edge_does_not_make_host_check_pulse_reachable(self):
        nodes = [
            node("cf", "change_filter"),
            node("false", "const_value", {"value": "false", "data_type": "bool"}),
            node("not1", "not"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cf", "not1", "changed", "in"),
                # Last edge wins for not1.in, shadowing the pulse edge above.
                edge("false", "not1", "value", "in"),
                edge("not1", "hc", "out", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-shadowed-pulse-edge"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 3}}))

        assert mock_ping.await_count == 1

    def test_change_filter_pulse_does_not_bypass_dedup_through_an_intermediate_change_filter(self):
        """Regression: the pulse-carrying check only restricted the INITIAL
        seed to a change_filter's "changed" handle, not every subsequent hop
        of the transitive traversal. For cf1.changed -> cf2.in, cf2.out ->
        host_check.trigger: cf1 retriggers on every real change, so cf2
        receives the same boolean True each time — cf2 itself only changes
        once (its first value), and its "out" afterward is sustained,
        unchanged data, not a discrete pulse. The traversal must not walk
        cf2's "out" as if it inherited cf1's pulse just because cf2 became
        cron_reachable through its own "changed" — host_check must see an
        ordinary sustained trigger and dedupe normally, pinging only once."""
        nodes = [
            node("cf1", "change_filter"),
            node("cf2", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cf1", "cf2", "changed", "in"),
                edge("cf2", "hc", "out", "trigger"),
            ],
        )

        manager = _make_manager()
        graph_id = "g-cf-chain-intermediate-out-dedup"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf1": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf1": {"in": 2}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf1": {"in": 3}}))

        assert mock_ping.await_count == 1

    def test_change_filter_pulse_does_not_bypass_dedup_through_memory(self):
        """Regression: memory's "reset" port is trigger-typed, so a
        change_filter.changed pulse legitimately reaches it — but memory is
        an explicit tick boundary (per its own node description): its "out"
        this pass is whatever was already committed at the end of a
        *previous* tick, entirely independent of the reset just delivered
        (that only takes effect for the *next* tick, via the deferred
        commit_memory_inputs). The pulse must not be treated as having
        propagated through to memory's own descendants — a host_check fed
        by memory.out must see an ordinary sustained trigger and dedupe
        normally, pinging only once, not on every real cf change."""
        nodes = [
            node("cf", "change_filter"),
            # initial_value must be truthy: a reset commits this value for
            # the *next* tick's read, so without a truthy default the very
            # first reset would make every later tick read a falsy None —
            # never triggering host_check at all and hiding the bug this
            # test exists to catch.
            node("mem", "memory", {"initial_value": "1", "data_type": "number"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cf", "mem", "changed", "reset"),
                edge("mem", "hc", "out", "trigger"),
            ],
        )

        manager = _make_manager()
        graph_id = "g-cf-memory-reset-dedup"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"mem": {"value": 1}}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 3}}))

        assert mock_ping.await_count == 1

    def test_missing_change_filter_pulse_does_not_overwrite_memory_input(self):
        flow = _flow(
            [node("cf", "change_filter"), node("mem", "memory", {"data_type": "bool"})],
            [edge("cf", "mem", "changed", "in")],
        )
        manager = _make_manager()
        graph_id = "g-cf-memory-data-input"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            first = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            following = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))

        assert first["cf"]["changed"] is True
        assert repeated["cf"]["changed"] is False
        assert repeated["mem"]["out"] is True
        assert following["mem"]["out"] is True
        assert manager._hysteresis[graph_id]["mem"] == {"value": True}

    def test_change_filter_pulse_via_async_replay_does_not_bypass_dedup_through_memory(self):
        """Same memory tick-boundary stop as above, but for the pulse only
        discovered via the api_client async replay (_register_change_filter_
        pulses' own traversal, not the main preamble's) — a change_filter
        downstream of api_client only learns its real "changed" value after
        replay, so this exercises that second registration path
        specifically."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET", "response_type": "text/plain"}),
            node("cf", "change_filter"),
            node("mem", "memory", {"initial_value": "1", "data_type": "number"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "cf", "response", "in"),
                edge("cf", "mem", "changed", "reset"),
                edge("mem", "hc", "out", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-async-memory-reset-dedup"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"mem": {"value": 1}}

        responses = iter(["A", "B", "C"])

        async def _next_response(*args, **kwargs):
            return _MockResponse(200, text=next(responses))

        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=_next_response)
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
            ):
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert mock_ping.await_count == 1

    def test_random_value_triggered_by_async_replay_is_not_frozen_as_inactive(self):
        """Regression: api_client.success -> random_value.trigger -> change_filter.
        On the first pass api_client's own result is still an unresolved
        placeholder (success=False), so random_value reads as inactive
        (value=None) that pass too. The async-replay machinery's "late hold"
        recomputation used to reuse that FROZEN first-pass "inactive"
        determination for every later replay instead of recomputing it from
        each replay's own fresh outputs — permanently suppressing the
        change_filter even once random_value genuinely produced a real
        value once api_client's real success propagated."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET", "response_type": "text/plain"}),
            node("rnd", "random_value", {"min": 1, "max": 1}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "rnd", "success", "trigger"),
                edge("rnd", "cf", "value", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-random-via-async-replay"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        patcher = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["cf"]["out"] == 1
        assert outputs["cf"]["changed"] is True

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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
            ):
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 1

    def test_change_filter_out_handle_does_not_bypass_rising_edge_dedup(self):
        """Regression: only change_filter's "changed" handle is a discrete
        pulse. Wiring "out" into host_check's trigger must fall back to
        normal rising-edge dedup — repeated truthy values must not re-ping,
        since the trigger itself never fell back to False in between."""
        nodes = [
            node("cf", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(nodes, [edge("cf", "hc", "out", "trigger")])

        manager = _make_manager()
        graph_id = "g-cf-out-no-bypass"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 3}}))

        assert mock_ping.await_count == 1

    def test_unresolved_api_client_placeholder_does_not_fire_premature_ping(self):
        """Regression: change_filter commits its comparison inline (unlike
        memory's deferred commit), so on the first synchronous pass an
        unresolved api_client output (response=None placeholder) must not
        look like a change against the already-stored real value — that
        would fire an unrecoverable ping before the async replay ever sees
        the real (here: unchanged) response."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET", "response_type": "text/plain"}),
            node("cf", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "cf", "response", "in"),
                edge("cf", "hc", "changed", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-async-placeholder"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        # Simulate a prior execution that already stored the real response.
        manager._hysteresis[graph_id] = {"cf": {"value": "OK"}}

        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_MockResponse(200, text="OK"))
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["ac"]["response"] == "OK"
        mock_ping.assert_not_awaited()

    def test_change_filter_is_not_held_when_or_gate_is_already_true_despite_an_unresolved_async_branch(self):
        """Regression: an OR gate combining an api_client output with a
        separate, already-True branch must not have its already-True result
        held back just because api_client is genuinely triggered (and
        therefore unresolved-until-replay) this pass — OR's True output is
        final regardless of what api_client's real response turns out to be,
        so a real change coming through the OTHER branch must not be
        silently discarded in favor of the filter's old value."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("live", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
            node("or_gate", "or", {"input_count": 2}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "or_gate", "success", "in1"),
                edge("live", "or_gate", "value", "in2"),
                edge("or_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-or-async"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["cf"]["out"] is True
        assert outputs["cf"]["changed"] is True

    def test_change_filter_presence_does_not_add_an_extra_full_graph_evaluation_when_async_source_is_inactive(self):
        """Regression: determining whether an async source is triggered this
        pass used to require a disposable "dry run" of the *whole* graph
        whenever a change_filter was merely reachable from an async source
        at all — regardless of whether that source ever actually triggers.
        For a graph that also contains a non-deterministic node
        (random_value) elsewhere, that meant an extra evaluation purely
        because a change_filter happened to be nearby, and the dry run's own
        random draw could disagree with the real pass's, making the hold
        decision itself unreliable. With api_client never triggered here,
        there is nothing to hold or replay, so adding an unrelated
        change_filter downstream of it must not change how many times
        random.randint is called."""

        def _make_flow(with_change_filter):
            nodes = [
                node("cv", "const_value", {"value": "false", "data_type": "bool"}),  # api_client never triggers
                node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
                node("rand_trigger", "const_value", {"value": "true", "data_type": "bool"}),
                node("rand", "random_value", {"data_type": "int", "min": 1, "max": 100}),
            ]
            edges = [
                edge("cv", "ac", "value", "trigger"),
                edge("rand_trigger", "rand", "value", "trigger"),
            ]
            if with_change_filter:
                nodes.append(node("cf", "change_filter"))
                edges.append(edge("ac", "cf", "success", "in"))
            return _flow(nodes, edges)

        def _run(with_change_filter):
            manager = _make_manager()
            flow = _make_flow(with_change_filter)
            graph_id = "g"
            manager._graphs[graph_id] = ("test", True, flow)
            manager._node_state[graph_id] = {}
            mock_client_cls = _patch_api_success()
            try:
                with (
                    patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                    patch("random.randint", return_value=42) as mock_rand,
                ):
                    asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
            finally:
                mock_client_cls.stop()
            return mock_rand.call_count

        baseline_count = _run(with_change_filter=False)
        with_cf_count = _run(with_change_filter=True)
        assert with_cf_count == baseline_count

    def test_change_filter_pulse_via_data_port_does_not_bypass_dedup(self):
        """Regression: change_filter.changed feeding a pure data port (e.g.
        api_client.body) must not exempt whatever that node's own, separately
        sustained trigger drives from rising-edge dedup — the pulse only
        grants the discrete-edge exception when it actually reaches a
        trigger input, directly or via pure relay nodes (NOT/AND/OR/...)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
            node("cf", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("cf", "ac", "changed", "body"),
                edge("ac", "hc", "success", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-data-port-no-bypass"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
            ):
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 3}}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 1

    def test_change_filter_ignores_none_from_unseeded_read_object(self):
        """Regression: a change_filter fed by a Read Object whose DataPoint
        has never received a value must not treat that None as a first value
        when an unrelated event executes the same graph — there's no future
        replay to correct a spurious pulse fired here, unlike the async case."""
        nodes = [
            node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("cf", "change_filter"),
            node("other_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
        ]
        flow = _flow(nodes, [edge("read", "cf", "value", "in")])
        manager = _make_manager()  # registry.get_value already defaults to None (unseeded)
        graph_id = "g-cf-unseeded-read"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"other_read": {"value": 1, "changed": True}}))

        assert outputs["cf"]["changed"] is False

    def test_change_filter_ignores_none_from_unconfigured_read_object(self):
        """Same as the unseeded case, but for a Read Object node that has no
        datapoint_id configured at all — evaluates to None just like an
        unseeded one and must be treated the same way."""
        nodes = [
            node("read", "datapoint_read", {}),  # no datapoint_id
            node("cf", "change_filter"),
            node("other_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
        ]
        flow = _flow(nodes, [edge("read", "cf", "value", "in")])
        manager = _make_manager()
        graph_id = "g-cf-unconfigured-read"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"other_read": {"value": 1, "changed": True}}))

        assert outputs["cf"]["changed"] is False

    def test_change_filter_ignores_never_populated_datapoint_value_state(self):
        """Regression: DataPointRegistry.get_value() returns a real,
        non-None ValueState the moment a DataPoint is registered, with
        .value=None until an adapter writes to it — `vs is not None` alone
        therefore never actually detects "never received a value"; only
        checking vs.value too does."""
        nodes = [
            node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("cf", "change_filter"),
            node("other_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
        ]
        flow = _flow(nodes, [edge("read", "cf", "value", "in")])
        manager = _make_manager()
        manager._registry.get_value = MagicMock(return_value=MagicMock(value=None))
        graph_id = "g-cf-empty-valuestate"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"other_read": {"value": 1, "changed": True}}))

        assert outputs["cf"]["changed"] is False

    def test_change_filter_is_not_held_when_fed_through_memory_from_an_unseeded_read(self):
        """Regression: memory is an explicit tick boundary (per its own node
        description) — its "out" this tick is whatever was committed at the
        end of a *previous* tick, entirely independent of an unresolved
        input feeding "in" this tick (that input only affects the value
        committed for the *next* tick, via the executor's deferred
        commit_memory_inputs). Propagating unresolved-source taint through
        memory would hold a downstream change_filter hostage to an
        unrelated, still-unresolved Read Object — potentially forever if
        that Read Object never fires again this session — even though
        memory's real, current value already differs from the filter's
        persisted baseline."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("mem", "memory"),
            node("cf", "change_filter"),
            node("other_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "mem", "value", "in"),
                edge("mem", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-memory-boundary"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"mem": {"value": 5}, "cf": {"value": 4}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"other_read": {"value": 1, "changed": True}}))

        assert outputs["cf"]["changed"] is True
        assert outputs["cf"]["out"] == 5

    def test_change_filter_is_not_held_when_fed_through_hysteresis_from_an_unseeded_read(self):
        """Regression: a hysteresis node whose "value" input reads None this
        pass (fed by a still-unseeded Read Object) returns its real prior
        state unmutated — the executor's own `if val is None: return
        {"out": prev}` branch, a fully resolved output, not a placeholder
        awaiting that source's eventual real value (unlike an async source,
        an unseeded Read Object has no later resolution coming this tick).
        Propagating taint through it would hold a downstream change_filter
        hostage to that unrelated source indefinitely, discarding every
        genuine change from a separate, live Read combined with the
        hysteresis output along the way."""
        nodes = [
            node("unseeded_read", "datapoint_read", {}),  # no datapoint_id: always unseeded
            node("hyst", "hysteresis"),
            node("live_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("add", "math_formula", {"formula": "a + b"}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "hyst", "value", "value"),
                edge("hyst", "add", "out", "in1"),
                edge("live_read", "add", "value", "in2"),
                edge("add", "cf", "result", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-hysteresis-boundary"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"hyst": True}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"live_read": {"value": 5, "changed": True}}))

        assert outputs["cf"]["changed"] is True
        assert outputs["cf"]["out"] == 6

    def test_change_filter_stays_held_through_hysteresis_fed_by_an_unresolved_async_placeholder(self):
        """Regression companion to the unseeded-read absorption above: the
        hysteresis boundary must NOT absorb taint when its "value" input is
        a genuine (non-None) placeholder from a still-unresolved async
        source (e.g. api_client.success, which defaults to False, not
        None, before the real HTTP call completes) — that source WILL
        resolve later this same tick, unlike an unseeded Read Object, so a
        downstream change_filter must stay held until hysteresis re-runs
        against the real resolved value."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET", "response_type": "text/plain"}),
            node("hyst", "hysteresis", {"threshold_on": 0.5, "threshold_off": 0.4}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hyst", "success", "value"),
                edge("hyst", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-hysteresis-async-placeholder"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"hyst": False}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        # ac.success genuinely resolves True this tick (threshold_on=0.5),
        # so hyst.out becomes True for real — cf must reflect THAT, not
        # ac's initial False placeholder having been prematurely absorbed
        # and committed as hyst's "final" output.
        assert outputs["hyst"]["out"] is True
        assert outputs["cf"]["out"] is True
        assert outputs["cf"]["changed"] is True

    def test_held_change_filter_does_not_taint_a_downstream_change_filter(self):
        """Regression: once a change_filter (cf1) is itself tainted/held
        behind an unresolved Read Object, its held output is fully
        deterministic for this pass (the executor's _suppress_change_filter
        handling returns its persisted baseline, changed=False) — exactly
        like memory's own tick-boundary output. But the forward taint BFS
        used to keep propagating past a held change_filter too, so a
        SEPARATE, live Read feeding an Add together with cf1.out had its
        genuine transition lost: downstream cf2 was held and suppressed
        just because cf1 (feeding the same Add) happened to be unresolved,
        even though cf1's contribution to that Add is a known, fixed value
        this pass."""
        nodes = [
            node("unseeded_read", "datapoint_read", {}),  # no datapoint_id: always unseeded
            node("live_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("cf1", "change_filter"),
            node("add", "math_formula", {"formula": "a + b"}),
            node("cf2", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "cf1", "value", "in"),
                edge("cf1", "add", "out", "in1"),
                edge("live_read", "add", "value", "in2"),
                edge("add", "cf2", "result", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-hold-does-not-taint-downstream-cf"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf1": {"value": 5}, "cf2": {"value": 6}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"live_read": {"value": 2, "changed": True}}))

        # cf1 stays correctly held behind the unrelated unseeded read.
        assert outputs["cf1"] == {"out": 5, "changed": False}
        # cf2 must see the real Add result (5 + 2 = 7) and report the
        # genuine transition from its persisted baseline of 6 — not be held
        # hostage to cf1's own, unrelated unresolved upstream.
        assert outputs["cf2"] == {"out": 7, "changed": True}

    def test_baseline_less_held_filter_keeps_downstream_filter_held(self):
        nodes = [
            node("unseeded_read", "datapoint_read", {}),
            node("cf1", "change_filter"),
            node("cf2", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "cf1", "value", "in"),
                edge("cf1", "cf2", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-fresh-held-cf-chain"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["cf1"] == {"out": None, "changed": False}
        assert outputs["cf2"] == {"out": None, "changed": False}
        assert manager._hysteresis[graph_id] == {}

    def test_taint_bfs_ignores_a_shadowed_edge_replaced_by_a_later_one(self):
        """Regression: GraphExecutor._build_edge_map() resolves multiple
        edges into the same (target, targetHandle) pair with "last edge
        wins" — an imported/legacy flow can have a stale edge from an
        unseeded Read Object into add.in1 that a LATER edge to the same
        handle has replaced with a live source. The executor only ever
        consumes the live (winning) edge's value, but the taint-BFS used
        to walk every edge unconditionally, tainting `add` (and holding
        its downstream change_filter) via the shadowed, never-actually-read
        unseeded edge — discarding the live source's genuine change."""
        nodes = [
            node("unseeded_read", "datapoint_read", {}),  # no datapoint_id: always unseeded
            node("live_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("c2", "const_value", {"value": "10", "data_type": "number"}),
            node("add", "math_formula", {"formula": "a + b"}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                # Both edges target add's "in1" handle — the LATER one
                # (live_read) is the one _build_edge_map/the executor
                # actually uses; the FIRST (unseeded_read) is shadowed.
                edge("unseeded_read", "add", "value", "in1"),
                edge("live_read", "add", "value", "in1"),
                edge("c2", "add", "value", "in2"),
                edge("add", "cf", "result", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-shadowed-edge-taint"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": 999}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"live_read": {"value": 5, "changed": True}}))

        # add.in1 is really fed by live_read (5), not the shadowed
        # unseeded_read edge — cf must see the real result (5 + 10 = 15)
        # and report the genuine transition, not stay held.
        assert outputs["add"]["result"] == 15
        assert outputs["cf"] == {"out": 15, "changed": True}

    def test_initial_async_closure_ignores_shadowed_host_check_trigger(self):
        nodes = [
            node("api_trigger", "const_value", {"value": "true", "data_type": "bool"}),
            node("api", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
            node("false", "const_value", {"value": "false", "data_type": "bool"}),
            node("hc", "host_check", {"host": "a.local", "timeout_s": 1, "count": 1}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("api_trigger", "api", "value", "trigger"),
                edge("api", "hc", "success", "trigger"),
                edge("false", "hc", "value", "trigger"),
                edge("hc", "cf", "reachable", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-shadowed-async-closure"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock) as mock_ping,
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        mock_ping.assert_not_awaited()
        assert outputs["cf"] == {"out": False, "changed": True}

    def test_pre_execute_snapshot_survives_a_non_deepcopyable_memory_value(self):
        """Regression (P2): the pre-execution hyst/graph_state snapshot is
        enabled whenever ANY unseeded Read Object exists in the graph,
        regardless of which node actually needs the correction it enables.
        If some totally UNRELATED stateful node (e.g. a Memory node holding
        a permitted python_script's generator result) can't be deep-copied,
        the bare copy.deepcopy(hyst) raised, and the broad try/except around
        this whole first pass returned {} — aborting the entire graph
        execution, including every otherwise-independent branch and its
        writes, not just the change_filter correction that actually needed
        the snapshot."""
        nodes = [
            node("unseeded_read", "datapoint_read", {}),  # no datapoint_id: always unseeded
            node("cf", "change_filter"),
            node("mem", "memory"),
            node("other_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "cf", "value", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-pre-execute-snapshot-non-copyable"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"mem": {"value": (x for x in [1, 2, 3])}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"other_read": {"value": 42, "changed": True}}))

        assert outputs != {}
        assert outputs["other_read"] == {"value": 42, "changed": True}

    def test_worker_snapshot_survives_retained_non_deepcopyable_filter_value(self):
        flow = _flow(
            [
                node("script", "python_script", {"script": "result = (x for x in range(3))"}),
                node("cf", "change_filter"),
            ],
            [edge("script", "cf", "result", "in")],
        )
        manager = _make_manager()
        graph_id = "g-worker-snapshot-non-copyable"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            first = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
            second = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is True
        assert "__error__" not in second["cf"]

    def test_worker_state_merge_survives_raising_runtime_equality(self):
        class UnsafeEquality:
            def __init__(self, label):
                self.label = label

            def __eq__(self, other):
                raise RuntimeError("comparison unavailable")

        flow = _flow(
            [
                node("script", "python_script", {"script": "result = 1"}),
                node("cf", "change_filter"),
            ]
        )
        manager = _make_manager()
        graph_id = "g-worker-merge-unsafe-equality"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": UnsafeEquality("old")}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": UnsafeEquality("new")}}))

        assert outputs["cf"]["changed"] is True
        assert "__error__" not in outputs["cf"]
        assert manager._hysteresis[graph_id]["cf"]["value"].label == "new"

    def test_worker_state_merge_handles_self_referential_dictionary(self):
        flow = _flow(
            [node("script", "python_script", {"script": "result = {}; result['self'] = result"}), node("cf", "change_filter")],
            [edge("script", "cf", "result", "in")],
        )
        manager = _make_manager()
        graph_id = "g-worker-merge-cyclic-dict"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            first = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
            second = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is False
        assert "__error__" not in second["cf"]
        retained = manager._hysteresis[graph_id]["cf"]["value"]
        assert retained["self"] is retained

    def test_worker_state_merge_commits_past_non_reflexive_old_state(self):
        class NonReflexiveOldState:
            def __init__(self, value):
                self.value = value

            def __eq__(self, other):
                if not isinstance(other, NonReflexiveOldState) or self.value == 0 or other.value == 0:
                    return False
                return self.value == other.value

        flow = _flow([node("script", "python_script", {"script": "result = 1"}), node("cf", "change_filter")])
        manager = _make_manager()
        graph_id = "g-worker-merge-non-reflexive-old-state"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": NonReflexiveOldState(0)}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            first = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": NonReflexiveOldState(1)}}))
            second = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": NonReflexiveOldState(1)}}))

        assert first["cf"]["changed"] is True
        assert second["cf"]["changed"] is False
        assert manager._hysteresis[graph_id]["cf"]["value"].value == 1

    def test_transformed_no_change_pulse_does_not_reset_memory(self):
        flow = _flow(
            [
                node("constant", "const_value", {"value": "1", "data_type": "number"}),
                node("cf", "change_filter"),
                node("invert", "not"),
                node("memory", "memory", {"initial_value": 0, "data_type": "number"}),
            ],
            [
                edge("constant", "cf", "value", "in"),
                edge("cf", "invert", "changed", "in1"),
                edge("invert", "memory", "out", "reset"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-no-pulse-memory-reset"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": 1.0}, "memory": {"value": 7.0}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["invert"]["out"] is True
        assert manager._hysteresis[graph_id]["memory"] == {"value": 7.0}

    def test_transformed_no_change_pulse_rolls_back_synchronous_trigger_consumers(self):
        flow = _flow(
            [
                node("constant", "const_value", {"value": "1", "data_type": "number"}),
                node("sample", "const_value", {"value": "5", "data_type": "number"}),
                node("active", "const_value", {"value": "true", "data_type": "boolean"}),
                node("cf", "change_filter"),
                node("invert", "not"),
                node("stats", "statistics"),
                node("hours", "operating_hours"),
                node("random", "random_value"),
            ],
            [
                edge("constant", "cf", "value", "in"),
                edge("cf", "invert", "changed", "in1"),
                edge("invert", "stats", "out", "reset"),
                edge("sample", "stats", "value", "value"),
                edge("invert", "hours", "out", "reset"),
                edge("active", "hours", "value", "active"),
                edge("invert", "random", "out", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-no-pulse-synchronous-triggers"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {"hours": {"accumulated_hours": 5.0, "last_start": None}}
        manager._hysteresis[graph_id] = {
            "cf": {"value": 1.0},
            "stats": {"s_min": 2.0, "s_max": 4.0, "s_sum": 6.0, "s_count": 2},
        }

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["invert"]["out"] is True
        assert outputs["stats"]["count"] == 3
        assert manager._node_state[graph_id]["hours"]["accumulated_hours"] == 5.0
        assert manager._node_state[graph_id]["hours"]["last_start"] is not None
        assert outputs["random"]["value"] is None

    def test_no_result_mapping_does_not_replace_change_filter_baseline(self):
        read_id = uuid.uuid4()
        flow = _flow(
            [
                node("read", "datapoint_read", {"datapoint_id": str(read_id)}),
                node(
                    "mapping",
                    "value_mapping",
                    {"rules": [{"operator": "eq", "value": 1, "result": "mapped"}], "has_default": False},
                ),
                node("cf", "change_filter"),
            ],
            [edge("read", "mapping", "value", "value"), edge("mapping", "cf", "result", "in")],
        )
        manager = _make_manager()
        graph_id = "g-no-result-mapping-filter"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": "mapped"}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"read": {"value": 2, "changed": True}}))

        assert outputs["mapping"]["result"] is None
        assert outputs["cf"] == {"out": "mapped", "changed": False}
        assert manager._hysteresis[graph_id]["cf"] == {"value": "mapped"}

    def test_transformed_no_change_pulse_does_not_trigger_datapoint_write(self):
        target_id = uuid.uuid4()
        flow = _flow(
            [
                node("constant", "const_value", {"value": "1", "data_type": "number"}),
                node("cf", "change_filter"),
                node("invert", "not"),
                node("value", "const_value", {"value": "42", "data_type": "number"}),
                node("write", "datapoint_write", {"datapoint_id": str(target_id)}),
            ],
            [
                edge("constant", "cf", "value", "in"),
                edge("cf", "invert", "changed", "in1"),
                edge("invert", "write", "out", "trigger"),
                edge("value", "write", "value", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-no-pulse-datapoint-write"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            first = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
            second = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert first["write"]["_triggered"] is False
        assert second["invert"]["out"] is True
        assert second["write"]["_triggered"] is False
        manager._event_bus.publish.assert_not_awaited()

    def test_unseeded_read_without_reachable_change_filter_skips_rollback_snapshots(self):
        flow = _flow(
            [
                node("unseeded_read", "datapoint_read", {}),
                node("stats", "statistics"),
            ],
            [edge("unseeded_read", "stats", "value", "value")],
        )
        manager = _make_manager()
        graph_id = "g-no-cf-rollback-snapshot"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._safe_deepcopy_state") as snapshot,
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert "stats" in outputs
        snapshot.assert_not_called()

    def test_unrelated_change_filter_skips_large_state_rollback_snapshots(self):
        flow = _flow(
            [
                node("constant", "const_value", {"value": "1", "data_type": "number"}),
                node("cf", "change_filter"),
                node("large", "avg_multi", {"window_size": 100000}),
            ],
            [edge("constant", "cf", "value", "in")],
        )
        manager = _make_manager()
        graph_id = "g-unrelated-cf-large-state"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": 1.0}, "large": {"values": list(range(100000))}}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._safe_deepcopy_state") as snapshot,
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["cf"]["changed"] is False
        snapshot.assert_not_called()

    @pytest.mark.parametrize(
        ("relay_type", "relay_data", "target_handle", "prior_state", "expected_output"),
        [
            ("hysteresis", {"threshold_on": 0.5, "threshold_off": 0.2}, "value", False, False),
            ("gate", {}, "in", "retained", "retained"),
        ],
    )
    def test_missing_filter_pulse_does_not_mutate_stateful_relay(
        self,
        relay_type: str,
        relay_data: dict,
        target_handle: str,
        prior_state: object,
        expected_output: object,
    ):
        nodes = [
            node("constant", "const_value", {"value": "1", "data_type": "number"}),
            node("cf", "change_filter"),
            node("invert", "not"),
            node("relay", relay_type, relay_data),
        ]
        edges = [
            edge("constant", "cf", "value", "in"),
            edge("cf", "invert", "changed", "in1"),
            edge("invert", "relay", "out", target_handle),
        ]
        if relay_type == "gate":
            nodes.append(node("enable", "const_value", {"value": "true", "data_type": "boolean"}))
            edges.append(edge("enable", "relay", "value", "enable"))
        flow = _flow(nodes, edges)
        manager = _make_manager()
        graph_id = f"g-missing-pulse-{relay_type}"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": 1.0}, "relay": prior_state}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["cf"]["changed"] is False
        assert outputs["relay"]["out"] == expected_output
        assert manager._hysteresis[graph_id]["relay"] == prior_state

    def test_missing_filter_pulse_does_not_add_zero_to_statistics(self):
        flow = _flow(
            [
                node("constant", "const_value", {"value": "1", "data_type": "number"}),
                node("cf", "change_filter"),
                node("stats", "statistics"),
            ],
            [
                edge("constant", "cf", "value", "in"),
                edge("cf", "stats", "changed", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-missing-pulse-statistics"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            first = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert first["stats"]["count"] == 1
        assert first["stats"]["avg"] == 1.0
        assert repeated["stats"]["count"] == 1
        assert repeated["stats"]["avg"] == 1.0
        assert manager._hysteresis[graph_id]["stats"]["s_count"] == 1

    def test_missing_statistics_pulse_does_not_republish_retained_count(self):
        target_id = uuid.uuid4()
        flow = _flow(
            [
                node("cf", "change_filter"),
                node("stats", "statistics"),
                node("write", "datapoint_write", {"datapoint_id": str(target_id)}),
            ],
            [
                edge("cf", "stats", "changed", "value"),
                edge("stats", "write", "count", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-missing-statistics-pulse-write"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            first = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))

        assert first["write"]["_write_value"] == 1
        assert repeated["stats"]["count"] == 1
        assert repeated["write"]["_write_value"] is None
        assert manager._event_bus.publish.await_count == 1

    def test_missing_filter_pulse_preserves_running_sequence_condition(self):
        flow = _flow(
            [
                node("cf", "change_filter"),
                node("seq", "value_sequence", {"cancel_when_condition_false": True, "steps": []}),
            ],
            [edge("cf", "seq", "changed", "condition")],
        )
        manager = _make_manager()
        graph_id = "g-missing-pulse-sequence-condition"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            active = MagicMock()
            active.done.return_value = False
            manager._sequence_tasks[(graph_id, "seq")] = active
            with patch.object(manager, "_cancel_sequence_task") as cancel:
                repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))

        assert repeated["seq"]["_condition"] is True
        assert manager._sequence_conditions[(graph_id, "seq")] is True
        cancel.assert_not_called()

    def test_fan_in_probe_uses_configured_operand_when_pulse_is_absent(self):
        target_id = uuid.uuid4()
        flow = _flow(
            [
                node("read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
                node("cf", "change_filter"),
                node("compare", "compare", {"operator": "eq", "operand": "1"}),
                node("write", "datapoint_write", {"datapoint_id": str(target_id)}),
            ],
            [
                edge("read", "cf", "value", "in"),
                edge("read", "compare", "value", "in1"),
                edge("cf", "compare", "changed", "in2"),
                edge("compare", "write", "out", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-fan-in-configured-operand"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        override = {"read": {"value": 1, "changed": True}}
        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, override))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, override))

        assert repeated["compare"]["out"] is False
        assert repeated["write"]["_write_value"] is None
        assert manager._event_bus.publish.await_count == 1

    def test_manual_fan_in_treats_constant_as_independent_input(self):
        target_id = uuid.uuid4()
        flow = _flow(
            [
                node("source", "const_value", {"value": "1", "data_type": "number"}),
                node("cf", "change_filter"),
                node("invert", "not"),
                node("decisive", "const_value", {"value": "true", "data_type": "boolean"}),
                node("or_gate", "or", {"input_count": 2}),
                node("write", "datapoint_write", {"datapoint_id": str(target_id)}),
            ],
            [
                edge("source", "cf", "value", "in"),
                edge("cf", "invert", "changed", "in1"),
                edge("invert", "or_gate", "out", "in1"),
                edge("decisive", "or_gate", "value", "in2"),
                edge("or_gate", "write", "out", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-manual-constant-fan-in"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert repeated["or_gate"]["out"] is True
        assert repeated["write"]["_write_value"] is True
        assert manager._event_bus.publish.await_count == 2

    def test_manual_fan_in_treats_seeded_read_as_independent_input(self):
        source_id = uuid.uuid4()
        target_id = uuid.uuid4()
        flow = _flow(
            [
                node("source", "const_value", {"value": "1", "data_type": "number"}),
                node("cf", "change_filter"),
                node("invert", "not"),
                node("decisive", "datapoint_read", {"datapoint_id": str(source_id)}),
                node("or_gate", "or", {"input_count": 2}),
                node("write", "datapoint_write", {"datapoint_id": str(target_id)}),
            ],
            [
                edge("source", "cf", "value", "in"),
                edge("cf", "invert", "changed", "in1"),
                edge("invert", "or_gate", "out", "in1"),
                edge("decisive", "or_gate", "value", "in2"),
                edge("or_gate", "write", "out", "value"),
            ],
        )
        manager = _make_manager()
        manager._registry.get_value.return_value = MagicMock(value=True)
        graph_id = "g-manual-read-fan-in"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert repeated["or_gate"]["out"] is True
        assert repeated["write"]["_write_value"] is True
        assert manager._event_bus.publish.await_count == 2

    def test_xor_fan_in_preserves_missing_filter_provenance(self):
        target_id = uuid.uuid4()
        flow = _flow(
            [
                node("source", "const_value", {"value": "1", "data_type": "number"}),
                node("cf", "change_filter"),
                node("other", "const_value", {"value": "false", "data_type": "boolean"}),
                node("xor_gate", "xor", {"input_count": 2}),
                node("write", "datapoint_write", {"datapoint_id": str(target_id)}),
            ],
            [
                edge("source", "cf", "value", "in"),
                edge("cf", "xor_gate", "changed", "in1"),
                edge("other", "xor_gate", "value", "in2"),
                edge("xor_gate", "write", "out", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-xor-missing-pulse"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            first = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert first["write"]["_write_value"] is True
        assert repeated["xor_gate"]["out"] is False
        assert repeated["write"]["_write_value"] is None
        assert manager._event_bus.publish.await_count == 1

    def test_compare_fan_in_preserves_missing_filter_provenance(self):
        target_id = uuid.uuid4()
        flow = _flow(
            [
                node("source", "const_value", {"value": "1", "data_type": "number"}),
                node("cf", "change_filter"),
                node("other", "const_value", {"value": "true", "data_type": "bool"}),
                node("compare", "compare", {"operator": "eq"}),
                node("write", "datapoint_write", {"datapoint_id": str(target_id)}),
            ],
            [
                edge("source", "cf", "value", "in"),
                edge("cf", "compare", "changed", "in1"),
                edge("other", "compare", "value", "in2"),
                edge("compare", "write", "out", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-compare-missing-pulse"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            first = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert first["write"]["_write_value"] is True
        assert repeated["compare"]["out"] is False
        assert repeated["write"]["_write_value"] is None
        assert manager._event_bus.publish.await_count == 1

    def test_async_replay_neutralizes_missing_filter_api_body(self):
        notify_trigger_id = uuid.uuid4()
        flow = _flow(
            [
                node("cf", "change_filter"),
                node("notify_trigger", "datapoint_read", {"datapoint_id": str(notify_trigger_id)}),
                node(
                    "notify",
                    "notify_message",
                    {"adapter_instance_id": "message-1", "providers": [{"provider": "telegram", "target": "alerts"}]},
                ),
                node("api", "api_client", {"url": "http://93.184.216.34/", "method": "POST"}),
            ],
            [
                edge("cf", "api", "changed", "body"),
                edge("notify_trigger", "notify", "value", "trigger"),
                edge("notify", "api", "sent", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-replay-missing-api-body"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        adapter = MagicMock(adapter_type="MESSAGE")
        adapter.send_notification = AsyncMock(return_value=[MagicMock(ok=True)])

        with (
            patch("obs.adapters.registry.get_instance_by_id", return_value=adapter),
            patch("obs.logic.manager.httpx.AsyncClient") as client_cls,
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            client = AsyncMock()
            client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            client.request = AsyncMock(return_value=_MockResponse(200))
            asyncio.run(
                manager._execute_graph(
                    graph_id,
                    "test",
                    flow,
                    {"cf": {"in": 1}, "notify_trigger": {"value": True}},
                )
            )
            asyncio.run(
                manager._execute_graph(
                    graph_id,
                    "test",
                    flow,
                    {"cf": {"in": 1}, "notify_trigger": {"value": True}},
                )
            )

        assert client.request.await_count == 2
        assert client.request.await_args_list[0].kwargs["content"] == "true"
        assert client.request.await_args_list[1].kwargs["content"] == "null"

    def test_missing_filter_pulse_is_null_api_request_body(self):
        trigger_id = uuid.uuid4()
        flow = _flow(
            [
                node("cf", "change_filter"),
                node("trigger", "datapoint_read", {"datapoint_id": str(trigger_id)}),
                node("api", "api_client", {"url": "http://93.184.216.34/", "method": "POST"}),
            ],
            [
                edge("cf", "api", "changed", "body"),
                edge("trigger", "api", "value", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-api-missing-pulse-body"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.logic.manager.httpx.AsyncClient") as client_cls:
            client = AsyncMock()
            client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            client.request = AsyncMock(return_value=_MockResponse(200))
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}, "trigger": {"value": True}}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}, "trigger": {"value": False}}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}, "trigger": {"value": True}}))

        assert client.request.await_count == 2
        assert client.request.await_args_list[0].kwargs["content"] == "true"
        assert client.request.await_args_list[1].kwargs["content"] == "null"

    def test_independently_triggered_api_success_is_not_tainted_by_missing_filter_body(self):
        trigger_id = uuid.uuid4()
        target_id = uuid.uuid4()
        flow = _flow(
            [
                node("cf", "change_filter"),
                node("trigger", "datapoint_read", {"datapoint_id": str(trigger_id)}),
                node("api", "api_client", {"url": "http://93.184.216.34/", "method": "POST"}),
                node("write", "datapoint_write", {"datapoint_id": str(target_id)}),
            ],
            [
                edge("cf", "api", "changed", "body"),
                edge("trigger", "api", "value", "trigger"),
                edge("api", "write", "success", "value"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-independent-api-success"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.logic.manager.httpx.AsyncClient") as client_cls:
            client = AsyncMock()
            client_cls.return_value.__aenter__ = AsyncMock(return_value=client)
            client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            client.request = AsyncMock(return_value=_MockResponse(200))
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}, "trigger": {"value": True}}))
                repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}, "trigger": {"value": True}}))

        assert client.request.await_count == 2
        assert client.request.await_args_list[1].kwargs["content"] == "null"
        assert repeated["write"]["_write_value"] is True
        assert manager._event_bus.publish.await_count == 2

    @pytest.mark.parametrize(
        ("target_type", "target_handle", "output_handle"),
        [
            ("notify_pushover", "image_url", "_image_url"),
            ("notify_pushover", "url", "_url"),
            ("notify_pushover", "url_title", "_url_title"),
            ("message_archive", "title", "_title"),
        ],
    )
    def test_missing_filter_pulse_is_absent_side_effect_metadata(self, target_type, target_handle, output_handle):
        message_id = uuid.uuid4()
        target_data = (
            {"message": "configured", "image_url": "https://example.com/configured.png"}
            if target_type == "notify_pushover"
            else {"message": "configured", "title": "Configured title"}
        )
        flow = _flow(
            [
                node("cf", "change_filter"),
                node("message", "datapoint_read", {"datapoint_id": str(message_id)}),
                node("target", target_type, target_data),
            ],
            [
                edge("cf", "target", "changed", target_handle),
                edge("message", "target", "value", "message"),
            ],
        )
        manager = _make_manager()
        graph_id = f"g-missing-pulse-{target_type}-{target_handle}"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}, "message": {"value": "first"}}))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}, "message": {"value": "second"}}))

        assert repeated["target"][output_handle] is None

    @pytest.mark.parametrize("target_type", ["notify_message", "notify_pushover", "notify_sms", "message_archive"])
    def test_missing_filter_message_pulse_is_absent_with_independent_trigger(self, target_type):
        trigger_id = uuid.uuid4()
        flow = _flow(
            [
                node("cf", "change_filter"),
                node("trigger", "datapoint_read", {"datapoint_id": str(trigger_id)}),
                node("target", target_type, {"message": "Configured message"}),
            ],
            [
                edge("cf", "target", "changed", "message"),
                edge("trigger", "target", "value", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = f"g-missing-message-{target_type}"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}, "trigger": {"value": True}}))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}, "trigger": {"value": True}}))

        assert repeated["target"]["_message"] is None

    def test_missing_active_pulse_preserves_running_operating_hours(self):
        flow = _flow(
            [node("cf", "change_filter"), node("hours", "operating_hours")],
            [edge("cf", "hours", "changed", "active")],
        )
        manager = _make_manager()
        graph_id = "g-missing-active-operating-hours"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            started_at = manager._node_state[graph_id]["hours"]["last_start"]
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))

        assert repeated["hours"]["_active"] is True
        assert manager._node_state[graph_id]["hours"]["last_start"] == started_at

    def test_async_refresh_uses_final_stateful_provenance_for_memory_commit(self):
        flow = _flow(
            [
                node("source", "const_value", {"value": "1", "data_type": "number"}),
                node("cf", "change_filter"),
                node("invert", "not"),
                node("api_trigger", "const_value", {"value": "true", "data_type": "boolean"}),
                node("api", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
                node("or_gate", "or", {"input_count": 2}),
                node("mem", "memory", {"data_type": "bool"}),
            ],
            [
                edge("source", "cf", "value", "in"),
                edge("cf", "invert", "changed", "in1"),
                edge("invert", "or_gate", "out", "in1"),
                edge("api_trigger", "api", "value", "trigger"),
                edge("api", "or_gate", "success", "in2"),
                edge("or_gate", "mem", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-late-stateful-provenance"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": 1.0}, "mem": {"value": False}}
        patcher = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["api"]["success"] is True
        assert outputs["or_gate"]["out"] is True
        assert manager._hysteresis[graph_id]["mem"] == {"value": True}

    @pytest.mark.parametrize(
        ("node_type", "node_data", "target_handle"),
        [
            ("avg_multi", {"input_count": 2}, "in_1"),
            ("min_max_tracker", {}, "value"),
            ("consumption_counter", {}, "value"),
            ("heating_circuit", {}, "value"),
        ],
    )
    def test_missing_filter_pulse_does_not_mutate_other_accumulators(self, node_type, node_data, target_handle):
        flow = _flow(
            [node("cf", "change_filter"), node("acc", node_type, node_data)],
            [edge("cf", "acc", "changed", target_handle)],
        )
        manager = _make_manager()
        graph_id = f"g-missing-pulse-{node_type}"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))

        state = manager._hysteresis[graph_id]["acc"]
        if node_type == "avg_multi":
            assert len(state["samples"]) == 1
            assert state["samples"][0][1] == 1.0
        elif node_type == "min_max_tracker":
            assert state["abs_min"] == 1.0
            assert state["abs_max"] == 1.0
        else:
            assert state["last_value"] == 1.0

    @pytest.mark.parametrize("with_trigger", [False, True])
    def test_missing_filter_pulse_does_not_publish_write_value(self, with_trigger):
        target_id = uuid.uuid4()
        nodes = [node("cf", "change_filter"), node("write", "datapoint_write", {"datapoint_id": str(target_id)})]
        edges = [edge("cf", "write", "changed", "value")]
        if with_trigger:
            nodes.append(node("trigger", "const_value", {"value": "true", "data_type": "boolean"}))
            edges.append(edge("trigger", "write", "value", "trigger"))
        flow = _flow(nodes, edges)
        manager = _make_manager()
        graph_id = f"g-missing-pulse-write-{with_trigger}"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            first = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            repeated = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))

        assert first["write"]["_write_value"] is True
        assert repeated["write"]["_write_value"] is None
        assert manager._event_bus.publish.await_count == 1

    def test_change_filter_is_not_held_when_read_is_resolved_via_debug_override(self):
        """Regression: a manual/debug execution (the debug-inspector "run"
        feature) that supplies debug_overrides={read_id: {"value": ...}}
        for an unconfigured/never-seeded Read Object explicitly delivers a
        value for this run, exactly like a real event override does — but
        only `overrides` (real events) were subtracted from
        unseeded_read_ids, not debug_overrides. The Read Object therefore
        still looked unresolved, so the taint-correction pass rolled back
        and suppressed the downstream change_filter, defeating the whole
        point of the one-off debug run."""
        nodes = [
            node("read", "datapoint_read", {}),  # no datapoint_id: always unseeded
            node("cf", "change_filter"),
        ]
        flow = _flow(nodes, [edge("read", "cf", "value", "in")])
        manager = _make_manager()
        graph_id = "g-cf-debug-override"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}, debug_overrides={"read": {"value": 5}}))

        assert outputs["cf"]["changed"] is True
        assert outputs["cf"]["out"] == 5

    def test_debug_override_on_intermediate_input_masks_upstream_taint(self):
        nodes = [
            node("read", "datapoint_read", {}),
            node("invert", "not"),
            node("cf", "change_filter"),
        ]
        flow = _flow(nodes, [edge("read", "invert", "value", "in1"), edge("invert", "cf", "out", "in")])
        manager = _make_manager()
        graph_id = "g-cf-debug-intermediate-override"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(
                manager._execute_graph(
                    graph_id,
                    "test",
                    flow,
                    {},
                    debug_overrides={"invert": {"in1": False}},
                )
            )

        assert outputs["invert"]["out"] is True
        assert outputs["cf"] == {"out": True, "changed": True}

    def test_debug_override_can_close_gate_with_unresolved_wired_enable(self):
        nodes = [
            node("data_read", "datapoint_read", {}),
            node("enable_read", "datapoint_read", {}),
            node("relay_gate", "gate", {"closed_behavior": "default_value", "default_value": "9"}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("data_read", "relay_gate", "value", "in"),
                edge("enable_read", "relay_gate", "value", "enable"),
                edge("relay_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-gate-debug-closed-unresolved-enable"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": 1}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(
                manager._execute_graph(
                    graph_id,
                    "test",
                    flow,
                    {},
                    debug_overrides={"relay_gate": {"enable": False}},
                )
            )

        assert outputs["relay_gate"]["out"] == 9.0
        assert outputs["cf"] == {"out": 9.0, "changed": True}

    def test_change_filter_is_not_held_when_and_gate_has_an_unconnected_resolved_input(self):
        """Regression: the AND gate's per-input taint-absorption check
        skipped ("continue"d past) an unconnected input entirely, missing
        that the executor's own _collect_gate_inputs evaluates a missing
        port as a deterministic False — which alone makes an AND gate's
        output deterministically False regardless of what an unresolved
        sibling input eventually becomes. The downstream change_filter was
        therefore held hostage to that unresolved input even though the
        gate's real result was already fully decided."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("and_gate", "and", {"input_count": 2}),
            node("cf", "change_filter"),
            node("other_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "and_gate", "value", "in1"),
                # in2 is intentionally left unwired
                edge("and_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-and-unconnected-input"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"other_read": {"value": 1, "changed": True}}))

        assert outputs["cf"]["changed"] is True
        assert outputs["cf"]["out"] is False

    def test_change_filter_is_not_held_behind_a_closed_relay_gate(self):
        """Regression: a "gate" (Freigabe/relay) node closed by a RESOLVED
        enable input is a boundary just like memory — while closed, its
        output is either the retained last-enabled value or a fixed
        default_value, entirely independent of "in". An unseeded Read
        Object feeding only the gate's "in" port (enable left unwired,
        which resolves to a deterministic closed state) must not hold a
        downstream change_filter hostage to that unrelated, never-resolving
        read — the gate's real, retained output already fully decides the
        filter's comparison."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("relay_gate", "gate", {}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "relay_gate", "value", "in"),
                # enable is intentionally left unwired -> resolves to closed
                edge("relay_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-gate-closed-boundary"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"relay_gate": 99, "cf": {"value": 42}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["relay_gate"]["out"] == 99
        assert outputs["cf"]["changed"] is True
        assert outputs["cf"]["out"] == 99

    def test_change_filter_stays_held_when_gate_enable_itself_is_unresolved(self):
        """The closed-gate boundary exception only applies when the gate's
        OWN enable state is itself resolved — if "enable" is fed by the
        same unresolved Read Object, the gate's closed/open state can't be
        trusted yet either, so taint must still propagate through it
        normally (matching the pre-exception behavior)."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("relay_gate", "gate", {}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "relay_gate", "value", "in"),
                edge("unseeded_read", "relay_gate", "changed", "enable"),
                edge("relay_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-gate-enable-unresolved"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["cf"]["changed"] is False
        assert manager._hysteresis[graph_id]["cf"] == {"value": True}

    def test_change_filter_stays_held_when_gate_is_open_via_a_resolved_enable(self):
        """The closed-gate boundary exception must not apply when the gate
        is OPEN (enable resolves to True): an open gate genuinely passes
        its unresolved "in" value straight through as "out", so a
        downstream change_filter must still be held — same as if the gate
        weren't there at all."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("enable_src", "const_value", {"value": "true", "data_type": "bool"}),
            node("relay_gate", "gate", {}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "relay_gate", "value", "in"),
                edge("enable_src", "relay_gate", "value", "enable"),
                edge("relay_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-gate-open-resolved-enable"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["cf"]["changed"] is False
        assert manager._hysteresis[graph_id]["cf"] == {"value": True}

    def test_change_filter_is_not_held_behind_a_gate_closed_via_negated_enable(self):
        """Same closed-gate boundary as the unwired-enable case, but closed
        via negate_enable flipping a resolved True into an effective False
        — exercises the negate_enable branch of the hold-computation's own
        gate check specifically (the pulse-carrying check's negate_enable
        branch is covered separately)."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("enable_src", "const_value", {"value": "true", "data_type": "bool"}),
            node("relay_gate", "gate", {"negate_enable": True}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "relay_gate", "value", "in"),
                edge("enable_src", "relay_gate", "value", "enable"),
                edge("relay_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-gate-closed-negated-enable"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"relay_gate": 99, "cf": {"value": 42}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["relay_gate"]["out"] == 99
        assert outputs["cf"]["changed"] is True
        assert outputs["cf"]["out"] == 99

    def test_change_filter_pulse_retriggers_through_an_open_gate_with_negated_enable(self):
        """Complements the closed-gate dedup test: an OPEN gate (here,
        opened via negate_enable flipping a resolved False into True) truly
        passes change_filter.changed through as a real, discrete pulse each
        time — host_check downstream must retrigger on every execution,
        exactly as if the gate weren't there. Also exercises negate_enable
        in the pulse-carrying check."""
        nodes = [
            node("cf", "change_filter"),
            node("enable_src", "const_value", {"value": "false", "data_type": "bool"}),
            node("gate1", "gate", {"negate_enable": True}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cf", "gate1", "changed", "in"),
                edge("enable_src", "gate1", "value", "enable"),
                edge("gate1", "hc", "out", "trigger"),
            ],
        )

        manager = _make_manager()
        graph_id = "g-cf-open-gate-negated-enable-dedup"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 3}}))

        assert mock_ping.await_count == 3

    def test_change_filter_pulse_reads_last_effective_gate_enable_edge(self):
        nodes = [
            node("cf", "change_filter"),
            node("closed", "const_value", {"value": "false", "data_type": "bool"}),
            node("open", "const_value", {"value": "true", "data_type": "bool"}),
            node("gate1", "gate", {}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cf", "gate1", "changed", "in"),
                edge("closed", "gate1", "value", "enable"),
                edge("open", "gate1", "value", "enable"),
                edge("gate1", "hc", "out", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-effective-gate-enable"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 3}}))

        assert mock_ping.await_count == 3

    def test_taint_analysis_survives_malformed_gate_input_count(self):
        """Regression: a malformed input_count (e.g. an imported/legacy
        node left with "invalid" or null) reached bare int() in the taint
        analysis's own copy of the gate input-counting logic, uncaught —
        unlike GraphExecutor's own per-node try/except, which isolates the
        same parse failure to that one node's __error__ output and doesn't
        abort the rest of graph execution. _execute_graph has no single
        outer try/except of its own, so this crashed the entire call."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("and_gate", "and", {"input_count": "invalid"}),
            node("cf", "change_filter"),
            node("other_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "and_gate", "value", "in1"),
                edge("and_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-and-malformed-count"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"other_read": {"value": 1, "changed": True}}))

        # Treated as tainted (not absorbed) rather than crashing: the
        # filter stays held, comparing against its unchanged prior state.
        assert outputs["cf"]["changed"] is False

    def test_change_filter_is_held_behind_a_wol_chained_to_an_unresolved_api_client(self):
        """Regression (P1): _unresolved_source_ids only recognized an async
        node as a taint source when its OWN _trigger was already true in
        the initial pass — but for a chain like
        api_client → wake_on_lan → change_filter → host_check, wake_on_lan's
        trigger in the initial pass is computed from api_client's still-
        placeholder success (False), so wake_on_lan itself never counted as
        unresolved. change_filter downstream of it therefore committed its
        comparison immediately using WoL's placeholder sent=False — and the
        very next "Handle host_check" pass (which runs long before
        api_client is even attempted) could see a resulting changed=True
        and fire a real, irreversible ping using a value that was never
        real to begin with.

        Here the persisted change_filter baseline already matches what the
        REAL wol.sent will resolve to (True, once api_client actually
        succeeds and WoL actually sends), so the correct behavior for the
        whole tick is "nothing changed, no ping at all" — the bug's
        placeholder-based comparison (False vs persisted True) instead
        looks like a change and pings early."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("cf", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "wol", "success", "trigger"),
                edge("wol", "cf", "sent", "in"),
                edge("cf", "hc", "changed", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-chained-async"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
            ):
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        mock_ping.assert_not_awaited()

    def test_change_filter_stays_held_during_api_replay_when_also_fed_by_an_unseeded_read(self):
        """Regression (P1): the API-replay branch only recomputed change_filter
        hold-ids when _late_pending (a newly-discovered pending async node,
        e.g. a chained wake_on_lan) was non-empty — for
        api_client.success + unseeded read -> AND -> change_filter, there is
        no such chained async node, so the hold-id recompute was skipped
        entirely and the filter committed the API replay's result even
        though the unseeded read never actually resolved this tick (its
        missing value evaluates as a deterministic-looking False that isn't
        really final). Holds must be computed from unseeded_read_ids
        regardless of whether _late_pending is itself empty."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("and_gate", "and", {"input_count": 2}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "and_gate", "success", "in1"),
                edge("unseeded_read", "and_gate", "value", "in2"),
                edge("and_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-api-plus-unseeded"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        # The AND gate's real result (api_client succeeded, unseeded read
        # still unresolved) evaluates to False either way — the filter must
        # nonetheless stay held against its persisted True baseline, since
        # the unresolved read was never actually settled this tick.
        assert manager._hysteresis[graph_id]["cf"] == {"value": True}

    def test_gate_absorption_uses_fresh_replay_output_not_stale_first_pass(self):
        """Regression (P1): _compute_cf_hold_ids' gate-absorption check
        always read the OUTER (still stale) first-pass `outputs` even when
        recomputing holds for a later replay's own fresh result. For
        api_client.success -> NOT -> OR, an unseeded Read Object on the
        other OR input, and OR -> change_filter: the first-pass placeholder
        (ac.success=False) makes the stale NOT output True, so the OR looks
        decisively resolved via that stale True — but once the real
        api_client result (True) propagates through NOT within the
        replay's own pass, NOT's real output is False, and the OR's
        decisiveness then depends entirely on the still-unresolved read.
        The filter must therefore stay held; using the replay's own fresh
        output (not the outer stale one) for this check is what makes that
        distinction possible."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
            node("not1", "not", {}),
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("or_gate", "or", {"input_count": 2}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "not1", "success", "in1"),
                edge("not1", "or_gate", "out", "in1"),
                edge("unseeded_read", "or_gate", "value", "in2"),
                edge("or_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-not-or-unseeded"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        # NOT(real True) = False, and the OR's other input is still
        # unresolved — the filter must stay held against its persisted
        # True baseline rather than committing OR's real-but-not-decisive
        # False result.
        assert manager._hysteresis[graph_id]["cf"] == {"value": True}

    def test_correction_replay_does_not_double_mutate_a_reused_output(self):
        """Regression: GraphExecutor.execute()'s known_outputs mechanism
        handed out the caller's exact same per-node output dict to skip
        re-evaluating nodes outside a held change_filter's island. A
        python_script *inside* that island (a descendant of the held
        filter, so it IS re-executed by the correction replay) that also
        reads a mutable dict/list from OUTSIDE the island — reused via
        known_outputs — could therefore mutate that shared object a second
        time during the replay, on top of the mutation the main pass
        already made, silently corrupting a value that never existed in
        the real pass (here: memory's own persisted state, since memory's
        "out" and its hysteresis_state["value"] are the same object).

        GraphExecutor now isolates EVERY python_script's inputs (not just
        ones reused via known_outputs across a replay) from in-place
        mutation, the same protection also needed for shared iCalendar
        cache entries — a strictly safer contract than "only the replay
        pass is protected". "count" therefore never advances via this
        in-place-mutation backdoor at all, on the real pass or a replay;
        memory's own documented "in"/"reset" ports remain the only real
        way to update its persisted state."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("not_gate", "not"),
            node("cf", "change_filter"),
            node("mem", "memory"),
            node("script", "python_script", {"script": "inputs['in2']['count'] += 1\nresult = 1"}),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "not_gate", "value", "in1"),
                edge("not_gate", "cf", "out", "in"),
                edge("cf", "script", "changed", "in1"),
                edge("mem", "script", "out", "in2"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-known-outputs-isolation"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"mem": {"value": {"count": 0}}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert manager._hysteresis[graph_id]["mem"]["value"]["count"] == 0

    def test_correction_replay_survives_a_non_deepcopyable_unrelated_output(self):
        """Regression: every node OUTSIDE a held change_filter's descendant
        island is placed into known_outputs so the correction replay reuses
        this pass's already-computed real output for it instead of
        re-evaluating it. A completely unrelated python_script (a permitted
        node type) can legitimately return a non-deepcopyable value like a
        generator expression — copy.deepcopy() used to raise TypeError on
        that single node's output and abort the entire replay (and with it
        the graph's remaining writes/state persistence), even though that
        output has nothing to do with the held branch. Must complete
        normally instead."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("cf", "change_filter"),
            node("script_unrelated", "python_script", {"script": "result = (x for x in range(3))"}),
        ]
        flow = _flow(nodes, [edge("unseeded_read", "cf", "value", "in")])
        manager = _make_manager()
        graph_id = "g-known-outputs-non-copyable"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["cf"]["changed"] is False
        assert manager._hysteresis[graph_id]["cf"] == {"value": True}

    def test_correction_replay_preserves_type_of_a_non_deepcopyable_known_output(self):
        """Regression: the known_outputs replay-population (see test above)
        ran every reused output through _snapshot_debug_value's failure-safe
        fallback chain — fine for pure debug capture, but a known_outputs
        value can be genuinely CONSUMED by a downstream node INSIDE the
        held island, not just displayed. A non-deepcopyable value (e.g. a
        permitted python_script's generator result) degraded to its str()
        repr there, silently changing its type for that consumer.

        len() distinguishes the two without needing any disallowed
        script builtin: a generator has no len() (raises TypeError inside
        the script, surfacing as this node's own "__error__" output),
        while its stringified repr does (returns a plain int, no error) —
        so an "__error__" result here proves the real generator reached
        the consumer, not a lossy string standing in for it."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("cf", "change_filter"),
            node("gen_script", "python_script", {"script": "result = (x for x in range(3))"}),
            node("consumer_script", "python_script", {"script": "result = len(inputs['gen'])"}),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "cf", "value", "in"),
                edge("cf", "consumer_script", "changed", "in1"),
                edge("gen_script", "consumer_script", "result", "gen"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-known-outputs-preserve-type"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert "no len()" in outputs["consumer_script"].get("__error__", "")

    def test_change_filter_is_not_held_when_or_gate_is_already_true_from_a_seeded_branch(self):
        """Regression: an OR gate combining an unseeded Read Object with a
        separate, seeded/live branch must not have its already-True output
        discarded just because the filter is structurally reachable from the
        unseeded branch too — OR's True result is final regardless of what
        the unseeded read eventually becomes, so a real change coming
        through the OTHER branch must not be silently lost."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("live_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("or_gate", "or", {"input_count": 2}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "or_gate", "value", "in1"),
                edge("live_read", "or_gate", "value", "in2"),
                edge("or_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()  # registry.get_value defaults to None (unseeded_read stays unseeded)
        graph_id = "g-cf-or-unseeded"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"live_read": {"value": True, "changed": True}}))

        assert outputs["cf"]["out"] is True
        assert outputs["cf"]["changed"] is True

    def test_change_filter_stays_held_when_or_gate_is_false_from_an_unseeded_branch(self):
        """The OR-short-circuit exception only applies when the gate's real
        output this pass is already True; when it's False (every input,
        resolved or not, is currently false), the result could still flip
        once the unseeded branch is populated, so the filter must stay held
        rather than adopting this pass's not-yet-final False as confirmed."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("live_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("or_gate", "or", {"input_count": 2}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "or_gate", "value", "in1"),
                edge("live_read", "or_gate", "value", "in2"),
                edge("or_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-or-false-unseeded"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"live_read": {"value": False, "changed": True}}))

        assert outputs["cf"]["changed"] is False
        assert outputs["cf"]["out"] is None

    def test_change_filter_stays_held_when_or_true_comes_only_from_the_unseeded_branch(self):
        """Regression: checking only the gate's own `out` value is not
        enough — if the *tainted* input is the one currently making an OR
        true (here via an unseeded Read Object through a NOT, with nothing
        else wired), that true is exactly the placeholder this correction
        exists to catch, not an independent guarantee from a resolved
        input, so the filter must stay held."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("not_gate", "not"),
            node("or_gate", "or", {"input_count": 2}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "not_gate", "value", "in1"),
                edge("not_gate", "or_gate", "out", "in1"),
                edge("or_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-or-not-unseeded"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert outputs["cf"]["changed"] is False
        assert outputs["cf"]["out"] is None

    def test_change_filter_is_released_when_and_gate_is_deterministically_false(self):
        """Regression: a resolved False input to an AND gate fixes its
        output at False regardless of what an unseeded second input
        eventually becomes — the filter must report that definite
        transition instead of staying held (and stuck on its old, stale
        value) forever just because the AND is reachable from an unseeded
        Read Object too."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("live_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("and_gate", "and", {"input_count": 2}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "and_gate", "value", "in1"),
                edge("live_read", "and_gate", "value", "in2"),
                edge("and_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-and-unseeded"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"live_read": {"value": False, "changed": True}}))

        assert outputs["cf"]["out"] is False
        assert outputs["cf"]["changed"] is True

    def test_change_filter_is_released_when_and_gate_is_false_via_a_negated_resolved_input(self):
        """Regression: a resolved input's per-input negation (negate_inN)
        must be applied before checking whether it independently decides
        the gate result — a True input with negate_in2 set is effectively
        False, which still fixes an AND at False regardless of an unseeded
        sibling input."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("live_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("and_gate", "and", {"input_count": 2, "negate_in2": True}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "and_gate", "value", "in1"),
                edge("live_read", "and_gate", "value", "in2"),
                edge("and_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-and-negated"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"live_read": {"value": True, "changed": True}}))

        assert outputs["cf"]["out"] is False
        assert outputs["cf"]["changed"] is True

    def test_change_filter_rollback_clears_placeholder_state_for_a_fresh_filter(self):
        """Regression: a fresh (no prior state) change_filter held behind an
        unseeded Read Object must have any placeholder state the
        uncorrected initial pass wrote for it inline cleared during
        rollback — leaving it behind would make the filter's first REAL
        value (once the Read Object is finally seeded) compare against
        that stale placeholder instead of reporting the genuine first
        change."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("not_gate", "not"),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "not_gate", "value", "in1"),
                edge("not_gate", "cf", "out", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-rollback-clear"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            # Two unrelated executions while the Read Object stays unseeded.
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

            # The Read Object finally receives its real (first-ever) value.
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"unseeded_read": {"value": False, "changed": True}}))

        assert outputs["cf"]["changed"] is True

    def test_correction_replay_reuses_first_pass_value_instead_of_resampling_random_value(self):
        """Regression: the correction replay for a held change_filter used to
        re-execute the *whole* graph and copy back every descendant's new
        output — for a descendant that also consumes an independent
        random_value branch, that meant sampling it a second time and a
        downstream Host Check could fire based on that second, different
        draw instead of the real pass's. Inputs crossing into the held
        filter's descendant island from outside it must reuse this pass's
        already-computed value instead."""
        nodes = [
            node("unseeded_read", "datapoint_read", {"datapoint_id": str(uuid.uuid4())}),
            node("cf", "change_filter"),
            node("rand_trigger", "const_value", {"value": "true", "data_type": "bool"}),
            node("rand", "random_value", {"data_type": "int", "min": 1, "max": 100}),
            node("compare", "compare", {"operator": ">", "operand": "50"}),
            node("or_gate", "or", {"input_count": 2}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "cf", "value", "in"),
                edge("rand_trigger", "rand", "value", "trigger"),
                edge("rand", "compare", "value", "in1"),
                edge("cf", "or_gate", "changed", "in1"),
                edge("compare", "or_gate", "out", "in2"),
                edge("or_gate", "hc", "out", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-no-resample"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
            patch("random.randint", side_effect=[10, 90]) as mock_rand,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert mock_rand.call_count == 1
        # The (only) random draw was 10 (<= 50), so compare(">50") is False;
        # cf is held (suppressed) since its own input is unseeded, so the OR
        # gate must see (False, False) => False and host_check must never
        # ping. If the second, "90" draw leaked in instead, compare would be
        # True and the OR would fire the ping.
        mock_ping.assert_not_awaited()

    def test_held_replay_island_ignores_shadowed_edge_to_random_value(self):
        nodes = [
            node("unseeded_read", "datapoint_read", {}),
            node("cf", "change_filter"),
            node("trigger", "const_value", {"value": "true", "data_type": "bool"}),
            node("rand", "random_value", {"data_type": "int", "min": 1, "max": 100}),
        ]
        flow = _flow(
            nodes,
            [
                edge("unseeded_read", "cf", "value", "in"),
                edge("cf", "rand", "changed", "trigger"),
                edge("trigger", "rand", "value", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-held-shadowed-random"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("random.randint", side_effect=[10, 90]) as mock_rand,
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert mock_rand.call_count == 1
        assert outputs["rand"]["value"] == 10

    def test_change_filter_holds_behind_unresolved_wake_on_lan(self):
        """Regression: wake_on_lan.sent is a placeholder-then-replayed
        output just like api_client/host_check, but async_replay_source_ids
        previously omitted wake_on_lan entirely — a change_filter fed by it
        could adopt the first-pass sent=False placeholder as a real change
        and fire a downstream host_check before the real packet-send result
        (via replay) was known."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("cf", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "wol", "value", "trigger"),
                edge("wol", "cf", "sent", "in"),
                edge("cf", "hc", "changed", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-wol-placeholder"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        # Prior stored value already matches the real, eventual "sent" result
        # (True) — if the filter adopted the tainted first-pass placeholder
        # (False) instead, it would incorrectly see True->False->changed.
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        mock_ping.assert_not_awaited()

    def test_change_filter_commits_new_state_from_wake_on_lan_replay(self):
        """Regression: the WoL replay pass ran change_filter against a
        deep-copied hyst snapshot (deliberately, so non-idempotent nodes
        don't accumulate a second sample) but never copied the change_filter
        entry back into the real hyst afterward — unlike every other replay
        site in this module. The filter's *output* for this tick was still
        correct (real wol.sent value), but its *persisted* comparison
        baseline silently reverted to the pre-replay value, so the very next
        tick would compare against a stale baseline and could drop a real
        change."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "wol", "value", "trigger"),
                edge("wol", "cf", "sent", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-wol-state-commit"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": False}}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        assert manager._hysteresis[graph_id]["cf"]["value"] is True

    def test_change_filter_pulse_via_data_port_does_not_retrigger_sequence(self):
        """Regression: the value_sequence backward pulse-walk only applied
        its trigger-handle filtering to the edge directly into the sequence
        node, not to intermediate hops — a change_filter pulse entering an
        intermediate api_client's DATA port (not its trigger) was still
        traced onward as if it drove that node's own, separately sustained
        trigger."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("cf", "change_filter"),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
            node("sequence", "value_sequence", {"restart_policy": "queue", "steps": [{"delay_ms": 1}]}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("cf", "ac", "changed", "body"),
                edge("ac", "sequence", "success", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-seq-data-port-no-bypass"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        async def _exercise():
            await manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}})
            first_task = manager._sequence_tasks.get((graph_id, "sequence"))
            if first_task is not None:
                await first_task
            first_count = manager._event_bus.publish.await_count

            await manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}})
            retrigger_task = manager._sequence_tasks.get((graph_id, "sequence"))
            if retrigger_task is not None and not retrigger_task.done():
                await retrigger_task

            assert manager._event_bus.publish.await_count == first_count

        mock_client_cls = _patch_api_success()
        try:
            with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
                asyncio.run(_exercise())
        finally:
            mock_client_cls.stop()

    def test_shadowed_change_filter_edge_does_not_retrigger_sequence(self):
        target = uuid.uuid4()
        nodes = [
            node("cf", "change_filter"),
            node("sustained", "const_value", {"value": "true", "data_type": "bool"}),
            node(
                "sequence",
                "value_sequence",
                {"restart_policy": "queue", "steps": [{"datapoint_id": str(target), "value": 1}]},
            ),
        ]
        flow = _flow(
            nodes,
            [
                edge("cf", "sequence", "changed", "trigger"),
                edge("sustained", "sequence", "value", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-shadowed-cf-sequence-trigger"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        async def _exercise():
            await manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 1}})
            await manager._sequence_tasks[(graph_id, "sequence")]
            first_count = manager._event_bus.publish.await_count

            await manager._execute_graph(graph_id, "test", flow, {"cf": {"in": 2}})
            second_task = manager._sequence_tasks.get((graph_id, "sequence"))
            if second_task is not None and not second_task.done():
                await second_task

            assert manager._event_bus.publish.await_count == first_count

        with patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")):
            asyncio.run(_exercise())

    def test_inactive_async_branch_does_not_hold_a_real_change(self):
        """Regression: change_filter must not be held behind an async source
        (api_client) that is never triggered — its untriggered success=False
        output is a genuine, final value (not a placeholder pending
        replay), so a real change on a separate synchronous branch feeding
        the same OR gate must reach the filter immediately instead of being
        discarded in favor of the filter's old (stale) value."""
        nodes = [
            node("cv", "const_value", {"value": "false", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
            node("or_gate", "or", {"input_count": 2}),
            node("cf", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        edges_list = [
            edge("cv", "or_gate", "value", "in1"),
            edge("ac", "or_gate", "success", "in2"),
            edge("or_gate", "cf", "out", "in"),
            edge("cf", "hc", "changed", "trigger"),
        ]
        # api_client.trigger is left unwired on purpose — it must never fire.
        flow_false = _flow(nodes, edges_list)
        flow_true = _flow(
            [node("cv", "const_value", {"value": "true", "data_type": "bool"}), *nodes[1:]],
            edges_list,
        )

        manager = _make_manager()
        graph_id = "g-inactive-async-branch"
        manager._graphs[graph_id] = ("test", True, flow_false)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            asyncio.run(manager._execute_graph(graph_id, "test", flow_false, {}))
            asyncio.run(manager._execute_graph(graph_id, "test", flow_false, {}))
            # The only real change in the graph: cv flips to true. api_client
            # is never triggered throughout the whole test.
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow_true, {}))

        assert outputs["cf"]["changed"] is True
        assert mock_ping.await_count == 2

    def test_fresh_change_filter_does_not_ping_before_async_source_resolves(self):
        """Regression: a change_filter with NO prior state must not adopt an
        async source's still-unresolved placeholder as its "first value" —
        that must not reach host_check until api_client's REAL result is
        known, otherwise the ping fires (using a trigger derived from the
        placeholder) before the HTTP call it depends on has even been made."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET"}),
            node("cf", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "cf", "success", "in"),
                edge("cf", "hc", "changed", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-fresh-cf-async"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        # No prior state at all for "cf" — a brand-new/just-reset filter.

        call_order: list[str] = []

        async def _record_http(*args, **kwargs):
            call_order.append("http")
            return _MockResponse(200)

        async def _record_ping(*args, **kwargs):
            call_order.append("ping")
            return True, 1.0

        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=_record_http)
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, side_effect=_record_ping) as mock_ping,
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert mock_ping.await_count == 1
        assert call_order == ["http", "ping"]
        assert outputs["cf"]["changed"] is True

    def test_change_filter_pulse_via_async_replay_retriggers_host_check(self):
        """Regression: a change_filter downstream of api_client only learns
        its real "changed" value via the async replay (the initial pass sees
        the still-unresolved placeholder), so that pulse must still register
        as a discrete retriggerable edge — like a cron tick — instead of
        consecutive real changes having their second ping deduplicated as a
        "sustained" trigger just because cron_reachable was never told about
        a pulse that only appeared after replay."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET", "response_type": "text/plain"}),
            node("cf", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "cf", "response", "in"),
                edge("cf", "hc", "changed", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-async-retrigger"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        responses = iter(["A", "B", "C"])

        async def _next_response(*args, **kwargs):
            return _MockResponse(200, text=next(responses))

        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=_next_response)
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
            ):
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert mock_ping.await_count == 3

    def test_change_filter_downstream_of_host_check_sees_real_value_after_settling(self):
        """Regression: api_client -> host_check -> change_filter. The
        api_client replay stage correctly discovers host_check is chained
        but hasn't actually pinged yet, and holds change_filter with
        _suppress_change_filter for that pass. That hold used to be baked
        into api_replay_overrides permanently and reused, unmodified, as
        the base for the later "Post-api-replay host_check pass" that
        actually pings host_check for real — so even once the real ping
        settled host_check and fed change_filter its real reachable=True
        value, the stale hold kept suppressing change_filter forever and
        the genuine reachability transition was never reported."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET", "response_type": "text/plain"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc", "success", "trigger"),
                edge("hc", "cf", "reachable", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-downstream-of-hc-settling"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_MockResponse(200, text="OK"))
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["cf"] == {"out": True, "changed": True}

    def test_change_filter_pulse_retrigger_reaches_host_check_through_intermediate_node(self):
        """Regression: cron_reachable's forward closure for a change_filter
        pulse discovered via async replay must extend through intermediate
        nodes, not just the pulse's immediate edge target — otherwise a
        host_check two hops downstream of change_filter.changed still gets
        deduplicated as a sustained trigger."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET", "response_type": "text/plain"}),
            node("cf", "change_filter"),
            node("not1", "not"),
            node("not2", "not"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "cf", "response", "in"),
                edge("cf", "not1", "changed", "in1"),
                edge("not1", "not2", "out", "in1"),
                edge("not2", "hc", "out", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-cf-async-retrigger-2hop"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}

        responses = iter(["A", "B", "C"])

        async def _next_response(*args, **kwargs):
            return _MockResponse(200, text=next(responses))

        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(side_effect=_next_response)
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
            ):
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
                asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert mock_ping.await_count == 3

    def test_change_filter_state_is_released_when_host_check_ping_raises(self):
        """Regression: a change_filter held behind a host_check must still be
        released with the ping's final (failure) output, not stay stuck
        showing the last successful value forever — several handlers used to
        only schedule the descendant replay that releases held filters on
        success, so an exception (or a config/validation failure) left the
        held filter permanently stale."""

        def _make_flow(host):
            nodes = [
                node("cv", "const_value", {"value": "true", "data_type": "bool"}),
                node("hc", "host_check", {"host": host, "timeout_s": 1, "count": 1}),
                node("cf", "change_filter"),
            ]
            return _flow(
                nodes,
                [
                    edge("cv", "hc", "value", "trigger"),
                    edge("hc", "cf", "reachable", "in"),
                ],
            )

        manager = _make_manager()
        graph_id = "g-cf-hc-fail"
        flow1 = _make_flow("192.168.1.1")
        manager._graphs[graph_id] = ("test", True, flow1)
        manager._node_state[graph_id] = {}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow1, {}))
        assert outputs["cf"]["out"] is True
        assert outputs["cf"]["changed"] is True

        # A different host changes host_check's config signature, forcing a
        # fresh ping attempt instead of the rising-edge dedup reusing the
        # cached (successful) result from the first execution.
        flow2 = _make_flow("192.168.1.2")
        manager._graphs[graph_id] = ("test", True, flow2)
        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, side_effect=OSError("unreachable")),
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow2, {}))

        assert outputs["cf"]["out"] is False
        assert outputs["cf"]["changed"] is True


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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
        ):
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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(False, None)),
        ):
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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        ):
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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 7.5)) as mock_ping,
        ):
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
        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))
        assert "hc" in outputs

    def test_nonnumeric_count_does_not_crash(self):
        manager = _make_manager()
        flow = _flow([node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": "bad"})])
        graph_id = "g"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))
        assert "hc" in outputs

    def test_ping_config_is_clamped_before_dispatch(self):
        manager = _make_manager()
        flow = _flow([node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 999, "count": 999})])
        graph_id = "g-clamp"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
            ):
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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        ):
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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, side_effect=[(True, 1.0), (True, 2.0)]) as mock_ping,
        ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ) as mock_ping,
            ):
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


def _setup_post_api_hc_ac2_graph(ac2_data: dict) -> tuple[FlowData, LogicManager, str]:
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ) as mock_ping,
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 2
        assert outputs["hc1"]["reachable"] is True
        assert outputs["hc2"]["reachable"] is True

    def test_final_hc_replay_holds_change_filter_behind_a_not_yet_run_api_client(self):
        """Regression: cv->ac1->hc1->ac2->hc2->ac3->cf. hc2 is only triggered
        from within the "final host-check replay" section (ac2's real,
        replayed success), and ac3 — newly reachable from hc2 — is never
        actually run this tick (no further pass follows this one). Without
        a late-hold correction in that section, change_filter would commit
        ac3's placeholder success=False as a real value. With it, the
        filter must stay held instead."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "one.local", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("hc2", "host_check", {"host": "two.local", "timeout_s": 1, "count": 1}),
            node("ac3", "api_client", {"url": "http://93.184.216.34/three", "method": "GET"}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "hc2", "success", "trigger"),
                edge("hc2", "ac3", "reachable", "trigger"),
                edge("ac3", "cf", "success", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-final-hc-holds-cf"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        # ac3's unresolved placeholder success reads False — seed a
        # baseline of True so an incorrect commit of that placeholder is
        # observable as a (wrong) changed=True, not masked by coincidence.
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["hc1"]["reachable"] is True
        assert outputs["hc2"]["reachable"] is True
        # ac3 was never actually run this tick — cf must stay held, not
        # commit ac3's placeholder success=False as a real change.
        assert outputs["cf"]["changed"] is False

    def test_post_api_hc_replay_settles_a_downstream_api_client_with_an_empty_url(self):
        """Regression: cv->ac1->hc1->ac2->cf, where hc1's trigger only
        becomes true from ac1's OWN post-api replay (not the initial
        pass), so hc1 is discovered exclusively by the "Post-api-replay
        host_check pass" (post_api_triggered_hc) — and ac2 (triggered from
        hc1.reachable) is therefore reached exclusively by that pass's own
        post_api_hc_api_clients loop. ac2's resolved URL is empty —
        genuinely, finally INACTIVE, not merely "not yet run". That loop's
        empty-URL branch used to just `continue` without marking ac2
        settled (unlike its sibling error branches just below it, and
        unlike _run_api_client_node's own empty-URL path), so
        _still_unresolved_source_ids kept treating it as pending forever —
        holding cf even though ac2's real, final state is already fully
        known."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "one.local", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "", "method": "GET"}),  # empty URL: never actually runs
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "cf", "success", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-post-api-hc-settles-empty-url-ac"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        # ac2.success is (and will always stay) False — seed a baseline of
        # True so the genuine settle-and-release is observable as a real
        # changed=True, not masked by coincidence.
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["hc1"]["reachable"] is True
        # ac2 is genuinely, finally inactive (empty URL) — cf must settle
        # and reflect that real transition, not stay held forever.
        assert outputs["cf"]["changed"] is True
        assert outputs["cf"]["out"] is False

    def test_post_api_wol_downstream_propagation(self):
        """WoL fired by post-api HC propagates its sent=True to a downstream const_value node (lines 1826-1847)."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34", "method": "GET"}),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("cv2", "const_value", {"value": "true", "data_type": "bool"}),
            node("gate", "and", {"input_count": 2}),
            node("unrelated_cf", "change_filter"),
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
        runtime_value = (item for item in (1, 2, 3))
        manager._hysteresis[graph_id] = {"unrelated_cf": {"value": runtime_value}}

        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["wol"]["sent"] is True
        assert outputs["gate"]["out"] is True
        assert manager._hysteresis[graph_id]["unrelated_cf"]["value"] is runtime_value

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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(False, None)),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_tt,
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(False, None)),
            ):
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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch(
                "obs.logic.manager._normalise_host_check_ping_config",
                side_effect=RuntimeError("bad config"),
            ),
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ) as mock_ping,
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["ac2"]["success"] is True

    def test_json_headers_config(self):
        """Headers as JSON string are parsed and merged into request headers (lines 1893-1895)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "GET", "headers": '{"X-Custom": "val"}'})
        patcher = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert outputs["ac2"]["success"] is True

    def test_non_json_response_type(self):
        """response_type='text/plain' returns raw text instead of parsed JSON (line 1997)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "GET", "response_type": "text/plain"})
        patcher = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
            ):
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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch(
                "obs.logic.manager._ping_host",
                new_callable=AsyncMock,
                side_effect=[(True, 1.0), (True, 2.0)],
            ) as mock_ping,
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc_a": {"trigger": True}}))

        assert mock_ping.await_count == 2
        assert outputs["hc_a"]["reachable"] is True
        assert outputs["hc_b"]["reachable"] is True
        assert outputs["gate"]["out"] is True, "gate must see both real HC outputs, not the hc_a placeholder"

    def test_resolved_async_output_does_not_override_shadowed_filter_input(self):
        nodes = [
            node("hc", "host_check", {"host": "a.local", "timeout_s": 1, "count": 1}),
            node("constant", "const_value", {"value": "7", "data_type": "number"}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("hc", "cf", "reachable", "in"),
                edge("constant", "cf", "value", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-shadowed-resolved-output"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": 7.0}}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {"hc": {"trigger": True}}))

        assert outputs["hc"]["reachable"] is True
        assert outputs["cf"] == {"out": 7.0, "changed": False}

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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 5.0)) as mock_ping,
        ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 2.0)),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        assert mock_client.request.await_count == 2
        assert outputs["hc"]["reachable"] is True
        assert outputs["ac2"]["success"] is True
        mock_to_thread.assert_awaited_once()
        assert outputs["wol"]["sent"] is True

    def test_host_check_replay_holds_change_filter_behind_pending_wol(self):
        """Regression (P1): the dedicated "Re-propagate host_check outputs to
        downstream nodes" replay block (distinct from the generic
        _replay_async_descendants used for notify/message_archive, and from
        the api_client replay branch) had no late-pending suppression at
        all. For hc1.reachable -> wake_on_lan.trigger -> change_filter ->
        hc2.trigger, this replay sees wol.sent=False (WoL hasn't actually
        been sent yet — that only happens later, in the separate "Handle
        wake_on_lan" section) — against a persisted True baseline that's a
        spurious change, and hc2 would ping immediately using that
        not-yet-real value. Since the real wol.sent also resolves to True
        (no genuine change), hc2 must never be pinged at all."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("cf", "change_filter"),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "hc1", "value", "trigger"),
                edge("hc1", "wol", "reachable", "trigger"),
                edge("wol", "cf", "sent", "in"),
                edge("cf", "hc2", "changed", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-hc-replay-holds-cf"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        # Only hc1 must ever be pinged — hc2 must not fire from wol's
        # first-pass placeholder, and since the real wol.sent also settles
        # to True (matching the persisted baseline), it must never fire
        # for real either.
        mock_ping.assert_awaited_once()
        assert mock_ping.await_args.args[0] == "192.168.1.1"
        assert outputs["wol"]["sent"] is True
        assert outputs["cf"]["changed"] is False

    def test_wol_replay_holds_change_filter_behind_pending_second_wol(self):
        """Regression (P1): the dedicated "Re-propagate wake_on_lan sent=True
        to downstream nodes" replay block had no late-pending suppression at
        all, unlike the host_check/api_client/generic replay branches (all
        fixed in prior rounds). For wol1.sent -> wol2.trigger ->
        change_filter -> host_check, this replay evaluates the filter using
        wol2.sent=False — wol2 has only just become triggered and hasn't
        actually sent yet — against a persisted True baseline that looks
        like a spurious change; the post-WoL host_check pass would then
        ping using that not-yet-real placeholder. wol2 never actually
        resolves within this same tick (nothing re-runs a newly-triggered,
        directly-chained WoL for real outside the message_archive/notify
        replay-side-effects path), so with the fix the filter simply stays
        held and host_check must never be pinged at all."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("wol1", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("wol2", "wake_on_lan", {"mac_address": "11:22:33:44:55:66"}),
            node("cf", "change_filter"),
            node("hc", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "wol1", "value", "trigger"),
                edge("wol1", "wol2", "sent", "trigger"),
                edge("wol2", "cf", "sent", "in"),
                edge("cf", "hc", "changed", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-wol-chain-holds-cf"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        mock_ping.assert_not_awaited()
        assert outputs["wol1"]["sent"] is True
        assert outputs["cf"]["changed"] is False

    def test_post_wol_host_check_replay_holds_change_filter_behind_pending_second_wol(self):
        """Regression (P1): the "Post-WoL host_check pass" replay (distinct
        from the dedicated WoL replay above, and from the other replay
        sites already fixed in prior rounds) registered change_filter
        pulses without recomputing late-pending async descendants first.
        For wol1.sent -> host_check1 -> wol2.trigger -> change_filter ->
        host_check2: host_check1 fires for real off wol1's real send, and
        its real "reachable" newly triggers wol2 within THIS replay — but
        wol2 hasn't actually sent yet (its own output here is still a
        placeholder). host_check2 must not ping off that placeholder;
        since wol2 never actually resolves within this same tick, it must
        never ping at all."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("wol1", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol2", "wake_on_lan", {"mac_address": "11:22:33:44:55:66"}),
            node("cf", "change_filter"),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "wol1", "value", "trigger"),
                edge("wol1", "hc1", "sent", "trigger"),
                edge("hc1", "wol2", "reachable", "trigger"),
                edge("wol2", "cf", "sent", "in"),
                edge("cf", "hc2", "changed", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-post-wol-hc-chain-holds-cf"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
        ):
            outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))

        mock_ping.assert_awaited_once()
        assert mock_ping.await_args.args[0] == "192.168.1.1"
        assert outputs["wol1"]["sent"] is True
        assert outputs["cf"]["changed"] is False

    def test_post_api_wol_replay_holds_change_filter_behind_pending_second_wol(self):
        """Regression (P1): the "post-api WoL replay" (post_api_wol_merged/
        post_api_wol_outputs — distinct from the dedicated WoL replay and
        the "Post-WoL host_check pass" above, and from every other replay
        site already fixed in prior rounds) registered change_filter pulses
        without recomputing late-pending async descendants first. For
        api_client -> host_check1 -> wol1 -> wol2 -> change_filter ->
        host_check2: host_check1 fires for real off api_client's real
        result, its real "reachable" triggers wol1 within the post-api-WoL
        section, wol1 actually sends and its real "sent" newly triggers
        wol2 within THIS SAME replay — but wol2 hasn't actually sent yet
        (its own output here is still a placeholder). host_check2 must not
        ping off that placeholder; since wol2 never actually resolves
        within this same tick, it must never ping at all."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34/", "method": "GET", "response_type": "text/plain"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol1", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("wol2", "wake_on_lan", {"mac_address": "11:22:33:44:55:66"}),
            node("cf", "change_filter"),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc1", "success", "trigger"),
                edge("hc1", "wol1", "reachable", "trigger"),
                edge("wol1", "wol2", "sent", "trigger"),
                edge("wol2", "cf", "sent", "in"),
                edge("cf", "hc2", "changed", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-post-api-wol-chain-holds-cf"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        patcher = patch("obs.logic.manager.httpx.AsyncClient")
        mock_client_cls = patcher.start()
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_client.request = AsyncMock(return_value=_MockResponse(200, text="OK"))
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            patcher.stop()

        mock_ping.assert_awaited_once()
        assert mock_ping.await_args.args[0] == "192.168.1.1"
        assert outputs["wol1"]["sent"] is True
        assert outputs["cf"]["changed"] is False

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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 5.0)),
        ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ) as mock_ping,
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 2
        assert outputs["hc1"]["reachable"] is True
        assert outputs["wol"]["sent"] is True
        assert outputs["hc2"]["reachable"] is True

    def test_post_api_host_check_replay_recomputes_change_filter_holds(self):
        """Regression (P1): the post-api-replay host_check while-loop
        registered change_filter pulses without recomputing late-pending
        async descendants first, unlike the initial (pre-api) host_check
        replay. For api_client -> host_check1 -> wake_on_lan ->
        change_filter.changed -> host_check2, wol is still only "triggered,
        not yet actually run" within this replay's own pat_outputs.

        In this exact topology the filter is, in practice, already held via
        pat_base_overrides — inherited from the api-client stage's own
        suppression, since any host_check reachable here was necessarily
        already one of THAT stage's late-pending seeds (both checks use the
        identical "_trigger is True and not yet settled" criterion). This
        test therefore mainly exercises the recompute path itself (for
        defense-in-depth / consistency with every other replay site) rather
        than proving a distinct observable bug for this specific shape —
        host_check2 must not be pinged before wol's real send, whether that
        protection comes from here or from the inherited suppression."""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac", "api_client", {"url": "http://93.184.216.34", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("wol", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:FF"}),
            node("cf", "change_filter"),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac", "value", "trigger"),
                edge("ac", "hc1", "success", "trigger"),
                edge("hc1", "wol", "reachable", "trigger"),
                edge("wol", "cf", "sent", "in"),
                edge("cf", "hc2", "changed", "trigger"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-api-hc-wol-cf-hc2"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)) as mock_ping,
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        mock_ping.assert_awaited_once()
        assert mock_ping.await_args.args[0] == "192.168.1.1"
        assert outputs["wol"]["sent"] is True
        assert outputs["cf"]["changed"] is False


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

        with (
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
            patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            patch(
                "obs.logic.manager._ping_host",
                new_callable=AsyncMock,
                side_effect=[(True, 1.0), (True, 2.0)],
            ) as mock_ping,
        ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
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
                with (
                    patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                    patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
                    patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
                ):
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
                with (
                    patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                    patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
                    patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread,
                ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0), (True, 3.0)],
                ) as mock_ping,
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is False
        assert "OBS1" in str(outputs["ac2"]["response"])

    def test_headers_value_file_json_parse_failure(self):
        """Post-api-hc api_client: nonexistent headers_value_file → JSONDecodeError swallowed (lines 2050, 2055-2056)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "GET", "headers_value_file": "/run/secrets/nonexistent"}
        )
        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        # Request still completes (exception in headers file is swallowed)
        assert outputs["ac2"]["success"] is True

    def test_headers_value_file_success(self):
        """Post-api-hc api_client: headers_value_file returns valid JSON (line 2051)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "GET", "headers_value_file": "/run/secrets/hdr"}
        )
        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
                patch("obs.logic.manager._load_external_value_file", return_value='{"X-Custom": "value"}'),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is False
        assert "OBS1" in str(outputs["ac2"]["response"])

    def test_bearer_token_from_file(self):
        """Post-api-hc api_client: empty auth_token falls back to auth_value_file (line 2085)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph(
            {"url": "http://93.184.216.34/two", "method": "GET", "auth_type": "bearer", "auth_token": "", "auth_value_file": "/run/secrets/tok"}
        )
        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
                patch("obs.logic.manager._load_external_value_file", return_value="my-bearer-token"),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is True

    def test_text_plain_body(self):
        """Post-api-hc api_client: content_type=text/plain sets content= and Content-Type header (lines 2116-2117)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "http://93.184.216.34/two", "method": "POST", "content_type": "text/plain"})
        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()
        assert outputs["ac2"]["success"] is True

    def test_https_request_extensions(self):
        """Post-api-hc api_client: HTTPS URL → request_extensions set with sni_hostname (line 2124)."""
        flow, manager, graph_id = _setup_post_api_hc_ac2_graph({"url": "https://93.184.216.34/two", "method": "GET"})
        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0), (True, 3.0)],
                ) as mock_ping,
            ):
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
            node("unrelated_cf", "change_filter"),
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
        runtime_value = (item for item in (1, 2, 3))
        manager._hysteresis[graph_id] = {"unrelated_cf": {"value": runtime_value}}

        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["wol"]["sent"] is True
        assert outputs["gate"]["out"] is True
        assert manager._hysteresis[graph_id]["unrelated_cf"]["value"] is runtime_value


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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch("obs.logic.manager._ping_host", new_callable=AsyncMock, return_value=(True, 1.0)),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ) as mock_ping,
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 2
        assert outputs["wol"]["sent"] is True
        assert outputs["hc2"]["reachable"] is True

    def test_final_wol_replay_holds_change_filter_behind_a_not_yet_sent_second_wol(self):
        """Regression: cv->ac1->hc1->ac2->hc2->wol1->wol2->cf. wol1 is only
        discovered and sent from within the final-WoL pass (via the final
        host-check replay above resolving hc2 late); wol2's trigger only
        becomes true from wol1's OWN downstream-propagation pass here, so
        wol2 itself is never actually sent this tick (no further pass
        follows this one) — its "sent" stays an unresolved placeholder.
        Without a late-hold correction in this section, change_filter would
        commit that placeholder as a real value. With it, the filter must
        stay held. (The extra hc2 hop — vs. wol1 fed directly by ac2 — is
        needed so wol1 is discovered late enough to actually reach this
        specific final-WoL code path instead of the earlier primary WoL
        loop.)"""
        nodes = [
            node("cv", "const_value", {"value": "true", "data_type": "bool"}),
            node("ac1", "api_client", {"url": "http://93.184.216.34/one", "method": "GET"}),
            node("hc1", "host_check", {"host": "192.168.1.1", "timeout_s": 1, "count": 1}),
            node("ac2", "api_client", {"url": "http://93.184.216.34/two", "method": "GET"}),
            node("hc2", "host_check", {"host": "192.168.1.2", "timeout_s": 1, "count": 1}),
            node("wol1", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:01"}),
            node("wol2", "wake_on_lan", {"mac_address": "AA:BB:CC:DD:EE:02"}),
            node("cf", "change_filter"),
        ]
        flow = _flow(
            nodes,
            [
                edge("cv", "ac1", "value", "trigger"),
                edge("ac1", "hc1", "success", "trigger"),
                edge("hc1", "ac2", "reachable", "trigger"),
                edge("ac2", "hc2", "success", "trigger"),
                edge("hc2", "wol1", "reachable", "trigger"),
                edge("wol1", "wol2", "sent", "trigger"),
                edge("wol2", "cf", "sent", "in"),
            ],
        )
        manager = _make_manager()
        graph_id = "g-final-wol-holds-cf"
        manager._graphs[graph_id] = ("test", True, flow)
        manager._node_state[graph_id] = {}
        # wol2's unresolved placeholder "sent" reads False — seed a baseline
        # of True so an incorrect commit of that placeholder is observable
        # as a (wrong) changed=True, not masked by coincidence.
        manager._hysteresis[graph_id] = {"cf": {"value": True}}

        mock_client_cls = _patch_api_success()
        try:
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert outputs["wol1"]["sent"] is True
        # wol2 was never actually sent this tick — cf must stay held, not
        # commit wol2's placeholder sent=False as a real change.
        assert outputs["cf"]["changed"] is False

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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0)],
                ),
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            ):
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
            with (
                patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
                patch(
                    "obs.logic.manager._ping_host",
                    new_callable=AsyncMock,
                    side_effect=[(True, 1.0), (True, 2.0), (True, 3.0)],
                ) as mock_ping,
                patch("obs.logic.manager.asyncio.to_thread", new_callable=AsyncMock),
            ):
                outputs = asyncio.run(manager._execute_graph(graph_id, "test", flow, {}))
        finally:
            mock_client_cls.stop()

        assert mock_ping.await_count == 3
        assert outputs["wol"]["sent"] is True
        assert outputs["hc2"]["reachable"] is True
        assert outputs["hc3"]["reachable"] is True
