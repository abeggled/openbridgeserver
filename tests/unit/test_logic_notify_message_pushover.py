"""Pushover options on the generic notification block (issue #1328)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from obs.adapters.message.providers.base import MessageSendResult
from tests.unit.conftest import edge, node
from tests.unit.test_message_archive_logic_node import _flow, _make_manager, _run

PUSHOVER = {"provider": "pushover", "target": "phone"}
TELEGRAM = {"provider": "telegram", "target": "chat"}


def _adapter(*results: MessageSendResult) -> MagicMock:
    adapter = MagicMock(adapter_type="MESSAGE")
    adapter.send_notification = AsyncMock(return_value=list(results) or [MessageSendResult("pushover", "phone", True)])
    return adapter


def _send(flow, overrides, adapter, manager=None):
    with (
        patch("obs.adapters.registry.get_instance_by_id", return_value=adapter),
        patch("obs.api.v1.websocket.get_ws_manager", side_effect=RuntimeError("no ws")),
    ):
        return _run(manager or _make_manager(), flow, overrides)


def _notify(**data) -> dict:
    return node("notify", "notify_message", {"adapter_instance_id": "message-1", "message": "Alarm", **data})


def test_static_options_are_sent_for_pushover_targets():
    adapter = _adapter()
    flow = _flow(
        [
            _notify(
                providers=[PUSHOVER],
                priority=2,
                url="https://obs.example/visu",
                url_title="Open",
                image_url="http://192.168.1.50/snapshot.jpg",
                sound="siren",
                format="html",
                ttl=None,
                timestamp="",
                retry=60,
                expire=600,
            )
        ]
    )

    outputs = _send(flow, {"notify": {"trigger": True}}, adapter)

    assert outputs["notify"]["sent"] is True
    kwargs = adapter.send_notification.await_args.kwargs
    assert kwargs["priority"] == 2
    assert kwargs["pushover"] == {
        "sound": "siren",
        "format": "html",
        "retry": 60,
        "expire": 600,
        "url": "https://obs.example/visu",
        "url_title": "Open",
        "image_url": "http://192.168.1.50/snapshot.jpg",
    }


def test_connected_inputs_override_static_values():
    adapter = _adapter()
    flow = _flow(
        [
            node("img", "const_value", {"value": "https://camera.example/live.jpg", "data_type": "string"}),
            node("link", "const_value", {"value": "https://obs.example/alarm", "data_type": "string"}),
            _notify(providers=[PUSHOVER], image_url="http://192.168.1.50/static.jpg", url="https://static.example", url_title="Static"),
        ],
        [edge("img", "notify", "value", "image_url"), edge("link", "notify", "value", "url")],
    )

    _send(flow, {"notify": {"trigger": True}}, adapter)

    options = adapter.send_notification.await_args.kwargs["pushover"]
    assert options["image_url"] == "https://camera.example/live.jpg"
    assert options["url"] == "https://obs.example/alarm"
    assert options["url_title"] == "Static"


def test_static_options_resolve_object_variables():
    datapoint_id = str(uuid.uuid4())
    manager = _make_manager()
    manager._registry.get_value.return_value = MagicMock(value="cam7")
    adapter = _adapter()
    flow = _flow(
        [
            _notify(
                providers=[PUSHOVER],
                image_url="http://192.168.1.50/###OBS1###.jpg",
                variables=[{"slot": 1, "datapoint_id": datapoint_id, "datapoint_name": "Kamera"}],
            )
        ]
    )

    _send(flow, {"notify": {"trigger": True}}, adapter, manager)

    assert adapter.send_notification.await_args.kwargs["pushover"]["image_url"] == "http://192.168.1.50/cam7.jpg"


def test_unresolvable_variable_in_option_blocks_the_send():
    adapter = _adapter()
    flow = _flow([_notify(providers=[PUSHOVER], url="https://obs.example/###OBS4###")])

    outputs = _send(flow, {"notify": {"trigger": True}}, adapter)

    assert outputs["notify"]["sent"] is False
    assert "OBS4" in outputs["notify"]["__error__"]
    adapter.send_notification.assert_not_awaited()


def test_option_variable_error_fails_only_the_pushover_targets():
    adapter = _adapter(MessageSendResult("telegram", "chat", True))
    flow = _flow([_notify(providers=[PUSHOVER, TELEGRAM], image_url="http://192.168.1.50/###OBS4###.jpg")])

    outputs = _send(flow, {"notify": {"trigger": True}}, adapter)

    kwargs = adapter.send_notification.await_args.kwargs
    assert kwargs["providers"] == [TELEGRAM]
    assert "pushover" not in kwargs
    assert outputs["notify"]["sent"] is False
    assert outputs["notify"]["__error__"].startswith("pushover/phone: ")
    assert "OBS4" in outputs["notify"]["__error__"]


def test_html_fallback_escapes_variable_values():
    datapoint_id = str(uuid.uuid4())
    manager = _make_manager()
    manager._registry.get_value.return_value = MagicMock(value="<script>")
    adapter = _adapter()
    variables = [{"slot": 1, "datapoint_id": datapoint_id, "datapoint_name": "Wert"}]
    flow = _flow([_notify(providers=[PUSHOVER], format="html", message="<b>###OBS1###</b>", variables=variables)])

    _send(flow, {"notify": {"trigger": True}}, adapter, manager)

    kwargs = adapter.send_notification.await_args.kwargs
    assert kwargs["message"] == "<b><script></b>"
    assert kwargs["pushover_message"] == "<b>&lt;script&gt;</b>"


def test_connected_message_is_escaped_for_html_pushover():
    adapter = _adapter()
    flow = _flow([_notify(providers=[PUSHOVER], format="HTML")])

    _send(flow, {"notify": {"message": "Temp < 5 & falling"}}, adapter)

    kwargs = adapter.send_notification.await_args.kwargs
    assert kwargs["message"] == "Temp < 5 & falling"
    assert kwargs["pushover_message"] == "Temp &lt; 5 &amp; falling"


def test_invalid_retry_with_priority_two_is_not_downgraded():
    adapter = _adapter()
    flow = _flow([_notify(providers=[PUSHOVER], priority=2, retry=0, expire=600)])

    _send(flow, {"notify": {"trigger": True}}, adapter)

    kwargs = adapter.send_notification.await_args.kwargs
    assert kwargs["priority"] == 2
    assert kwargs["pushover"]["retry"] == 0


def test_options_are_not_sent_without_a_pushover_target():
    adapter = _adapter(MessageSendResult("telegram", "chat", True))
    flow = _flow([_notify(providers=[TELEGRAM], image_url="http://192.168.1.50/snap.jpg", sound="siren", format="html")])

    outputs = _send(flow, {"notify": {"trigger": True}}, adapter)

    assert outputs["notify"]["sent"] is True
    assert "pushover" not in adapter.send_notification.await_args.kwargs


def test_pushover_target_without_options_keeps_the_previous_call():
    adapter = _adapter()
    flow = _flow([_notify(providers=[PUSHOVER, TELEGRAM])])

    _send(flow, {"notify": {"trigger": True}}, adapter)

    assert "pushover" not in adapter.send_notification.await_args.kwargs


def test_invalid_options_report_an_error_and_do_not_fire_sent():
    adapter = _adapter()
    adapter.send_notification = AsyncMock(side_effect=ValueError("Pushover emergency priority (2) requires retry and expire"))
    flow = _flow([_notify(providers=[PUSHOVER], priority=2)])

    outputs = _send(flow, {"notify": {"trigger": True}}, adapter)

    assert outputs["notify"]["sent"] is False
    assert outputs["notify"]["__error__"] == "Pushover emergency priority (2) requires retry and expire"


def test_stored_emergency_priority_without_retry_keeps_the_legacy_clamp(caplog):
    adapter = _adapter()
    flow = _flow([_notify(providers=[PUSHOVER], priority=5)])

    outputs = _send(flow, {"notify": {"trigger": True}}, adapter)

    assert outputs["notify"]["sent"] is True
    assert adapter.send_notification.await_args.kwargs["priority"] == 1
    assert "priority 5 without valid emergency settings is sent as priority 1" in caplog.text


def test_stored_priority_two_without_retry_keeps_delivering_as_one(caplog):
    adapter = _adapter()
    flow = _flow([_notify(providers=[PUSHOVER], priority=2, retry=60)])

    outputs = _send(flow, {"notify": {"trigger": True}}, adapter)

    assert outputs["notify"]["sent"] is True
    assert adapter.send_notification.await_args.kwargs["priority"] == 1
    assert "pushover" not in adapter.send_notification.await_args.kwargs
    assert "priority 2 without valid emergency settings is sent as priority 1" in caplog.text


def test_stored_priority_two_without_pushover_target_is_clamped_silently(caplog):
    adapter = _adapter(MessageSendResult("telegram", "chat", True))
    flow = _flow([_notify(providers=[TELEGRAM], priority=2)])

    _send(flow, {"notify": {"trigger": True}}, adapter)

    assert adapter.send_notification.await_args.kwargs["priority"] == 1
    assert "sent as priority 1" not in caplog.text


def test_static_url_variables_are_url_encoded_but_title_is_not():
    datapoint_id = str(uuid.uuid4())
    manager = _make_manager()
    manager._registry.get_value.return_value = MagicMock(value="Küche 1+")
    adapter = _adapter()
    variables = [{"slot": 1, "datapoint_id": datapoint_id, "datapoint_name": "Raum"}]
    flow = _flow([_notify(providers=[PUSHOVER], url="https://obs.example/?room=###OBS1###", url_title="###OBS1###", variables=variables)])

    _send(flow, {"notify": {"trigger": True}}, adapter, manager)

    options = adapter.send_notification.await_args.kwargs["pushover"]
    assert options["url"] == "https://obs.example/?room=K%C3%BCche%201%2B"
    assert options["url_title"] == "Küche 1+"


def test_leftover_retry_and_expire_are_dropped_below_emergency_priority():
    adapter = _adapter()
    flow = _flow([_notify(providers=[PUSHOVER], priority=1, retry=10, expire=600, sound="siren")])

    _send(flow, {"notify": {"trigger": True}}, adapter)

    assert adapter.send_notification.await_args.kwargs["pushover"] == {"sound": "siren"}


def test_failed_attachment_does_not_fire_sent():
    adapter = _adapter(
        MessageSendResult("pushover", "phone", False, "image attachment failed (too_large): image is larger than the Pushover limit of 5 MB")
    )
    flow = _flow([_notify(providers=[PUSHOVER], image_url="https://camera.example/huge.jpg")])

    outputs = _send(flow, {"notify": {"trigger": True}}, adapter)

    assert outputs["notify"]["sent"] is False
    assert "too_large" in outputs["notify"]["__error__"]
