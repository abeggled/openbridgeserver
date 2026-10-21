"""Unit tests — Visu device profiles (``obs.visu_devices`` and the system routes)."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from obs.api.v1 import config as config_api
from obs.api.v1.system import get_visu_devices, update_visu_devices
from obs.db.database import Database
from obs.visu_devices import (
    MAX_VISU_DEVICES,
    VISU_DEVICES_KEY,
    VisuDevice,
    VisuDeviceOut,
    VisuDevicesIn,
    dump_visu_devices,
    load_visu_devices,
    parse_visu_devices,
)


def _request(path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": "PUT",
            "path": path,
            "headers": [],
            "client": ("127.0.0.1", 12345),
            "server": ("testserver", 80),
            "scheme": "http",
            "query_string": b"",
        }
    )


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


async def _create_page(db: Database, node_id: str) -> None:
    now = datetime.now(UTC).isoformat()
    await db.execute_and_commit(
        "INSERT INTO visu_nodes (id, parent_id, name, type, node_order, created_at, updated_at) VALUES (?, NULL, ?, 'PAGE', 0, ?, ?)",
        (node_id, f"Page {node_id}", now, now),
    )


# ── Model ─────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("slug", ["a", "handy", "visu-pc-2", "0", "a" * 40])
def test_slug_accepts_url_friendly_values(slug):
    assert VisuDevice(slug=slug, name="X").slug == slug


@pytest.mark.parametrize("slug", ["Handy", "-handy", "handy-", "visu pc", "ä", "a" * 41, ""])
def test_slug_rejects_other_values(slug):
    with pytest.raises(ValueError):
        VisuDevice(slug=slug, name="X")


def test_name_is_trimmed_and_must_not_be_blank():
    assert VisuDevice(slug="a", name="  Flur  ").name == "Flur"
    with pytest.raises(ValueError):
        VisuDevice(slug="a", name="   ")


def test_empty_start_node_becomes_none_and_defaults_apply():
    device = VisuDevice(slug="a", name="A", start_node_id="")
    assert device.start_node_id is None
    assert device.return_after_s == 0
    assert VisuDevice(slug="a", name="A", start_node_id="node-1").start_node_id == "node-1"


@pytest.mark.parametrize("seconds", [-1, 86_401])
def test_return_after_is_bounded(seconds):
    with pytest.raises(ValueError):
        VisuDevice(slug="a", name="A", return_after_s=seconds)


def test_slugs_must_be_unique_and_count_is_limited():
    with pytest.raises(ValueError, match="duplicate slug: a"):
        VisuDevicesIn(devices=[{"slug": "a", "name": "A"}, {"slug": "a", "name": "B"}])
    with pytest.raises(ValueError):
        VisuDevicesIn(devices=[{"slug": f"d{i}", "name": "D"} for i in range(MAX_VISU_DEVICES + 1)])
    assert len(VisuDevicesIn(devices=[{"slug": f"d{i}", "name": "D"} for i in range(MAX_VISU_DEVICES)]).devices) == MAX_VISU_DEVICES


# ── Stored value ──────────────────────────────────────────────────────────────


def test_dump_and_parse_round_trip():
    devices = [VisuDevice(slug="panel", name="Wandpanel Küche", start_node_id="n1", return_after_s=60)]
    raw = dump_visu_devices(devices)
    assert "Küche" in raw  # stored readable, not \\u-escaped
    assert parse_visu_devices(raw) == devices


@pytest.mark.parametrize("raw", ["not json", '{"slug": "a"}', '[{"slug": "A", "name": "x"}]'])
def test_parse_rejects_invalid_values(raw):
    with pytest.raises(ValueError):
        parse_visu_devices(raw)


def test_load_is_lenient(caplog):
    assert load_visu_devices(None) == []
    assert load_visu_devices("") == []
    with caplog.at_level(logging.WARNING):
        assert load_visu_devices("broken") == []
    assert VISU_DEVICES_KEY in caplog.text
    assert load_visu_devices('[{"slug": "a", "name": "A"}]') == [VisuDevice(slug="a", name="A")]


# ── Routes ────────────────────────────────────────────────────────────────────


async def test_get_returns_empty_list_without_setting(db):
    assert await get_visu_devices(db=db) == []


async def test_get_ignores_a_broken_stored_value(db):
    await db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES (?, ?)", (VISU_DEVICES_KEY, "broken"))
    assert await get_visu_devices(db=db) == []


async def test_put_stores_profiles_and_writes_audit(db):
    await _create_page(db, "start-1")
    body = VisuDevicesIn(
        devices=[
            {"slug": "panel", "name": "Panel", "start_node_id": "start-1", "return_after_s": 90},
            {"slug": "handy", "name": "Handy"},
        ]
    )

    result = await update_visu_devices(body=body, request=_request("/api/v1/system/visu-devices"), db=db, _admin="admin")

    assert result == body.devices
    assert await get_visu_devices(db=db) == [VisuDeviceOut(**d.model_dump()) for d in body.devices]
    row = await db.fetchone("SELECT value FROM app_settings WHERE key = ?", (VISU_DEVICES_KEY,))
    assert json.loads(row["value"])[0] == {"slug": "panel", "name": "Panel", "start_node_id": "start-1", "return_after_s": 90}
    audit = await db.fetchone("SELECT * FROM audit_log_entries WHERE action = 'system.visu_devices.updated'")
    assert audit is not None
    assert audit["actor"] == "admin"
    assert audit["resource_type"] == "visu_devices"
    assert audit["resource_id"] == "global"


async def test_get_reports_a_deleted_start_page_but_keeps_it(db):
    await _create_page(db, "kept")
    await _create_page(db, "gone")
    body = VisuDevicesIn(
        devices=[
            {"slug": "a", "name": "A", "start_node_id": "kept"},
            {"slug": "b", "name": "B", "start_node_id": "gone"},
            {"slug": "c", "name": "C"},
        ]
    )
    await update_visu_devices(body=body, request=_request("/api/v1/system/visu-devices"), db=db, _admin="admin")
    await db.execute_and_commit("DELETE FROM visu_nodes WHERE id = 'gone'")

    devices = await get_visu_devices(db=db)

    assert [(d.start_node_id, d.start_node_missing) for d in devices] == [("kept", False), ("gone", True), (None, False)]


async def test_put_without_start_pages_skips_the_lookup(db):
    body = VisuDevicesIn(devices=[{"slug": "handy", "name": "Handy", "return_after_s": 30}])
    result = await update_visu_devices(body=body, request=_request("/api/v1/system/visu-devices"), db=db, _admin="admin")
    assert result == body.devices


async def test_put_clears_all_profiles(db):
    await update_visu_devices(
        body=VisuDevicesIn(devices=[{"slug": "a", "name": "A"}]), request=_request("/api/v1/system/visu-devices"), db=db, _admin="admin"
    )
    await update_visu_devices(body=VisuDevicesIn(devices=[]), request=_request("/api/v1/system/visu-devices"), db=db, _admin="admin")
    assert await get_visu_devices(db=db) == []


async def test_put_rejects_unknown_start_pages_and_keeps_the_old_value(db):
    await _create_page(db, "known")
    body = VisuDevicesIn(
        devices=[
            {"slug": "a", "name": "A", "start_node_id": "known"},
            {"slug": "b", "name": "B", "start_node_id": "missing-2"},
            {"slug": "c", "name": "C", "start_node_id": "missing-1"},
        ]
    )

    with pytest.raises(HTTPException) as exc:
        await update_visu_devices(body=body, request=_request("/api/v1/system/visu-devices"), db=db, _admin="admin")

    assert exc.value.status_code == 422
    assert exc.value.detail == "Unknown start page: missing-1, missing-2"
    assert await get_visu_devices(db=db) == []


# ── Config import ─────────────────────────────────────────────────────────────


class _EmptyRegistry:
    def all(self) -> list:
        return []


async def test_config_import_validates_visu_devices_and_passes_other_keys(db, monkeypatch):
    good = json.dumps([{"slug": "panel", "name": "Panel", "start_node_id": None, "return_after_s": 30}])
    body = config_api.ConfigExport(
        obs_version="test",
        exported_at="2026-10-18T00:00:00+00:00",
        datapoints=[],
        bindings=[],
        app_settings=[
            config_api.ExportedAppSetting(key=VISU_DEVICES_KEY, value="[{broken"),
            config_api.ExportedAppSetting(key="history.backend", value="sqlite"),
        ],
    )
    monkeypatch.setattr(config_api, "get_registry", lambda: _EmptyRegistry())
    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
    ):
        rejected = await config_api.import_config(body=body, _user="admin", db=db)
        body.app_settings[0] = config_api.ExportedAppSetting(key=VISU_DEVICES_KEY, value=good)
        accepted = await config_api.import_config(body=body, _user="admin", db=db)

    assert any(e.startswith(f"AppSetting {VISU_DEVICES_KEY}") for e in rejected.errors)
    assert rejected.app_settings_upserted == 1  # the unrelated key still went through
    assert accepted.errors == []
    assert accepted.app_settings_upserted == 2
    assert await get_visu_devices(db=db) == [VisuDeviceOut(slug="panel", name="Panel", return_after_s=30)]
    row = await db.fetchone("SELECT value FROM app_settings WHERE key = 'history.backend'")
    assert row["value"] == "sqlite"
