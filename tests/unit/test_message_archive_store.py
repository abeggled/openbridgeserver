from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from obs.config import MessageArchiveSettings, Settings
from obs.message_archive import (
    ArchiveInput,
    ArchivePatch,
    EntryInput,
    EntryPatch,
    EntryPredicate,
    EntryQuery,
    MessageArchiveStore,
    _json_loads_list,
    _json_loads_object,
    activate_message_archive_service,
    broadcast_message_archive_entry,
    close_message_archive_store,
    get_message_archive_service,
    get_message_archive_store,
    init_message_archive_store,
    reset_message_archive_store,
)


async def test_message_archive_store_bounds_wal_growth(tmp_path):
    """See issue #908: without these pragmas the -wal sidecar grows without bound
    on a continuously-written table like message_archive_entries."""
    path = tmp_path / "messages.sqlite3"
    store = MessageArchiveStore(str(path))
    await store.connect()
    try:
        cur = await store.conn.execute("PRAGMA wal_autocheckpoint")
        row = await cur.fetchone()
        assert int(row[0]) == 1000

        cur = await store.conn.execute("PRAGMA journal_size_limit")
        row = await cur.fetchone()
        assert int(row[0]) == 67108864
    finally:
        await store.disconnect()


async def test_message_archive_store_persists_entries_in_separate_db(tmp_path):
    path = tmp_path / "messages.sqlite3"
    store = MessageArchiveStore(str(path))
    await store.connect()
    try:
        await store.create_entry(
            EntryInput(
                archive_id="system",
                type="system",
                severity="info",
                source="test",
                title="OBS gestartet",
                message="Server ist bereit.",
            )
        )
    finally:
        await store.disconnect()

    reopened = MessageArchiveStore(str(path))
    await reopened.connect()
    try:
        result = await reopened.query_entries(EntryQuery(archive_ids=["system"], username="admin"))
        assert result["total"] == 1
        assert result["items"][0]["title"] == "OBS gestartet"
    finally:
        await reopened.disconnect()


async def test_message_archive_store_filters_and_read_state(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        info = await store.create_entry(EntryInput(archive_id="system", severity="info", source="core", title="Info"))
        await store.create_entry(EntryInput(archive_id="security", type="security", severity="warning", source="auth", title="Warnung"))
        read = await store.mark_read("system", info["id"], "alice")
        assert read is not None
        assert read["status"] == "open"

        unread = await store.query_entries(EntryQuery(read_state="unread", username="alice"))
        assert unread["total"] == 1
        assert unread["items"][0]["archive_id"] == "security"

        new_status = await store.query_entries(EntryQuery(status="new", username="alice"))
        assert new_status["total"] == 1
        assert new_status["items"][0]["archive_id"] == "security"

        security = await store.query_entries(EntryQuery(archive_ids=["security"], severity="warning", username="alice"))
        assert security["total"] == 1
        assert security["items"][0]["source"] == "auth"
    finally:
        await store.disconnect()


async def test_message_archive_store_filters_multiple_values(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.create_entry(EntryInput(archive_id="system", type="system", severity="info", source="core", title="System"))
        await store.create_entry(EntryInput(archive_id="adapter", type="adapter", severity="warning", source="knx", title="Adapter"))
        await store.create_entry(EntryInput(archive_id="security", type="security", severity="critical", source="auth", title="Security"))

        result = await store.query_entries(
            EntryQuery(
                types=["system", "adapter"],
                severities=["info", "warning"],
                sources=["core", "knx"],
                username="alice",
            )
        )

        assert result["total"] == 2
        assert {item["title"] for item in result["items"]} == {"System", "Adapter"}
    finally:
        await store.disconnect()


async def test_message_archive_store_filters_predicates_and_time_ranges(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.create_entry(
            EntryInput(
                archive_id="system",
                type="system",
                severity="info",
                source="core",
                title="System Start",
                created_at="2026-01-01T10:00:00+00:00",
            )
        )
        await store.create_entry(
            EntryInput(
                archive_id="security",
                type="security",
                severity="critical",
                source="auth",
                title="Login blocked",
                message="User denied",
                created_at="2026-01-02T10:00:00+00:00",
            )
        )
        await store.create_entry(
            EntryInput(
                archive_id="adapter",
                type="adapter",
                severity="warning",
                source="knx",
                title="Adapter warning",
                created_at="2026-01-03T10:00:00+00:00",
            )
        )

        result = await store.query_entries(
            EntryQuery(
                from_ts="2026-01-01T12:00:00+00:00",
                to_ts="2026-01-03T00:00:00+00:00",
                q="blocked",
                predicates=[
                    EntryPredicate(archive_ids=["SECURITY"], types=["security"], severities=["critical"], statuses=["new"], sources=["auth"]),
                    EntryPredicate(archive_ids=[]),
                ],
                username="alice",
            )
        )

        assert result["total"] == 1
        assert result["items"][0]["archive_id"] == "security"
    finally:
        await store.disconnect()


async def test_message_archive_store_validates_and_normalizes_entry_timestamps(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        entry = await store.create_entry(
            EntryInput(
                archive_id="system",
                title="Offset timestamp",
                created_at="2026-01-01T12:30:00+02:00",
            )
        )

        assert entry["created_at"] == "2026-01-01T10:30:00+00:00"

        with pytest.raises(ValueError, match="created_at"):
            await store.create_entry(EntryInput(archive_id="system", title="Invalid timestamp", created_at="not-a-date"))
    finally:
        await store.disconnect()


async def test_message_archive_store_normalizes_time_range_filters(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.create_entry(
            EntryInput(
                archive_id="system",
                title="Boundary",
                created_at="2026-07-02T10:00:00+00:00",
            )
        )

        result = await store.query_entries(
            EntryQuery(
                archive_ids=["system"],
                from_ts="2026-07-02T10:00:00Z",
                to_ts="2026-07-02T10:00:00Z",
                username="alice",
            )
        )

        assert result["total"] == 1

        with pytest.raises(ValueError, match="from_ts"):
            await store.query_entries(EntryQuery(from_ts="not-a-date", username="alice"))
    finally:
        await store.disconnect()


async def test_message_archive_acknowledge_marks_entry_read(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        entry = await store.create_entry(EntryInput(archive_id="system", title="Quittieren"))

        acknowledged = await store.acknowledge_entry("system", entry["id"], "alice")

        assert acknowledged is not None
        assert acknowledged["status"] == "acknowledged"
        assert acknowledged["is_read"] is True
        assert acknowledged["read_at"] is not None

        unread = await store.query_entries(EntryQuery(read_state="unread", username="alice"))
        assert unread["total"] == 0
    finally:
        await store.disconnect()


async def test_message_archive_store_update_entry_delete_clear_and_exports(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        entry = await store.create_entry(
            EntryInput(
                archive_id="system",
                type="system",
                severity="info",
                source="core",
                title="Original",
                message="Old",
                payload={"a": 1},
            )
        )

        updated = await store.update_entry(
            "system",
            entry["id"],
            EntryPatch(type="adapter", severity="warning", status="closed", source="knx", title="Updated", message="New", payload={"b": 2}),
        )
        assert updated is not None
        assert updated["type"] == "adapter"
        assert updated["payload"] == {"b": 2}
        assert await store.update_entry("system", "missing", EntryPatch(title="Missing")) is None

        jsonl = await store.export_jsonl("SYSTEM")
        assert '"title": "Updated"' in jsonl
        csv_export = await store.export_csv("system")
        assert "archive_id" in csv_export
        assert "Updated" in csv_export

        assert await store.clear_archive("missing") == 0
        assert await store.delete_archive("missing") == -1
        assert await store.clear_archive("system") == 1
        assert await store.delete_archive("system") == 0
    finally:
        await store.disconnect()


async def test_message_archive_store_names_auto_created_system_archive(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.create_entry(EntryInput(archive_id="system", title="Startup"))
        archive = await store.get_archive("system")
        assert archive is not None
        assert archive["name"] == "System"
    finally:
        await store.disconnect()


async def test_message_archive_store_repairs_legacy_lowercase_system_name(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.ensure_archive("system", name="system")
        await store.ensure_archive("system")
        archive = await store.get_archive("system")
        assert archive is not None
        assert archive["name"] == "System"
    finally:
        await store.disconnect()


async def test_message_archive_store_ensure_archive_is_idempotent(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.ensure_archive("alerts", name="Alerts")
        await store.ensure_archive("alerts", name="Ignored")
        archives = await store.list_archives()
        assert [archive["id"] for archive in archives] == ["alerts"]
        assert archives[0]["name"] == "Alerts"
    finally:
        await store.disconnect()


async def test_message_archive_store_normalizes_archive_ids_to_lowercase(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        entry = await store.create_entry(EntryInput(archive_id="System.Events", title="Mixed Case"))
        assert entry["archive_id"] == "system.events"
        assert await store.get_archive("System.Events") is not None
    finally:
        await store.disconnect()


async def test_message_archive_store_rejects_invalid_archive_ids_and_unconnected_store(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    with pytest.raises(RuntimeError, match="connect"):
        _ = store.conn
    await store.connect()
    try:
        with pytest.raises(ValueError, match="empty"):
            await store.ensure_archive(" ")
        with pytest.raises(ValueError, match="at most 80"):
            await store.ensure_archive("a" * 81)
        with pytest.raises(ValueError, match="may only contain"):
            await store.ensure_archive("invalid id!")
    finally:
        await store.disconnect()


async def test_message_archive_store_enforces_max_entries_retention(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.create_archive(
            ArchiveInput(
                id="system",
                name="System",
                retention_max_entries=2,
            )
        )
        for idx in range(4):
            await store.create_entry(EntryInput(archive_id="system", title=f"Entry {idx}"))

        result = await store.query_entries(EntryQuery(archive_ids=["system"], sort="asc", username="admin"))
        assert result["total"] == 2
        assert [item["title"] for item in result["items"]] == ["Entry 2", "Entry 3"]
    finally:
        await store.disconnect()


async def test_message_archive_store_create_entry_broadcasts_refresh_on_pruning(tmp_path, monkeypatch):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    refreshes: list[str | None] = []

    class _WsManager:
        async def broadcast_message_archive_entry(self, entry, previous_entry=None):
            pass

        async def broadcast_message_archive_refresh(self, archive_id=None):
            refreshes.append(archive_id)

    monkeypatch.setattr("obs.api.v1.websocket.get_ws_manager", lambda: _WsManager())
    try:
        await store.create_archive(ArchiveInput(id="system", name="System", retention_max_entries=2))

        await store.create_entry(EntryInput(archive_id="system", title="Entry 0"))
        assert refreshes == []

        await store.create_entry(EntryInput(archive_id="system", title="Entry 1"))
        assert refreshes == []

        await store.create_entry(EntryInput(archive_id="system", title="Entry 2"))
        assert refreshes == ["system"]
    finally:
        await store.disconnect()


async def test_message_archive_store_enforce_retention_missing_archive(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        assert await store.enforce_retention("missing") == 0
    finally:
        await store.disconnect()


async def test_message_archive_store_rejects_entry_removed_by_retention(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.create_archive(ArchiveInput(id="system", name="System", retention_max_age_days=1))
        old_created_at = (datetime.now(UTC) - timedelta(days=2)).isoformat()

        with pytest.raises(ValueError, match="removed by retention"):
            await store.create_entry(EntryInput(archive_id="system", title="Zu alt", created_at=old_created_at))
    finally:
        await store.disconnect()


async def test_message_archive_store_json_helpers_fall_back_for_malformed_payloads(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        entry = await store.create_entry(EntryInput(archive_id="system", title="Malformed"))
        await store.conn.execute("UPDATE message_archive_entries SET payload=? WHERE id=?", ("[]", entry["id"]))
        await store.conn.execute("UPDATE message_archives SET tags=? WHERE id=?", ('{"not":"a-list"}', "system"))
        await store.conn.commit()

        archive = await store.get_archive("system")
        fetched = await store.get_entry("system", entry["id"])

        assert archive is not None
        assert archive["tags"] == []
        assert fetched is not None
        assert fetched["payload"] == {}
    finally:
        await store.disconnect()


def test_json_loads_object_falls_back_on_malformed_json():
    """Genuinely unparsable JSON (not just wrong-shaped JSON) must fall back to
    ``{}`` via the ``json.JSONDecodeError`` branch, not raise."""
    assert _json_loads_object("{not valid json") == {}
    assert _json_loads_object(None) == {}


def test_json_loads_list_falls_back_on_malformed_json():
    """Genuinely unparsable JSON (not just wrong-shaped JSON) must fall back to
    ``[]`` via the ``json.JSONDecodeError`` branch, not raise."""
    assert _json_loads_list("[not valid json") == []
    assert _json_loads_list(None) == []


async def test_message_archive_store_applies_default_type_and_clears_explicit_nulls(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.create_archive(ArchiveInput(id="system", name="System", default_type="adapter", retention_max_entries=3))

        entry = await store.create_entry(EntryInput(archive_id="system", title="Default type"))
        assert entry["type"] == "adapter"

        await store.update_archive(
            "system",
            ArchivePatch(default_type=None, retention_max_entries=None, fields_set={"default_type", "retention_max_entries"}),
        )
        archive = await store.get_archive("system")
        assert archive is not None
        assert archive["default_type"] is None
        assert archive["retention_max_entries"] is None
    finally:
        await store.disconnect()


async def test_message_archive_service_not_published_when_store_connect_fails(tmp_path):
    await close_message_archive_store()
    not_a_directory = tmp_path / "not-a-directory"
    not_a_directory.write_text("x")
    settings = Settings(message_archive=MessageArchiveSettings(path=str(not_a_directory / "messages.sqlite3")))
    try:
        store = await init_message_archive_store(settings)

        assert store.status == "degraded"
        with pytest.raises(RuntimeError, match="not initialized"):
            get_message_archive_service()
    finally:
        await close_message_archive_store()


async def test_message_archive_global_store_lifecycle_and_service_broadcast(tmp_path, monkeypatch):
    await close_message_archive_store()
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    broadcasted: list[dict] = []

    class _WsManager:
        async def broadcast_message_archive_entry(self, entry, previous_entry=None):
            broadcasted.append(entry)

    monkeypatch.setattr("obs.api.v1.websocket.get_ws_manager", lambda: _WsManager())
    try:
        activate_message_archive_service(store)
        assert get_message_archive_store() is store

        service = get_message_archive_service()
        entry = await service.record("system", type="system", severity="info", title="Broadcast")

        assert entry["title"] == "Broadcast"
        assert broadcasted[0]["id"] == entry["id"]
    finally:
        await close_message_archive_store()


async def test_message_archive_broadcast_ignores_missing_or_failing_ws_manager(monkeypatch, caplog):
    async def _run_missing():
        def _missing_manager():
            raise RuntimeError("no ws")

        monkeypatch.setattr("obs.api.v1.websocket.get_ws_manager", _missing_manager)
        await broadcast_message_archive_entry({"id": "entry-1"})

    async def _run_unavailable():
        def _broken_manager():
            raise ValueError("boom")

        monkeypatch.setattr("obs.api.v1.websocket.get_ws_manager", _broken_manager)
        await broadcast_message_archive_entry({"id": "entry-2"})

    async def _run_broadcast_failure():
        class _FailingManager:
            async def broadcast_message_archive_entry(self, _entry, previous_entry=None):
                raise ValueError("broadcast failed")

        monkeypatch.setattr("obs.api.v1.websocket.get_ws_manager", lambda: _FailingManager())
        await broadcast_message_archive_entry({"id": "entry-3"})

    await _run_missing()
    await _run_unavailable()
    await _run_broadcast_failure()

    assert "WebSocket manager unavailable" in caplog.text
    assert "WebSocket broadcast failed" in caplog.text


async def test_message_archive_service_rejects_inactive_store(tmp_path):
    await close_message_archive_store()
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.disconnect()
        with pytest.raises(RuntimeError, match="not connected"):
            activate_message_archive_service(store)
        reset_message_archive_store()
        with pytest.raises(RuntimeError, match="not initialized"):
            get_message_archive_store()
        with pytest.raises(RuntimeError, match="not initialized"):
            get_message_archive_service()
    finally:
        await close_message_archive_store()


async def test_message_archive_store_enforces_retention_after_settings_update(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        await store.create_archive(ArchiveInput(id="system", name="System"))
        for idx in range(4):
            await store.create_entry(EntryInput(archive_id="system", title=f"Entry {idx}"))

        await store.update_archive("system", ArchivePatch(retention_max_entries=2, fields_set={"retention_max_entries"}))

        result = await store.query_entries(EntryQuery(archive_ids=["system"], sort="asc", username="admin"))
        assert result["total"] == 2
        assert [item["title"] for item in result["items"]] == ["Entry 2", "Entry 3"]
    finally:
        await store.disconnect()


async def test_message_archive_integrity_check_reports_ok(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        result = await store.integrity_check()
        assert result["ok"] is True
        assert result["status"] == "ok"
    finally:
        await store.disconnect()
