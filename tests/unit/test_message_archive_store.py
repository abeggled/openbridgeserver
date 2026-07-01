from __future__ import annotations

from obs.message_archive import ArchiveInput, EntryInput, EntryQuery, MessageArchiveStore


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


async def test_message_archive_store_normalizes_archive_ids_to_lowercase(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        entry = await store.create_entry(EntryInput(archive_id="System.Events", title="Mixed Case"))
        assert entry["archive_id"] == "system.events"
        assert await store.get_archive("System.Events") is not None
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


async def test_message_archive_integrity_check_reports_ok(tmp_path):
    store = MessageArchiveStore(str(tmp_path / "messages.sqlite3"))
    await store.connect()
    try:
        result = await store.integrity_check()
        assert result["ok"] is True
        assert result["status"] == "ok"
    finally:
        await store.disconnect()
