"""
LogicManager — manages all logic graphs and integrates with the EventBus.

- Subscribes to DataValueEvents
- Triggers graphs whose datapoint_read nodes watch the changed DataPoint
- Executes the graph and writes outputs back via the registry
- Schedules timer_cron nodes via asyncio tasks (requires croniter)
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from opentws.logic.executor import GraphExecutor
from opentws.logic.models import FlowData

logger = logging.getLogger(__name__)

_THROTTLE_UNITS: dict[str, float] = {"ms": 1.0, "s": 1000.0, "min": 60_000.0, "h": 3_600_000.0}

_manager: "LogicManager | None" = None


def get_logic_manager() -> "LogicManager":
    if _manager is None:
        raise RuntimeError("LogicManager not initialised")
    return _manager


def init_logic_manager(db: Any, event_bus: Any, registry: Any) -> "LogicManager":
    global _manager
    _manager = LogicManager(db, event_bus, registry)
    return _manager


class LogicManager:
    def __init__(self, db: Any, event_bus: Any, registry: Any):
        self._db = db
        self._event_bus = event_bus
        self._registry = registry
        # hysteresis state per graph per node
        self._hysteresis: dict[str, dict[str, bool]] = {}
        # graph cache: id → (name, enabled, FlowData)
        self._graphs: dict[str, tuple[str, bool, FlowData]] = {}
        # per-node runtime state for filter/throttle
        # {graph_id: {node_id: {last_value, last_ts, last_write_val, last_write_ts}}}
        self._node_state: dict[str, dict[str, dict[str, Any]]] = {}
        # cron tasks: (graph_id, node_id) → asyncio.Task
        self._cron_tasks: dict[tuple[str, str], asyncio.Task] = {}  # type: ignore[type-arg]

    async def start(self) -> None:
        """Subscribe to EventBus, load all graphs and start cron schedulers."""
        await self._load_graphs()
        from opentws.core.event_bus import DataValueEvent
        self._event_bus.subscribe(DataValueEvent, self._on_value_event)
        self._start_cron_tasks()
        logger.info("LogicManager started — %d graphs loaded", len(self._graphs))

    async def stop(self) -> None:
        from opentws.core.event_bus import DataValueEvent
        self._event_bus.unsubscribe(DataValueEvent, self._on_value_event)
        for task in self._cron_tasks.values():
            task.cancel()
        self._cron_tasks.clear()

    async def reload(self) -> None:
        """Reload graph cache from DB and restart cron schedulers."""
        for task in self._cron_tasks.values():
            task.cancel()
        self._cron_tasks.clear()
        await self._load_graphs()
        self._start_cron_tasks()

    # ── Cron Scheduler ────────────────────────────────────────────────────

    def _start_cron_tasks(self) -> None:
        """Start asyncio tasks for all timer_cron nodes in enabled graphs."""
        try:
            import croniter as _croniter_check  # noqa: F401
        except ImportError:
            logger.warning(
                "croniter not installed — timer_cron nodes will not auto-execute. "
                "Install with: pip install croniter"
            )
            return

        for graph_id, (name, enabled, flow) in self._graphs.items():
            if not enabled:
                continue
            for node in flow.nodes:
                if node.type != "timer_cron":
                    continue
                key = (graph_id, node.id)
                if key in self._cron_tasks and not self._cron_tasks[key].done():
                    continue  # already running
                cron_expr = node.data.get("cron", "0 7 * * *")
                task = asyncio.create_task(
                    self._cron_loop(graph_id, node.id, cron_expr),
                    name=f"cron-{graph_id[:8]}-{node.id[:8]}",
                )
                self._cron_tasks[key] = task
                logger.info(
                    "Cron scheduled: graph=%s (%s) node=%s expr=%r",
                    graph_id[:8], name, node.id[:8], cron_expr,
                )

    async def _cron_loop(self, graph_id: str, node_id: str, cron_expr: str) -> None:
        """Fires a timer_cron graph node on its cron schedule — runs indefinitely."""
        from croniter import croniter

        while True:
            try:
                now     = datetime.now(timezone.utc)
                it      = croniter(cron_expr, now)
                next_dt = it.get_next(datetime)
                wait_s  = max(0.0, (next_dt - now).total_seconds())
                logger.debug(
                    "Cron graph %s: sleeping %.0fs until %s",
                    graph_id[:8], wait_s, next_dt.isoformat(),
                )
                await asyncio.sleep(wait_s)

                entry = self._graphs.get(graph_id)
                if entry and entry[1]:  # still exists and enabled
                    g_name, _, flow = entry
                    overrides = {node_id: {"trigger": True}}
                    await self._execute_graph(graph_id, g_name, flow, overrides)
                    logger.info(
                        "Cron graph %s (%s) fired at %s",
                        graph_id[:8], g_name, next_dt.isoformat(),
                    )

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Cron loop error graph=%s: %s", graph_id[:8], exc)
                await asyncio.sleep(60)  # back-off on unexpected errors

    # ── Event Handler ─────────────────────────────────────────────────────

    async def _on_value_event(self, event: Any) -> None:
        dp_id = str(event.datapoint_id)
        now   = datetime.now(timezone.utc)

        for graph_id, (name, enabled, flow) in self._graphs.items():
            if not enabled:
                continue
            trigger_nodes = [
                n for n in flow.nodes
                if n.type == "datapoint_read" and n.data.get("datapoint_id") == dp_id
            ]
            if not trigger_nodes:
                continue

            graph_state = self._node_state.setdefault(graph_id, {})
            overrides: dict[str, dict[str, Any]] = {}

            for tn in trigger_nodes:
                ns  = graph_state.setdefault(tn.id, {})
                d   = tn.data
                new_val  = event.value
                last_val = ns.get("last_value")
                last_ts  = ns.get("last_ts")

                # ── Filter: trigger_on_change ────────────────────────────
                toc = d.get("trigger_on_change")
                if toc is True or toc == "true":
                    if new_val == last_val:
                        continue

                # ── Filter: min_delta ────────────────────────────────────
                raw_delta = d.get("min_delta")
                if raw_delta not in (None, "", 0) and last_val is not None:
                    try:
                        if abs(float(new_val) - float(last_val)) < float(raw_delta):
                            continue
                    except (TypeError, ValueError):
                        pass

                # ── Filter: min_delta_pct ────────────────────────────────
                raw_pct = d.get("min_delta_pct")
                if raw_pct not in (None, "", 0) and last_val is not None:
                    try:
                        base = abs(float(last_val)) or 1.0
                        if abs(float(new_val) - float(last_val)) / base * 100 < float(raw_pct):
                            continue
                    except (TypeError, ValueError):
                        pass

                # ── Filter: throttle (value + unit) ──────────────────────
                tv = d.get("throttle_value")
                if tv not in (None, "", 0) and last_ts is not None:
                    try:
                        unit_ms = _THROTTLE_UNITS.get(d.get("throttle_unit", "s"), 1000.0)
                        throttle_ms = float(tv) * unit_ms
                        elapsed_ms  = (now - last_ts).total_seconds() * 1000
                        if elapsed_ms < throttle_ms:
                            continue
                    except (TypeError, ValueError):
                        pass

                # All filters passed — update state and add override
                ns["last_value"] = new_val
                ns["last_ts"]    = now
                overrides[tn.id] = {"value": new_val, "changed": True}

            if not overrides:
                continue
            await self._execute_graph(graph_id, name, flow, overrides)

    # ── Execution ─────────────────────────────────────────────────────────

    async def execute_graph(self, graph_id: str) -> dict[str, Any]:
        """Manually trigger a graph (e.g. from API).

        Reads current registry values for all datapoint_read nodes so that
        a manual run shows real values — not None.
        """
        entry = self._graphs.get(graph_id)
        if not entry:
            raise KeyError(f"Graph {graph_id} not in cache")
        name, enabled, flow = entry

        # Seed overrides from current registry values
        overrides: dict[str, dict[str, Any]] = {}
        for node in flow.nodes:
            if node.type != "datapoint_read":
                continue
            dp_id_str = node.data.get("datapoint_id")
            if not dp_id_str:
                continue
            try:
                dp_id = uuid.UUID(dp_id_str)
                vs = self._registry.get_value(dp_id)
                if vs is not None:
                    overrides[node.id] = {"value": vs.value, "changed": False}
            except Exception:
                pass

        return await self._execute_graph(graph_id, name, flow, overrides)

    async def _execute_graph(
        self,
        graph_id: str,
        name: str,
        flow: FlowData,
        overrides: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        hyst = self._hysteresis.setdefault(graph_id, {})
        executor = GraphExecutor(flow, hyst)
        try:
            outputs = executor.execute(overrides)
        except Exception as exc:
            logger.error("Graph %s (%s) execution error: %s", graph_id, name, exc)
            return {}

        # Broadcast execution results to all WS clients (live debug mode)
        try:
            from opentws.api.v1.websocket import get_ws_manager

            def _safe(v: Any) -> Any:
                if v is None or isinstance(v, (bool, int, float, str)):
                    return v
                return str(v)

            safe_outputs = {
                nid: {k: _safe(val) for k, val in node_out.items()}
                for nid, node_out in outputs.items()
                if isinstance(node_out, dict)
            }
            await get_ws_manager().broadcast({
                "action":   "logic_run",
                "graph_id": graph_id,
                "outputs":  safe_outputs,
            })
        except Exception:
            pass  # WS not ready or no clients — non-critical

        # Process datapoint_write outputs — apply trigger gating + write-side filters,
        # then publish DataValueEvent so registry, ring-buffer, MQTT and WS all get notified.
        from opentws.core.event_bus import DataValueEvent
        graph_state = self._node_state.setdefault(graph_id, {})
        write_now   = datetime.now(timezone.utc)

        # Build set of node+handle pairs that have an incoming edge (= are wired)
        wired_inputs: set[tuple[str, str]] = {
            (e.target, e.targetHandle or "in")
            for e in flow.edges
        }

        for node in flow.nodes:
            if node.type != "datapoint_write":
                continue
            node_out  = outputs.get(node.id, {})
            write_val = node_out.get("_write_value")

            # ── Trigger gating ───────────────────────────────────────────
            # If the trigger handle is wired, only write when trigger is truthy.
            if (node.id, "trigger") in wired_inputs:
                triggered = node_out.get("_triggered")
                if not GraphExecutor._to_bool(triggered):
                    continue

            if write_val is None:
                continue
            dp_id_str = node.data.get("datapoint_id")
            if not dp_id_str:
                continue

            d  = node.data
            ns = graph_state.setdefault(node.id, {})
            last_wr = ns.get("last_write_val")
            last_ts = ns.get("last_write_ts")

            # ── Filter: only_on_change ───────────────────────────────────
            ooc = d.get("only_on_change")
            if ooc is True or ooc == "true":
                if write_val == last_wr:
                    continue

            # ── Filter: min_delta (write side) ───────────────────────────
            raw_delta = d.get("min_delta")
            if raw_delta not in (None, "", 0) and last_wr is not None:
                try:
                    if abs(float(write_val) - float(last_wr)) < float(raw_delta):
                        continue
                except (TypeError, ValueError):
                    pass

            # ── Filter: throttle (value + unit, write side) ───────────────
            tv = d.get("throttle_value")
            if tv not in (None, "", 0) and last_ts is not None:
                try:
                    unit_ms    = _THROTTLE_UNITS.get(d.get("throttle_unit", "s"), 1000.0)
                    throttle_ms = float(tv) * unit_ms
                    elapsed_ms  = (write_now - last_ts).total_seconds() * 1000
                    if elapsed_ms < throttle_ms:
                        continue
                except (TypeError, ValueError):
                    pass

            # All filters passed — update state and publish
            ns["last_write_val"] = write_val
            ns["last_write_ts"]  = write_now
            try:
                dp_id = uuid.UUID(dp_id_str)
                event = DataValueEvent(
                    datapoint_id=dp_id,
                    value=write_val,
                    quality="good",
                    source_adapter="logic",
                )
                await self._event_bus.publish(event)
                logger.debug("Graph %s: wrote dp %s = %s", graph_id, dp_id_str, write_val)
            except Exception as exc:
                logger.warning("Graph %s: failed to write dp %s: %s", graph_id, dp_id_str, exc)

        return outputs

    # ── Cache ─────────────────────────────────────────────────────────────

    async def _load_graphs(self) -> None:
        rows = await self._db.fetchall(
            "SELECT id, name, enabled, flow_data FROM logic_graphs"
        )
        self._graphs = {}
        for row in rows:
            try:
                raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
                flow = FlowData.model_validate(raw)
                self._graphs[row["id"]] = (row["name"], bool(row["enabled"]), flow)
            except Exception as exc:
                logger.warning("Failed to parse graph %s: %s", row["id"], exc)

    def invalidate_cache(self, graph_id: str) -> None:
        self._graphs.pop(graph_id, None)
        self._hysteresis.pop(graph_id, None)
        self._node_state.pop(graph_id, None)
        # Cancel cron tasks for this specific graph
        to_remove = [k for k in self._cron_tasks if k[0] == graph_id]
        for k in to_remove:
            self._cron_tasks[k].cancel()
            del self._cron_tasks[k]
