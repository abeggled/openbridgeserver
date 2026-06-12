"""Coverage-boost tests for autobackup, logic API, zeitschaltuhr, snmp, homeassistant.

Targets:
  - obs/api/v1/autobackup.py   (0% → ≥60%)
  - obs/api/v1/logic.py        (0% → ≥60%)
  - obs/adapters/zeitschaltuhr/adapter.py (68% → higher)
  - obs/adapters/snmp/adapter.py          (70% → higher)
  - obs/adapters/homeassistant/adapter.py (63% → higher)
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, date, datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ---------------------------------------------------------------------------
# DB Stub (shared)
# ---------------------------------------------------------------------------


class _Row(dict):
    """Dict with attribute access (simulates aiosqlite Row)."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)


def _make_row(**kwargs) -> _Row:
    return _Row(kwargs)


class _DbStub:
    def __init__(self, rows=None, one=None):
        self._rows = rows or []
        self._one = one
        self.last_query: str = ""
        self.last_params: tuple = ()
        self.execute_calls: list[tuple] = []

    async def fetchone(self, query, params=()):
        return self._one

    async def fetchall(self, query, params=()):
        return list(self._rows)

    async def execute_and_commit(self, query, params=()):
        self.last_query = query
        self.last_params = params
        self.execute_calls.append((query, params))

    async def fetchone_or_raise(self, query, params=(), detail="Not found"):
        row = self._one
        if row is None:
            from fastapi import HTTPException

            raise HTTPException(404, detail)
        return row


# ===========================================================================
# obs/api/v1/autobackup.py
# ===========================================================================


class TestLoadConfig:
    @pytest.mark.asyncio
    async def test_defaults_when_no_rows(self):
        from obs.api.v1.autobackup import _load_config

        db = _DbStub(rows=[])
        cfg = await _load_config(db)
        assert cfg.enabled is False
        assert cfg.hour == 3
        assert cfg.retention_days == 7

    @pytest.mark.asyncio
    async def test_reads_stored_values(self):
        from obs.api.v1.autobackup import _load_config

        rows = [
            _make_row(key="autobackup.enabled", value="1"),
            _make_row(key="autobackup.hour", value="5"),
            _make_row(key="autobackup.retention_days", value="14"),
        ]
        db = _DbStub(rows=rows)
        cfg = await _load_config(db)
        assert cfg.enabled is True
        assert cfg.hour == 5
        assert cfg.retention_days == 14


class TestSaveConfig:
    @pytest.mark.asyncio
    async def test_saves_three_rows(self):
        from obs.api.v1.autobackup import AutobackupConfig, _save_config

        db = _DbStub()
        cfg = AutobackupConfig(enabled=True, hour=2, retention_days=10)
        await _save_config(db, cfg)
        assert len(db.execute_calls) == 3
        keys = [call[1][0] for call in db.execute_calls]
        assert "autobackup.enabled" in keys
        assert "autobackup.hour" in keys
        assert "autobackup.retention_days" in keys

    @pytest.mark.asyncio
    async def test_disabled_saves_zero(self):
        from obs.api.v1.autobackup import AutobackupConfig, _save_config

        db = _DbStub()
        cfg = AutobackupConfig(enabled=False, hour=0, retention_days=1)
        await _save_config(db, cfg)
        enabled_call = next(c for c in db.execute_calls if c[1][0] == "autobackup.enabled")
        assert enabled_call[1][1] == "0"


class TestListBackups:
    def test_returns_empty_list_for_empty_dir(self, tmp_path):
        from obs.api.v1.autobackup import _list_backups

        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            result = _list_backups()
        assert result == []

    def test_returns_entries_for_existing_files(self, tmp_path):
        from obs.api.v1.autobackup import _list_backups

        (tmp_path / "20240506-0300.json").write_text("{}")
        (tmp_path / "20240507-0300.json").write_text("{}")
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            result = _list_backups()
        assert len(result) == 2
        assert result[0].name == "20240507-0300"
        assert result[1].name == "20240506-0300"

    def test_parses_valid_datetime_from_stem(self, tmp_path):
        from obs.api.v1.autobackup import _list_backups

        (tmp_path / "20260101-1200.json").write_text("{}")
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            result = _list_backups()
        assert len(result) == 1
        assert result[0].created_at == "2026-01-01T12:00:00"

    def test_falls_back_to_stem_for_invalid_name(self, tmp_path):
        from obs.api.v1.autobackup import _list_backups

        (tmp_path / "custom_backup.json").write_text("{}")
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            result = _list_backups()
        assert result[0].created_at == "custom_backup"


class TestPruneOldBackups:
    def test_deletes_oldest_beyond_retention(self, tmp_path):
        from obs.api.v1.autobackup import _prune_old_backups

        for i in range(5):
            (tmp_path / f"2026010{i + 1}-0300.json").write_text("{}")
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            deleted = _prune_old_backups(retention_days=3)
        assert deleted == 2
        remaining = list(tmp_path.glob("*.json"))
        assert len(remaining) == 3

    def test_no_deletion_when_within_retention(self, tmp_path):
        from obs.api.v1.autobackup import _prune_old_backups

        (tmp_path / "20260101-0300.json").write_text("{}")
        (tmp_path / "20260102-0300.json").write_text("{}")
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            deleted = _prune_old_backups(retention_days=7)
        assert deleted == 0


class TestSetAutobackupConfigEndpoint:
    @pytest.mark.asyncio
    async def test_invalid_hour_raises_400(self):
        from fastapi import HTTPException

        from obs.api.v1.autobackup import AutobackupConfig, set_autobackup_config

        db = _DbStub()
        body = AutobackupConfig(enabled=True, hour=25, retention_days=7)
        with pytest.raises(HTTPException) as exc_info:
            await set_autobackup_config(body=body, _admin="admin", db=db)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_retention_days_raises_400(self):
        from fastapi import HTTPException

        from obs.api.v1.autobackup import AutobackupConfig, set_autobackup_config

        db = _DbStub()
        body = AutobackupConfig(enabled=True, hour=3, retention_days=0)
        with pytest.raises(HTTPException) as exc_info:
            await set_autobackup_config(body=body, _admin="admin", db=db)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_valid_config_saved(self):
        from obs.api.v1.autobackup import AutobackupConfig, set_autobackup_config

        db = _DbStub()
        body = AutobackupConfig(enabled=True, hour=2, retention_days=5)
        with patch("obs.api.v1.autobackup._notify_config_change"):
            result = await set_autobackup_config(body=body, _admin="admin", db=db)
        assert result.hour == 2
        assert result.retention_days == 5


class TestGetAutobackupConfigEndpoint:
    @pytest.mark.asyncio
    async def test_returns_config(self):
        from obs.api.v1.autobackup import get_autobackup_config

        rows = [
            _make_row(key="autobackup.enabled", value="1"),
            _make_row(key="autobackup.hour", value="4"),
            _make_row(key="autobackup.retention_days", value="14"),
        ]
        db = _DbStub(rows=rows)
        cfg = await get_autobackup_config(_admin="admin", db=db)
        assert cfg.enabled is True
        assert cfg.hour == 4


class TestListAutobackupsEndpoint:
    @pytest.mark.asyncio
    async def test_returns_list(self, tmp_path):
        from obs.api.v1.autobackup import list_autobackups

        (tmp_path / "20260101-0300.json").write_text("{}")
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            result = await list_autobackups(_admin="admin")
        assert len(result) == 1
        assert result[0].name == "20260101-0300"


class TestDeleteAutobackupEndpoint:
    @pytest.mark.asyncio
    async def test_invalid_name_raises_400(self):
        from fastapi import HTTPException

        from obs.api.v1.autobackup import delete_autobackup

        with pytest.raises(HTTPException) as exc_info:
            await delete_autobackup(name="../../etc/passwd", _admin="admin")
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self, tmp_path):
        from fastapi import HTTPException

        from obs.api.v1.autobackup import delete_autobackup

        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            with pytest.raises(HTTPException) as exc_info:
                await delete_autobackup(name="20260101-0300", _admin="admin")
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_deletes_existing_file(self, tmp_path):
        from obs.api.v1.autobackup import delete_autobackup

        (tmp_path / "20260101-0300.json").write_text("{}")
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            result = await delete_autobackup(name="20260101-0300", _admin="admin")
        assert result["ok"] is True
        assert not (tmp_path / "20260101-0300.json").exists()


class TestRestoreAutobackupEndpoint:
    @pytest.mark.asyncio
    async def test_invalid_name_raises_400(self, tmp_path):
        from fastapi import HTTPException

        from obs.api.v1.autobackup import restore_autobackup

        db = _DbStub()
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            with pytest.raises(HTTPException) as exc_info:
                await restore_autobackup(name="../bad-name", _admin="admin", db=db)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self, tmp_path):
        from fastapi import HTTPException

        from obs.api.v1.autobackup import restore_autobackup

        db = _DbStub()
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            with pytest.raises(HTTPException) as exc_info:
                await restore_autobackup(name="20260101-0300", _admin="admin", db=db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_invalid_json_raises_400(self, tmp_path):
        from fastapi import HTTPException

        from obs.api.v1.autobackup import restore_autobackup

        (tmp_path / "20260101-0300.json").write_text("NOT JSON")
        db = _DbStub()
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            with pytest.raises(HTTPException) as exc_info:
                await restore_autobackup(name="20260101-0300", _admin="admin", db=db)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_export_format_raises_400(self, tmp_path):
        from fastapi import HTTPException

        from obs.api.v1.autobackup import restore_autobackup

        (tmp_path / "20260101-0300.json").write_text(json.dumps({"bad_key": "bad_value"}))
        db = _DbStub()
        with patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path):
            with pytest.raises(HTTPException) as exc_info:
                await restore_autobackup(name="20260101-0300", _admin="admin", db=db)
        assert exc_info.value.status_code == 400


class TestNotifyConfigChange:
    def test_notify_with_no_event_is_safe(self):
        import obs.api.v1.autobackup as ab

        original = ab._config_changed_event
        ab._config_changed_event = None
        try:
            ab._notify_config_change()  # must not raise
        finally:
            ab._config_changed_event = original

    def test_notify_sets_event(self):
        import obs.api.v1.autobackup as ab

        event = asyncio.Event()
        original = ab._config_changed_event
        ab._config_changed_event = event
        try:
            ab._notify_config_change()
            assert event.is_set()
        finally:
            ab._config_changed_event = original


class TestAutobackupSchedulerStopStart:
    @pytest.mark.asyncio
    async def test_stop_with_no_task_is_safe(self):
        from obs.api.v1.autobackup import AutobackupScheduler

        db = _DbStub()
        scheduler = AutobackupScheduler(db)
        await scheduler.stop()

    @pytest.mark.asyncio
    async def test_stop_cancels_running_task(self):
        from obs.api.v1.autobackup import AutobackupScheduler

        db = _DbStub(rows=[_make_row(key="autobackup.enabled", value="0")])
        scheduler = AutobackupScheduler(db)
        scheduler.start()
        assert scheduler._task is not None
        await scheduler.stop()
        assert scheduler._task.done()


class TestGetAutobackupScheduler:
    def test_raises_when_not_initialized(self):
        import obs.api.v1.autobackup as ab

        original = ab._scheduler
        ab._scheduler = None
        try:
            with pytest.raises(RuntimeError, match="nicht initialisiert"):
                ab.get_autobackup_scheduler()
        finally:
            ab._scheduler = original


class TestRunAutobackupNowEndpoint:
    @pytest.mark.asyncio
    async def test_returns_ok_with_name(self, tmp_path):
        from obs.api.v1.autobackup import run_autobackup_now

        rows = [
            _make_row(key="autobackup.enabled", value="1"),
            _make_row(key="autobackup.hour", value="3"),
            _make_row(key="autobackup.retention_days", value="7"),
        ]
        db = _DbStub(rows=rows)

        fake_export = MagicMock()
        fake_export.model_dump.return_value = {"datapoints": [], "bindings": []}

        with (
            patch("obs.api.v1.autobackup._autobackup_dir", return_value=tmp_path),
            patch("obs.api.v1.config.export_config", new_callable=AsyncMock, return_value=fake_export),
        ):
            result = await run_autobackup_now(_admin="admin", db=db)

        assert result["ok"] is True
        assert "name" in result
        assert "old_backups_deleted" in result


# ===========================================================================
# obs/api/v1/logic.py
# ===========================================================================


def _make_graph_row(
    gid: str = None,
    name: str = "Test Graph",
    description: str = "",
    enabled: int = 1,
    flow_data: str | None = None,
    created_at: str = "2026-01-01T00:00:00",
    updated_at: str = "2026-01-01T00:00:00",
) -> _Row:
    if gid is None:
        gid = str(uuid.uuid4())
    if flow_data is None:
        flow_data = json.dumps({"nodes": [], "edges": []})
    return _Row(
        id=gid,
        name=name,
        description=description,
        enabled=enabled,
        flow_data=flow_data,
        created_at=created_at,
        updated_at=updated_at,
    )


class TestRowToOut:
    def test_converts_row_with_empty_flow(self):
        from obs.api.v1.logic import _row_to_out

        row = _make_graph_row(flow_data=json.dumps({"nodes": [], "edges": []}))
        out = _row_to_out(row)
        assert out.id == row["id"]
        assert out.enabled is True
        assert out.flow_data.nodes == []

    def test_converts_row_with_null_flow(self):
        from obs.api.v1.logic import _row_to_out

        row = _make_graph_row(flow_data=None)
        out = _row_to_out(row)
        assert out.flow_data.nodes == []

    def test_converts_disabled_graph(self):
        from obs.api.v1.logic import _row_to_out

        row = _make_graph_row(enabled=0)
        out = _row_to_out(row)
        assert out.enabled is False

    def test_description_defaults_to_empty(self):
        from obs.api.v1.logic import _row_to_out

        row = _make_graph_row(description=None)
        out = _row_to_out(row)
        assert out.description == ""


class TestListGraphs:
    @pytest.mark.asyncio
    async def test_returns_empty_list(self):
        from obs.api.v1.logic import list_graphs

        db = _DbStub(rows=[])
        result = await list_graphs(_user="user", db=db)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_all_graphs(self):
        from obs.api.v1.logic import list_graphs

        rows = [_make_graph_row(name="G1"), _make_graph_row(name="G2")]
        db = _DbStub(rows=rows)
        result = await list_graphs(_user="user", db=db)
        assert len(result) == 2


class TestGetGraph:
    @pytest.mark.asyncio
    async def test_returns_graph_when_found(self):
        from obs.api.v1.logic import get_graph

        row = _make_graph_row(name="My Graph")
        db = _DbStub(one=row)
        result = await get_graph(graph_id=row["id"], _user="user", db=db)
        assert result.name == "My Graph"

    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        from fastapi import HTTPException

        from obs.api.v1.logic import get_graph

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await get_graph(graph_id="non-existent", _user="user", db=db)
        assert exc_info.value.status_code == 404


class TestCreateGraph:
    @pytest.mark.asyncio
    async def test_creates_and_returns_graph(self):
        from obs.api.v1.logic import create_graph

        from obs.logic.models import FlowData, LogicGraphCreate

        body = LogicGraphCreate(name="New Graph", description="desc", enabled=True, flow_data=FlowData())
        created_row = _make_graph_row(name="New Graph", description="desc")
        db = _DbStub(one=created_row)

        with patch("obs.logic.manager.get_logic_manager", side_effect=RuntimeError("no manager")):
            result = await create_graph(body=body, _user="user", db=db)

        assert result.name == "New Graph"
        assert len(db.execute_calls) == 1

    @pytest.mark.asyncio
    async def test_reloads_logic_manager_on_create(self):
        from obs.api.v1.logic import create_graph

        from obs.logic.models import FlowData, LogicGraphCreate

        body = LogicGraphCreate(name="Graph", flow_data=FlowData())
        row = _make_graph_row(name="Graph")
        db = _DbStub(one=row)

        mock_manager = MagicMock()
        mock_manager.reload = AsyncMock()

        with patch("obs.logic.manager.get_logic_manager", return_value=mock_manager):
            await create_graph(body=body, _user="user", db=db)

        mock_manager.reload.assert_called_once()


class TestUpdateGraphFull:
    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        from fastapi import HTTPException

        from obs.api.v1.logic import update_graph_full

        from obs.logic.models import FlowData, LogicGraphCreate

        body = LogicGraphCreate(name="Updated", flow_data=FlowData())
        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await update_graph_full(graph_id="missing", body=body, _user="user", db=db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_updates_and_returns_graph(self):
        from obs.api.v1.logic import update_graph_full

        from obs.logic.models import FlowData, LogicGraphCreate

        row = _make_graph_row(name="Old Name")
        gid = row["id"]
        body = LogicGraphCreate(name="New Name", flow_data=FlowData())
        updated_row = _make_graph_row(gid=gid, name="New Name")

        call_count = {"n": 0}

        async def _fetchone(query, params=()):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return row
            return updated_row

        db = _DbStub()
        db.fetchone = _fetchone

        with patch("obs.logic.manager.get_logic_manager", side_effect=RuntimeError("no manager")):
            result = await update_graph_full(graph_id=gid, body=body, _user="user", db=db)

        assert result.name == "New Name"


class TestUpdateGraphPartial:
    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        from fastapi import HTTPException

        from obs.api.v1.logic import update_graph_partial

        from obs.logic.models import LogicGraphUpdate

        body = LogicGraphUpdate(name="New")
        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await update_graph_partial(graph_id="missing", body=body, _user="user", db=db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_partial_update_name_only(self):
        from obs.api.v1.logic import update_graph_partial

        from obs.logic.models import LogicGraphUpdate

        row = _make_graph_row(name="Old", description="Desc")
        gid = row["id"]
        body = LogicGraphUpdate(name="New")
        updated_row = _make_graph_row(gid=gid, name="New", description="Desc")

        call_count = {"n": 0}

        async def _fetchone(query, params=()):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return row
            return updated_row

        db = _DbStub()
        db.fetchone = _fetchone

        with patch("obs.logic.manager.get_logic_manager", side_effect=RuntimeError("no manager")):
            result = await update_graph_partial(graph_id=gid, body=body, _user="user", db=db)

        assert result.name == "New"
        assert result.description == "Desc"

    @pytest.mark.asyncio
    async def test_partial_update_enabled_only(self):
        from obs.api.v1.logic import update_graph_partial

        from obs.logic.models import LogicGraphUpdate

        row = _make_graph_row(enabled=1, name="G")
        gid = row["id"]
        body = LogicGraphUpdate(enabled=False)
        updated_row = _make_graph_row(gid=gid, name="G", enabled=0)

        call_count = {"n": 0}

        async def _fetchone(query, params=()):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return row
            return updated_row

        db = _DbStub()
        db.fetchone = _fetchone

        with patch("obs.logic.manager.get_logic_manager", side_effect=RuntimeError("no manager")):
            result = await update_graph_partial(graph_id=gid, body=body, _user="user", db=db)

        assert result.enabled is False


class TestDeleteGraph:
    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        from fastapi import HTTPException

        from obs.api.v1.logic import delete_graph

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await delete_graph(graph_id="missing", _user="user", db=db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_deletes_existing_graph(self):
        from obs.api.v1.logic import delete_graph

        row = _make_graph_row()
        db = _DbStub(one=row)
        with patch("obs.logic.manager.get_logic_manager", side_effect=RuntimeError("no manager")):
            await delete_graph(graph_id=row["id"], _user="user", db=db)
        assert len(db.execute_calls) == 1
        assert "DELETE" in db.execute_calls[0][0]

    @pytest.mark.asyncio
    async def test_invalidates_cache_on_delete(self):
        from obs.api.v1.logic import delete_graph

        row = _make_graph_row()
        db = _DbStub(one=row)
        mock_manager = MagicMock()
        mock_manager.invalidate_cache = MagicMock()

        with patch("obs.logic.manager.get_logic_manager", return_value=mock_manager):
            await delete_graph(graph_id=row["id"], _user="user", db=db)

        mock_manager.invalidate_cache.assert_called_once_with(row["id"])


class TestImportGraph:
    @pytest.mark.asyncio
    async def test_raises_400_for_wrong_export_type(self):
        from fastapi import HTTPException

        from obs.api.v1.logic import import_graph

        from obs.logic.models import FlowData, LogicGraphImport

        body = LogicGraphImport(obs_export="wrong_type", version=1, name="G", flow_data=FlowData())
        db = _DbStub()
        with pytest.raises(HTTPException) as exc_info:
            await import_graph(body=body, _user="user", db=db)
        assert exc_info.value.status_code == 400

    @pytest.mark.asyncio
    async def test_imports_valid_graph(self):
        from obs.api.v1.logic import import_graph

        from obs.logic.models import FlowData, LogicGraphImport

        body = LogicGraphImport(obs_export="logic_graph", version=1, name="Imported", flow_data=FlowData())
        created_row = _make_graph_row(name="Imported")
        db = _DbStub(one=created_row)

        with (
            patch("obs.core.registry.get_registry", side_effect=RuntimeError("no registry")),
            patch("obs.logic.manager.get_logic_manager", side_effect=RuntimeError("no manager")),
        ):
            result = await import_graph(body=body, _user="user", db=db)

        assert result.name == "Imported"

    @pytest.mark.asyncio
    async def test_missing_node_type_replaced(self):
        from obs.api.v1.logic import import_graph

        from obs.logic.models import FlowData, LogicGraphImport, LogicNode, NodePosition

        node = LogicNode(id="n1", type="unknown_type_xyz", position=NodePosition(x=0, y=0), data={})
        flow = FlowData(nodes=[node])
        body = LogicGraphImport(obs_export="logic_graph", version=1, name="G", flow_data=flow)
        created_row = _make_graph_row(name="G")
        db = _DbStub(one=created_row)

        with (
            patch("obs.core.registry.get_registry", side_effect=RuntimeError("no registry")),
            patch("obs.logic.manager.get_logic_manager", side_effect=RuntimeError("no manager")),
        ):
            await import_graph(body=body, _user="user", db=db)

        saved_flow_json = db.execute_calls[0][1][4]
        saved_flow = json.loads(saved_flow_json)
        assert saved_flow["nodes"][0]["type"] == "missing_node"


class TestRunGraph:
    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        from fastapi import HTTPException

        from obs.api.v1.logic import run_graph

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await run_graph(graph_id="missing", _user="user", db=db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_raises_422_when_disabled(self):
        from fastapi import HTTPException

        from obs.api.v1.logic import run_graph

        row = _make_graph_row(enabled=0)
        db = _DbStub(one=row)
        with pytest.raises(HTTPException) as exc_info:
            await run_graph(graph_id=row["id"], _user="user", db=db)
        assert exc_info.value.status_code == 422

    @pytest.mark.asyncio
    async def test_runs_enabled_graph(self):
        from obs.api.v1.logic import run_graph

        row = _make_graph_row(enabled=1)
        db = _DbStub(one=row)
        mock_manager = MagicMock()
        mock_manager.execute_graph = AsyncMock(return_value={"output": 1})

        with patch("obs.logic.manager.get_logic_manager", return_value=mock_manager):
            result = await run_graph(graph_id=row["id"], _user="user", db=db)

        assert result["status"] == "ok"
        assert result["outputs"] == {"output": 1}

    @pytest.mark.asyncio
    async def test_raises_500_on_execution_error(self):
        from fastapi import HTTPException

        from obs.api.v1.logic import run_graph

        row = _make_graph_row(enabled=1)
        db = _DbStub(one=row)
        mock_manager = MagicMock()
        mock_manager.execute_graph = AsyncMock(side_effect=RuntimeError("execution failed"))

        with patch("obs.logic.manager.get_logic_manager", return_value=mock_manager):
            with pytest.raises(HTTPException) as exc_info:
                await run_graph(graph_id=row["id"], _user="user", db=db)
        assert exc_info.value.status_code == 500


class TestDuplicateGraph:
    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        from fastapi import HTTPException

        from obs.api.v1.logic import duplicate_graph

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await duplicate_graph(graph_id="missing", _user="user", db=db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_duplicate_creates_copy(self):
        from obs.api.v1.logic import duplicate_graph

        from obs.logic.models import FlowData, LogicEdge, LogicNode, NodePosition

        node = LogicNode(id="n1", type="and", position=NodePosition(x=0, y=0), data={})
        edge = LogicEdge(id="e1", source="n1", target="n1")
        flow = FlowData(nodes=[node], edges=[edge])
        original_row = _make_graph_row(name="Original", flow_data=flow.model_dump_json())

        new_id = str(uuid.uuid4())
        copy_row = _make_graph_row(gid=new_id, name="Kopie von Original")

        call_count = {"n": 0}

        async def _fetchone(query, params=()):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return original_row
            return copy_row

        db = _DbStub()
        db.fetchone = _fetchone

        with patch("obs.logic.manager.get_logic_manager", side_effect=RuntimeError("no manager")):
            result = await duplicate_graph(graph_id=original_row["id"], _user="user", db=db)

        assert result.name == "Kopie von Original"
        assert len(db.execute_calls) == 1


class TestExportGraph:
    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        from fastapi import HTTPException

        from obs.api.v1.logic import export_graph

        db = _DbStub(one=None)
        with pytest.raises(HTTPException) as exc_info:
            await export_graph(graph_id="missing", _user="user", db=db)
        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_json_response(self):
        from obs.api.v1.logic import export_graph

        row = _make_graph_row(name="My Graph")
        db = _DbStub(one=row)
        response = await export_graph(graph_id=row["id"], _user="user", db=db)
        content = json.loads(response.body)
        assert content["obs_export"] == "logic_graph"
        assert content["name"] == "My Graph"
        assert "exported_at" in content


class TestGetDatapointLogicUsages:
    @pytest.mark.asyncio
    async def test_returns_empty_for_no_graphs(self):
        from obs.api.v1.logic import get_datapoint_logic_usages

        db = _DbStub(rows=[])
        result = await get_datapoint_logic_usages(dp_id="dp-123", _user="user", db=db)
        assert result == []

    @pytest.mark.asyncio
    async def test_finds_datapoint_read_usage(self):
        from obs.api.v1.logic import get_datapoint_logic_usages

        from obs.logic.models import FlowData, LogicNode, NodePosition

        dp_id = str(uuid.uuid4())
        node = LogicNode(id="n1", type="datapoint_read", position=NodePosition(x=0, y=0), data={"datapoint_id": dp_id})
        flow = FlowData(nodes=[node])
        row = _make_graph_row(name="G", flow_data=flow.model_dump_json())
        db = _DbStub(rows=[row])
        result = await get_datapoint_logic_usages(dp_id=dp_id, _user="user", db=db)
        assert len(result) == 1
        assert result[0].direction == "SOURCE"

    @pytest.mark.asyncio
    async def test_finds_datapoint_write_usage(self):
        from obs.api.v1.logic import get_datapoint_logic_usages

        from obs.logic.models import FlowData, LogicNode, NodePosition

        dp_id = str(uuid.uuid4())
        node = LogicNode(id="n1", type="datapoint_write", position=NodePosition(x=0, y=0), data={"datapoint_id": dp_id})
        flow = FlowData(nodes=[node])
        row = _make_graph_row(name="G", flow_data=flow.model_dump_json())
        db = _DbStub(rows=[row])
        result = await get_datapoint_logic_usages(dp_id=dp_id, _user="user", db=db)
        assert len(result) == 1
        assert result[0].direction == "DEST"

    @pytest.mark.asyncio
    async def test_ignores_other_node_types(self):
        from obs.api.v1.logic import get_datapoint_logic_usages

        from obs.logic.models import FlowData, LogicNode, NodePosition

        dp_id = str(uuid.uuid4())
        node = LogicNode(id="n1", type="and", position=NodePosition(x=0, y=0), data={"datapoint_id": dp_id})
        flow = FlowData(nodes=[node])
        row = _make_graph_row(name="G", flow_data=flow.model_dump_json())
        db = _DbStub(rows=[row])
        result = await get_datapoint_logic_usages(dp_id=dp_id, _user="user", db=db)
        assert result == []


# ===========================================================================
# obs/adapters/zeitschaltuhr/adapter.py — missing lines
# ===========================================================================


def _make_zs_adapter(**cfg_overrides) -> Any:
    from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrAdapter, ZeitschaltuhrConfig

    bus = MagicMock()
    bus.publish = AsyncMock()
    adapter = ZeitschaltuhrAdapter(event_bus=bus, config={})
    adapter._tz = UTC
    adapter._hol = {}
    adapter._cfg = ZeitschaltuhrConfig(**cfg_overrides)
    adapter._bindings = []
    adapter._bus = bus
    return adapter


class TestZeitschaltuhrFireBinding:
    @pytest.mark.asyncio
    async def test_fires_true_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="1")
        await adapter._fire_binding(binding, cfg)
        adapter._bus.publish.assert_called_once()
        event = adapter._bus.publish.call_args[0][0]
        assert event.value is True

    @pytest.mark.asyncio
    async def test_fires_false_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="0")
        await adapter._fire_binding(binding, cfg)
        event = adapter._bus.publish.call_args[0][0]
        assert event.value is False

    @pytest.mark.asyncio
    async def test_fires_int_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="42")
        await adapter._fire_binding(binding, cfg)
        event = adapter._bus.publish.call_args[0][0]
        assert event.value == 42

    @pytest.mark.asyncio
    async def test_fires_float_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="3.14")
        await adapter._fire_binding(binding, cfg)
        event = adapter._bus.publish.call_args[0][0]
        assert abs(event.value - 3.14) < 0.001

    @pytest.mark.asyncio
    async def test_fires_string_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="hello_world")
        await adapter._fire_binding(binding, cfg)
        event = adapter._bus.publish.call_args[0][0]
        assert event.value == "hello_world"

    @pytest.mark.asyncio
    async def test_fires_on_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="on")
        await adapter._fire_binding(binding, cfg)
        event = adapter._bus.publish.call_args[0][0]
        assert event.value is True

    @pytest.mark.asyncio
    async def test_fires_off_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="off")
        await adapter._fire_binding(binding, cfg)
        event = adapter._bus.publish.call_args[0][0]
        assert event.value is False

    @pytest.mark.asyncio
    async def test_fires_ein_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="ein")
        await adapter._fire_binding(binding, cfg)
        event = adapter._bus.publish.call_args[0][0]
        assert event.value is True

    @pytest.mark.asyncio
    async def test_fires_aus_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="aus")
        await adapter._fire_binding(binding, cfg)
        event = adapter._bus.publish.call_args[0][0]
        assert event.value is False

    @pytest.mark.asyncio
    async def test_fires_true_string_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="true")
        await adapter._fire_binding(binding, cfg)
        event = adapter._bus.publish.call_args[0][0]
        assert event.value is True

    @pytest.mark.asyncio
    async def test_fires_false_string_value(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        cfg = ZeitschaltuhrBindingConfig(value="false")
        await adapter._fire_binding(binding, cfg)
        event = adapter._bus.publish.call_args[0][0]
        assert event.value is False


class TestZeitschaltuhrMetaBindings:
    @pytest.mark.asyncio
    async def test_publishes_holiday_today_meta(self):
        from obs.adapters.zeitschaltuhr.adapter import MetaType, TimerType

        adapter = _make_zs_adapter()
        today = date(2026, 1, 1)
        adapter._hol = {today: "Neujahr"}

        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        binding.config = {"timer_type": TimerType.META, "meta_type": MetaType.HOLIDAY_TODAY}

        adapter._bindings = [binding]
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        await adapter._publish_meta_bindings(now)

        adapter._bus.publish.assert_called_once()
        event = adapter._bus.publish.call_args[0][0]
        assert event.value is True

    @pytest.mark.asyncio
    async def test_publishes_holiday_tomorrow_meta(self):
        from obs.adapters.zeitschaltuhr.adapter import MetaType, TimerType

        adapter = _make_zs_adapter()
        tomorrow = date(2026, 1, 2)
        adapter._hol = {tomorrow: "Some Holiday"}

        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        binding.config = {"timer_type": TimerType.META, "meta_type": MetaType.HOLIDAY_TOMORROW}

        adapter._bindings = [binding]
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        await adapter._publish_meta_bindings(now)

        event = adapter._bus.publish.call_args[0][0]
        assert event.value is True

    @pytest.mark.asyncio
    async def test_publishes_holiday_name_today_meta(self):
        from obs.adapters.zeitschaltuhr.adapter import MetaType, TimerType

        adapter = _make_zs_adapter()
        today = date(2026, 1, 1)
        adapter._hol = {today: "Neujahr"}

        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        binding.config = {"timer_type": TimerType.META, "meta_type": MetaType.HOLIDAY_NAME_TODAY}

        adapter._bindings = [binding]
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        await adapter._publish_meta_bindings(now)

        event = adapter._bus.publish.call_args[0][0]
        assert event.value == "Neujahr"

    @pytest.mark.asyncio
    async def test_publishes_holiday_name_tomorrow_meta(self):
        from obs.adapters.zeitschaltuhr.adapter import MetaType, TimerType

        adapter = _make_zs_adapter()
        tomorrow = date(2026, 1, 2)
        adapter._hol = {tomorrow: "Neujahrstag2"}

        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        binding.config = {"timer_type": TimerType.META, "meta_type": MetaType.HOLIDAY_NAME_TOMORROW}

        adapter._bindings = [binding]
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        await adapter._publish_meta_bindings(now)

        event = adapter._bus.publish.call_args[0][0]
        assert event.value == "Neujahrstag2"

    @pytest.mark.asyncio
    async def test_publishes_vacation_1_meta(self):
        from obs.adapters.zeitschaltuhr.adapter import MetaType, TimerType

        adapter = _make_zs_adapter(
            vacation_1_start="2026-07-01",
            vacation_1_end="2026-07-31",
        )

        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        binding.config = {"timer_type": TimerType.META, "meta_type": MetaType.VACATION_1}

        adapter._bindings = [binding]
        now = datetime(2026, 7, 15, 8, 0, tzinfo=UTC)
        await adapter._publish_meta_bindings(now)

        event = adapter._bus.publish.call_args[0][0]
        assert event.value is True

    @pytest.mark.asyncio
    async def test_publishes_vacation_2_meta(self):
        from obs.adapters.zeitschaltuhr.adapter import MetaType, TimerType

        adapter = _make_zs_adapter(
            vacation_2_start="2026-08-01",
            vacation_2_end="2026-08-15",
        )

        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        binding.config = {"timer_type": TimerType.META, "meta_type": MetaType.VACATION_2}

        adapter._bindings = [binding]
        now = datetime(2026, 8, 10, 8, 0, tzinfo=UTC)
        await adapter._publish_meta_bindings(now)

        event = adapter._bus.publish.call_args[0][0]
        assert event.value is True

    @pytest.mark.asyncio
    async def test_skips_meta_type_none(self):
        from obs.adapters.zeitschaltuhr.adapter import MetaType, TimerType

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        binding.config = {"timer_type": TimerType.META, "meta_type": MetaType.NONE}

        adapter._bindings = [binding]
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        await adapter._publish_meta_bindings(now)
        adapter._bus.publish.assert_not_called()

    @pytest.mark.asyncio
    async def test_skips_non_meta_bindings(self):
        from obs.adapters.zeitschaltuhr.adapter import TimerType

        adapter = _make_zs_adapter()
        binding = MagicMock()
        binding.datapoint_id = uuid.uuid4()
        binding.id = uuid.uuid4()
        binding.config = {"timer_type": TimerType.DAILY, "time_ref": "absolute", "hour": 8, "minute": 0}

        adapter._bindings = [binding]
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        await adapter._publish_meta_bindings(now)
        adapter._bus.publish.assert_not_called()


class TestZeitschaltuhrReloadBindings:
    @pytest.mark.asyncio
    async def test_filters_non_source_bindings(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrAdapter

        bus = MagicMock()
        bus.publish = AsyncMock()
        adapter = ZeitschaltuhrAdapter(event_bus=bus, config={})
        adapter._tz = UTC
        adapter._hol = {}
        adapter._connected = False

        b_source = MagicMock()
        b_source.id = uuid.uuid4()
        b_source.direction = "SOURCE"

        b_dest = MagicMock()
        b_dest.id = uuid.uuid4()
        b_dest.direction = "DEST"

        b_both = MagicMock()
        b_both.id = uuid.uuid4()
        b_both.direction = "BOTH"

        await adapter.reload_bindings([b_source, b_dest, b_both])
        assert b_source in adapter._bindings
        assert b_dest not in adapter._bindings
        assert b_both not in adapter._bindings


class TestZeitschaltuhrSunEvent:
    def test_get_sun_event_exception_returns_none(self):
        from obs.adapters.zeitschaltuhr.adapter import TimeRef

        adapter = _make_zs_adapter()
        # Patch astral to raise an exception
        with patch("obs.adapters.zeitschaltuhr.adapter.ZeitschaltuhrAdapter._get_sun_event", return_value=None):
            from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrBindingConfig

            cfg = ZeitschaltuhrBindingConfig(time_ref=TimeRef.SUNRISE)
            result = adapter._calculate_target_time(cfg, date(2026, 6, 21))
        assert result is None

    def test_calculate_target_time_with_sunrise(self):
        from obs.adapters.zeitschaltuhr.adapter import TimeRef, ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        fake_sunrise = datetime(2026, 6, 21, 5, 30, tzinfo=UTC)

        with patch.object(adapter, "_get_sun_event", return_value=fake_sunrise):
            cfg = ZeitschaltuhrBindingConfig(time_ref=TimeRef.SUNRISE, offset_minutes=15)
            result = adapter._calculate_target_time(cfg, date(2026, 6, 21))

        assert result is not None
        assert result.hour == 5
        assert result.minute == 45

    def test_calculate_target_time_with_solar_altitude(self):
        from obs.adapters.zeitschaltuhr.adapter import SunDirection, TimeRef, ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        fake_time = datetime(2026, 6, 21, 7, 0, tzinfo=UTC)

        with patch.object(adapter, "_get_solar_altitude_time", return_value=fake_time):
            cfg = ZeitschaltuhrBindingConfig(
                time_ref=TimeRef.SOLAR_ALTITUDE,
                solar_altitude_deg=30.0,
                sun_direction=SunDirection.RISING,
            )
            result = adapter._calculate_target_time(cfg, date(2026, 6, 21))
        assert result is not None
        assert result.hour == 7

    def test_calculate_target_time_solar_altitude_none(self):
        from obs.adapters.zeitschaltuhr.adapter import TimeRef, ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        with patch.object(adapter, "_get_solar_altitude_time", return_value=None):
            cfg = ZeitschaltuhrBindingConfig(time_ref=TimeRef.SOLAR_ALTITUDE, solar_altitude_deg=30.0)
            result = adapter._calculate_target_time(cfg, date(2026, 6, 21))
        assert result is None


class TestZeitschaltuhrResolveTimezone:
    @pytest.mark.asyncio
    async def test_uses_explicit_timezone(self):
        import zoneinfo

        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrAdapter

        bus = MagicMock()
        bus.publish = AsyncMock()
        adapter = ZeitschaltuhrAdapter(event_bus=bus, config={"timezone": "Europe/Zurich"})
        tz = await adapter._resolve_timezone()
        assert isinstance(tz, zoneinfo.ZoneInfo)
        assert str(tz) == "Europe/Zurich"

    @pytest.mark.asyncio
    async def test_falls_back_to_utc_for_invalid_timezone(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrAdapter

        bus = MagicMock()
        bus.publish = AsyncMock()
        adapter = ZeitschaltuhrAdapter(event_bus=bus, config={"timezone": "Invalid/Timezone"})
        tz = await adapter._resolve_timezone()
        assert tz == UTC

    @pytest.mark.asyncio
    async def test_reads_timezone_from_db_when_empty(self):
        import zoneinfo

        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrAdapter

        bus = MagicMock()
        bus.publish = AsyncMock()
        adapter = ZeitschaltuhrAdapter(event_bus=bus, config={"timezone": ""})

        fake_row = _make_row(value="Europe/Berlin")
        with patch("obs.db.database.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.fetchone = AsyncMock(return_value=fake_row)
            mock_get_db.return_value = mock_db
            tz = await adapter._resolve_timezone()

        assert isinstance(tz, zoneinfo.ZoneInfo)
        assert str(tz) == "Europe/Berlin"

    @pytest.mark.asyncio
    async def test_falls_back_to_utc_when_no_db_row(self):
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrAdapter

        bus = MagicMock()
        bus.publish = AsyncMock()
        adapter = ZeitschaltuhrAdapter(event_bus=bus, config={"timezone": ""})

        with patch("obs.db.database.get_db") as mock_get_db:
            mock_db = MagicMock()
            mock_db.fetchone = AsyncMock(return_value=None)
            mock_get_db.return_value = mock_db
            tz = await adapter._resolve_timezone()

        from zoneinfo import ZoneInfo

        assert tz == ZoneInfo("UTC")


class TestZeitschaltuhrParseVacationPeriod:
    def test_valid_period(self):
        adapter = _make_zs_adapter(vacation_1_start="2026-07-01", vacation_1_end="2026-07-31")
        start, end = adapter._parse_vacation_period(1)
        assert start == date(2026, 7, 1)
        assert end == date(2026, 7, 31)

    def test_empty_period_returns_none(self):
        adapter = _make_zs_adapter()
        start, end = adapter._parse_vacation_period(1)
        assert start is None
        assert end is None

    def test_invalid_date_returns_none(self):
        adapter = _make_zs_adapter(vacation_2_start="not-a-date", vacation_2_end="also-bad")
        start, end = adapter._parse_vacation_period(2)
        assert start is None
        assert end is None


class TestZeitschaltuhrDateWindowGate:
    def test_should_fire_blocked_by_date_window(self):
        from obs.adapters.zeitschaltuhr.adapter import TimeRef, ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        cfg = ZeitschaltuhrBindingConfig(
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            date_window_enabled=True,
            date_window_from="05-01",
            date_window_to="08-31",
        )
        now = datetime(2026, 4, 1, 8, 0, tzinfo=UTC)
        assert adapter._should_fire(cfg, now) is False

    def test_should_fire_allowed_by_date_window(self):
        from obs.adapters.zeitschaltuhr.adapter import TimeRef, ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        cfg = ZeitschaltuhrBindingConfig(
            time_ref=TimeRef.ABSOLUTE,
            hour=8,
            minute=0,
            date_window_enabled=True,
            date_window_from="05-01",
            date_window_to="08-31",
        )
        now = datetime(2026, 6, 15, 8, 0, tzinfo=UTC)
        assert adapter._should_fire(cfg, now) is True


class TestZeitschaltuhrHolidayTypeEveryHour:
    def test_holiday_type_every_hour(self):
        from obs.adapters.zeitschaltuhr.adapter import TimerType, ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        d = date(2026, 1, 1)
        adapter._hol = {d: "Neujahr"}
        cfg = ZeitschaltuhrBindingConfig(timer_type=TimerType.HOLIDAY, every_hour=True, minute=30)
        now = datetime(2026, 1, 1, 9, 30, tzinfo=UTC)
        assert adapter._should_fire(cfg, now) is True
        now_wrong = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
        assert adapter._should_fire(cfg, now_wrong) is False

    def test_holiday_type_vacation_only_mode_fires_when_also_vacation(self):
        from obs.adapters.zeitschaltuhr.adapter import HolidayMode, TimerType, ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter(vacation_1_start="2026-01-01", vacation_1_end="2026-01-07")
        d = date(2026, 1, 1)
        adapter._hol = {d: "Neujahr"}
        cfg = ZeitschaltuhrBindingConfig(
            timer_type=TimerType.HOLIDAY,
            vacation_mode=HolidayMode.ONLY,
            time_ref="absolute",
            hour=8,
            minute=0,
        )
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        assert adapter._should_fire(cfg, now) is True

    def test_holiday_type_not_in_vacation_with_only_mode(self):
        from obs.adapters.zeitschaltuhr.adapter import HolidayMode, TimerType, ZeitschaltuhrBindingConfig

        adapter = _make_zs_adapter()
        d = date(2026, 1, 1)
        adapter._hol = {d: "Neujahr"}
        cfg = ZeitschaltuhrBindingConfig(
            timer_type=TimerType.HOLIDAY,
            vacation_mode=HolidayMode.ONLY,
            time_ref="absolute",
            hour=8,
            minute=0,
        )
        now = datetime(2026, 1, 1, 8, 0, tzinfo=UTC)
        assert adapter._should_fire(cfg, now) is False


class TestGetHolidaysForYear:
    def test_returns_sorted_list(self):
        adapter = _make_zs_adapter(custom_holidays=["12-25:Weihnachten", "01-01:Neujahr"])
        result = adapter.get_holidays_for_year(2026)
        dates = [h["date"] for h in result]
        assert dates == sorted(dates)

    def test_custom_only_when_no_library(self):
        adapter = _make_zs_adapter(custom_holidays=["01-01:Neujahr"])
        with patch.dict("sys.modules", {"holidays": None}):
            result = adapter.get_holidays_for_year(2026)
        # At least the custom entry should be present
        assert any(h["name"] == "Neujahr" for h in result)


class TestZeitschaltuhrBuildHolidays:
    def test_returns_dict_with_custom_holidays(self):
        adapter = _make_zs_adapter(custom_holidays=["01-01:Neujahr", "12-25:Weihnachten"])
        hols = adapter._build_holidays()
        assert isinstance(hols, dict)
        assert any(d.month == 1 and d.day == 1 for d in hols)

    def test_handles_import_error_gracefully(self):
        adapter = _make_zs_adapter()
        with patch.dict("sys.modules", {"holidays": None}):
            hols = adapter._build_holidays()
        assert isinstance(hols, dict)

    def test_language_fallback_on_exception(self):
        """When holidays library raises for the language kwarg, retries without it."""
        adapter = _make_zs_adapter(holiday_language="invalid_lang")

        class _FakeHolidays:
            def __init__(self):
                self._data = {date(2026, 1, 1): "Neujahr"}

            def items(self):
                return self._data.items()

        call_count = {"n": 0}

        def _country_holidays(country, **kwargs):
            call_count["n"] += 1
            if "language" in kwargs:
                raise Exception("language not supported")
            return _FakeHolidays()

        mock_holidays = MagicMock()
        mock_holidays.country_holidays = _country_holidays
        with patch.dict("sys.modules", {"holidays": mock_holidays}):
            hols = adapter._build_holidays()

        assert isinstance(hols, dict)


# ===========================================================================
# obs/adapters/snmp/adapter.py — missing lines
# ===========================================================================


class TestSnmpCoerceValueNativePython:
    def test_native_int_auto(self):
        from obs.adapters.snmp.adapter import _coerce_value

        assert _coerce_value(42, "auto") == 42

    def test_native_int_float_cast(self):
        from obs.adapters.snmp.adapter import _coerce_value

        assert _coerce_value(42, "float") == 42.0

    def test_native_int_hex_cast(self):
        from obs.adapters.snmp.adapter import _coerce_value

        result = _coerce_value(255, "hex")
        assert result == hex(255)

    def test_native_int_string_cast(self):
        from obs.adapters.snmp.adapter import _coerce_value

        assert _coerce_value(42, "string") == "42"

    def test_native_int_counter_cast(self):
        from obs.adapters.snmp.adapter import _coerce_value

        assert _coerce_value(1000, "counter") == 1000

    def test_native_int_gauge_cast(self):
        from obs.adapters.snmp.adapter import _coerce_value

        assert _coerce_value(500, "gauge") == 500

    def test_native_int_timeticks_cast(self):
        from obs.adapters.snmp.adapter import _coerce_value

        assert _coerce_value(3600, "timeticks") == 3600

    def test_bytes_hex(self):
        from obs.adapters.snmp.adapter import _coerce_value

        result = _coerce_value(b"\xde\xad", "hex")
        assert result == "dead"

    def test_bytes_auto_hex(self):
        from obs.adapters.snmp.adapter import _coerce_value

        result = _coerce_value(b"\xca\xfe", "auto")
        assert result == "cafe"

    def test_bytes_string_decode(self):
        from obs.adapters.snmp.adapter import _coerce_value

        result = _coerce_value(b"hello", "string")
        assert result == "hello"

    def test_str_auto_numeric(self):
        from obs.adapters.snmp.adapter import _coerce_value

        assert _coerce_value("123", "auto") == 123

    def test_str_auto_float(self):
        from obs.adapters.snmp.adapter import _coerce_value

        assert abs(_coerce_value("3.14", "auto") - 3.14) < 0.001

    def test_str_auto_non_numeric_passthrough(self):
        from obs.adapters.snmp.adapter import _coerce_value

        assert _coerce_value("hello", "auto") == "hello"

    def test_str_explicit_string_passthrough(self):
        from obs.adapters.snmp.adapter import _coerce_value

        assert _coerce_value("hello", "string") == "hello"


class TestSnmpPrettyHelper:
    def test_pretty_with_prettyprint(self):
        from obs.adapters.snmp.adapter import _pretty

        v = MagicMock()
        v.prettyPrint.return_value = "hello"
        assert _pretty(v) == "hello"

    def test_pretty_without_prettyprint(self):
        from obs.adapters.snmp.adapter import _pretty

        assert _pretty(42) == "42"
        assert _pretty("text") == "text"


class TestSnmpBuildTransportTarget:
    @pytest.mark.asyncio
    async def test_uses_constructor_when_no_create(self):
        from obs.adapters.snmp.adapter import _build_transport_target

        fake_target = MagicMock()
        fake_cls = MagicMock(return_value=fake_target)
        # ensure no 'create' attribute
        if hasattr(fake_cls, "create"):
            del fake_cls.create

        symbols = {"UdpTransportTarget": fake_cls}
        result = await _build_transport_target(symbols, "192.168.1.1", 161, 5.0, 1)
        assert result == fake_target
        fake_cls.assert_called_with(("192.168.1.1", 161), timeout=5.0, retries=1)

    @pytest.mark.asyncio
    async def test_uses_async_create_when_available(self):
        from obs.adapters.snmp.adapter import _build_transport_target

        fake_target = MagicMock()

        async def _async_create(addr, *, timeout, retries):
            return fake_target

        fake_cls = MagicMock()
        fake_cls.create = _async_create

        symbols = {"UdpTransportTarget": fake_cls}
        result = await _build_transport_target(symbols, "192.168.1.1", 161, 5.0, 1)
        assert result == fake_target


class TestSnmpV3EmptySecurityName:
    def test_raises_when_security_name_empty(self):
        from obs.adapters.snmp.adapter import SnmpAdapter, SnmpAdapterConfig

        bus = MagicMock()
        bus.publish = AsyncMock()
        adapter = SnmpAdapter(event_bus=bus, config={"version": "3", "security_name": "", "security_level": "noAuthNoPriv"})
        adapter._snmp = {
            "_auth_map": {},
            "_priv_map": {},
            "_no_auth": MagicMock(),
            "_no_priv": MagicMock(),
            "UsmUserData": MagicMock(),
            "CommunityData": MagicMock(),
        }
        cfg = SnmpAdapterConfig(version="3", security_name="", security_level="noAuthNoPriv")
        with pytest.raises(ValueError, match="Security Name"):
            adapter._build_auth(cfg)


class TestSnmpEncodeWriteValueEdgeCases:
    def test_hex_value_from_valid_hex_string(self):
        try:
            from pysnmp.proto.rfc1902 import OctetString

            from obs.adapters.snmp.adapter import _encode_write_value

            result = _encode_write_value("deadbeef", "hex")
            assert isinstance(result, OctetString)
        except ImportError:
            pytest.skip("pysnmp not installed")

    def test_hex_invalid_string_falls_back_to_octetstring(self):
        try:
            from pysnmp.proto.rfc1902 import OctetString

            from obs.adapters.snmp.adapter import _encode_write_value

            result = _encode_write_value("not-hex!", "hex")
            assert isinstance(result, OctetString)
        except ImportError:
            pytest.skip("pysnmp not installed")

    def test_float_encoded_as_octetstring(self):
        try:
            from pysnmp.proto.rfc1902 import OctetString

            from obs.adapters.snmp.adapter import _encode_write_value

            result = _encode_write_value(3.14, "float")
            assert isinstance(result, OctetString)
        except ImportError:
            pytest.skip("pysnmp not installed")

    def test_gauge_type(self):
        try:
            from pysnmp.proto.rfc1902 import Integer32

            from obs.adapters.snmp.adapter import _encode_write_value

            result = _encode_write_value(42, "gauge")
            assert isinstance(result, Integer32)
        except ImportError:
            pytest.skip("pysnmp not installed")

    def test_int_auto_native(self):
        try:
            from pysnmp.proto.rfc1902 import Integer32

            from obs.adapters.snmp.adapter import _encode_write_value

            result = _encode_write_value(99, "auto")
            assert isinstance(result, Integer32)
        except ImportError:
            pytest.skip("pysnmp not installed")

    def test_string_auto_native(self):
        try:
            from pysnmp.proto.rfc1902 import OctetString

            from obs.adapters.snmp.adapter import _encode_write_value

            result = _encode_write_value("text", "auto")
            assert isinstance(result, OctetString)
        except ImportError:
            pytest.skip("pysnmp not installed")


class TestSnmpImportPysnmp:
    def test_returns_empty_dict_when_not_installed(self):
        from obs.adapters.snmp.adapter import _import_pysnmp

        with patch.dict(
            "sys.modules",
            {
                "pysnmp": None,
                "pysnmp.hlapi": None,
                "pysnmp.hlapi.v3arch": None,
                "pysnmp.hlapi.v3arch.asyncio": None,
                "pysnmp.hlapi.asyncio": None,
            },
        ):
            result = _import_pysnmp()
        assert result == {}


class TestSnmpWalkEdgeCases:
    @pytest.mark.asyncio
    async def test_walk_stops_on_error_status(self):
        from obs.adapters.snmp.adapter import SnmpAdapter

        bus = MagicMock()
        bus.publish = AsyncMock()

        snmp_symbols = {
            "SnmpEngine": MagicMock(return_value=MagicMock()),
            "CommunityData": MagicMock(),
            "UdpTransportTarget": MagicMock(return_value=MagicMock()),
            "ContextData": MagicMock(),
            "ObjectType": MagicMock(),
            "ObjectIdentity": MagicMock(),
            "_auth_map": {},
            "_priv_map": {},
            "_no_auth": MagicMock(),
            "_no_priv": MagicMock(),
        }

        error_status = MagicMock()
        error_status.prettyPrint.return_value = "noSuchObject"

        async def fake_next(*args, **kwargs):
            return (None, error_status, None, [])

        snmp_symbols["nextCmd"] = fake_next

        adapter = SnmpAdapter(event_bus=bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        results = await adapter.snmp_walk(host="192.168.1.1", oid="1.3.6.1.2.1")
        assert results == []

    @pytest.mark.asyncio
    async def test_walk_stops_on_empty_var_binds(self):
        from obs.adapters.snmp.adapter import SnmpAdapter

        bus = MagicMock()
        bus.publish = AsyncMock()

        snmp_symbols = {
            "SnmpEngine": MagicMock(return_value=MagicMock()),
            "CommunityData": MagicMock(),
            "UdpTransportTarget": MagicMock(return_value=MagicMock()),
            "ContextData": MagicMock(),
            "ObjectType": MagicMock(),
            "ObjectIdentity": MagicMock(),
            "_auth_map": {},
            "_priv_map": {},
            "_no_auth": MagicMock(),
            "_no_priv": MagicMock(),
        }

        async def fake_next_empty(*args, **kwargs):
            return (None, None, None, [])

        snmp_symbols["nextCmd"] = fake_next_empty

        adapter = SnmpAdapter(event_bus=bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        results = await adapter.snmp_walk(host="192.168.1.1", oid="1.3.6.1.2.1")
        assert results == []


class TestSnmpGetErrorStatus:
    @pytest.mark.asyncio
    async def test_snmp_get_raises_on_error_status(self):
        from obs.adapters.snmp.adapter import SnmpAdapter, SnmpBindingConfig

        bus = MagicMock()
        bus.publish = AsyncMock()

        error_status = MagicMock()
        error_status.prettyPrint.return_value = "noSuchObject"

        snmp_symbols = {
            "SnmpEngine": MagicMock(return_value=MagicMock()),
            "CommunityData": MagicMock(),
            "UdpTransportTarget": MagicMock(return_value=MagicMock()),
            "ContextData": MagicMock(),
            "ObjectType": MagicMock(),
            "ObjectIdentity": MagicMock(),
            "_auth_map": {"MD5": MagicMock()},
            "_priv_map": {"DES": MagicMock()},
            "_no_auth": MagicMock(),
            "_no_priv": MagicMock(),
        }

        async def fake_get(*args, **kwargs):
            return (None, error_status, MagicMock(__int__=MagicMock(return_value=0)), [])

        snmp_symbols["getCmd"] = fake_get

        adapter = SnmpAdapter(event_bus=bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        bc = SnmpBindingConfig(host="192.168.1.1", port=161, oid="1.3.6.1.2.1.1.1.0")
        with pytest.raises(RuntimeError):
            await adapter._snmp_get(bc)

    @pytest.mark.asyncio
    async def test_snmp_get_raises_on_error_indication(self):
        from obs.adapters.snmp.adapter import SnmpAdapter, SnmpBindingConfig

        bus = MagicMock()
        bus.publish = AsyncMock()

        error_indication = MagicMock()
        error_indication.__str__ = MagicMock(return_value="Timeout")

        snmp_symbols = {
            "SnmpEngine": MagicMock(return_value=MagicMock()),
            "CommunityData": MagicMock(),
            "UdpTransportTarget": MagicMock(return_value=MagicMock()),
            "ContextData": MagicMock(),
            "ObjectType": MagicMock(),
            "ObjectIdentity": MagicMock(),
            "_auth_map": {"MD5": MagicMock()},
            "_priv_map": {"DES": MagicMock()},
            "_no_auth": MagicMock(),
            "_no_priv": MagicMock(),
        }

        async def fake_get(*args, **kwargs):
            return (error_indication, None, None, [])

        snmp_symbols["getCmd"] = fake_get

        adapter = SnmpAdapter(event_bus=bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        bc = SnmpBindingConfig(host="192.168.1.1", port=161, oid="1.3.6.1.2.1.1.1.0")
        with pytest.raises(RuntimeError):
            await adapter._snmp_get(bc)


class TestSnmpSetErrorCases:
    @pytest.mark.asyncio
    async def test_snmp_set_raises_on_error_status(self):
        from obs.adapters.snmp.adapter import SnmpAdapter, SnmpBindingConfig

        bus = MagicMock()
        bus.publish = AsyncMock()

        error_status = MagicMock()
        error_status.prettyPrint.return_value = "noAccess"

        snmp_symbols = {
            "SnmpEngine": MagicMock(return_value=MagicMock()),
            "CommunityData": MagicMock(),
            "UdpTransportTarget": MagicMock(return_value=MagicMock()),
            "ContextData": MagicMock(),
            "ObjectType": MagicMock(),
            "ObjectIdentity": MagicMock(),
            "_auth_map": {"MD5": MagicMock()},
            "_priv_map": {"DES": MagicMock()},
            "_no_auth": MagicMock(),
            "_no_priv": MagicMock(),
        }

        async def fake_set(*args, **kwargs):
            return (None, error_status, MagicMock(__int__=MagicMock(return_value=0)), [])

        snmp_symbols["setCmd"] = fake_set

        adapter = SnmpAdapter(event_bus=bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        bc = SnmpBindingConfig(host="192.168.1.1", port=161, oid="1.3.6.1.4.1.1.0")
        with pytest.raises(RuntimeError):
            await adapter._snmp_set(bc, 42)

    @pytest.mark.asyncio
    async def test_snmp_set_raises_on_error_indication(self):
        from obs.adapters.snmp.adapter import SnmpAdapter, SnmpBindingConfig

        bus = MagicMock()
        bus.publish = AsyncMock()

        error_indication = MagicMock()
        error_indication.__str__ = MagicMock(return_value="Timeout")

        snmp_symbols = {
            "SnmpEngine": MagicMock(return_value=MagicMock()),
            "CommunityData": MagicMock(),
            "UdpTransportTarget": MagicMock(return_value=MagicMock()),
            "ContextData": MagicMock(),
            "ObjectType": MagicMock(),
            "ObjectIdentity": MagicMock(),
            "_auth_map": {"MD5": MagicMock()},
            "_priv_map": {"DES": MagicMock()},
            "_no_auth": MagicMock(),
            "_no_priv": MagicMock(),
        }

        async def fake_set(*args, **kwargs):
            return (error_indication, None, None, [])

        snmp_symbols["setCmd"] = fake_set

        adapter = SnmpAdapter(event_bus=bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        bc = SnmpBindingConfig(host="192.168.1.1", port=161, oid="1.3.6.1.4.1.1.0")
        with pytest.raises(RuntimeError):
            await adapter._snmp_set(bc, 42)


# ===========================================================================
# obs/adapters/homeassistant/adapter.py — missing lines
# ===========================================================================


def _make_ha_adapter(config: dict | None = None) -> Any:
    from obs.adapters.homeassistant.adapter import HaAdapterConfig, HomeAssistantAdapter

    bus = MagicMock()
    bus.publish = AsyncMock()
    cfg = config or {"host": "ha.local", "port": 8123, "token": "test-token", "ssl": False}
    adapter = HomeAssistantAdapter(event_bus=bus, config=cfg)
    adapter._cfg = HaAdapterConfig(**cfg)
    mock_client = MagicMock()
    mock_client.aclose = AsyncMock()
    adapter._http_client = mock_client
    adapter._bus = bus
    return adapter


class TestHaAdapterConnect:
    @pytest.mark.asyncio
    async def test_connect_without_websockets_package(self):
        from obs.adapters.homeassistant.adapter import HomeAssistantAdapter

        bus = MagicMock()
        bus.publish = AsyncMock()
        adapter = HomeAssistantAdapter(event_bus=bus, config={"host": "ha.local", "port": 8123, "token": "tok", "ssl": False})

        with patch.dict("sys.modules", {"websockets": None}):
            await adapter.connect()

        event = bus.publish.call_args[0][0]
        assert event.connected is False

    @pytest.mark.asyncio
    async def test_connect_with_ssl(self):
        from obs.adapters.homeassistant.adapter import HomeAssistantAdapter

        bus = MagicMock()
        bus.publish = AsyncMock()
        adapter = HomeAssistantAdapter(event_bus=bus, config={"host": "ha.local", "port": 8123, "token": "tok", "ssl": True})

        mock_client = MagicMock()
        mock_client.aclose = AsyncMock()

        with (
            patch.dict("sys.modules", {"websockets": MagicMock()}),
            patch("obs.adapters.homeassistant.adapter.httpx.AsyncClient", return_value=mock_client),
        ):
            await adapter.connect()

        assert adapter._cfg.ssl is True

    @pytest.mark.asyncio
    async def test_disconnect_clears_state(self):
        adapter = _make_ha_adapter()
        adapter._entity_map["sensor.temp"] = [MagicMock()]

        async def _dummy():
            await asyncio.sleep(9999)

        t = asyncio.create_task(_dummy())
        adapter._ws_task = t

        await adapter.disconnect()
        await asyncio.sleep(0)

        assert adapter._http_client is None
        assert adapter._ws_task is None
        assert adapter._entity_map == {}


class TestHaAdapterBindingsReloaded:
    @pytest.mark.asyncio
    async def test_source_bindings_go_into_entity_map(self):
        from tests.adapters.conftest import make_binding

        adapter = _make_ha_adapter()
        adapter._bindings = [
            make_binding({"entity_id": "sensor.temp"}, direction="SOURCE"),
            make_binding({"entity_id": "switch.fan"}, direction="BOTH"),
            make_binding({"entity_id": "sensor.dest_only"}, direction="DEST"),
        ]

        def _close_coro(coro, *, name=None):
            coro.close()
            return MagicMock()

        with patch("obs.adapters.homeassistant.adapter.asyncio.create_task", side_effect=_close_coro):
            await adapter._on_bindings_reloaded()

        assert "sensor.temp" in adapter._entity_map
        assert "switch.fan" in adapter._entity_map
        assert "sensor.dest_only" not in adapter._entity_map

    @pytest.mark.asyncio
    async def test_no_ws_task_when_no_source_bindings(self):
        from tests.adapters.conftest import make_binding

        adapter = _make_ha_adapter()
        adapter._bindings = [make_binding({"entity_id": "sensor.dest"}, direction="DEST")]

        with patch("obs.adapters.homeassistant.adapter.asyncio.create_task") as mock_task:
            await adapter._on_bindings_reloaded()
        mock_task.assert_not_called()

    @pytest.mark.asyncio
    async def test_cancels_old_ws_task_on_reload(self):
        from tests.adapters.conftest import make_binding

        adapter = _make_ha_adapter()

        async def _dummy():
            await asyncio.sleep(9999)

        old_task = asyncio.create_task(_dummy())
        adapter._ws_task = old_task

        adapter._bindings = [make_binding({"entity_id": "sensor.temp"}, direction="SOURCE")]

        def _close_coro(coro, *, name=None):
            coro.close()
            return MagicMock()

        with patch("obs.adapters.homeassistant.adapter.asyncio.create_task", side_effect=_close_coro):
            await adapter._on_bindings_reloaded()

        await asyncio.sleep(0)
        assert old_task.cancelled()

    @pytest.mark.asyncio
    async def test_invalid_binding_config_skipped(self):
        from tests.adapters.conftest import make_binding

        adapter = _make_ha_adapter()
        # Missing entity_id → invalid HaBindingConfig
        bad_binding = make_binding({}, direction="SOURCE")
        adapter._bindings = [bad_binding]

        def _close_coro(coro, *, name=None):
            coro.close()
            return MagicMock()

        with patch("obs.adapters.homeassistant.adapter.asyncio.create_task", side_effect=_close_coro):
            await adapter._on_bindings_reloaded()
        # Should not raise — bad binding is skipped
        assert len(adapter._entity_map) == 0

    @pytest.mark.asyncio
    async def test_cfg_none_returns_early(self):
        from tests.adapters.conftest import make_binding

        adapter = _make_ha_adapter()
        adapter._cfg = None  # simulate not connected
        adapter._bindings = [make_binding({"entity_id": "sensor.temp"}, direction="SOURCE")]

        with patch("obs.adapters.homeassistant.adapter.asyncio.create_task") as mock_task:
            await adapter._on_bindings_reloaded()

        mock_task.assert_not_called()
        assert len(adapter._entity_map) == 0


class TestHaAdapterOnStateChangedFormula:
    @pytest.mark.asyncio
    async def test_formula_applied(self):
        from tests.adapters.conftest import make_binding

        adapter = _make_ha_adapter()
        binding = make_binding({"entity_id": "sensor.temp"}, value_formula="x * 2")
        adapter._entity_map["sensor.temp"] = [binding]

        await adapter._on_state_changed(
            {
                "entity_id": "sensor.temp",
                "new_state": {"state": "10", "attributes": {}},
            }
        )

        event = adapter._bus.publish.call_args[0][0]
        assert event.value == pytest.approx(20.0)


class TestHaNextWsId:
    def test_increments(self):
        import obs.adapters.homeassistant.adapter as ha

        before = ha._ws_id_counter
        id1 = ha._next_ws_id()
        id2 = ha._next_ws_id()
        assert id2 == id1 + 1
        assert id1 == before + 1
