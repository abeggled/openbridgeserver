"""
Shared fixtures and helpers for adapter unit tests.
"""
from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_bus():
    """A fake EventBus whose publish() is an AsyncMock."""
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def make_binding(
    config: dict,
    direction: str = "SOURCE",
    value_formula: str | None = None,
    value_map: dict | None = None,
    send_on_change: bool = False,
    send_throttle_ms: int | None = None,
    send_min_delta: float | None = None,
    send_min_delta_pct: float | None = None,
) -> MagicMock:
    """Factory for mock AdapterBinding objects."""
    b = MagicMock()
    b.id = uuid.uuid4()
    b.datapoint_id = uuid.uuid4()
    b.adapter_type = "test"
    b.adapter_instance_id = uuid.uuid4()
    b.config = config
    b.direction = direction
    b.value_formula = value_formula
    b.value_map = value_map
    b.send_on_change = send_on_change
    b.send_throttle_ms = send_throttle_ms
    b.send_min_delta = send_min_delta
    b.send_min_delta_pct = send_min_delta_pct
    return b
