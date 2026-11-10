"""###VAR### in text fields of notification, archive, string and iCal blocks (#1301)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

from obs.adapters.message.providers.base import MessageSendResult
from obs.logic.executor import GraphExecutor
from obs.logic.manager import LogicManager
from obs.logic.models import FlowData
from tests.unit.conftest import node

SUMMER = datetime(2026, 6, 8, 5, 5, 9, tzinfo=UTC)
CONFIG = {"timezone": "Europe/Zurich", "language": "de"}
OBS1 = [{"slot": 1, "datapoint_id": "dp1", "datapoint_name": "Lamp"}]
OBS1_UUID = [{"slot": 1, "datapoint_id": "11111111-1111-1111-1111-111111111111", "datapoint_name": "Lamp"}]


def _executor(nodes, lookup=None):
    flow = FlowData.model_validate({"nodes": nodes, "edges": []})
    return GraphExecutor(flow, app_config=CONFIG, run_time=SUMMER, datapoint_lookup=lookup)


def _manager(value=None):
    db = AsyncMock()
    db.fetchall = AsyncMock(return_value=[])
    db.execute_and_commit = AsyncMock()
    registry = MagicMock()
    state = MagicMock(value=value) if value is not None else None
    registry.get_value.side_effect = lambda _id: state
    return LogicManager(db, AsyncMock(), registry)


def _run(manager, nodes, overrides):
    flow = FlowData.model_validate({"nodes": nodes, "edges": []})
    manager._graphs["g"] = ("G", True, flow)
    manager._node_state["g"] = {}
    manager._app_config.update(CONFIG)
    return asyncio.run(manager._execute_graph("g", "G", flow, overrides))


class TestStringConcat:
    def test_static_text_is_expanded_but_connected_input_is_not(self):
        n = node("c", "string_concat", {"count": 2, "separator": "|", "text_1": "h=###HH###", "text_2": "static"})
        out = _executor([n]).execute({"c": {"in_2": "###HH###"}})["c"]
        assert out["result"] == "h=07|###HH###"
        assert "_issues" not in out

    def test_obs_slot_error_gives_none_and_issue(self):
        n = node("c", "string_concat", {"count": 2, "text_1": "v=###OBS1###", "variables": OBS1})
        out = _executor([n], lookup=lambda dp: None).execute({})["c"]
        assert out["result"] is None
        assert any("has no value" in i for i in out["_issues"])

    def test_obs_value(self):
        n = node("c", "string_concat", {"count": 2, "text_1": "v=###OBS1###", "variables": OBS1})
        assert _executor([n], lookup=lambda dp: 5).execute({})["c"]["result"] == "v=5"

    def test_unknown_variable_is_flagged_but_literal(self):
        n = node("c", "string_concat", {"count": 2, "text_1": "###FOO###"})
        out = _executor([n]).execute({})["c"]
        assert out["result"] == "###FOO###"
        assert out["_issues"] == ["unknown variable ###FOO###"]


class TestStringReplace:
    def test_plain_replacement_text(self):
        n = node("r", "string_replace", {"rules": [{"search": "X", "replace": "###yyyy###-###MM###"}]})
        assert _executor([n]).execute({"r": {"text": "aXb"}})["r"] == {"result": "a2026-06b"}

    def test_regex_rule_keeps_group_refs_and_escapes_values(self):
        rules = [{"search": r"(a)", "replace": r"\1###OBS1###", "mode": "regex"}]
        n = node("r", "string_replace", {"rules": rules, "variables": OBS1})
        out = _executor([n], lookup=lambda dp: "\\1x").execute({"r": {"text": "a"}})["r"]
        assert out["result"] == "a\\1x"

    def test_missing_obs_gives_none_and_issue(self):
        n = node("r", "string_replace", {"rules": [{"search": "X", "replace": "###OBS2###"}, {"search": "Y"}]})
        out = _executor([n]).execute({"r": {"text": "aXb"}})["r"]
        assert out["result"] is None
        assert out["_issues"] == ["variable OBS2 is not configured"]


class TestArchiveAndNotify:
    def test_archive_expands_fallbacks_only(self):
        manager = _manager(value=3)
        service = MagicMock(record=AsyncMock(return_value={}))
        nodes = [
            node(
                "ma",
                "message_archive",
                {"archive_id": "A", "title": "T ###HH###", "message": "M ###OBS1###", "variables": OBS1_UUID},
            )
        ]
        with (
            patch("obs.message_archive.get_message_archive_service", return_value=service),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, nodes, {"ma": {"trigger": True}})
        kwargs = service.record.await_args.kwargs
        assert out["ma"]["stored"] is True
        assert kwargs["message"] == "M 3"
        assert kwargs["title"].startswith("T ") and kwargs["title"][2:].isdigit()

    def test_notify_object_variable_ignores_debug_read_override(self):
        manager = _manager(value="REGISTRY")
        adapter = MagicMock(adapter_type="MESSAGE")
        adapter.send_notification = AsyncMock(return_value=[MessageSendResult("telegram", "t", True)])
        nodes = [
            node("r", "datapoint_read", {"datapoint_id": OBS1_UUID[0]["datapoint_id"]}),
            node(
                "n",
                "notify_message",
                {
                    "adapter_instance_id": "m1",
                    "providers": [{"provider": "telegram", "target": "t"}],
                    "message": "###OBS1###",
                    "variables": OBS1_UUID,
                },
            ),
        ]
        flow = FlowData.model_validate({"nodes": nodes, "edges": []})
        manager._graphs["g"] = ("G", True, flow)
        manager._node_state["g"] = {}
        ws = MagicMock(has_logic_debug_subscribers=lambda _id: False, broadcast_logic_run=AsyncMock())
        with (
            patch("obs.adapters.registry.get_instance_by_id", return_value=adapter),
            patch("obs.api.v1.websocket.get_ws_manager", return_value=ws),
        ):
            asyncio.run(
                manager._execute_graph("g", "G", flow, {}, debug_overrides={"r": {"value": "DEBUG", "changed": True}, "n": {"trigger": True}})
            )
        assert adapter.send_notification.await_args.kwargs["message"] == "REGISTRY"

    def test_archive_wired_values_are_not_expanded(self):
        manager = _manager()
        service = MagicMock(record=AsyncMock(return_value={}))
        nodes = [node("ma", "message_archive", {"archive_id": "A", "title": "###FOO###", "message": "###OBS9###"})]
        with (
            patch("obs.message_archive.get_message_archive_service", return_value=service),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            _run(manager, nodes, {"ma": {"trigger": True, "message": "###HH###", "title": "###HH###"}})
        kwargs = service.record.await_args.kwargs
        assert (kwargs["message"], kwargs["title"]) == ("###HH###", "###HH###")

    def test_archive_variable_error_is_visible_and_not_stored(self):
        manager = _manager()
        service = MagicMock(record=AsyncMock(return_value={}))
        nodes = [node("ma", "message_archive", {"archive_id": "A", "message": "###OBS1###", "variables": OBS1_UUID})]
        with (
            patch("obs.message_archive.get_message_archive_service", return_value=service),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, nodes, {"ma": {"trigger": True}})
        service.record.assert_not_awaited()
        assert "OBS1" in out["ma"]["__error__"]
        assert out["ma"].get("stored") is not True

    def _notify(self, manager, data, overrides):
        adapter = MagicMock(adapter_type="MESSAGE")
        adapter.send_notification = AsyncMock(return_value=[MessageSendResult("telegram", "t", True)])
        base = {"adapter_instance_id": "m1", "providers": [{"provider": "telegram", "target": "t"}]}
        with (
            patch("obs.adapters.registry.get_instance_by_id", return_value=adapter),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = _run(manager, [node("n", "notify_message", {**base, **data})], overrides)
        return out, adapter

    def test_notify_expands_title_and_fallback_message(self):
        out, adapter = self._notify(
            _manager(value=7),
            {"title": "Day ###dd###", "message": "v=###OBS1###", "variables": OBS1_UUID},
            {"n": {"trigger": True}},
        )
        assert out["n"]["sent"] is True
        kwargs = adapter.send_notification.await_args.kwargs
        assert kwargs["message"] == "v=7"
        assert kwargs["title"].startswith("Day ")

    def test_notify_wired_message_is_not_expanded_and_empty_title_is_none(self):
        _out, adapter = self._notify(_manager(), {}, {"n": {"trigger": True, "message": "###HH###"}})
        kwargs = adapter.send_notification.await_args.kwargs
        assert kwargs["message"] == "###HH###"
        assert kwargs["title"] is None

    def test_notify_variable_error_blocks_send(self):
        out, adapter = self._notify(_manager(), {"title": "###OBS5###"}, {"n": {"trigger": True}})
        adapter.send_notification.assert_not_awaited()
        assert "OBS5" in out["n"]["__error__"]


class TestIcalUrl:
    def _run_ical(self, data, value=None):
        manager = _manager(value=value)
        captured = []

        def fake_targets(url):
            captured.append(url)
            raise RuntimeError("stop")

        with (
            patch("obs.logic.manager._build_ical_fetch_targets", side_effect=fake_targets),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            _run(manager, [node("i", "ical", data)], {})
        return captured

    def test_date_and_obs_variables_in_path_and_query(self):
        urls = self._run_ical(
            {"url": "https://example.com/cal/###yyyy###.ics?room=###OBS1###", "variables": OBS1_UUID},
            value="a b",
        )
        year = str(datetime.now(UTC).year)
        assert urls == [f"https://example.com/cal/{year}.ics?room=a%20b"]

    def test_missing_obs_reports_error_instead_of_reusing_old_calendar(self):
        manager = _manager()
        manager._hysteresis["g"] = {"i": {"raw": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n", "fetched_url": "https://old/x.ics"}}
        out = _run(manager, [node("i", "ical", {"url": "https://example.com/###OBS3###.ics"})], {})
        assert "not configured" in out["i"]["__error__"]
        assert "raw" not in manager._hysteresis["g"]["i"]
        assert not out["i"].get("raw")

    def test_changed_variable_url_drops_old_calendar_even_if_fetch_fails(self):
        manager = _manager(value="new")
        manager._hysteresis["g"] = {"i": {"raw": "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n", "fetched_url": "https://example.com/old.ics"}}
        out = _run(manager, [node("i", "ical", {"url": "https://example.com/###OBS1###.ics", "variables": OBS1_UUID})], {})
        assert not out["i"].get("raw")
        assert "fetched_url" not in manager._hysteresis["g"]["i"]

    def test_old_calendar_stored_by_an_in_flight_refresh_is_dropped_under_the_lock(self):
        manager = _manager(value="new")
        old = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nEND:VCALENDAR\r\n"
        manager._hysteresis["g"] = {"i": {}}
        lock = asyncio.Lock()
        manager._ical_fetch_locks[("g", "i")] = lock

        async def scenario():
            await lock.acquire()
            flow = FlowData.model_validate(
                {"nodes": [node("i", "ical", {"url": "https://example.com/###OBS1###.ics", "variables": OBS1_UUID})], "edges": []}
            )
            manager._graphs["g"] = ("G", True, flow)
            manager._node_state["g"] = {}
            manager._app_config.update(CONFIG)
            task = asyncio.create_task(manager._execute_graph("g", "G", flow, {}))
            await asyncio.sleep(0.05)
            # the preceding refresh publishes the old URL's calendar, then releases
            manager._hysteresis["g"]["i"].update({"raw": old, "fetched_url": "https://example.com/old.ics"})
            lock.release()
            return await task

        with (
            patch("obs.logic.manager._build_ical_fetch_targets", side_effect=RuntimeError("offline")),
            patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
        ):
            out = asyncio.run(scenario())
        assert not out["i"].get("raw")

    def test_variable_in_host_is_rejected_and_not_fetched(self):
        assert self._run_ical({"url": "https://###OBS1###.example.com/c.ics", "variables": OBS1_UUID}, value="x") == []

    def test_missing_obs_skips_fetch(self):
        assert self._run_ical({"url": "https://example.com/###OBS3###.ics"}) == []

    def test_static_url_is_untouched(self):
        assert self._run_ical({"url": "https://example.com/a.ics"}) == ["https://example.com/a.ics"]
