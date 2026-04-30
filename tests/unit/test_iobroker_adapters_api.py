from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from obs.api.v1 import adapters as adapters_api
from obs.api.v1.adapters import IoBrokerImportRequest


class _FakeDb:
    def __init__(self, row: dict, binding_rows: list[dict] | None = None):
        self._row = row
        self._binding_rows = binding_rows or []

    async def fetchone(self, _query: str, _params: tuple):
        return self._row

    async def fetchall(self, query: str, _params: tuple):
        if "FROM adapter_bindings" in query:
            return self._binding_rows
        return []


@pytest.mark.asyncio
async def test_browse_states_uses_running_instance_even_if_connected_flag_is_false(
    monkeypatch,
):
    fake_instance = type("FakeIoBrokerInstance", (), {})()
    fake_instance.connected = False
    fake_instance.browse_states = AsyncMock(
        return_value=[
            {
                "id": "hue.0.Küche_3.on",
                "name": "Küche 3 On",
                "type": "boolean",
                "role": "switch.light",
                "read": True,
                "write": True,
                "value": False,
                "unit": None,
            }
        ]
    )

    monkeypatch.setattr(
        adapters_api.adapter_registry,
        "get_instance_by_id",
        lambda _instance_id: fake_instance,
    )

    result = await adapters_api.iobroker_browse_states(
        instance_id="96f4d53c-455d-47ff-a9d0-a9def24951ff",
        q="Küche",
        limit=10,
        _user="admin",
        db=_FakeDb({"adapter_type": "IOBROKER"}),
    )

    assert len(result) == 1
    assert result[0].id == "hue.0.Küche_3.on"
    fake_instance.browse_states.assert_awaited_once_with("Küche", 10)


@pytest.mark.asyncio
async def test_import_preview_uses_running_instance_even_if_connected_flag_is_false(
    monkeypatch,
):
    fake_instance = type("FakeIoBrokerInstance", (), {})()
    fake_instance.connected = False
    fake_instance.browse_states = AsyncMock(
        return_value=[
            {
                "id": "0_userdata.0.obs.home.eg.kueche.licht.on",
                "name": "Küche Licht",
                "type": "boolean",
                "role": "switch.light",
                "read": True,
                "write": True,
                "value": False,
                "unit": None,
            }
        ]
    )

    monkeypatch.setattr(
        adapters_api.adapter_registry,
        "get_instance_by_id",
        lambda _instance_id: fake_instance,
    )

    result = await adapters_api.iobroker_import_preview(
        instance_id="96f4d53c-455d-47ff-a9d0-a9def24951ff",
        body=IoBrokerImportRequest(
            prefix="Küche",
            direction="auto",
            tags=["eg", "kueche"],
            limit=10,
        ),
        _user="admin",
        db=_FakeDb({"adapter_type": "IOBROKER"}),
    )

    assert len(result.preview) == 1
    assert result.preview[0].state_id == "0_userdata.0.obs.home.eg.kueche.licht.on"
    assert result.preview[0].exists is False
    fake_instance.browse_states.assert_awaited_once_with("Küche", 10)
