from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from obs.api.v1 import adapters as adapters_api
from obs.api.v1.adapters import OneWireAliasRequest


class _FakeDb:
    def __init__(self, row: dict | None):
        self._row = row
        self.execute_and_commit = AsyncMock()

    async def fetchone(self, _query: str, _params: tuple):
        return self._row


INSTANCE_ID = "96f4d53c-455d-47ff-a9d0-a9def24951ff"


# ---------------------------------------------------------------------------
# onewire_browse_sensors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_browse_sensors_returns_scan_result(monkeypatch):
    fake_instance = type("FakeOneWireInstance", (), {})()
    fake_instance.browse_sensors = AsyncMock(
        return_value=[
            {"rom_id": "28.4B057F0A1C10", "family": "28", "properties": ["temperature"], "alias": "Gästebad"},
        ],
    )
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: fake_instance)

    result = await adapters_api.onewire_browse_sensors(
        instance_id=INSTANCE_ID,
        _user="admin",
        db=_FakeDb({"adapter_type": "ONEWIRE"}),
    )

    assert len(result) == 1
    assert result[0].rom_id == "28.4B057F0A1C10"
    assert result[0].alias == "Gästebad"
    fake_instance.browse_sensors.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_browse_sensors_404_when_instance_missing():
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.onewire_browse_sensors(
            instance_id=INSTANCE_ID,
            _user="admin",
            db=_FakeDb(None),
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_browse_sensors_400_when_wrong_adapter_type():
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.onewire_browse_sensors(
            instance_id=INSTANCE_ID,
            _user="admin",
            db=_FakeDb({"adapter_type": "MQTT"}),
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_browse_sensors_503_when_instance_not_running(monkeypatch):
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: None)

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.onewire_browse_sensors(
            instance_id=INSTANCE_ID,
            _user="admin",
            db=_FakeDb({"adapter_type": "ONEWIRE"}),
        )
    assert exc_info.value.status_code == 503


@pytest.mark.asyncio
async def test_browse_sensors_501_when_not_implemented(monkeypatch):
    fake_instance = type("FakeOneWireInstance", (), {})()  # no browse_sensors attribute
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: fake_instance)

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.onewire_browse_sensors(
            instance_id=INSTANCE_ID,
            _user="admin",
            db=_FakeDb({"adapter_type": "ONEWIRE"}),
        )
    assert exc_info.value.status_code == 501


@pytest.mark.asyncio
async def test_browse_sensors_503_when_scan_raises(monkeypatch):
    fake_instance = type("FakeOneWireInstance", (), {})()
    fake_instance.browse_sensors = AsyncMock(side_effect=RuntimeError("owserver unreachable"))
    monkeypatch.setattr(adapters_api.adapter_registry, "get_instance_by_id", lambda _id: fake_instance)

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.onewire_browse_sensors(
            instance_id=INSTANCE_ID,
            _user="admin",
            db=_FakeDb({"adapter_type": "ONEWIRE"}),
        )
    assert exc_info.value.status_code == 503


# ---------------------------------------------------------------------------
# onewire_set_alias
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_alias_merges_into_empty_aliases(monkeypatch):
    restart = AsyncMock()
    monkeypatch.setattr(adapters_api.adapter_registry, "restart_instance", restart)
    db = _FakeDb({"adapter_type": "ONEWIRE", "config": json.dumps({"host": "localhost", "port": 4304})})

    with patch("obs.core.event_bus.get_event_bus", return_value=AsyncMock()):
        result = await adapters_api.onewire_set_alias(
            instance_id=INSTANCE_ID,
            body=OneWireAliasRequest(rom_id="28.4B057F0A1C10", label="Gästebad Estrich"),
            _user="admin",
            db=db,
        )

    assert result.rom_id == "28.4B057F0A1C10"
    assert result.label == "Gästebad Estrich"
    saved_config = json.loads(db.execute_and_commit.call_args[0][1][0])
    assert saved_config["aliases"] == {"28.4B057F0A1C10": "Gästebad Estrich"}
    restart.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_alias_preserves_existing_aliases(monkeypatch):
    monkeypatch.setattr(adapters_api.adapter_registry, "restart_instance", AsyncMock())
    existing_config = json.dumps({"host": "localhost", "port": 4304, "aliases": {"10.AA": "Keller"}})
    db = _FakeDb({"adapter_type": "ONEWIRE", "config": existing_config})

    with patch("obs.core.event_bus.get_event_bus", return_value=AsyncMock()):
        await adapters_api.onewire_set_alias(
            instance_id=INSTANCE_ID,
            body=OneWireAliasRequest(rom_id="28.BB", label="Bad"),
            _user="admin",
            db=db,
        )

    saved_config = json.loads(db.execute_and_commit.call_args[0][1][0])
    assert saved_config["aliases"] == {"10.AA": "Keller", "28.BB": "Bad"}


@pytest.mark.asyncio
async def test_set_alias_404_when_instance_missing():
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.onewire_set_alias(
            instance_id=INSTANCE_ID,
            body=OneWireAliasRequest(rom_id="28.AA", label="x"),
            _user="admin",
            db=_FakeDb(None),
        )
    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_set_alias_400_when_wrong_adapter_type():
    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.onewire_set_alias(
            instance_id=INSTANCE_ID,
            body=OneWireAliasRequest(rom_id="28.AA", label="x"),
            _user="admin",
            db=_FakeDb({"adapter_type": "MQTT", "config": "{}"}),
        )
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_set_alias_422_on_invalid_merged_config(monkeypatch):
    from obs.adapters.onewire.adapter import OneWireAdapter

    # port is not int-coercible -> OneWireAdapterConfig(**config) validation fails
    monkeypatch.setattr(adapters_api.adapter_registry, "get_class", lambda _t: OneWireAdapter)
    db = _FakeDb({"adapter_type": "ONEWIRE", "config": json.dumps({"port": "not-a-port"})})

    with pytest.raises(HTTPException) as exc_info:
        await adapters_api.onewire_set_alias(
            instance_id=INSTANCE_ID,
            body=OneWireAliasRequest(rom_id="28.AA", label="x"),
            _user="admin",
            db=db,
        )
    assert exc_info.value.status_code == 422
