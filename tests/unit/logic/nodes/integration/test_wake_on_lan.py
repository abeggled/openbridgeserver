from __future__ import annotations

from obs.logic.nodes.integration.wake_on_lan import NODE_TYPE


def test_new_blocks_fire_on_every_trigger_event():
    """Issue #1274: freshly placed blocks default to event triggering."""
    assert NODE_TYPE.config_schema["trigger_mode"]["enum"] == ["event", "edge"]
    assert NODE_TYPE.config_schema["trigger_mode"]["default"] == "event"
