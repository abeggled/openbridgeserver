#!/usr/bin/env python3
"""1-Wire owserver smoke test — issue #6

Exercises the real OneWireAdapter code path (obs/adapters/onewire/adapter.py)
against a live owserver instance. ElabNET provides no PBM documentation beyond
"plug & play", so this script is the practical way to find out what owserver
actually exposes for a given busmaster/sensor combination — connect, scan,
read every discovered property once, and optionally test a single write.

Usage:
    tools/with-venv python tools/onewire_smoke_test.py --host localhost --port 4304
    tools/with-venv python tools/onewire_smoke_test.py --write 29.1122334455AA PIO.0 1

Requires a running owserver reachable at --host/--port (default localhost:4304).
Does not touch the OBS database or event bus — this is a standalone diagnostic.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from obs.adapters.onewire.adapter import OneWireAdapter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
logger = logging.getLogger("onewire-smoke-test")


class _NullBus:
    async def publish(self, event) -> None:
        pass


def _binding(sensor_id: str, property_name: str):
    return SimpleNamespace(id=f"{sensor_id}/{property_name}", config={"sensor_id": sensor_id, "property": property_name})


async def _scan_and_read(adapter: OneWireAdapter) -> None:
    sensors = await adapter.browse_sensors()
    if not sensors:
        logger.warning("owserver reported zero devices — check USB/PBM wiring and that owserver sees the bus")
        return

    logger.info("Found %d device(s):", len(sensors))
    for sensor in sensors:
        alias = f" ({sensor['alias']})" if sensor["alias"] else ""
        logger.info("  %s%s — family %s — properties: %s", sensor["rom_id"], alias, sensor["family"], ", ".join(sensor["properties"]))

    logger.info("Reading every discovered property once:")
    for sensor in sensors:
        for prop in sensor["properties"]:
            binding = _binding(sensor["rom_id"], prop)
            value = await adapter.read(binding)
            logger.info("  %s/%s = %r", sensor["rom_id"], prop, value)


async def _test_write(adapter: OneWireAdapter, rom_id: str, prop: str, raw_value: str) -> None:
    binding = _binding(rom_id, prop)
    before = await adapter.read(binding)
    logger.info("Before write: %s/%s = %r", rom_id, prop, before)

    await adapter.write(binding, raw_value)
    after = await adapter.read(binding)
    logger.info("After write:  %s/%s = %r", rom_id, prop, after)

    if before == after:
        logger.warning("Value did not change — property may be read-only, or owserver rejected the write (check logs above).")


async def _main(args: argparse.Namespace) -> int:
    adapter = OneWireAdapter(event_bus=_NullBus(), config={"host": args.host, "port": args.port})
    await adapter.connect()
    if not adapter.connected:
        logger.error("Could not connect to owserver at %s:%d (detail: %s)", args.host, args.port, adapter.last_detail)
        return 1

    try:
        if args.write:
            rom_id, prop, raw_value = args.write
            if not args.yes:
                confirm = input(f"About to write {raw_value!r} to {rom_id}/{prop} on real hardware. Continue? [y/N] ")
                if confirm.strip().lower() != "y":
                    logger.info("Aborted.")
                    return 0
            await _test_write(adapter, rom_id, prop, raw_value)
        else:
            await _scan_and_read(adapter)
    finally:
        await adapter.disconnect()

    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default="localhost", help="owserver host (default: localhost)")
    parser.add_argument("--port", type=int, default=4304, help="owserver port (default: 4304)")
    parser.add_argument(
        "--write",
        nargs=3,
        metavar=("ROM_ID", "PROPERTY", "VALUE"),
        help="test a single write instead of scanning, e.g. --write 29.1122334455AA PIO.0 1",
    )
    parser.add_argument("--yes", action="store_true", help="skip the write confirmation prompt")
    return parser.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(_main(_parse_args())))
