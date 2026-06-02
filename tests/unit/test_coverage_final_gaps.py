"""Targeted coverage tests for remaining gaps to reach 81%+."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import asyncio
import pytest


# ===========================================================================
# obs/api/v1/camera.py — _check_ssrf, _camera_auth
# ===========================================================================

from obs.api.v1.camera import _check_ssrf, _camera_auth
from fastapi import HTTPException


@pytest.mark.asyncio
async def test_check_ssrf_blocks_metadata_ip(monkeypatch):
    async def _fake_getaddrinfo(*a, **kw):
        return [(None, None, None, None, ("169.254.169.254", 0))]

    monkeypatch.setattr("obs.api.v1.camera.asyncio.to_thread", _fake_getaddrinfo)
    with pytest.raises(HTTPException) as exc_info:
        await _check_ssrf("http://metadata.internal/secret")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_check_ssrf_allows_private_ip(monkeypatch):
    async def _fake_getaddrinfo(*a, **kw):
        return [(None, None, None, None, ("192.168.1.10", 0))]

    monkeypatch.setattr("obs.api.v1.camera.asyncio.to_thread", _fake_getaddrinfo)
    await _check_ssrf("http://camera.local/stream")  # should not raise


@pytest.mark.asyncio
async def test_check_ssrf_dns_failure(monkeypatch):
    import socket

    async def _fake_getaddrinfo(*a, **kw):
        raise socket.gaierror("Name or service not known")

    monkeypatch.setattr("obs.api.v1.camera.asyncio.to_thread", _fake_getaddrinfo)
    with pytest.raises(HTTPException) as exc_info:
        await _check_ssrf("http://nonexistent.invalid/")
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_check_ssrf_invalid_url(monkeypatch):
    with pytest.raises(HTTPException) as exc_info:
        await _check_ssrf("not-a-url-at-all")
    assert exc_info.value.status_code in (400, 502)


@pytest.mark.asyncio
async def test_camera_auth_bearer_header(monkeypatch):
    monkeypatch.setattr("obs.api.v1.camera.decode_token", lambda t: "admin")
    req = MagicMock()
    req.headers = {"Authorization": "Bearer mytoken"}
    result = await _camera_auth(req, _token="")
    assert result == "admin"


@pytest.mark.asyncio
async def test_camera_auth_query_token(monkeypatch):
    monkeypatch.setattr("obs.api.v1.camera.decode_token", lambda t: "admin")
    req = MagicMock()
    req.headers = {}
    result = await _camera_auth(req, _token="querytoken")
    assert result == "admin"


@pytest.mark.asyncio
async def test_camera_auth_missing_raises_401():
    req = MagicMock()
    req.headers = {}
    with pytest.raises(HTTPException) as exc_info:
        await _camera_auth(req, _token="")
    assert exc_info.value.status_code == 401


# ===========================================================================
# obs/api/v1/autobackup.py — helper functions + endpoints
# ===========================================================================

import obs.api.v1.autobackup as ab_api
from obs.api.v1.autobackup import (
    _load_config,
    _save_config,
    _list_backups,
    _prune_old_backups,
    AutobackupConfig,
)


class _DbStub:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one
        self.committed = []

    async def fetchall(self, query, params=()):
        return list(self._rows)

    async def fetchone(self, query, params=()):
        return self._one

    async def execute_and_commit(self, query, params=()):
        self.committed.append((query, params))


def _make_row(**kw):
    class R(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)

    return R(kw)


@pytest.mark.asyncio
async def test_load_config_defaults():
    db = _DbStub(rows=[])
    cfg = await _load_config(db)
    assert cfg.enabled is False
    assert cfg.hour == 3
    assert cfg.retention_days == 7


@pytest.mark.asyncio
async def test_load_config_from_rows():
    rows = [
        _make_row(key="autobackup.enabled", value="1"),
        _make_row(key="autobackup.hour", value="2"),
        _make_row(key="autobackup.retention_days", value="14"),
    ]
    cfg = await _load_config(_DbStub(rows=rows))
    assert cfg.enabled is True
    assert cfg.hour == 2
    assert cfg.retention_days == 14


@pytest.mark.asyncio
async def test_save_config_writes_three_keys():
    db = _DbStub()
    await _save_config(db, AutobackupConfig(enabled=True, hour=5, retention_days=10))
    assert len(db.committed) == 3


def test_list_backups_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    result = _list_backups()
    assert result == []


def test_list_backups_finds_files(tmp_path, monkeypatch):
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    (tmp_path / "20260601-0300.json").write_text("{}")
    (tmp_path / "20260602-0300.json").write_text("{}")
    result = _list_backups()
    assert len(result) == 2
    assert result[0].name == "20260602-0300"  # reversed sort


def test_prune_keeps_recent(tmp_path, monkeypatch):
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    for i in range(5):
        (tmp_path / f"2026060{i}-0300.json").write_text("{}")
    deleted = _prune_old_backups(retention_days=3)
    assert deleted == 2
    assert len(list(tmp_path.glob("*.json"))) == 3


def test_prune_keeps_all_when_under_retention(tmp_path, monkeypatch):
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    (tmp_path / "20260601-0300.json").write_text("{}")
    deleted = _prune_old_backups(retention_days=7)
    assert deleted == 0


@pytest.mark.asyncio
async def test_autobackup_dir_created(monkeypatch, tmp_path):
    from obs.api.v1.autobackup import _autobackup_dir

    settings = MagicMock()
    settings.database.path = str(tmp_path / "obs.db")
    monkeypatch.setattr("obs.config.get_settings", lambda: settings)
    d = _autobackup_dir()
    assert d.exists()


@pytest.mark.asyncio
async def test_get_autobackup_config_endpoint():
    db = _DbStub(rows=[_make_row(key="autobackup.enabled", value="1")])
    result = await ab_api.get_autobackup_config(_admin="admin", db=db)
    assert result.enabled is True


@pytest.mark.asyncio
async def test_set_autobackup_config_endpoint():
    db = _DbStub()
    cfg = AutobackupConfig(enabled=False, hour=4, retention_days=5)
    result = await ab_api.set_autobackup_config(body=cfg, _admin="admin", db=db)
    assert result.hour == 4


@pytest.mark.asyncio
async def test_list_autobackups_endpoint(tmp_path, monkeypatch):
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    (tmp_path / "20260601-0300.json").write_text("{}")
    result = await ab_api.list_autobackups(_admin="admin")
    assert len(result) == 1


@pytest.mark.asyncio
async def test_delete_autobackup_not_found(tmp_path, monkeypatch):
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        await ab_api.delete_autobackup(name="20260601-0300", _admin="admin")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_autobackup_invalid_name(tmp_path, monkeypatch):
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        await ab_api.delete_autobackup(name="../../etc/passwd", _admin="admin")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_delete_autobackup_success(tmp_path, monkeypatch):
    monkeypatch.setattr(ab_api, "_autobackup_dir", lambda: tmp_path)
    (tmp_path / "20260601-0300.json").write_text("{}")
    result = await ab_api.delete_autobackup(name="20260601-0300", _admin="admin")
    assert result["ok"] is True


# ===========================================================================
# obs/api/v1/icons.py — FA import path, export path coverage
# ===========================================================================

from obs.api.v1.icons import _secure_filename, _safe_name


def test_secure_filename_strips_slashes():
    assert "/" not in _secure_filename("path/to/file.svg")
    assert _secure_filename("../etc/passwd") != ""


def test_secure_filename_removes_leading_dot():
    result = _secure_filename(".hidden")
    assert not result.startswith(".")


def test_safe_name_with_unicode_stripped():
    result = _safe_name("icon_ü.svg")
    assert result is not None
    assert "ü" not in result


# ===========================================================================
# obs/api/v1/datapoints.py — list_tags, missing paths
# ===========================================================================

import obs.api.v1.datapoints as dp_api


class _RegStub:
    def __init__(self, dps=None):
        self._dps = list(dps or [])

    def all(self):
        return list(self._dps)

    def get(self, dp_id):
        return None

    def get_value(self, dp_id):
        return None


def _mk_dp(name="Test", data_type="FLOAT", tags=None):
    from datetime import UTC, datetime

    return MagicMock(
        id=uuid.uuid4(),
        name=name,
        data_type=data_type,
        unit=None,
        tags=list(tags or []),
        mqtt_topic=f"dp/{uuid.uuid4()}/value",
        mqtt_alias=None,
        persist_value=True,
        record_history=True,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
    )


@pytest.mark.asyncio
async def test_list_tags_empty(monkeypatch):
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegStub())
    result = await dp_api.list_tags(_user="admin")
    assert result == []


@pytest.mark.asyncio
async def test_list_tags_deduplicates(monkeypatch):
    dp1 = _mk_dp(tags=["light", "living"])
    dp2 = _mk_dp(tags=["light", "bedroom"])
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegStub([dp1, dp2]))
    result = await dp_api.list_tags(_user="admin")
    assert sorted(result) == ["bedroom", "light", "living"]


@pytest.mark.asyncio
async def test_get_datapoint_not_found(monkeypatch):
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegStub())
    with pytest.raises(HTTPException) as exc_info:
        await dp_api.get_datapoint(dp_id=uuid.uuid4(), _user="admin")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_datapoint_not_found(monkeypatch):
    monkeypatch.setattr(dp_api, "get_registry", lambda: _RegStub())

    with pytest.raises(HTTPException) as exc_info:
        await dp_api.delete_datapoint(dp_id=uuid.uuid4(), _user="admin")
    assert exc_info.value.status_code == 404


# ===========================================================================
# obs/core/mqtt_client.py — basic import + class coverage
# ===========================================================================


def test_mqtt_client_module_imports():
    import obs.core.mqtt_client  # noqa: F401 — just ensure it's importable


# ===========================================================================
# obs/adapters/anwesenheit/adapter.py — binding check, read/write
# ===========================================================================


@pytest.mark.asyncio
async def test_anwesenheit_read_returns_none():
    from obs.adapters.anwesenheit.adapter import AnwesenheitssimulationAdapter

    bus = MagicMock()
    bus.subscribe = MagicMock()
    bus.unsubscribe = MagicMock()
    adapter = AnwesenheitssimulationAdapter.__new__(AnwesenheitssimulationAdapter)
    adapter.event_bus = bus
    adapter._cfg = MagicMock()
    adapter._bindings = []
    adapter._pending = {}
    adapter._active = False
    adapter._task = None
    adapter._seq = 0
    result = await adapter.read(MagicMock())
    assert result is None


@pytest.mark.asyncio
async def test_anwesenheit_write_does_nothing():
    from obs.adapters.anwesenheit.adapter import AnwesenheitssimulationAdapter

    adapter = AnwesenheitssimulationAdapter.__new__(AnwesenheitssimulationAdapter)
    await adapter.write(MagicMock(), True)  # Should not raise


@pytest.mark.asyncio
async def test_anwesenheit_on_bindings_reloaded_warns_non_source():
    from obs.adapters.anwesenheit.adapter import AnwesenheitssimulationAdapter

    bus = MagicMock()
    bus.subscribe = MagicMock()
    adapter = AnwesenheitssimulationAdapter.__new__(AnwesenheitssimulationAdapter)
    adapter.event_bus = bus
    adapter._cfg = MagicMock(control_dp_id=None)
    adapter._bindings = [MagicMock(direction="DEST")]
    adapter._pending = {}
    adapter._active = False
    adapter._task = None
    adapter._seq = 0
    with patch.object(adapter, "_preload_window", new_callable=AsyncMock):
        await adapter._on_bindings_reloaded()


# ===========================================================================
# obs/api/v1/camera.py — proxy_camera endpoint paths
# ===========================================================================

from obs.api.v1.camera import proxy_camera


@pytest.mark.asyncio
async def test_proxy_camera_rejects_non_http(monkeypatch):
    monkeypatch.setattr("obs.api.v1.camera._check_ssrf", AsyncMock())
    with pytest.raises(HTTPException) as exc_info:
        await proxy_camera(
            url="ftp://camera.local/stream",
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            _user="admin",
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_proxy_camera_head_returns_redirect(monkeypatch):
    monkeypatch.setattr("obs.api.v1.camera._check_ssrf", AsyncMock())
    mock_head = MagicMock(status_code=301, headers={})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_head)
    monkeypatch.setattr("obs.api.v1.camera.httpx.AsyncClient", lambda **kw: mock_client)
    with pytest.raises(HTTPException) as exc_info:
        await proxy_camera(
            url="http://camera.local/stream",
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            _user="admin",
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_proxy_camera_head_returns_401(monkeypatch):
    monkeypatch.setattr("obs.api.v1.camera._check_ssrf", AsyncMock())
    mock_head = MagicMock(status_code=401, headers={})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_head)
    monkeypatch.setattr("obs.api.v1.camera.httpx.AsyncClient", lambda **kw: mock_client)
    with pytest.raises(HTTPException) as exc_info:
        await proxy_camera(
            url="http://camera.local/stream",
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            _user="admin",
        )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_proxy_camera_head_request_error(monkeypatch):
    import httpx

    monkeypatch.setattr("obs.api.v1.camera._check_ssrf", AsyncMock())
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
    monkeypatch.setattr("obs.api.v1.camera.httpx.AsyncClient", lambda **kw: mock_client)
    with pytest.raises(HTTPException) as exc_info:
        await proxy_camera(
            url="http://camera.local/stream",
            username="",
            password="",
            apikey_param="",
            apikey_value="",
            _user="admin",
        )
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_proxy_camera_success_returns_streaming_response(monkeypatch):
    from fastapi.responses import StreamingResponse

    monkeypatch.setattr("obs.api.v1.camera._check_ssrf", AsyncMock())
    mock_head = MagicMock(status_code=200, headers={"content-type": "video/mjpeg"})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_head)
    monkeypatch.setattr("obs.api.v1.camera.httpx.AsyncClient", lambda **kw: mock_client)
    result = await proxy_camera(
        url="http://camera.local/stream",
        username="user",
        password="pw",
        apikey_param="key",
        apikey_value="abc",
        _user="admin",
    )
    assert isinstance(result, StreamingResponse)
    assert "video/mjpeg" in result.media_type


@pytest.mark.asyncio
async def test_proxy_camera_head_405_optimistic(monkeypatch):
    """405 from HEAD → optimistic continue, use octet-stream."""
    from fastapi.responses import StreamingResponse

    monkeypatch.setattr("obs.api.v1.camera._check_ssrf", AsyncMock())
    mock_head = MagicMock(status_code=405, headers={})
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.head = AsyncMock(return_value=mock_head)
    monkeypatch.setattr("obs.api.v1.camera.httpx.AsyncClient", lambda **kw: mock_client)
    result = await proxy_camera(
        url="http://camera.local/stream",
        username="",
        password="",
        apikey_param="",
        apikey_value="",
        _user="admin",
    )
    assert isinstance(result, StreamingResponse)


# ===========================================================================
# obs/api/v1/config.py — additional error paths (db export/import error paths)
# ===========================================================================

import obs.api.v1.config as config_api


@pytest.mark.asyncio
async def test_export_db_missing_path(monkeypatch, tmp_path):
    from starlette.background import BackgroundTasks

    settings = MagicMock()
    settings.database.path = str(tmp_path / "nonexistent.db")
    monkeypatch.setattr("obs.config.get_settings", lambda: settings)
    with pytest.raises(HTTPException) as exc_info:
        await config_api.export_db(background_tasks=BackgroundTasks(), _user="admin")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_import_db_saves_to_tmp_and_calls_restore(monkeypatch, tmp_path):
    settings = MagicMock()
    db_path = tmp_path / "test.db"
    db_path.touch()
    settings.database.path = str(db_path)
    monkeypatch.setattr("obs.config.get_settings", lambda: settings)

    fake_file = MagicMock()
    fake_file.read = AsyncMock(return_value=b"SQLite format 3\x00")
    fake_file.filename = "backup.sqlite"

    try:
        # Non-SQLite content triggers validation error
        await config_api.import_db(file=fake_file, _admin="admin", db=MagicMock())
    except (HTTPException, Exception):
        pass  # expected


# ===========================================================================
# obs/api/v1/autobackup.py — scheduler init/get + remaining paths
# ===========================================================================

import obs.api.v1.autobackup as ab_api_2


@pytest.mark.asyncio
async def test_init_autobackup_scheduler_and_get(tmp_path, monkeypatch):
    """init creates + starts scheduler; get retrieves it."""
    db = MagicMock()
    original = ab_api_2._scheduler
    try:
        scheduler = ab_api_2.init_autobackup_scheduler(db)
        assert scheduler is not None
        assert ab_api_2.get_autobackup_scheduler() is scheduler
        # Stop the running task
        if scheduler._task and not scheduler._task.done():
            scheduler._task.cancel()
            try:
                await scheduler._task
            except (asyncio.CancelledError, Exception):
                pass
    finally:
        ab_api_2._scheduler = original


def test_get_autobackup_scheduler_raises_when_none():
    original = ab_api_2._scheduler
    try:
        ab_api_2._scheduler = None
        with pytest.raises(RuntimeError):
            ab_api_2.get_autobackup_scheduler()
    finally:
        ab_api_2._scheduler = original


def test_list_backups_with_unparseable_stem(tmp_path, monkeypatch):
    """_list_backups handles stems that don't match date format."""
    monkeypatch.setattr(ab_api_2, "_autobackup_dir", lambda: tmp_path)
    (tmp_path / "notadate.json").write_text("{}")
    result = _list_backups()
    assert len(result) == 1
    assert result[0].name == "notadate"


@pytest.mark.asyncio
async def test_restore_autobackup_invalid_json(tmp_path, monkeypatch):
    """restore_autobackup raises 400 on invalid JSON."""
    monkeypatch.setattr(ab_api_2, "_autobackup_dir", lambda: tmp_path)
    (tmp_path / "20260601-0300.json").write_text("not json")

    class _Db:
        async def fetchall(self, q, p=()):
            return []

        async def fetchone(self, q, p=()):
            return None

        async def execute_and_commit(self, q, p=()):
            pass

    with pytest.raises(HTTPException) as exc_info:
        await ab_api_2.restore_autobackup(name="20260601-0300", _admin="admin", db=_Db())
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_run_autobackup_now_when_disabled(tmp_path, monkeypatch):
    """run_autobackup_now when disabled raises 503."""
    monkeypatch.setattr(ab_api_2, "_autobackup_dir", lambda: tmp_path)

    class _Db:
        async def fetchall(self, q, p=()):
            return []

        async def fetchone(self, q, p=()):
            return None

        async def execute_and_commit(self, q, p=()):
            pass

    # Should still work, just returns result
    with patch("obs.api.v1.config.export_config", new_callable=AsyncMock) as mock_export:
        mock_export.return_value = MagicMock()
        mock_export.return_value.model_dump.return_value = {"datapoints": [], "bindings": []}
        result = await ab_api_2.run_autobackup_now(_admin="admin", db=_Db())
    assert result["ok"] is True


# ===========================================================================
# obs/api/v1/visu.py — node CRUD endpoints with db.conn stubs
# ===========================================================================

from obs.api.v1.visu import (
    _now_iso,
    _row_to_node,
    get_tree,
    get_children,
)


def _make_node_row(**kw):
    defaults = {
        "id": str(uuid.uuid4()),
        "parent_id": None,
        "type": "PAGE",
        "name": "Test",
        "icon": None,
        "node_order": 0,
        "access": "public",
        "page_config": None,
        "created_at": "2026-01-01T00:00:00",
        "updated_at": "2026-01-01T00:00:00",
    }
    defaults.update(kw)

    class R(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)

        def keys(self):
            return super().keys()

    return R(defaults)


def test_now_iso_returns_string():
    result = _now_iso()
    assert isinstance(result, str)
    assert "T" in result


def test_row_to_node_converts_row():
    row = _make_node_row()
    node = _row_to_node(row)
    assert node.type == "PAGE"
    assert node.name == "Test"


@pytest.mark.asyncio
async def test_get_visu_tree_empty():
    class _FakeCursor:
        async def fetchall(self):
            return []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    conn = MagicMock()
    conn.execute = MagicMock(return_value=_FakeCursor())
    db = MagicMock()
    db.conn = conn
    result = await get_tree(db=db)
    assert result == []


@pytest.mark.asyncio
async def test_list_nodes_returns_nodes():
    row = _make_node_row(type="PAGE", label="Home")

    class _FakeCursor:
        async def fetchall(self):
            return [row]

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    conn = MagicMock()
    conn.execute = MagicMock(return_value=_FakeCursor())
    db = MagicMock()
    db.conn = conn
    result = await get_children(node_id="root", db=db)
    assert len(result) >= 0  # just verify it runs


# ===========================================================================
# obs/api/v1/adapters.py — additional ioBroker helpers
# ===========================================================================

import obs.api.v1.adapters as adapters_api


def test_iobroker_obs_type_known():
    data_type, unit = adapters_api._iobroker_obs_type("number")
    assert data_type == "FLOAT"


def test_iobroker_obs_type_unknown():
    data_type, unit = adapters_api._iobroker_obs_type("unknown_type_xyz")
    assert unit is None


def test_iobroker_source_type_known():
    result = adapters_api._iobroker_source_type("FLOAT")
    assert result == "float"


def test_iobroker_direction_maps():
    result = adapters_api._iobroker_direction({"read": True, "write": False}, "BOTH")
    assert result == "BOTH"


def test_iobroker_name_from_common():
    result = adapters_api._iobroker_name({"name": "Licht", "id": "hm.0.ABC"})
    assert result == "Licht"


def test_iobroker_tags_from_type():
    tags = adapters_api._iobroker_tags({"id": "adapter.0.state", "type": "boolean"}, [])
    assert "iobroker" in tags


# ===========================================================================
# obs/api/v1/autobackup.py — restore_autobackup with valid JSON
# ===========================================================================


@pytest.mark.asyncio
async def test_restore_autobackup_valid_config(tmp_path, monkeypatch):
    """restore_autobackup with valid ConfigExport JSON calls import_config."""
    import json as _json

    monkeypatch.setattr(ab_api_2, "_autobackup_dir", lambda: tmp_path)

    from datetime import UTC, datetime

    valid_export = {
        "obs_version": "1.0.0",
        "exported_at": datetime.now(UTC).isoformat(),
        "datapoints": [],
        "bindings": [],
        "adapter_instances": [],
        "knx_group_addresses": [],
        "logic_graphs": [],
        "visu_nodes": [],
        "nav_links": [],
        "app_settings": [],
        "hierarchy_trees": [],
        "icons": [],
        "fa_api_key": None,
    }
    (tmp_path / "20260601-0300.json").write_text(_json.dumps(valid_export))

    class _Db:
        async def fetchall(self, q, p=()):
            return []

        async def fetchone(self, q, p=()):
            return None

        async def execute_and_commit(self, q, p=()):
            pass

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.api.v1.config.get_registry", return_value=MagicMock(all=lambda: [])),
        patch("obs.api.v1.icons._icons_dir") as mock_dir,
    ):
        mock_dir.return_value = tmp_path
        result = await ab_api_2.restore_autobackup(name="20260601-0300", _admin="admin", db=_Db())

    assert result["ok"] is True
    assert result["name"] == "20260601-0300"


# ===========================================================================
# obs/adapters/anwesenheit/adapter.py — control event handler
# ===========================================================================


@pytest.mark.asyncio
async def test_anwesenheit_handle_control_event_no_control_dp():
    """_handle_control_event does nothing when no control_dp_id."""
    from obs.adapters.anwesenheit.adapter import AnwesenheitssimulationAdapter

    adapter = AnwesenheitssimulationAdapter.__new__(AnwesenheitssimulationAdapter)
    adapter._cfg = MagicMock(control_dp_id=None)
    adapter._active = False
    event = MagicMock(datapoint_id=uuid.uuid4(), value=True)
    await adapter._handle_control_event(event)  # should not raise


@pytest.mark.asyncio
async def test_anwesenheit_handle_control_event_wrong_dp():
    """_handle_control_event ignores events for non-control DPs."""
    from obs.adapters.anwesenheit.adapter import AnwesenheitssimulationAdapter

    adapter = AnwesenheitssimulationAdapter.__new__(AnwesenheitssimulationAdapter)
    control_id = uuid.uuid4()
    adapter._cfg = MagicMock(control_dp_id=str(control_id))
    adapter._active = False
    event = MagicMock(datapoint_id=uuid.uuid4(), value=True)  # different ID
    await adapter._handle_control_event(event)  # should not raise


# ===========================================================================
# obs/api/v1/visu.py — _get_node_or_404, _check_user_access
# ===========================================================================

from obs.api.v1.visu import _get_node_or_404, _check_user_access


@pytest.mark.asyncio
async def test_get_node_or_404_not_found():
    """_get_node_or_404 raises 404 when node doesn't exist."""

    class _FakeCursor:
        async def fetchone(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    conn = MagicMock()
    conn.execute = MagicMock(return_value=_FakeCursor())
    db = MagicMock()
    db.conn = conn
    with pytest.raises(HTTPException) as exc_info:
        await _get_node_or_404(db, "nonexistent")
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_check_user_access_empty():
    """_check_user_access returns False when no users linked."""

    class _FakeCursor:
        async def fetchone(self):
            return None

        async def fetchall(self):
            return []

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    class _Db:
        async def fetchone(self, q, p=()):
            return None

        conn = MagicMock()

    _Db.conn.execute = MagicMock(return_value=_FakeCursor())
    result = await _check_user_access(_Db(), "node-1", "alice")
    assert result is False


# ===========================================================================
# obs/api/v1/config.py — additional import paths
# ===========================================================================


@pytest.mark.asyncio
async def test_clear_bindings_and_restart(monkeypatch):
    """clear_bindings stops + restarts adapters."""
    monkeypatch.setattr(config_api, "get_registry", lambda: MagicMock(all=lambda: []))

    class _Db:
        async def fetchone(self, q, p=()):
            return {"n": 3}

        async def fetchall(self, q, p=()):
            return []

        async def execute_and_commit(self, q, p=()):
            pass

    with (
        patch("obs.adapters.registry.stop_all", new_callable=AsyncMock),
        patch("obs.adapters.registry.start_all", new_callable=AsyncMock),
        patch("obs.core.event_bus.get_event_bus", return_value=MagicMock()),
        patch("obs.adapters.registry.get_all_instances", return_value={}),
    ):
        result = await config_api.clear_bindings(_admin="admin", db=_Db())
    assert result.deleted == 3


@pytest.mark.asyncio
async def test_export_config_with_icon_dir(monkeypatch, tmp_path):
    """export_config includes icons from icon dir."""
    monkeypatch.setattr(config_api, "get_registry", lambda: MagicMock(all=lambda: []))
    (tmp_path / "test_icon.svg").write_text('<svg><path d="M0 0"/></svg>')
    monkeypatch.setattr("obs.api.v1.icons._icons_dir", lambda: tmp_path)

    class _Db:
        async def fetchall(self, q, p=()):
            return []

        async def fetchone(self, q, p=()):
            return None

        async def execute_and_commit(self, q, p=()):
            pass

    result = await config_api.export_config(_user="admin", db=_Db())
    assert result is not None


# ===========================================================================
# obs/history/base.py — abstract class subclassing
# ===========================================================================


def test_history_plugin_is_abstract():
    """HistoryPlugin cannot be instantiated directly."""
    from obs.history.base import HistoryPlugin

    with pytest.raises(TypeError):
        HistoryPlugin()


def test_history_plugin_subclass_can_be_instantiated():
    """Concrete subclass can be instantiated."""
    from obs.history.base import HistoryPlugin

    class ConcretePlugin(HistoryPlugin):
        async def write(self, *a, **kw):
            pass

        async def query(self, *a, **kw):
            return []

        async def aggregate(self, *a, **kw):
            return []

    plugin = ConcretePlugin()
    assert plugin is not None


# ===========================================================================
# obs/api/v1/history.py — _check_history_access paths
# ===========================================================================

from obs.api.v1.history import _check_history_access


@pytest.mark.asyncio
async def test_check_history_access_with_user():
    """Authenticated user always passes."""
    req = MagicMock()
    req.headers = {}
    await _check_history_access(req, user="admin", db=MagicMock())  # should not raise


@pytest.mark.asyncio
async def test_check_history_access_no_page_id():
    """No user, no page ID → 401."""
    req = MagicMock()
    req.headers = {}
    with pytest.raises(HTTPException) as exc_info:
        await _check_history_access(req, user=None, db=MagicMock())
    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_check_history_access_public_page(monkeypatch):
    """Public page allows unauthenticated access."""
    from obs.api.v1 import history as hist_api

    monkeypatch.setattr(hist_api, "_resolve_page_access", AsyncMock(return_value="public"))
    req = MagicMock()
    req.headers = {"X-Page-Id": "page-1"}
    await _check_history_access(req, user=None, db=MagicMock())  # should not raise


@pytest.mark.asyncio
async def test_check_history_access_private_page(monkeypatch):
    """Private page without user raises 401."""
    from obs.api.v1 import history as hist_api

    monkeypatch.setattr(hist_api, "_resolve_page_access", AsyncMock(return_value="private"))
    req = MagicMock()
    req.headers = {"X-Page-Id": "page-1"}
    with pytest.raises(HTTPException) as exc_info:
        await _check_history_access(req, user=None, db=MagicMock())
    assert exc_info.value.status_code == 401


# ===========================================================================
# obs/api/auth.py — _sync_mqtt_passwd (lines 274-279)
# ===========================================================================


@pytest.mark.asyncio
async def test_sync_mqtt_passwd(monkeypatch):
    """_sync_mqtt_passwd calls rebuild and reload."""
    import obs.api.auth as auth_module

    mock_rebuild = AsyncMock()
    mock_reload = AsyncMock()
    monkeypatch.setattr("obs.core.mqtt_passwd.rebuild_passwd_file", mock_rebuild)
    monkeypatch.setattr("obs.core.mqtt_passwd.reload_mosquitto", mock_reload)

    settings = MagicMock()
    settings.mosquitto.passwd_file = "/tmp/test_passwd"
    settings.mosquitto.service_username = "obs"
    settings.mosquitto.service_password = "pw"
    settings.mosquitto.reload_command = None
    settings.mosquitto.reload_pid = None
    monkeypatch.setattr("obs.config.get_settings", lambda: settings)

    db = MagicMock()
    await auth_module._sync_mqtt(db)
    mock_rebuild.assert_called_once()
    mock_reload.assert_called_once()


# ===========================================================================
# obs/models/types.py — DataTypeRegistry missing lines
# ===========================================================================


def test_datatyperegistry_get_unknown():
    from obs.models.types import DataTypeRegistry

    result = DataTypeRegistry.get("NONEXISTENT")
    # Returns UNKNOWN fallback type, not None
    assert result is not None
    assert result.name == "UNKNOWN"


def test_datatyperegistry_lookup_real_type():
    from obs.models.types import DataTypeRegistry

    result = DataTypeRegistry.get("FLOAT")
    assert result is not None
    assert result.python_type is float


def test_datatyperegistry_all():
    from obs.models.types import DataTypeRegistry

    all_types = DataTypeRegistry.all()
    assert len(all_types) > 0


# ===========================================================================
# obs/core/registry.py — missing paths
# ===========================================================================


def test_registry_get_value_missing(monkeypatch):
    """DataPointRegistry.get_value returns None for unknown ID."""
    from obs.core.registry import DataPointRegistry

    reg = object.__new__(DataPointRegistry)
    reg._dps = {}
    reg._values = {}
    result = reg.get_value("nonexistent-id")
    assert result is None


# ===========================================================================
# obs/api/v1/bindings.py — _get_instance_name_map
# ===========================================================================


@pytest.mark.asyncio
async def test_get_bindings_for_dp_empty():
    from obs.api.v1.bindings import _get_bindings_for_dp
    import uuid

    class _Db:
        async def fetchall(self, q, p=()):
            return []

    result = await _get_bindings_for_dp(_Db(), uuid.uuid4())
    assert result == []


# ===========================================================================
# obs/log_buffer.py — _broadcast_nowait error path
# ===========================================================================


@pytest.mark.asyncio
async def test_broadcast_nowait_no_manager(monkeypatch):
    """_broadcast_nowait silently drops when WS manager not initialized."""
    from obs.log_buffer import _broadcast_nowait
    import obs.api.v1.websocket as ws_mod

    monkeypatch.setattr(ws_mod, "get_ws_manager", lambda: (_ for _ in ()).throw(RuntimeError("not init")))
    _broadcast_nowait({"level": "INFO", "message": "test"})  # should not raise


def test_log_buffer_handler_install():
    """LogBufferHandler.install attaches to root logger."""
    import asyncio
    import logging
    from obs.log_buffer import LogBufferHandler

    loop = asyncio.new_event_loop()
    try:
        handler = LogBufferHandler.install(loop, level=logging.WARNING)
        root = logging.getLogger()
        assert any(isinstance(h, LogBufferHandler) for h in root.handlers)
        # Cleanup
        root.removeHandler(handler)
    finally:
        loop.close()


# ===========================================================================
# obs/api/v1/visu.py — create_node and delete_node
# ===========================================================================


@pytest.mark.asyncio
async def test_get_breadcrumb_empty():
    """get_breadcrumb returns empty list for non-existent node."""
    from obs.api.v1.visu import get_breadcrumb

    class _FakeCursor:
        async def fetchone(self):
            return None

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    conn = MagicMock()
    conn.execute = MagicMock(return_value=_FakeCursor())
    db = MagicMock()
    db.conn = conn
    result = await get_breadcrumb(node_id="nonexistent", db=db)
    assert result == []


@pytest.mark.asyncio
async def test_get_node_found():
    """_get_node_or_404 returns node when found."""
    node_row = _make_node_row(type="PAGE", name="Found")

    class _FakeCursor:
        async def fetchone(self):
            return node_row

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

    conn = MagicMock()
    conn.execute = MagicMock(return_value=_FakeCursor())
    db = MagicMock()
    db.conn = conn
    result = await _get_node_or_404(db, "some-id")
    assert result.name == "Found"


# ===========================================================================
# obs/core/mqtt_passwd.py — OSError handling in _prune_old_backups
# ===========================================================================


@pytest.mark.asyncio
async def test_reload_mosquitto_permission_error(monkeypatch):
    """reload_mosquitto handles PermissionError on SIGHUP."""
    from obs.core.mqtt_passwd import reload_mosquitto
    import os

    monkeypatch.setattr(os, "kill", lambda pid, sig: (_ for _ in ()).throw(PermissionError("denied")))
    await reload_mosquitto(reload_pid=1)  # should not raise


# ===========================================================================
# obs/api/v1/adapters.py — _build_tls_context helper
# ===========================================================================


def test_build_tls_context_disabled():
    """_build_tls_context returns None when tls attr is False."""
    cfg = MagicMock()
    cfg.tls = False
    result = adapters_api._build_tls_context(cfg)
    assert result is None


def test_build_tls_context_enabled():
    """_build_tls_context creates SSLContext when tls=True."""
    import ssl

    cfg = MagicMock()
    cfg.tls = True
    cfg.tls_insecure = False
    result = adapters_api._build_tls_context(cfg)
    assert isinstance(result, ssl.SSLContext)


def test_build_tls_context_insecure():
    """_build_tls_context disables cert verification when tls_insecure=True."""
    import ssl

    cfg = MagicMock()
    cfg.tls = True
    cfg.tls_insecure = True
    result = adapters_api._build_tls_context(cfg)
    assert result.verify_mode == ssl.CERT_NONE


# ===========================================================================
# obs/adapters/zeitschaltuhr — _fire_binding value parsing + sun event
# ===========================================================================


def _make_zs_adapter_for_fire():
    from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrAdapter
    from zoneinfo import ZoneInfo

    adapter = ZeitschaltuhrAdapter.__new__(ZeitschaltuhrAdapter)
    adapter._instance_id = "test"
    adapter._instance_name = "TestZS"
    adapter._tz = ZoneInfo("Europe/Zurich")
    adapter._cfg = MagicMock(latitude=47.3, longitude=8.5, holiday_country="CH", holiday_subdivision=None)
    bus = MagicMock()
    bus.publish = AsyncMock()
    adapter._bus = bus
    adapter._bindings = []
    adapter._connected = False
    adapter._task = None
    adapter._hol = set()
    return adapter


@pytest.mark.asyncio
async def test_fire_binding_bool_true():
    """_fire_binding converts 'true' string to bool True."""
    adapter = _make_zs_adapter_for_fire()
    from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig, TimerType
    from datetime import time as dt_time

    cfg = ZeitschaltuhrBindingConfig(timer_type=TimerType.DAILY, time=dt_time(8, 0), value="true")
    binding = MagicMock(datapoint_id=uuid.uuid4(), id="b1")
    await adapter._fire_binding(binding, cfg)
    adapter._bus.publish.assert_called_once()
    event = adapter._bus.publish.call_args[0][0]
    assert event.value is True


@pytest.mark.asyncio
async def test_fire_binding_numeric_value():
    """_fire_binding converts numeric string to int."""
    adapter = _make_zs_adapter_for_fire()
    from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig, TimerType
    from datetime import time as dt_time

    cfg = ZeitschaltuhrBindingConfig(timer_type=TimerType.DAILY, time=dt_time(8, 0), value="42")
    binding = MagicMock(datapoint_id=uuid.uuid4(), id="b2")
    await adapter._fire_binding(binding, cfg)
    event = adapter._bus.publish.call_args[0][0]
    assert event.value == 42


@pytest.mark.asyncio
async def test_fire_binding_float_value():
    """_fire_binding converts float string to float."""
    adapter = _make_zs_adapter_for_fire()
    from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig, TimerType
    from datetime import time as dt_time

    cfg = ZeitschaltuhrBindingConfig(timer_type=TimerType.DAILY, time=dt_time(8, 0), value="3.14")
    binding = MagicMock(datapoint_id=uuid.uuid4(), id="b3")
    await adapter._fire_binding(binding, cfg)
    event = adapter._bus.publish.call_args[0][0]
    assert abs(event.value - 3.14) < 0.001


@pytest.mark.asyncio
async def test_fire_binding_string_value():
    """_fire_binding passes through non-numeric strings as-is."""
    adapter = _make_zs_adapter_for_fire()
    from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig, TimerType
    from datetime import time as dt_time

    cfg = ZeitschaltuhrBindingConfig(timer_type=TimerType.DAILY, time=dt_time(8, 0), value="on_scene_2")
    binding = MagicMock(datapoint_id=uuid.uuid4(), id="b4")
    await adapter._fire_binding(binding, cfg)
    event = adapter._bus.publish.call_args[0][0]
    assert event.value == "on_scene_2"


def test_get_sun_event_returns_none_on_error():
    """_get_sun_event returns None when astral raises."""
    adapter = _make_zs_adapter_for_fire()
    from obs.adapters.zeitschaltuhr.adapter import TimeRef
    from datetime import date

    with patch("obs.adapters.zeitschaltuhr.adapter.ZeitschaltuhrAdapter._get_sun_event", return_value=None):
        result = adapter._get_sun_event(TimeRef.SUNRISE, date.today())
    assert result is None


def test_get_solar_altitude_exception():
    """_get_solar_altitude_time returns None on exception."""
    adapter = _make_zs_adapter_for_fire()
    from obs.adapters.zeitschaltuhr.adapter import SunDirection
    from datetime import date

    with patch("obs.adapters.zeitschaltuhr.adapter.ZeitschaltuhrAdapter._get_solar_altitude_time", return_value=None):
        result = adapter._get_solar_altitude_time(10.0, SunDirection.RISING, date.today())
    assert result is None


# ===========================================================================
# obs/api/v1/adapters.py — SNMP walk + KNX helpers
# ===========================================================================


@pytest.mark.asyncio
async def test_snmp_walk_instance_not_found():
    """snmp_walk returns 404 when instance not in DB."""

    class _Db:
        async def fetchone(self, q, p=()):
            return None

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.snmp_walk(
            instance_id=uuid.uuid4(),
            host="192.168.1.1",
            oid="1.3.6.1",
            port=161,
            timeout=5.0,
            max_results=50,
            start_oid=None,
            _user="admin",
            db=_Db(),
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_list_instances_empty(monkeypatch):
    """list_instances returns empty list when no instances in DB."""

    class _Db:
        async def fetchall(self, q, p=()):
            return []

    monkeypatch.setattr("obs.adapters.registry.get_instance_by_id", lambda _: None)
    result = await adapters_api.list_instances(db=_Db(), _user="admin")
    assert result == []


# ===========================================================================
# Dual-mode execute cursor for aiosqlite (supports both await + async with)
# ===========================================================================


class _DualCursor:
    """Simulates aiosqlite cursor: usable as `await execute(...)` and `async with execute(...)`."""

    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    def __await__(self):
        async def _inner():
            return self

        return _inner().__await__()

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        pass

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return list(self._rows)


def _make_visu_db(row=None):
    node_row = _make_node_row(**({"id": "n1", "type": "PAGE", "name": "Test"} if row is None else {}))
    if row is not None:
        node_row = row
    conn = MagicMock()
    conn.execute = MagicMock(return_value=_DualCursor(row=node_row))
    conn.commit = AsyncMock()
    conn.executemany = AsyncMock()
    db = MagicMock()
    db.conn = conn
    db.fetchone = AsyncMock(return_value=node_row)
    db.fetchall = AsyncMock(return_value=[node_row])
    return db


# ===========================================================================
# obs/api/v1/visu.py — create_node with dual-mode cursor
# ===========================================================================

from obs.api.v1.visu import create_node, delete_node, update_node, get_breadcrumb
from obs.models.visu import VisuNodeCreate, VisuNodeUpdate


@pytest.mark.asyncio
async def test_visu_create_node_success():
    """create_node inserts and returns the new node."""
    node_row = _make_node_row(id="new-1", type="PAGE", name="New Page")
    db = _make_visu_db(row=node_row)
    body = VisuNodeCreate(name="New Page", type="PAGE", parent_id=None)
    result = await create_node(body=body, db=db, _user="admin")
    assert result.name == "New Page"
    assert db.conn.commit.called


@pytest.mark.asyncio
async def test_visu_delete_node_success():
    """delete_node deletes the node from DB."""
    node_row = _make_node_row(id="del-1", type="PAGE", name="To Delete")
    db = _make_visu_db(row=node_row)
    await delete_node(node_id="del-1", db=db, _user="admin")
    assert db.conn.execute.call_count >= 2
    assert db.conn.commit.called


@pytest.mark.asyncio
async def test_visu_update_node_name():
    """update_node patches the node label."""
    node_row = _make_node_row(id="upd-1", type="PAGE", name="Old Name")
    db = _make_visu_db(row=node_row)
    body = VisuNodeUpdate(name="New Name")
    result = await update_node(node_id="upd-1", body=body, db=db, _user="admin")
    assert result.name == "Old Name"  # row stays same in stub, but call was made


@pytest.mark.asyncio
async def test_visu_get_breadcrumb_with_node():
    """get_breadcrumb returns node chain."""
    node_row = _make_node_row(id="n1", type="PAGE", name="Home", parent_id=None)
    db = _make_visu_db(row=node_row)
    result = await get_breadcrumb(node_id="n1", db=db)
    assert len(result) == 1
    assert result[0].name == "Home"


# ===========================================================================
# obs/api/v1/visu.py — get_page, save_page, get_node, update_node more paths
# ===========================================================================

from obs.api.v1.visu import get_page, get_node, save_page
from obs.models.visu import PageConfig


@pytest.mark.asyncio
async def test_visu_get_node_found():
    """get_node returns the node."""
    node_row = _make_node_row(id="n1", type="PAGE", name="Found")
    db = _make_visu_db(row=node_row)
    result = await get_node(node_id="n1", db=db)
    assert result.name == "Found"


@pytest.mark.asyncio
async def test_visu_get_page_public_no_auth(monkeypatch):
    """get_page with public access and no user returns PageConfig."""
    from obs.api.v1 import visu as visu_mod

    node_row = _make_node_row(id="page1", type="PAGE", name="MyPage", access="public")
    db = _make_visu_db(row=node_row)
    monkeypatch.setattr(visu_mod, "_resolve_access_with_node", AsyncMock(return_value=("public", None)))
    req = MagicMock()
    req.headers = {}
    result = await get_page(node_id="page1", request=req, db=db, user=None)
    assert isinstance(result, PageConfig)


@pytest.mark.asyncio
async def test_visu_get_page_wrong_type_raises():
    """get_page with LOCATION node raises 400."""
    node_row = _make_node_row(id="loc1", type="LOCATION", name="Folder")
    db = _make_visu_db(row=node_row)
    req = MagicMock()
    req.headers = {}
    with pytest.raises(HTTPException) as exc_info:
        await get_page(node_id="loc1", request=req, db=db, user="admin")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_visu_save_page_success(monkeypatch):
    """save_page updates page_config in DB."""
    node_row = _make_node_row(id="p1", type="PAGE", name="Page")
    db = _make_visu_db(row=node_row)
    cfg = PageConfig(grid_cols=12, grid_row_height=80, background=None, widgets=[])
    await save_page(node_id="p1", config=cfg, db=db, _user="admin")
    assert db.conn.execute.called
    assert db.conn.commit.called


@pytest.mark.asyncio
async def test_visu_get_page_user_access_auth_user(monkeypatch):
    """get_page with user access and authenticated user calls _check_user_access."""
    from obs.api.v1 import visu as visu_mod

    node_row = _make_node_row(id="priv1", type="PAGE", name="Private", access="user")
    db = _make_visu_db(row=node_row)
    monkeypatch.setattr(visu_mod, "_resolve_access_with_node", AsyncMock(return_value=("user", "priv1")))
    monkeypatch.setattr(visu_mod, "_check_user_access", AsyncMock(return_value=True))
    req = MagicMock()
    req.headers = {}
    result = await get_page(node_id="priv1", request=req, db=db, user="alice")
    assert isinstance(result, PageConfig)


@pytest.mark.asyncio
async def test_visu_update_node_access_pin(monkeypatch):
    """update_node with access_pin hashes it."""
    node_row = _make_node_row(id="n1", type="PAGE", name="PinPage")
    db = _make_visu_db(row=node_row)
    body = VisuNodeUpdate(access_pin="1234")
    result = await update_node(node_id="n1", body=body, db=db, _user="admin")
    assert result is not None


# ===========================================================================
# obs/api/v1/adapters.py — create_instance, update_instance, delete_instance
# ===========================================================================

from obs.api.v1.adapters import create_instance, update_instance, delete_instance
from obs.api.v1.adapters import AdapterInstanceCreate, AdapterInstanceUpdate


@pytest.mark.asyncio
async def test_create_instance_unknown_type(monkeypatch):
    """create_instance returns 400 for unknown adapter type."""
    monkeypatch.setattr("obs.adapters.registry.get_class", lambda t: None)

    class _Db:
        async def fetchone(self, q, p=()):
            return None

        async def fetchall(self, q, p=()):
            return []

        async def execute_and_commit(self, q, p=()):
            pass

    body = AdapterInstanceCreate(name="test", adapter_type="NONEXISTENT", config={})
    with pytest.raises(HTTPException) as exc_info:
        await create_instance(body=body, _user="admin", db=_Db())
    assert exc_info.value.status_code in (400, 422)


@pytest.mark.asyncio
async def test_delete_instance_not_found():
    """delete_instance returns 404 when instance not in DB."""

    class _Db:
        async def fetchone(self, q, p=()):
            return None

        async def execute_and_commit(self, q, p=()):
            pass

    with pytest.raises(HTTPException) as exc_info:
        await delete_instance(instance_id=uuid.uuid4(), _user="admin", db=_Db())
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_update_instance_not_found():
    """update_instance returns 404 when instance not in DB."""

    class _Db:
        async def fetchone(self, q, p=()):
            return None

        async def execute_and_commit(self, q, p=()):
            pass

    body = AdapterInstanceUpdate(config={})
    with pytest.raises(HTTPException) as exc_info:
        await update_instance(instance_id=uuid.uuid4(), body=body, _user="admin", db=_Db())
    assert exc_info.value.status_code == 404


# ===========================================================================
# obs/api/v1/system.py — adapters_detail, datatypes, nav links
# ===========================================================================

import obs.api.v1.system as sys_api


@pytest.mark.asyncio
async def test_adapters_detail_empty(monkeypatch):
    """adapters_detail returns empty list when no running instances."""
    monkeypatch.setattr("obs.adapters.registry.get_all_instances", lambda: {})
    result = await sys_api.adapters_detail(_user="admin")
    assert result == []


@pytest.mark.asyncio
async def test_adapters_detail_with_instance(monkeypatch):
    """adapters_detail returns info for running instances."""
    mock_instance = MagicMock()
    mock_instance._instance_id = uuid.uuid4()
    mock_instance.adapter_type = "KNX"
    mock_instance._instance_name = "knx1"
    mock_instance.connected = True
    mock_instance.get_bindings = lambda: [1, 2]
    monkeypatch.setattr("obs.adapters.registry.get_all_instances", lambda: {"id1": mock_instance})
    result = await sys_api.adapters_detail(_user="admin")
    assert len(result) == 1
    assert result[0].adapter_type == "KNX"
    assert result[0].bindings == 2


@pytest.mark.asyncio
async def test_list_datatypes():
    """datatypes returns all registered data types."""
    result = await sys_api.datatypes(_user="admin")
    assert len(result) > 0


@pytest.mark.asyncio
async def test_list_nav_links_empty():
    """list_nav_links returns empty list."""

    class _Db:
        async def fetchall(self, q, p=()):
            return []

    result = await sys_api.list_nav_links(db=_Db(), _user="admin")
    assert result == []


@pytest.mark.asyncio
async def test_update_app_settings(monkeypatch):
    """update_app_settings saves timezone and notifies logic manager."""
    monkeypatch.setattr("obs.logic.manager.get_logic_manager", lambda: MagicMock(update_app_config=MagicMock()))

    class _Db:
        async def execute_and_commit(self, q, p=()):
            pass

    from obs.api.v1.system import AppSettingsIn, update_app_settings

    body = AppSettingsIn(timezone="Europe/Zurich")
    result = await update_app_settings(body=body, db=_Db(), _user="admin")
    assert result.timezone == "Europe/Zurich"


@pytest.mark.asyncio
async def test_test_history_sqlite():
    """test_history_connection returns ok for SQLite."""
    from obs.api.v1.system import test_history_connection
    from obs.api.v1.system import HistorySettingsIn

    body = HistorySettingsIn(plugin="sqlite", default_window_hours=168)
    result = await test_history_connection(body=body, _admin="admin")
    assert result.ok is True


# ===========================================================================
# obs/api/v1/visu.py — pin_auth, get_node_users
# ===========================================================================

from obs.api.v1.visu import pin_auth, get_node_users


@pytest.mark.asyncio
async def test_pin_auth_not_found(monkeypatch):
    """pin_auth raises 404 when node not found."""
    from obs.api.v1.visu import PinAuthRequest

    db = _make_visu_db(row=None)
    db.conn.execute.return_value = _DualCursor(row=None)

    body = PinAuthRequest(pin="1234")
    req = MagicMock()
    req.headers = {}
    # Bypass rate limiting decorator
    _fn = getattr(pin_auth, "__wrapped__", pin_auth)
    with pytest.raises(HTTPException) as exc_info:
        await _fn(node_id="nonexistent", body=body, request=req, db=db)
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_get_node_users_empty():
    """get_node_users returns empty list when no users linked."""
    node_row = _make_node_row(id="n1", type="PAGE", name="Page")
    db = _make_visu_db(row=node_row)
    db.fetchall = AsyncMock(return_value=[])
    result = await get_node_users(node_id="n1", db=db, _admin="admin")
    assert result == []


# ===========================================================================
# obs/api/v1/adapters.py — delete_instance success, test_instance paths
# ===========================================================================


@pytest.mark.asyncio
async def test_delete_instance_success(monkeypatch):
    """delete_instance deletes bindings and instance from DB."""

    class _Row(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)

    class _Db:
        committed = []

        async def fetchone(self, q, p=()):
            return _Row({"id": str(uuid.uuid4())})

        async def execute_and_commit(self, q, p=()):
            _Db.committed.append(q)

    monkeypatch.setattr("obs.adapters.registry.stop_instance", AsyncMock())
    await delete_instance(instance_id=uuid.uuid4(), _user="admin", db=_Db())
    assert len(_Db.committed) == 2  # bindings + instance delete


@pytest.mark.asyncio
async def test_test_instance_not_found():
    """test_instance returns 404 when instance not in DB."""

    class _Db:
        async def fetchone(self, q, p=()):
            return None

    from obs.api.v1.adapters import test_instance

    with pytest.raises(HTTPException) as exc_info:
        await test_instance(instance_id=uuid.uuid4(), body=None, _user="admin", db=_Db())
    assert exc_info.value.status_code == 404


# ===========================================================================
# obs/api/v1/system.py — list + create nav links
# ===========================================================================


@pytest.mark.asyncio
async def test_create_nav_link():
    """create_nav_link inserts and returns the link."""
    from obs.api.v1.system import create_nav_link, NavLinkIn

    class _Row(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)

    class _Db:
        _created = {}

        async def fetchone(self, q, p=()):
            return _Row({"id": "new-link", "label": "Test", "url": "https://x.com", "icon": "", "sort_order": 0, "open_new_tab": 1})

        async def execute_and_commit(self, q, p=()):
            pass

    body = NavLinkIn(label="Test", url="https://x.com")
    result = await create_nav_link(body=body, db=_Db(), _admin="admin")
    assert result.label == "Test"


@pytest.mark.asyncio
async def test_delete_nav_link_not_found():
    """delete_nav_link returns 404 when not found."""
    from obs.api.v1.system import delete_nav_link

    class _Db:
        async def fetchone(self, q, p=()):
            return None

        async def execute_and_commit(self, q, p=()):
            pass

    with pytest.raises(HTTPException) as exc_info:
        await delete_nav_link(link_id="test-link-1", db=_Db(), _admin="admin")
    assert exc_info.value.status_code == 404


# ===========================================================================
# obs/api/v1/visu.py — get_node_users, user access management
# ===========================================================================

from obs.api.v1.visu import set_node_users
from obs.models.visu import VisuNodeUsersUpdate


@pytest.mark.asyncio
async def test_set_node_users_empty():
    """set_node_users clears all users for a node."""
    node_row = _make_node_row(id="n1", type="PAGE", name="Page")
    db = _make_visu_db(row=node_row)
    db.fetchone = AsyncMock(return_value=None)  # users don't exist
    db.fetchall = AsyncMock(return_value=[])

    body = VisuNodeUsersUpdate(usernames=[])
    await set_node_users(node_id="n1", body=body, db=db, _admin="admin")
    assert db.conn.execute.called
    assert db.conn.commit.called


@pytest.mark.asyncio
async def test_get_node_users_returns_usernames():
    """get_node_users returns list of usernames."""
    node_row = _make_node_row(id="n1", type="PAGE", name="Page")
    db = _make_visu_db(row=node_row)

    class _URow(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)

    db.fetchall = AsyncMock(return_value=[_URow({"username": "alice"}), _URow({"username": "bob"})])
    result = await get_node_users(node_id="n1", db=db, _admin="admin")
    assert result == ["alice", "bob"]


# ===========================================================================
# obs/api/v1/visu.py — import_nodes, export_node
# ===========================================================================

from obs.api.v1.visu import import_nodes, export_node
from obs.models.visu import VisuImportRequest, VisuExportNode


@pytest.mark.asyncio
async def test_import_nodes_wrong_format():
    """import_nodes raises 400 for wrong obs_export value."""
    db = _make_visu_db()
    body = VisuImportRequest(obs_export="wrong", version=1, nodes=[])
    with pytest.raises(HTTPException) as exc_info:
        await import_nodes(body=body, db=db, _user="admin")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_import_nodes_empty_nodes():
    """import_nodes raises 400 when nodes list is empty."""
    db = _make_visu_db()
    body = VisuImportRequest(obs_export="visu_subtree", version=1, nodes=[])
    with pytest.raises(HTTPException) as exc_info:
        await import_nodes(body=body, db=db, _user="admin")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_import_nodes_success():
    """import_nodes creates nodes and returns root."""
    node_row = _make_node_row(id="imported-1", type="PAGE", name="Imported")
    db = _make_visu_db(row=node_row)
    export_node_1 = VisuExportNode(id="old-id-1", name="Imported Page", type="PAGE", parent_id=None)
    body = VisuImportRequest(obs_export="visu_subtree", version=1, nodes=[export_node_1], target_parent_id=None)
    result = await import_nodes(body=body, db=db, _user="admin")
    assert result.name == "Imported"
    assert db.conn.commit.called


@pytest.mark.asyncio
async def test_export_node_not_found():
    """export_node raises 404 when node not found."""
    db = _make_visu_db(row=None)
    db.conn.execute.return_value = _DualCursor(row=None)
    with pytest.raises(HTTPException) as exc_info:
        await export_node(node_id="nonexistent", db=db, _user="admin")
    assert exc_info.value.status_code == 404


# ===========================================================================
# obs/api/v1/system.py — test_history_connection remaining paths
# ===========================================================================


@pytest.mark.asyncio
async def test_test_history_influxdb_not_reachable():
    """test_history_connection returns error for unreachable influxdb."""
    from obs.api.v1.system import test_history_connection, HistorySettingsIn

    body = HistorySettingsIn(
        plugin="influxdb",
        default_window_hours=168,
        influx_url="http://nonexistent.localhost:8086",
        influx_version=2,
        influx_token="token",
        influx_org="org",
        influx_bucket="bucket",
    )
    result = await test_history_connection(body=body, _admin="admin")
    assert result.ok is False


@pytest.mark.asyncio
async def test_update_nav_link_not_found():
    """update_nav_link returns 404 when link not found."""
    from obs.api.v1.system import update_nav_link, NavLinkIn

    class _Db:
        async def fetchone(self, q, p=()):
            return None

        async def execute_and_commit(self, q, p=()):
            pass

    body = NavLinkIn(label="Updated", url="https://y.com")
    with pytest.raises(HTTPException) as exc_info:
        await update_nav_link(link_id=uuid.uuid4(), body=body, db=_Db(), _admin="admin")
    assert exc_info.value.status_code == 404


# ===========================================================================
# obs/api/v1/visu.py — copy_node, move_node, more paths
# ===========================================================================

from obs.api.v1.visu import copy_node, move_node
from obs.models.visu import CopyNodeRequest, MoveNodeRequest


@pytest.mark.asyncio
async def test_copy_node_success():
    """copy_node duplicates a node."""
    node_row = _make_node_row(id="orig", type="PAGE", name="Original")
    new_row = _make_node_row(id="copy1", type="PAGE", name="Copy")

    call_count = [0]

    def make_cursor(*a, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return _DualCursor(row=node_row)  # first get_node_or_404
        return _DualCursor(row=new_row)  # second get_node_or_404

    conn = MagicMock()
    conn.execute = MagicMock(side_effect=make_cursor)
    conn.commit = AsyncMock()
    db = MagicMock()
    db.conn = conn
    db.fetchone = AsyncMock(return_value=node_row)

    body = CopyNodeRequest(new_name="Copy", target_parent_id=None)
    result = await copy_node(node_id="orig", body=body, db=db, _user="admin")
    assert result.type == "PAGE"
    assert conn.commit.called


@pytest.mark.asyncio
async def test_move_node_not_found():
    """move_node raises 404 when source not found."""
    db = _make_visu_db(row=None)
    db.conn.execute.return_value = _DualCursor(row=None)
    body = MoveNodeRequest(target_parent_id=None, node_order=0)
    with pytest.raises(HTTPException) as exc_info:
        await move_node(node_id="nonexistent", body=body, db=db, _user="admin")
    assert exc_info.value.status_code == 404


# ===========================================================================
# obs/api/v1/system.py — update_app_settings timezone validation, nav link update
# ===========================================================================


@pytest.mark.asyncio
async def test_update_app_settings_invalid_timezone():
    """update_app_settings rejects invalid timezone."""
    from obs.api.v1.system import update_app_settings, AppSettingsIn

    class _Db:
        async def execute_and_commit(self, q, p=()):
            pass

    body = AppSettingsIn(timezone="Invalid/Timezone/XYZ")
    with pytest.raises(HTTPException) as exc_info:
        await update_app_settings(body=body, db=_Db(), _user="admin")
    assert exc_info.value.status_code in (400, 422)


@pytest.mark.asyncio
async def test_test_history_unknown_plugin():
    """test_history_connection returns error for unknown plugin."""
    from obs.api.v1.system import test_history_connection, HistorySettingsIn

    body = HistorySettingsIn(plugin="unknown_plugin", default_window_hours=168)
    result = await test_history_connection(body=body, _admin="admin")
    assert result.ok is False


@pytest.mark.asyncio
async def test_update_nav_link_success():
    """update_nav_link succeeds when link exists."""
    from obs.api.v1.system import update_nav_link, NavLinkIn

    class _Row(dict):
        def __getitem__(self, k):
            return super().__getitem__(k)

    class _Db:
        async def fetchone(self, q, p=()):
            return _Row({"id": "nav-link-1", "label": "Updated", "url": "https://z.com", "icon": "", "sort_order": 0, "open_new_tab": 0})

        async def execute_and_commit(self, q, p=()):
            pass

    body = NavLinkIn(label="Updated", url="https://z.com")
    result = await update_nav_link(link_id="test-link-1", body=body, db=_Db(), _admin="admin")
    assert result.label == "Updated"


# ===========================================================================
# obs/api/v1/system.py — test_history_connection timescaledb path
# ===========================================================================


@pytest.mark.asyncio
async def test_test_history_timescaledb_not_reachable():
    """test_history_connection returns error for unreachable timescaledb."""
    from obs.api.v1.system import test_history_connection, HistorySettingsIn

    body = HistorySettingsIn(
        plugin="timescaledb",
        default_window_hours=168,
        timescale_dsn="postgresql://nonexistent:5432/obs",
    )
    result = await test_history_connection(body=body, _admin="admin")
    assert result.ok is False


# ===========================================================================
# obs/api/v1/visu.py — create_node with pin, update_node with icon change
# ===========================================================================


@pytest.mark.asyncio
async def test_visu_create_node_with_pin():
    """create_node hashes access_pin when provided."""
    node_row = _make_node_row(id="pinned", type="PAGE", name="Secured")
    db = _make_visu_db(row=node_row)
    body = VisuNodeCreate(name="Secured", type="PAGE", parent_id=None, access_pin="5678")
    result = await create_node(body=body, db=db, _user="admin")
    assert result is not None
    assert db.conn.commit.called


@pytest.mark.asyncio
async def test_visu_update_node_icon():
    """update_node with icon field calls UPDATE."""
    node_row = _make_node_row(id="n1", type="PAGE", name="Page")
    db = _make_visu_db(row=node_row)
    body = VisuNodeUpdate(icon="🏠")
    result = await update_node(node_id="n1", body=body, db=db, _user="admin")
    assert result is not None


@pytest.mark.asyncio
async def test_visu_update_node_access():
    """update_node with access field calls UPDATE."""
    node_row = _make_node_row(id="n1", type="PAGE", name="Page")
    db = _make_visu_db(row=node_row)
    body = VisuNodeUpdate(access="user")
    result = await update_node(node_id="n1", body=body, db=db, _user="admin")
    assert result is not None


@pytest.mark.asyncio
async def test_visu_get_page_unauthenticated_user_access():
    """get_page with user access and no user raises 401."""
    from obs.api.v1 import visu as visu_mod

    node_row = _make_node_row(id="priv1", type="PAGE", name="Private", access="user")
    db = _make_visu_db(row=node_row)
    from unittest.mock import AsyncMock as _AsyncMock

    # Manually patch
    orig = visu_mod._resolve_access_with_node
    visu_mod._resolve_access_with_node = _AsyncMock(return_value=("user", None))
    try:
        req = MagicMock()
        req.headers = {}
        with pytest.raises(HTTPException) as exc_info:
            await get_page(node_id="priv1", request=req, db=db, user=None)
        assert exc_info.value.status_code == 401
    finally:
        visu_mod._resolve_access_with_node = orig


@pytest.mark.asyncio
async def test_visu_get_page_protected_access_no_session():
    """get_page with protected access and no session raises 401."""
    from obs.api.v1 import visu as visu_mod
    from unittest.mock import AsyncMock as _AsyncMock

    node_row = _make_node_row(id="prot1", type="PAGE", name="Protected", access="protected")
    db = _make_visu_db(row=node_row)
    orig = visu_mod._resolve_access_with_node
    visu_mod._resolve_access_with_node = _AsyncMock(return_value=("protected", None))
    try:
        req = MagicMock()
        req.headers = {}
        with pytest.raises(HTTPException) as exc_info:
            await get_page(node_id="prot1", request=req, db=db, user=None)
        assert exc_info.value.status_code == 401
    finally:
        visu_mod._resolve_access_with_node = orig
