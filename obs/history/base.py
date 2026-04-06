"""
HistoryPlugin ABC — Phase 5
"""
from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class HistoryPlugin(ABC):
    """
    Abstract base for history backends (SQLite, InfluxDB, TimescaleDB, ...).
    Implement all three methods to add a new backend.
    """

    @abstractmethod
    async def write(
        self,
        datapoint_id: uuid.UUID,
        value: Any,
        unit: str | None,
        quality: str,
        ts: datetime | None = None,
        source_adapter: str | None = None,
    ) -> None:
        """Persist a single value."""
        ...

    @abstractmethod
    async def query(
        self,
        datapoint_id: uuid.UUID,
        from_ts: datetime,
        to_ts: datetime,
        limit: int = 1000,
    ) -> list[dict]:
        """
        Return raw values in [from_ts, to_ts].
        Each dict: {ts: str, v: Any, u: str|None, q: str}
        """
        ...

    @abstractmethod
    async def aggregate(
        self,
        datapoint_id: uuid.UUID,
        fn: str,        # avg | min | max | last
        interval: str,  # 1m | 5m | 15m | 30m | 1h | 6h | 12h | 1d
        from_ts: datetime,
        to_ts: datetime,
    ) -> list[dict]:
        """
        Return aggregated values bucketed by *interval*.
        Each dict: {bucket: str, v: float|Any}
        """
        ...
