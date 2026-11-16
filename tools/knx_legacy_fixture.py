"""Generate the pre-#1296 legacy KNX fixtures used by the upgrade test (seam S4).

Must run against the code of b765f253 (the last state before #1296), with this
checkout's ``tests`` package for the project variants::

    git archive b765f253 obs | tar -x -C /tmp/obs-b765f253
    PYTHONPATH=/tmp/obs-b765f253:. tools/with-venv python tools/knx_legacy_fixture.py

For the two-level and the free demo variant it imports the project with a KNX
adapter instance into a fresh database, records what the read endpoints return
("before") and dumps the rows of a small slice of the KNX tables to
``tests/fixtures/knx_legacy_<style>.json``. The slice keeps the demo device, its
communication objects and links, the function and its link, and the group
addresses/bindings/datapoints of six addresses.
"""

from __future__ import annotations

import asyncio
import io
import json
import pathlib
import tempfile
import uuid

from fastapi import UploadFile

from tests.knxproj_style_variants import CO_SWITCH_RAW, DEVICE_PA, NOTATION, knxproj_in_style

OUT = pathlib.Path(__file__).parent.parent / "tests" / "fixtures"
RAW_SLICE = range(2305, 2311)  # 1/1/1 … 1/1/6
TABLES = (
    "knx_group_addresses",
    "knx_devices",
    "knx_comm_objects",
    "knx_co_ga_links",
    "knx_locations",
    "knx_functions",
    "knx_function_ga_links",
    "knx_space_device_links",
    "adapter_instances",
    "datapoints",
    "adapter_bindings",
)


async def _generate(style: str) -> dict:
    from obs.api.v1 import knxproj as api
    from obs.api.v1.services.knx_traceability import build_datapoint_knx_context
    from obs.db.database import Database

    assert not hasattr(api, "parse_knxproj_with_style"), "run against b765f253, not against #1296"
    with tempfile.TemporaryDirectory() as tmp:
        db = Database(str(pathlib.Path(tmp) / "legacy.db"))
        await db.connect()
        now = "2026-01-01T00:00:00+00:00"
        await db.execute_and_commit(
            "INSERT INTO adapter_instances (id, adapter_type, name, config, enabled, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), "KNX", "KNX-Bestand", "{}", 0, now, now),
        )
        await api.import_knxproj_file(
            file=UploadFile(file=io.BytesIO(knxproj_in_style(style)), filename=f"{style}.knxproj"),
            request=None,
            password=None,
            adapter_name="KNX-Bestand",
            direction="SOURCE",
            hierarchy_modes=None,
            hierarchy_auto_link=True,
            hierarchy_replace_existing=True,
            _user="admin",
            db=db,
        )
        slice_addresses = [NOTATION_FOR[style](raw) for raw in RAW_SLICE]
        marks = ",".join("?" * len(slice_addresses))
        binding_rows = await db.fetchall(
            f"SELECT * FROM adapter_bindings WHERE json_extract(config, '$.group_address') IN ({marks})", slice_addresses
        )
        dp_ids = [row["datapoint_id"] for row in binding_rows]
        rows: dict[str, list[dict]] = {}
        for table in TABLES:
            if table == "knx_group_addresses":
                selected = await db.fetchall(f"SELECT * FROM {table} WHERE address IN ({marks})", slice_addresses)
            elif table == "adapter_bindings":
                selected = binding_rows
            elif table == "datapoints":
                selected = await db.fetchall(f"SELECT * FROM datapoints WHERE id IN ({','.join('?' * len(dp_ids))})", dp_ids)
            else:
                selected = await db.fetchall(f"SELECT * FROM {table}")
            rows[table] = [dict(row) for row in selected]

        devices = await api.list_knx_devices_for_group_address(ga=NOTATION[style][CO_SWITCH_RAW], page=0, size=50, _user="admin", db=db)
        device_dps = await api.get_knx_device_datapoints(pa=DEVICE_PA, _user="admin", db=db)
        contexts = [await build_datapoint_knx_context(uuid.UUID(dp_id), db) for dp_id in dp_ids]
        before = {
            "devices_for_switch_ga": [item.pa for item in devices.items],
            "device_datapoints": len(device_dps.datapoints),
            "contexts_with_name": sum(1 for ctx in contexts for ga in ctx.group_addresses if ga.name),
            "contexts_with_device": sum(1 for ctx in contexts for ga in ctx.group_addresses if ga.devices),
            "bindings": len(binding_rows),
        }
        await db.disconnect()
    return {"generated_with": "b765f253", "style": style, "before": before, "rows": rows}


def _two_level(raw: int) -> str:
    return f"{raw >> 11}/{raw & 0x7FF}"


NOTATION_FOR = {"TwoLevel": _two_level, "Free": str}


def main() -> None:
    for style in NOTATION_FOR:
        data = asyncio.run(_generate(style))
        path = OUT / f"knx_legacy_{style.lower()}.json"
        path.write_text(json.dumps(data, indent=1, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        print(path, data["before"], {table: len(rows) for table, rows in data["rows"].items()})


if __name__ == "__main__":
    main()
