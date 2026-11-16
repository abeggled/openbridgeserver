"""Regression tests for snapshot isolation, concurrent persistence and rollback.

All cases use disposable file databases and no adapters or network.
"""

import asyncio
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock, patch

import obs.api.v1.knxproj as api
from obs.core.event_bus import DataValueEvent
from obs.core.registry import DataPointRegistry, ValueState
from obs.db.database import Database
from obs.models.datapoint import DataPoint


class TestAtomicSnapshot(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="obs-atomic-probe-")
        self.db = Database(str(Path(self.temp.name) / "isolated.sqlite"))
        await self.db.connect()
        self.tasks = []
        self.registry = DataPointRegistry(self.db, NS(publish_value=AsyncMock()), None)
        self.dp = DataPoint(name="synthetic fixture", data_type="BOOLEAN")
        await self.registry.insert(self.dp)
        self.registry._points[self.dp.id] = self.dp
        self.registry._values[self.dp.id] = ValueState()
        await self.db.execute("INSERT INTO knx_devices(id,individual_address,imported_at) VALUES('old-device','1.1.1','old')")
        await self.db.execute("INSERT INTO knx_comm_objects(id,device_id,imported_at) VALUES('old-co','old-device','old')")
        await self.db.execute("INSERT INTO knx_group_addresses(address) VALUES('1/1/1')")
        await self.db.execute("INSERT INTO knx_co_ga_links VALUES('old-co','1/1/1')")
        await self.db.execute("INSERT INTO knx_locations(id,imported_at) VALUES('space','old')")
        await self.db.commit()
        self.parsed = (
            [
                NS(
                    identifier="new-device",
                    individual_address="1.1.2",
                    name="fixture",
                    description="",
                    manufacturer_name="",
                    order_number="",
                    application="",
                    space_id="space",
                )
            ],
            [NS(identifier="new-co", device_address="1.1.2", number=1, name="fixture", text="", function_text="", dpts=["DPT1.001"])],
            [NS(comm_object_id="new-co", ga_address="1/1/2")],
        )
        self.writer_started = asyncio.Event()
        self.reader_started = asyncio.Event()
        self.reader = None
        self.writer = None
        self.inject_error = None
        self.visibility_during_delete = None
        original_execute = self.db.execute
        original_many = self.db.executemany

        async def writer():
            self.writer_started.set()
            await self.registry.handle_value_event(DataValueEvent(datapoint_id=self.dp.id, value=True, quality="good", source_adapter="synthetic"))

        async def reader():
            self.reader_started.set()
            return [r["id"] for r in await self.db.fetchall("SELECT id FROM knx_devices ORDER BY id")]

        async def execute(sql, params=()):
            result = await original_execute(sql, params)
            if sql == "SAVEPOINT knx_device_snapshot":
                self.writer = asyncio.create_task(writer())
                self.tasks.append(self.writer)
                await self.writer_started.wait()
            if sql == "DELETE FROM knx_devices":
                self.reader = asyncio.create_task(reader())
                self.tasks.append(self.reader)
                await self.reader_started.wait()
                done, _ = await asyncio.wait({self.reader}, timeout=0.02)
                self.visibility_during_delete = self.reader.result() if done else "waiting_for_snapshot"
            return result

        async def executemany(sql, params):
            if self.inject_error and "INSERT INTO knx_comm_objects" in sql:
                raise self.inject_error
            return await original_many(sql, params)

        self.db.execute = execute
        self.db.executemany = executemany

    async def asyncTearDown(self):
        for t in self.tasks:
            if not t.done():
                t.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        if self.db._legacy_write_owner is asyncio.current_task():
            await self.db.rollback()
        await self.db.disconnect()
        self.temp.cleanup()

    async def run_import(self):
        with patch.object(api, "parse_knxproj_devices", return_value=self.parsed):
            function = api._import_knx_devices_and_comm_objects
            return await function(file_bytes=b"synthetic", password=None, db=self.db, now=datetime.now(UTC).isoformat())

    async def finish_background(self):
        await asyncio.wait_for(asyncio.gather(*self.tasks), 2)

    async def assert_snapshot(self, expected):
        self.assertEqual([r["id"] for r in await self.db.fetchall("SELECT id FROM knx_devices")], [expected + "-device"])
        self.assertEqual([r["id"] for r in await self.db.fetchall("SELECT id FROM knx_comm_objects")], [expected + "-co"])
        self.assertEqual([r["comm_object_id"] for r in await self.db.fetchall("SELECT comm_object_id FROM knx_co_ga_links")], [expected + "-co"])

    async def test_scoped_import_completes_with_real_value_persistence_and_reader(self):
        self.assertEqual(await asyncio.wait_for(self.run_import(), 3), (1, 1))
        await self.finish_background()
        self.assertEqual(self.visibility_during_delete, "waiting_for_snapshot")
        self.assertEqual(self.reader.result(), ["new-device"])
        await self.assert_snapshot("new")
        row = await self.db.fetchone("SELECT value FROM datapoint_last_values WHERE datapoint_id=?", (str(self.dp.id),))
        self.assertIsNotNone(row)

    async def test_snapshot_failure_rolls_back_and_releases_background(self):
        self.inject_error = RuntimeError("synthetic failure")
        with self.assertRaisesRegex(RuntimeError, "synthetic failure"):
            await asyncio.wait_for(self.run_import(), 3)
        await self.finish_background()
        self.assertEqual(self.reader.result(), ["old-device"])
        await self.assert_snapshot("old")
        self.assertFalse(self.db._write_lock.locked())
        self.assertFalse(self.db._memory_operation_lock.locked())

    async def test_snapshot_cancellation_rolls_back_and_releases_background(self):
        self.inject_error = asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await asyncio.wait_for(self.run_import(), 3)
        await self.finish_background()
        self.assertEqual(self.reader.result(), ["old-device"])
        await self.assert_snapshot("old")
        self.assertFalse(self.db._write_lock.locked())
        self.assertFalse(self.db._memory_operation_lock.locked())

    async def test_nested_outer_transaction_keeps_commit_boundary(self):
        with self.assertRaisesRegex(RuntimeError, "outer failure"):
            async with self.db.transaction():
                await self.run_import()
                await self.assert_snapshot("new")
                raise RuntimeError("outer failure")
        await self.finish_background()
        self.assertEqual(self.reader.result(), ["old-device"])
        await self.assert_snapshot("old")

    async def test_parser_runs_outside_snapshot_transaction(self):
        seen = []

        def parser(*args):
            seen.append(self.db._write_lock.locked())
            return self.parsed

        with patch.object(api, "parse_knxproj_devices", side_effect=parser):
            function = api._import_knx_devices_and_comm_objects
            await function(file_bytes=b"synthetic", password=None, db=self.db, now=datetime.now(UTC).isoformat())
        await self.finish_background()
        self.assertEqual(seen, [False])

    async def test_empty_snapshot_is_committed(self):
        self.parsed = ([], [], [])
        self.assertEqual(await self.run_import(), (0, 0))
        await self.finish_background()
        self.assertEqual(await self.db.fetchall("SELECT id FROM knx_devices"), [])
        self.assertEqual(await self.db.fetchall("SELECT id FROM knx_comm_objects"), [])

    async def test_unknown_space_does_not_create_link(self):
        self.parsed[0][0].space_id = "missing-space"
        self.assertEqual(await self.run_import(), (1, 1))
        await self.finish_background()
        self.assertEqual(await self.db.fetchall("SELECT * FROM knx_space_device_links"), [])
