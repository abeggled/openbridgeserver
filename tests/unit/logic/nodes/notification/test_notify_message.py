from __future__ import annotations

import pytest

from obs.logic.nodes.notification.notify_message import NODE_TYPE
from obs.logic.nodes.notification.notify_pushover import NODE_TYPE as NOTIFY_PUSHOVER
from obs.logic.nodes.notification.notify_sms import NODE_TYPE as NOTIFY_SMS


def test_generic_notification_replaces_legacy_nodes_in_palette_metadata():
    assert NODE_TYPE.category == "notification"
    assert [port.id for port in NODE_TYPE.inputs] == ["trigger", "message"]
    assert [port.id for port in NODE_TYPE.outputs] == ["sent"]
    assert "app_token" not in NODE_TYPE.config_schema
    assert "api_key" not in NODE_TYPE.config_schema


def test_generic_notification_routes_through_a_message_adapter_instance():
    assert NODE_TYPE.config_schema["adapter_instance_id"]["default"] == ""
    assert NODE_TYPE.config_schema["providers"]["type"] == "array"


@pytest.mark.parametrize("legacy", [NOTIFY_PUSHOVER, NOTIFY_SMS], ids=lambda node_type: node_type.type)
def test_legacy_notification_nodes_stay_editable_but_hidden(legacy):
    assert legacy.hidden_from_palette is True
    assert legacy.legacy is True
