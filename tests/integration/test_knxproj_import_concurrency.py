"""Exercise the complete HTTP import against concurrent real DB consumers."""

import asyncio
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import UUID

from obs.api.v1 import knxproj as knxproj_api
from obs.core.event_bus import DataValueEvent
from obs.core.registry import get_registry
from obs.db.database import get_db


async def test_http_import_with_parallel_persistence_and_reads(client, auth_headers, monkeypatch):
    db = get_db()
    registry = get_registry()
    response = await client.post(
        "/api/v1/datapoints/",
        json={"name": "KNX import concurrency fixture", "data_type": "BOOLEAN"},
        headers=auth_headers,
    )
    assert response.status_code == 201
    point_id = response.json()["id"]
    started = asyncio.Event()
    tasks = []
    original_execute = db.execute
    elapsed = []

    async def persist():
        started.set()
        await registry.handle_value_event(DataValueEvent(datapoint_id=UUID(point_id), value=True, quality="good", source_adapter="synthetic"))

    async def read():
        return await client.get("/api/v1/knxproj/devices", headers=auth_headers)

    async def execute(sql, params=()):
        result = await original_execute(sql, params)
        if sql == "SAVEPOINT knx_device_snapshot":
            begin = time.monotonic()
            tasks.extend([asyncio.create_task(persist()), asyncio.create_task(read())])
            await started.wait()
            # Give both consumers a turn while the import owns its transaction.
            await asyncio.sleep(0)
            elapsed.append(begin)
        return result

    monkeypatch.setattr(db, "execute", execute)
    # The repository demo contains GAs but no usable device snapshot.
    # Supply a realistic snapshot size; the GA/location/trade parsers stay real.
    devices = [
        SimpleNamespace(
            identifier=f"fixture-{i}",
            individual_address=f"1.1.{i + 1}",
            name="fixture",
            description="",
            manufacturer_name="",
            order_number="",
            application="",
            space_id=None,
        )
        for i in range(30)
    ]
    objects = [
        SimpleNamespace(
            identifier=f"co-{i}",
            device_address=devices[i % 30].individual_address,
            number=i,
            name="fixture",
            text="",
            function_text="",
            dpts=["DPT1.001"],
        )
        for i in range(551)
    ]
    links = [SimpleNamespace(comm_object_id=f"co-{i % 551}", ga_address=f"1/1/{i % 200}") for i in range(572)]
    monkeypatch.setattr(knxproj_api, "parse_knxproj_devices", lambda *_args: (devices, objects, links))
    demo = Path(__file__).parents[2] / "tools/Demo-Test-Projekt-2026-04-06-17-18.knxproj"
    try:
        response = await asyncio.wait_for(
            client.post(
                "/api/v1/knxproj/import",
                files={"file": ("demo.knxproj", demo.read_bytes(), "application/octet-stream")},
                headers=auth_headers,
            ),
            timeout=30,
        )
        assert response.status_code == 200, response.text
        assert response.json()["imported"] > 0
        assert len(elapsed) == 1
        results = await asyncio.wait_for(asyncio.gather(*tasks), 5)
        assert results[1].status_code == 200
        assert (await db.fetchone("SELECT COUNT(*) AS n FROM knx_devices"))["n"] == 30
        assert (await db.fetchone("SELECT COUNT(*) AS n FROM knx_comm_objects"))["n"] == 551
        assert await db.fetchone("SELECT value FROM datapoint_last_values WHERE datapoint_id=?", (point_id,))
        assert not db._write_lock.locked()
        print(f"Snapshot plus waiting consumers: {time.monotonic() - elapsed[0]:.3f}s")
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await client.delete(f"/api/v1/datapoints/{point_id}", headers=auth_headers)
        async with db.transaction():
            await db.execute("DELETE FROM knx_devices WHERE id LIKE 'fixture-%'")
