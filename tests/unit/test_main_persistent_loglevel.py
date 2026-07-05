from __future__ import annotations

from types import SimpleNamespace

import pytest

from obs.main import _init_persisted_ringbuffer, _read_persistent_log_level, _stop_optional_ringbuffer


class FakeDb:
    def __init__(self, row=None, exc: Exception | None = None) -> None:
        self.row = row
        self.exc = exc

    async def fetchone(self, _sql: str):
        if self.exc:
            raise self.exc
        return self.row


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"value": "debug"}, "DEBUG"),
        ({"value": "INFO"}, "INFO"),
        ({"value": "trace"}, None),
        ({"value": ""}, None),
        (None, None),
    ],
)
async def test_read_persistent_log_level(row, expected):
    assert await _read_persistent_log_level(FakeDb(row=row)) == expected


async def test_read_persistent_log_level_ignores_db_errors():
    assert await _read_persistent_log_level(FakeDb(exc=RuntimeError("missing table"))) is None


async def test_init_persisted_ringbuffer_subscribes_when_enabled(monkeypatch):
    events: list[tuple[str, object]] = []

    class BusStub:
        def subscribe(self, event_type, handler):
            events.append(("subscribe", (event_type, handler)))

    class RingBufferStub:
        handle_value_event = object()

    db = object()
    ringbuffer = RingBufferStub()

    monkeypatch.setattr(
        "obs.ringbuffer.persisted_config.ensure_legacy_migration_decision",
        lambda _db, **_kwargs: _async_value(None),
    )
    monkeypatch.setattr(
        "obs.ringbuffer.persisted_config.load_persisted_ringbuffer_config",
        lambda _db, **_kwargs: _async_value(
            {
                "enabled": True,
                "max_entries": 42,
                "max_file_size_bytes": 1024,
                "max_age": 3600,
            }
        ),
    )
    monkeypatch.setattr(
        "obs.ringbuffer.ringbuffer.default_ringbuffer_disk_path",
        lambda path: f"{path}.ringbuffer",
    )
    monkeypatch.setattr("obs.ringbuffer.ringbuffer.set_ringbuffer_enabled", lambda enabled: events.append(("enabled", enabled)))

    async def _init_ringbuffer(**kwargs):
        events.append(("ringbuffer_path", kwargs["disk_path"]))
        return ringbuffer

    monkeypatch.setattr("obs.ringbuffer.ringbuffer.init_ringbuffer", _init_ringbuffer)

    await _init_persisted_ringbuffer(db, BusStub(), "/tmp/obs.sqlite", object)

    assert ("enabled", True) in events
    assert ("ringbuffer_path", "/tmp/obs.sqlite.ringbuffer") in events
    assert events[-1][0] == "subscribe"


async def test_init_persisted_ringbuffer_disables_without_initializing(monkeypatch):
    events: list[tuple[str, object]] = []

    monkeypatch.setattr(
        "obs.ringbuffer.persisted_config.ensure_legacy_migration_decision",
        lambda _db, **_kwargs: _async_value(None),
    )
    monkeypatch.setattr(
        "obs.ringbuffer.persisted_config.load_persisted_ringbuffer_config",
        lambda _db, **_kwargs: _async_value(
            {
                "enabled": False,
                "max_entries": 42,
                "max_file_size_bytes": 1024,
                "max_age": 3600,
            }
        ),
    )
    monkeypatch.setattr("obs.ringbuffer.ringbuffer.set_ringbuffer_enabled", lambda enabled: events.append(("enabled", enabled)))
    monkeypatch.setattr("obs.ringbuffer.ringbuffer.reset_ringbuffer", lambda: events.append(("reset", None)))
    monkeypatch.setattr("obs.ringbuffer.ringbuffer.init_ringbuffer", lambda **_kwargs: pytest.fail("ringbuffer should not start"))

    await _init_persisted_ringbuffer(object(), SimpleNamespace(subscribe=lambda *_args: None), "/tmp/obs.sqlite", object)

    assert events == [("reset", None), ("enabled", False)]


async def test_init_persisted_ringbuffer_memory_path_forces_unsegmented(monkeypatch):
    # Ist der abgeleitete RingBuffer-Disk-Pfad ein Memory-Pfad (``:memory:``),
    # darf der segmentierte Store (#919) NICHT starten – sonst würde ein reales
    # ``:memory:_segments``-Verzeichnis mit Manifest-/Segment-Dateien auf die Platte
    # geschrieben, während das Memory-Cleanup ein No-op ist (Codex #951).
    events: list[tuple[str, object]] = []

    class BusStub:
        def subscribe(self, event_type, handler):
            events.append(("subscribe", (event_type, handler)))

    class RingBufferStub:
        handle_value_event = object()

    monkeypatch.setattr(
        "obs.ringbuffer.persisted_config.ensure_legacy_migration_decision",
        lambda _db, **_kwargs: _async_value(None),
    )
    monkeypatch.setattr(
        "obs.ringbuffer.persisted_config.load_persisted_ringbuffer_config",
        lambda _db, **_kwargs: _async_value(
            {
                "enabled": True,
                "max_entries": 42,
                "max_file_size_bytes": 1024,
                "max_age": 3600,
                # Neue Default-Config setzt segmented=true.
                "segmented": True,
                "segment_max_bytes": 4 * 1024 * 1024,
            }
        ),
    )
    monkeypatch.setattr(
        "obs.ringbuffer.ringbuffer.default_ringbuffer_disk_path",
        lambda _path: ":memory:",
    )
    monkeypatch.setattr("obs.ringbuffer.ringbuffer.set_ringbuffer_enabled", lambda enabled: events.append(("enabled", enabled)))

    async def _init_ringbuffer(**kwargs):
        events.append(("segmented", kwargs["segmented"]))
        return RingBufferStub()

    monkeypatch.setattr("obs.ringbuffer.ringbuffer.init_ringbuffer", _init_ringbuffer)

    await _init_persisted_ringbuffer(object(), BusStub(), ":memory:", object)

    assert ("segmented", False) in events


async def test_init_persisted_ringbuffer_file_path_stays_segmented(monkeypatch):
    # Gegenprobe: ein realer File-Pfad reicht segmented=true unverändert durch.
    events: list[tuple[str, object]] = []

    class BusStub:
        def subscribe(self, event_type, handler):
            events.append(("subscribe", (event_type, handler)))

    class RingBufferStub:
        handle_value_event = object()

    monkeypatch.setattr(
        "obs.ringbuffer.persisted_config.ensure_legacy_migration_decision",
        lambda _db, **_kwargs: _async_value(None),
    )
    monkeypatch.setattr(
        "obs.ringbuffer.persisted_config.load_persisted_ringbuffer_config",
        lambda _db, **_kwargs: _async_value(
            {
                "enabled": True,
                "max_entries": 42,
                "max_file_size_bytes": 1024,
                "max_age": 3600,
                "segmented": True,
                "segment_max_bytes": 4 * 1024 * 1024,
            }
        ),
    )
    monkeypatch.setattr(
        "obs.ringbuffer.ringbuffer.default_ringbuffer_disk_path",
        lambda path: f"{path}.ringbuffer",
    )
    monkeypatch.setattr("obs.ringbuffer.ringbuffer.set_ringbuffer_enabled", lambda enabled: events.append(("enabled", enabled)))

    async def _init_ringbuffer(**kwargs):
        events.append(("segmented", kwargs["segmented"]))
        return RingBufferStub()

    monkeypatch.setattr("obs.ringbuffer.ringbuffer.init_ringbuffer", _init_ringbuffer)

    await _init_persisted_ringbuffer(object(), BusStub(), "/tmp/obs.sqlite", object)

    assert ("segmented", True) in events


async def test_stop_optional_ringbuffer_stops_active_ringbuffer(monkeypatch):
    events: list[str] = []

    class RingBufferStub:
        async def stop(self):
            events.append("stopped")

    monkeypatch.setattr("obs.ringbuffer.ringbuffer.get_optional_ringbuffer", lambda: RingBufferStub())

    await _stop_optional_ringbuffer()

    assert events == ["stopped"]


async def test_stop_optional_ringbuffer_ignores_missing_ringbuffer(monkeypatch):
    monkeypatch.setattr("obs.ringbuffer.ringbuffer.get_optional_ringbuffer", lambda: None)

    await _stop_optional_ringbuffer()


async def _async_value(value):
    return value
