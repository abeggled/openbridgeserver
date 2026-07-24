"""Periodic WAL maintenance for the main SQLite database.

The main database runs in WAL mode (see ``obs/db/database.py``). Under continuous
history writes the ``obs.db-wal`` sidecar can grow without bound because the default
PASSIVE auto-checkpoint never truncates the file on disk. This scheduler periodically
forces a ``PRAGMA wal_checkpoint(TRUNCATE)``, mirroring what the ringbuffer already does
after each prune, so the WAL cannot fill the disk. See issue #908.
"""

from __future__ import annotations

import asyncio
import logging

from obs.db.database import Database

logger = logging.getLogger(__name__)

# How often to force a TRUNCATE WAL checkpoint on the main DB.
CHECKPOINT_INTERVAL_SECONDS = 300.0


class DatabaseMaintenanceScheduler:
    """Background task that periodically truncates the main DB's WAL file."""

    def __init__(self, db: Database, interval_seconds: float = CHECKPOINT_INTERVAL_SECONDS) -> None:
        self._db = db
        self._interval = interval_seconds
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="db-maintenance")
        logger.info("DB-Wartung gestartet (WAL-Checkpoint alle %.0fs).", self._interval)

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("DB-Wartung gestoppt.")

    async def _loop(self) -> None:
        # Checkpoint once up front, before the first sleep: a restart that recovers from
        # the exact full-disk WAL condition this guards against must reclaim space
        # immediately rather than waiting a full interval. See issue #908.
        while True:
            try:
                await self._db.checkpoint()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("DB-Wartung: WAL-Checkpoint fehlgeschlagen")
            try:
                await asyncio.sleep(self._interval)
            except asyncio.CancelledError:
                raise


_scheduler: DatabaseMaintenanceScheduler | None = None


def init_db_maintenance_scheduler(db: Database, interval_seconds: float = CHECKPOINT_INTERVAL_SECONDS) -> DatabaseMaintenanceScheduler:
    global _scheduler
    _scheduler = DatabaseMaintenanceScheduler(db, interval_seconds)
    _scheduler.start()
    return _scheduler


def get_db_maintenance_scheduler() -> DatabaseMaintenanceScheduler:
    if _scheduler is None:
        raise RuntimeError("DatabaseMaintenanceScheduler nicht initialisiert.")
    return _scheduler
