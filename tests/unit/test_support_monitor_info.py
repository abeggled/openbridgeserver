"""Support package monitor diagnostics tests."""

from __future__ import annotations

import json
from unittest.mock import patch

from obs.api.v1.support import _build_monitor_info


class _FakeDb:
    async def fetchone(self, _query, _params):
        return {
            "value": json.dumps(
                {
                    "enabled": False,
                    "max_entries": 123,
                    "max_file_size_bytes": 456,
                    "max_age": 789,
                }
            )
        }


async def test_build_monitor_info_preserves_disabled_config():
    with (
        patch("obs.ringbuffer.ringbuffer.is_ringbuffer_enabled", return_value=False),
        patch("obs.ringbuffer.ringbuffer.get_optional_ringbuffer", return_value=None),
    ):
        monitor = await _build_monitor_info(_FakeDb())

    assert monitor["available"] is True
    assert monitor["stats"]["enabled"] is False
    assert monitor["stats"]["max_entries"] == 123
    assert monitor["stats"]["max_file_size_bytes"] == 456
    assert monitor["stats"]["max_age"] == 789
    assert monitor["recent_sample_size"] == 0
    assert monitor["recent_source_adapter_counts"] == {}
    assert monitor["recent_quality_counts"] == {}
