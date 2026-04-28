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

import httpx

from obs.logic.executor import GraphExecutor
from obs.logic.models import FlowData

logger = logging.getLogger(__name__)


def _msg_to_str(v: object) -> str:
    """Convert any node output value to a message string.

    Uses explicit None-check rather than truthiness so that falsy values
    (0, False, 0.0, "") are preserved as their string representation instead
    of being silently replaced by a fallback.
    """
    import json as _j  # noqa: PLC0415
    if isinstance(v, (dict, list)):
        return _j.dumps(v, ensure_ascii=False)
    return str(v)

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
        # persistent state per graph per node (hysteresis bool, statistics accumulators, …)
        self._hysteresis: dict[str, dict[str, Any]] = {}
        # graph cache: id → (name, enabled, FlowData)
        self._graphs: dict[str, tuple[str, bool, FlowData]] = {}
        # per-node runtime state for filter/throttle
        # {graph_id: {node_id: {last_value, last_ts, last_write_val, last_write_ts}}}
        self._node_state: dict[str, dict[str, dict[str, Any]]] = {}
        # cron tasks: (graph_id, node_id) → asyncio.Task
        self._cron_tasks: dict[tuple[str, str], asyncio.Task] = {}  # type: ignore[type-arg]
        # application-level config (e.g. timezone) — loaded from app_settings table
        self._app_config: dict[str, Any] = {"timezone": "Europe/Zurich"}

    async def start(self) -> None:
        """Subscribe to EventBus, load all graphs and start cron schedulers."""
        await self._load_app_config()
        await self._load_graphs()
        from obs.core.event_bus import DataValueEvent
        self._event_bus.subscribe(DataValueEvent, self._on_value_event)
        self._start_cron_tasks()
        logger.info("LogicManager started — %d graphs loaded", len(self._graphs))

    async def stop(self) -> None:
        from obs.core.event_bus import DataValueEvent
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

    # ── App Config ────────────────────────────────────────────────────────

    async def _load_app_config(self) -> None:
        """Load app-level settings (e.g. timezone) from the database."""
        try:
            rows = await self._db.fetchall("SELECT key, value FROM app_settings")
            for row in rows:
                self._app_config[row["key"]] = row["value"]
            logger.debug("LogicManager: app_config loaded: %s", self._app_config)
        except Exception as exc:
            logger.warning("LogicManager: could not load app_settings: %s", exc)

    def update_app_config(self, config: dict[str, Any]) -> None:
        """Hot-update app config (called by settings API on PUT /system/settings)."""
        self._app_config.update(config)
        logger.info("LogicManager: app_config updated: %s", config)

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

        Registry seeding for all datapoint_read nodes is handled inside
        _execute_graph, so no extra overrides are needed here.
        """
        entry = self._graphs.get(graph_id)
        if not entry:
            raise KeyError(f"Graph {graph_id} not in cache")
        name, enabled, flow = entry
        return await self._execute_graph(graph_id, name, flow, {})

    async def _execute_graph(
        self,
        graph_id: str,
        name: str,
        flow: FlowData,
        overrides: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        execute_now = datetime.now(timezone.utc)
        graph_state = self._node_state.setdefault(graph_id, {})

        # ── Seed all datapoint_read nodes from registry ───────────────────
        # In event-driven execution only the triggered node(s) have overrides.
        # All other DP-LESEN nodes would receive None, which propagates as 0.0
        # through _to_num() in downstream blocks. Fix: pre-seed from registry so
        # every DP-LESEN node has the latest known value. Caller overrides
        # (event value + changed=True) are applied on top and take priority.
        aug_overrides: dict[str, dict[str, Any]] = {}
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
                    aug_overrides[node.id] = {"value": vs.value, "changed": False}
            except Exception:
                pass
        # Event / manual overrides take priority over registry seed
        aug_overrides.update(overrides)

        # ── Pre-compute operating_hours values to inject as overrides ─────
        for node in flow.nodes:
            if node.type == "operating_hours":
                ns  = graph_state.setdefault(node.id, {"accumulated_hours": 0.0, "last_start": None})
                acc = ns["accumulated_hours"]
                if ns.get("last_start"):
                    acc += (execute_now - ns["last_start"]).total_seconds() / 3600
                aug_overrides[node.id] = {**aug_overrides.get(node.id, {}), "_computed_hours": round(acc, 6)}

        hyst = self._hysteresis.setdefault(graph_id, {})
        executor = GraphExecutor(flow, hyst, self._app_config)
        try:
            outputs = executor.execute(aug_overrides)
        except Exception as exc:
            logger.error("Graph %s (%s) execution error: %s", graph_id, name, exc)
            return {}

        # ── Update operating_hours state ─────────────────────────────────
        for node in flow.nodes:
            if node.type != "operating_hours":
                continue
            out      = outputs.get(node.id, {})
            ns       = graph_state.setdefault(node.id, {"accumulated_hours": 0.0, "last_start": None})
            is_reset  = out.get("_reset", False)
            is_active = out.get("_active", False)
            if is_reset:
                ns["accumulated_hours"] = 0.0
                ns["last_start"] = execute_now if is_active else None
            elif is_active:
                if not ns.get("last_start"):
                    ns["last_start"] = execute_now
            else:
                if ns.get("last_start"):
                    ns["accumulated_hours"] += (execute_now - ns["last_start"]).total_seconds() / 3600
                    ns["last_start"] = None

        # ── Handle api_client ─────────────────────────────────────────────
        # Track which api_client nodes completed an HTTP call so we can
        # re-propagate their real outputs to downstream nodes afterwards.
        triggered_api_clients: set[str] = set()
        import json as _json  # noqa: PLC0415
        for node in flow.nodes:
            if node.type != "api_client":
                continue
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                continue
            url = (node.data.get("url") or "").strip()
            if not url:
                continue
            method       = (node.data.get("method", "GET") or "GET").upper()
            content_type = node.data.get("content_type", "application/json")
            resp_type    = node.data.get("response_type", "application/json")
            verify_ssl   = node.data.get("verify_ssl", True)
            if isinstance(verify_ssl, str):
                verify_ssl = verify_ssl.lower() not in ("false", "0", "no")
            timeout_s = float(node.data.get("timeout_s", 10) or 10)
            extra_headers: dict[str, str] = {}
            hdr_str = (node.data.get("headers") or "").strip()
            if hdr_str:
                try:
                    extra_headers = _json.loads(hdr_str)
                except Exception:
                    pass
            body = out.get("_body")
            # ── Authentication ──────────────────────────────────────────
            auth_type = (node.data.get("auth_type") or "none").lower()
            auth: Any = None
            if auth_type in ("basic", "digest"):
                username = (node.data.get("auth_username") or "").strip()
                password = (node.data.get("auth_password") or "").strip()
                if username:
                    auth = httpx.BasicAuth(username, password) if auth_type == "basic" \
                        else httpx.DigestAuth(username, password)
            elif auth_type == "bearer":
                token = (node.data.get("auth_token") or "").strip()
                if token:
                    extra_headers = {**extra_headers, "Authorization": f"Bearer {token}"}
            try:
                req_kwargs: dict[str, Any] = {"headers": extra_headers, "timeout": timeout_s}
                if method in ("POST", "PUT", "PATCH"):
                    if content_type == "application/json":
                        req_kwargs["content"] = _json.dumps(body) if not isinstance(body, (str, bytes)) else body
                        req_kwargs["headers"] = {**extra_headers, "Content-Type": "application/json"}
                    elif content_type == "application/x-www-form-urlencoded":
                        req_kwargs["data"] = body if isinstance(body, dict) else {"data": str(body)}
                    else:
                        req_kwargs["content"] = str(body or "")
                        req_kwargs["headers"] = {**extra_headers, "Content-Type": "text/plain"}
                async with httpx.AsyncClient(auth=auth, verify=verify_ssl) as client:
                    resp = await client.request(method, url, **req_kwargs)
                    if resp_type in ("json", "application/json"):
                        try:
                            resp_data: Any = resp.json()
                        except Exception:
                            resp_data = resp.text
                    else:
                        resp_data = resp.text
                    outputs[node.id].update({
                        "response": resp_data,
                        "status":   resp.status_code,
                        "success":  200 <= resp.status_code < 300,
                    })
                    logger.info("Graph %s: API %s %s → %d", graph_id[:8], method, url, resp.status_code)
                    triggered_api_clients.add(node.id)
            except Exception as exc:
                logger.warning("Graph %s: api_client failed: %s", graph_id[:8], exc)
                outputs[node.id].update({"response": str(exc), "status": None, "success": False})

        # ── Re-propagate api_client outputs to downstream nodes ───────────
        # The first executor pass computed downstream nodes with the placeholder
        # success=False. Now that we have the real HTTP results, we re-run the
        # executor for those downstream nodes using input overrides so their
        # outputs (and downstream datapoint writes, etc.) reflect the real values.
        if triggered_api_clients:
            downstream_overrides: dict[str, dict[str, Any]] = {}
            for e in flow.edges:
                if e.source in triggered_api_clients:
                    src_handle = e.sourceHandle or "out"
                    tgt_handle = e.targetHandle or "in"
                    downstream_overrides.setdefault(e.target, {})[tgt_handle] = (
                        outputs[e.source].get(src_handle)
                    )
            if downstream_overrides:
                second_executor = GraphExecutor(flow, hyst, self._app_config)
                second_outputs  = second_executor.execute(downstream_overrides)
                api_client_ids  = {n.id for n in flow.nodes if n.type == "api_client"}
                for nid, vals in second_outputs.items():
                    if nid not in api_client_ids:
                        outputs[nid] = vals

        # ── Handle notify_pushover ────────────────────────────────────────
        # Runs AFTER api_client second-pass so that graphs with api_client →
        # json_extractor → notify see the real HTTP response, not placeholders.
        for node in flow.nodes:
            if node.type != "notify_pushover":
                continue
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                continue
            app_token = (node.data.get("app_token") or "").strip()
            user_key  = (node.data.get("user_key")  or "").strip()
            if not app_token or not user_key:
                logger.warning("Pushover: app_token or user_key missing on node %s", node.id[:8])
                continue
            _raw_msg = out.get("_message")
            msg       = _msg_to_str(_raw_msg) if _raw_msg is not None \
                else str(node.data.get("message") or "")
            title     = node.data.get("title", "open bridge server")
            prio      = int(node.data.get("priority", 0))
            # Input port value takes precedence over static config
            _out_url   = out.get("_url")
            _out_utit  = out.get("_url_title")
            _out_img   = out.get("_image_url")
            url       = (_msg_to_str(_out_url)  if _out_url  is not None else (node.data.get("url")       or "")).strip()
            url_title = (_msg_to_str(_out_utit) if _out_utit is not None else (node.data.get("url_title") or "")).strip()
            image_url = (_msg_to_str(_out_img)  if _out_img  is not None else (node.data.get("image_url") or "")).strip()
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    payload: dict[str, object] = {
                        "token": app_token, "user": user_key,
                        "title": str(title), "message": msg, "priority": prio,
                    }
                    if url:
                        payload["url"] = url
                    if url_title:
                        payload["url_title"] = url_title

                    if image_url:
                        # Download image and attach as multipart
                        img_r = await client.get(image_url, timeout=10.0)
                        img_r.raise_for_status()
                        content_type = img_r.headers.get("content-type", "image/jpeg")
                        fname = image_url.split("?")[0].split("/")[-1] or "image.jpg"
                        r = await client.post(
                            "https://api.pushover.net/1/messages.json",
                            data=payload,
                            files={"attachment": (fname, img_r.content, content_type)},
                        )
                    else:
                        r = await client.post(
                            "https://api.pushover.net/1/messages.json",
                            data=payload,
                        )
                    r.raise_for_status()
                    outputs[node.id]["sent"] = True
                    logger.info("Graph %s: Pushover sent (msg=%r)", graph_id[:8], msg[:40])
            except Exception as exc:
                logger.warning("Graph %s: Pushover failed (msg=%r): %s", graph_id[:8], msg[:40], exc)

        # ── Handle notify_sms ─────────────────────────────────────────────
        for node in flow.nodes:
            if node.type != "notify_sms":
                continue
            out = outputs.get(node.id, {})
            if not GraphExecutor._to_bool(out.get("_trigger")):
                continue
            api_key = (node.data.get("api_key") or "").strip()
            to      = (node.data.get("to")      or "").strip()
            if not api_key or not to:
                logger.warning("seven.io SMS: api_key or to missing on node %s", node.id[:8])
                continue
            _raw_msg = out.get("_message")
            msg    = _msg_to_str(_raw_msg) if _raw_msg is not None \
                else str(node.data.get("message") or "")
            sender = node.data.get("sender", "obs")
            try:
                async with httpx.AsyncClient(timeout=15.0) as client:
                    r = await client.post(
                        "https://gateway.seven.io/api/sms",
                        headers={"X-Api-Key": api_key},
                        data={"to": to, "from": str(sender), "text": msg},
                    )
                    r.raise_for_status()
                    # seven.io returns the number of sent messages as body (e.g. "1").
                    # A value of "0" means failure (no credits, invalid number, etc.)
                    # even though the HTTP status is 200.
                    body = r.text.strip()
                    logger.info("Graph %s: seven.io response status=%d body=%r", graph_id[:8], r.status_code, body[:80])
                    # seven.io returns the number of sent messages on success (e.g. "1"),
                    # or a numeric error code on failure. Known error codes:
                    _SEVEN_ERRORS = {
                        100: "Unbekannter Fehler / Empfänger nicht angegeben",
                        200: "Absender nicht angegeben",
                        201: "Absender zu lang (max 11 Zeichen)",
                        300: "Nachricht nicht angegeben",
                        301: "Nachricht zu lang",
                        401: "API-Key ungültig oder nicht autorisiert",
                        402: "Nicht genug Guthaben",
                        403: "Absender nicht erlaubt",
                        500: "Server-Fehler bei seven.io",
                    }
                    try:
                        body_int = int(body)
                        if body_int in _SEVEN_ERRORS:
                            raise ValueError(f"seven.io Fehlercode {body_int}: {_SEVEN_ERRORS[body_int]}")
                        if body_int <= 0:
                            raise ValueError(f"seven.io: 0 Nachrichten gesendet (body={body!r})")
                    except ValueError:
                        raise  # re-raise error code or zero-count errors
                    except TypeError:
                        pass  # non-numeric body → assume success (future API changes)
                    outputs[node.id]["sent"] = True
                    logger.info("Graph %s: seven.io SMS sent to %s (msg=%r)", graph_id[:8], to, msg[:40])
            except Exception as exc:
                logger.warning("Graph %s: seven.io SMS failed (msg=%r): %s", graph_id[:8], msg[:40], exc)

        # ── Process datapoint_write outputs — apply trigger gating + write-side filters,
        # then publish DataValueEvent so registry, ring-buffer, MQTT and WS all get notified.
        from obs.core.event_bus import DataValueEvent
        write_now = execute_now

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

        # ── Persist node state (statistics / hysteresis) to DB ───────────
        # Nodes with persist_state=False are excluded from the saved snapshot
        # so their accumulators reset on server restart (opt-out behaviour).
        hyst = self._hysteresis.get(graph_id)
        if hyst:
            try:
                graph_entry = self._graphs.get(graph_id)
                if graph_entry:
                    _, _, _flow = graph_entry
                    no_persist = {
                        n.id for n in _flow.nodes
                        if n.data.get("persist_state") is False
                    }
                    state_to_save = {nid: s for nid, s in hyst.items() if nid not in no_persist}
                else:
                    state_to_save = hyst
                await self._db.execute_and_commit(
                    "UPDATE logic_graphs SET node_state = ? WHERE id = ?",
                    (json.dumps(state_to_save), graph_id),
                )
            except Exception as exc:
                logger.warning("Graph %s: failed to persist node_state: %s", graph_id[:8], exc)

        # ── Broadcast final execution results to all WS clients ──────────
        # Broadcast happens here — after all async ops (api_client HTTP calls,
        # second-pass re-execution, etc.) — so the debug view shows the real
        # success/response values and not the executor's initial placeholders.
        try:
            from obs.api.v1.websocket import get_ws_manager

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

        return outputs

    # ── Cache ─────────────────────────────────────────────────────────────

    async def _load_graphs(self) -> None:
        rows = await self._db.fetchall(
            "SELECT id, name, enabled, flow_data, node_state FROM logic_graphs"
        )
        self._graphs = {}
        for row in rows:
            try:
                raw = json.loads(row["flow_data"]) if row["flow_data"] else {}
                flow = FlowData.model_validate(raw)
                self._graphs[row["id"]] = (row["name"], bool(row["enabled"]), flow)

                # Restore persisted node state (statistics, hysteresis, …) from DB,
                # but only when there is no in-memory state already — so a reload()
                # triggered by a graph save does NOT overwrite the live accumulators.
                if row["id"] not in self._hysteresis:
                    try:
                        saved = json.loads(row["node_state"] or "{}")
                        if isinstance(saved, dict) and saved:
                            self._hysteresis[row["id"]] = saved
                            logger.debug(
                                "Graph %s: restored node_state (%d nodes)",
                                row["id"][:8], len(saved),
                            )
                    except Exception:
                        pass
            except Exception as exc:
                logger.warning("Failed to parse graph %s: %s", row["id"], exc)

    def invalidate_cache(self, graph_id: str) -> None:
        self._graphs.pop(graph_id, None)
        # NOTE: _hysteresis is intentionally NOT cleared here.
        # When a graph is saved (PUT/PATCH), invalidate_cache + reload() are called.
        # Clearing _hysteresis would reset statistics accumulators on every save.
        # The state is re-used by the next execution after reload.
        # On DELETE the graph row is gone from DB so no persistence concerns remain;
        # the in-memory entry is a no-op and will be GC'd naturally.
        self._node_state.pop(graph_id, None)
        # Cancel cron tasks for this specific graph
        to_remove = [k for k in self._cron_tasks if k[0] == graph_id]
        for k in to_remove:
            self._cron_tasks[k].cancel()
            del self._cron_tasks[k]
