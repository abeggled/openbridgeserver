"""Pushover message options in MESSAGE bindings and the shared send path (issue #1328)."""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError

from obs.adapters.message.adapter import MessageAdapter, MessageBindingConfig
from obs.adapters.message.providers.base import MessageSendResult
from obs.adapters.message.providers.pushover import (
    PushoverConfig,
    PushoverMessageOptions,
    PushoverProvider,
    PushoverTarget,
    build_pushover_payload,
    parse_pushover_options,
)
from obs.adapters.message.providers.pushover_attachment import PushoverImage
from obs.adapters.message.providers.registry import register_provider
from obs.core.event_bus import DataValueEvent
from tests.adapters.test_message_adapter import _drain_sends, _DummyProvider, _FakeAsyncClient, _message_binding


def _payload(**options):
    return build_pushover_payload(
        PushoverConfig(enabled=True, api_token="app"),
        PushoverTarget(user_key="user", sound="pushover"),
        title=None,
        message="Body",
        options=PushoverMessageOptions(**options),
    )


# ── Options model ───────────────────────────────────────────────────────────


def test_unset_options_do_not_reach_the_payload():
    assert _payload() == {"token": "app", "user": "user", "message": "Body", "sound": "pushover"}


def test_format_is_case_insensitive():
    assert PushoverMessageOptions(format=" HTML ").format == "html"
    assert PushoverMessageOptions(format="  ").format == "text"
    assert PushoverMessageOptions(format=None).format == "text"


def test_blank_values_count_as_unset():
    options = PushoverMessageOptions(image_url=" ", url="", url_title=None, sound="", format="", ttl="", priority=None)
    assert options.model_dump(exclude_none=True) == {"format": "text"}


def test_url_title_sound_and_formatting_reach_the_payload():
    assert _payload(url="https://obs.example", url_title="Open", sound="siren", format="html") == {
        "token": "app",
        "user": "user",
        "message": "Body",
        "sound": "siren",
        "url": "https://obs.example",
        "url_title": "Open",
        "html": 1,
    }
    payload = _payload(format="monospace", ttl=600, timestamp=1700000000, priority=-1)
    assert payload["monospace"] == 1
    assert "html" not in payload
    assert (payload["ttl"], payload["timestamp"], payload["priority"]) == (600, 1700000000, -1)


def test_emergency_priority_sends_retry_and_expire_but_no_ttl():
    payload = _payload(priority=2, retry=60, expire=3600, ttl=300)
    assert (payload["priority"], payload["retry"], payload["expire"]) == (2, 60, 3600)
    assert "ttl" not in payload


def test_retry_and_expire_are_ignored_below_emergency_priority():
    payload = _payload(priority=1, retry=60, expire=3600)
    assert "retry" not in payload
    assert "expire" not in payload


@pytest.mark.parametrize(
    ("options", "message"),
    [
        ({"priority": 2}, "emergency priority (2) requires retry and expire"),
        ({"priority": 2, "retry": 60}, "emergency priority (2) requires retry and expire"),
        ({"priority": 3}, "priority must be one of -2, -1, 0, 1 or 2"),
        ({"priority": 2, "retry": 29, "expire": 60}, "retry: Input should be greater than or equal to 30"),
        ({"priority": 2, "retry": 30, "expire": 10801}, "expire: Input should be less than or equal to 10800"),
        ({"ttl": 0}, "ttl: Input should be greater than 0"),
        ({"format": "markdown"}, "format: Input should be 'text', 'html' or 'monospace'"),
        ({"sound": "my sound"}, "sound: String should match pattern"),
        ({"url": "x" * 513}, "url: String should have at most 512 characters"),
    ],
)
def test_invalid_combinations_are_rejected_with_readable_messages(options, message):
    with pytest.raises(ValueError) as exc_info:
        parse_pushover_options(options)
    assert message in str(exc_info.value)


def test_url_title_rules_static_versus_rendered():
    from obs.adapters.message.providers.pushover import check_static_url_title

    with pytest.raises(ValueError, match="url_title requires a url"):
        check_static_url_title({"url_title": "Open", "url": " "})
    check_static_url_title({"url": "https://x", "url_title": "Open"})
    check_static_url_title(None)
    # At send time an empty (rendered) URL simply drops the title.
    assert parse_pushover_options({"url": "", "url_title": "Open"}).url_title is None


def test_validation_messages_never_echo_the_rejected_input():
    secret_url = "http://admin:camera-pass@192.168.1.50/" + "x" * 2100
    with pytest.raises(ValueError) as exc_info:
        parse_pushover_options({"image_url": secret_url})
    assert "camera-pass" not in str(exc_info.value)
    assert "image_url" in str(exc_info.value)


def test_leftover_retry_and_expire_are_ignored_below_emergency_priority():
    options = parse_pushover_options({"retry": 10, "expire": 99999}, priority=1)
    assert (options.retry, options.expire) == (None, None)
    assert _binding_config(priority=1, pushover={"retry": 10, "expire": 99999}).priority == 1
    assert parse_pushover_options({"priority": 2, "retry": 30, "expire": 60}).retry == 30
    with pytest.raises(ValueError, match="priority"):
        parse_pushover_options({"priority": "bad", "retry": 10})


def test_legacy_priority_applies_only_when_options_leave_it_unset():
    assert parse_pushover_options(None, priority=1).priority == 1
    assert parse_pushover_options({"priority": ""}, priority=-1).priority == -1
    assert parse_pushover_options({"priority": " "}, priority=1).priority == 1
    assert parse_pushover_options({"priority": 0}, priority=1).priority == 0
    assert parse_pushover_options("not a dict").priority is None


# ── Provider: emergency + formatting via context ────────────────────────────


@pytest.mark.asyncio
async def test_provider_sends_emergency_priority_with_retry_and_expire(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = {"status": 1}
    _FakeAsyncClient.status_code = 200
    monkeypatch.setattr("obs.adapters.message.providers.pushover.httpx.AsyncClient", _FakeAsyncClient)

    result = await PushoverProvider().send(
        provider_config={"enabled": True, "api_token": "app", "targets": {}},
        target_name="phone",
        target_config={"user_key": "user"},
        title=None,
        message="Fire",
        context={"priority": 2, "pushover": {"retry": 30, "expire": 300, "format": "monospace", "sound": "siren"}},
    )

    assert result.ok is True
    data = _FakeAsyncClient.calls[0][1]["data"]
    assert data == {"token": "app", "user": "user", "message": "Fire", "sound": "siren", "priority": 2, "retry": 30, "expire": 300, "monospace": 1}
    assert "files" not in _FakeAsyncClient.calls[0][1]


@pytest.mark.asyncio
async def test_provider_attaches_fetched_image(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = {"status": 1}
    _FakeAsyncClient.status_code = 200
    monkeypatch.setattr("obs.adapters.message.providers.pushover.httpx.AsyncClient", _FakeAsyncClient)
    fetch = AsyncMock(return_value=PushoverImage("snap.jpg", b"\xff\xd8\xff", "image/jpeg"))
    monkeypatch.setattr("obs.adapters.message.providers.pushover.fetch_pushover_image", fetch)

    result = await PushoverProvider().send(
        provider_config={"enabled": True, "api_token": "app", "targets": {}},
        target_name="phone",
        target_config={"user_key": "user"},
        title=None,
        message="Door",
        context={"pushover": {"image_url": "https://camera.example/snap.jpg"}},
    )

    assert result.ok is True
    fetch.assert_awaited_once_with("https://camera.example/snap.jpg")
    assert _FakeAsyncClient.calls[0][1]["files"] == {"attachment": ("snap.jpg", b"\xff\xd8\xff", "image/jpeg")}


@pytest.mark.asyncio
async def test_provider_drops_the_url_title_when_the_url_rendered_empty(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = {"status": 1}
    _FakeAsyncClient.status_code = 200
    monkeypatch.setattr("obs.adapters.message.providers.pushover.httpx.AsyncClient", _FakeAsyncClient)

    result = await PushoverProvider().send(
        provider_config={"enabled": True, "api_token": "app", "targets": {}},
        target_name="phone",
        target_config={"user_key": "user"},
        title=None,
        message="Door",
        context={"pushover": {"url": " ", "url_title": "Open"}},
    )

    assert result.ok is True
    assert "url_title" not in _FakeAsyncClient.calls[0][1]["data"]


@pytest.mark.asyncio
async def test_provider_rejects_rendered_options_that_became_invalid(monkeypatch):
    _FakeAsyncClient.calls = []
    monkeypatch.setattr("obs.adapters.message.providers.pushover.httpx.AsyncClient", _FakeAsyncClient)

    result = await PushoverProvider().send(
        provider_config={"enabled": True, "api_token": "app", "targets": {}},
        target_name="phone",
        target_config={"user_key": "user"},
        title=None,
        message="Door",
        context={"pushover": {"url": "https://x/" + "a" * 600}},
    )

    assert result.ok is False
    assert result.detail.startswith("invalid Pushover options: url:")
    assert _FakeAsyncClient.calls == []


# ── Binding configuration ───────────────────────────────────────────────────


def _binding_config(**config) -> MessageBindingConfig:
    return MessageBindingConfig(providers=[{"provider": "pushover", "target": "phone"}], **config)


def test_existing_binding_without_options_stays_valid():
    cfg = _binding_config(priority=1)
    assert cfg.pushover is None


def test_binding_accepts_emergency_priority_with_retry_and_expire():
    cfg = _binding_config(pushover={"priority": 2, "retry": 60, "expire": 600})
    assert cfg.pushover["priority"] == 2


def test_binding_legacy_priority_combines_with_options():
    assert _binding_config(priority=2, pushover={"retry": 60, "expire": 600}).priority == 2
    with pytest.raises(ValidationError, match="emergency priority"):
        _binding_config(priority=2, pushover={"retry": 60})


def test_binding_accepts_placeholders_in_text_options():
    cfg = _binding_config(pushover={"image_url": "http://###DP###/snap.jpg", "url": "https://obs.example/###DPI###", "url_title": "###DPN###"})
    assert cfg.pushover["url"] == "https://obs.example/###DPI###"


def test_binding_errors_never_echo_the_options():
    with pytest.raises(ValidationError) as exc_info:
        _binding_config(pushover={"priority": 2, "image_url": "https://admin:camera-pass@cam.example/x.jpg"})
    assert "camera-pass" not in str(exc_info.value)
    assert "emergency priority" in str(exc_info.value)


def test_binding_without_pushover_target_ignores_invalid_leftover_options():
    cfg = MessageBindingConfig(providers=[{"provider": "telegram", "target": "a"}], pushover={"retry": 5, "sound": "a b", "ttl": 0})
    assert cfg.pushover["retry"] == 5


def test_binding_with_pushover_target_rejects_invalid_field_values():
    with pytest.raises(ValidationError, match="retry"):
        _binding_config(pushover={"retry": 5, "priority": 2, "expire": 60})


def test_binding_without_pushover_target_keeps_leftover_options():
    cfg = MessageBindingConfig(providers=[{"provider": "telegram", "target": "chat"}], pushover={"url_title": "Open", "priority": 2})
    assert cfg.pushover["url_title"] == "Open"


def test_binding_rejects_url_title_without_url():
    with pytest.raises(ValidationError, match="url_title requires a url"):
        _binding_config(pushover={"url_title": "Open"})


def test_binding_options_are_not_combined_with_priority_without_pushover_target():
    cfg = MessageBindingConfig(providers=[{"provider": "telegram", "target": "chat"}], priority=2, pushover={"sound": "siren"})
    assert cfg.priority == 2


# ── Adapter send path ───────────────────────────────────────────────────────


class _RecordingPushover(_DummyProvider):
    provider_type = "pushover"

    def __init__(self) -> None:
        super().__init__()
        self.send = AsyncMock(return_value=MessageSendResult("pushover", "phone", True))


def _adapter_with(*providers) -> MessageAdapter:
    for provider in providers:
        register_provider(provider)
    return MessageAdapter(
        event_bus=AsyncMock(),
        config={
            "providers": {
                "dummy": {"enabled": True, "targets": {"default": {}}},
                "pushover": {"enabled": True, "api_token": "app", "targets": {"phone": {"user_key": "user"}}},
            }
        },
    )


@pytest.fixture
def _restore_pushover():
    yield
    register_provider(PushoverProvider())


@pytest.mark.asyncio
async def test_binding_renders_placeholders_and_passes_options_only_to_pushover(monkeypatch, _restore_pushover):
    dummy = _DummyProvider()
    pushover = _RecordingPushover()
    adapter = _adapter_with(dummy, pushover)
    monkeypatch.setattr(
        "obs.adapters.message.adapter._datetime_settings",
        AsyncMock(
            return_value={
                "timezone": "UTC",
                "date_format": "DD.MM.YYYY",
                "time_format": "HH:mm",
                "language": "de",
                "region_format": "de-CH",
                "currency": "CHF",
            }
        ),
    )
    dp_id = uuid.uuid4()
    binding = _message_binding(
        dp_id,
        operator="any",
        providers=[{"provider": "dummy", "target": "default"}, {"provider": "pushover", "target": "phone"}],
        pushover={
            "image_url": "http://192.168.1.50/cam/###DP###.jpg",
            "url": "https://obs.example/dp/###DPI###?at=###TS###&name=###DPN###",
            "url_title": "###DPN### öffnen",
            "sound": "siren",
            "format": "html",
        },
    )
    adapter._bindings = [binding]
    await adapter._on_bindings_reloaded()

    await adapter._on_value_event(
        DataValueEvent(datapoint_id=dp_id, value="front", quality="good", source_adapter="test", ts=datetime(2026, 1, 1, tzinfo=UTC))
    )
    await _drain_sends(adapter)

    pushover_context = pushover.send.await_args.kwargs["context"]
    assert pushover_context["pushover"] == {
        "image_url": "http://192.168.1.50/cam/front.jpg",
        "url": f"https://obs.example/dp/{dp_id}?at=2026-01-01T00:00:00%2B00:00&name={dp_id}",
        "url_title": f"{dp_id} öffnen",
        "sound": "siren",
        "format": "html",
    }
    dummy_kwargs = dummy.send.await_args.kwargs
    assert "pushover" not in dummy_kwargs["context"]
    assert dummy_kwargs["message"] == pushover.send.await_args.kwargs["message"]


@pytest.mark.asyncio
async def test_binding_without_options_sends_unchanged_context(_restore_pushover):
    pushover = _RecordingPushover()
    adapter = _adapter_with(pushover)
    dp_id = uuid.uuid4()
    cfg = _message_binding(dp_id, providers=[{"provider": "pushover", "target": "phone"}], priority=1).config
    event = DataValueEvent(datapoint_id=dp_id, value=1, quality="good", source_adapter="test")

    await adapter._send_to_targets(adapter.binding_config_schema(**cfg), _message_binding(dp_id), event, "body")

    context = pushover.send.await_args.kwargs["context"]
    assert "pushover" not in context
    assert context["priority"] == 1


@pytest.mark.asyncio
async def test_failed_provider_result_is_logged(caplog, _restore_pushover):
    pushover = _RecordingPushover()
    pushover.send = AsyncMock(return_value=MessageSendResult("pushover", "phone", False, "image attachment failed (timeout): slow"))
    adapter = _adapter_with(pushover)
    dp_id = uuid.uuid4()
    cfg = _message_binding(dp_id, providers=[{"provider": "pushover", "target": "phone"}]).config
    event = DataValueEvent(datapoint_id=dp_id, value=1, quality="good", source_adapter="test")
    caplog.set_level(logging.WARNING)

    results = await adapter._send_to_targets(adapter.binding_config_schema(**cfg), _message_binding(dp_id), event, "body")

    assert results[0].ok is False
    assert "MESSAGE provider 'pushover' target 'phone' failed: image attachment failed (timeout): slow" in caplog.text


@pytest.mark.asyncio
async def test_send_notification_passes_options_and_reports_invalid_ones(_restore_pushover):
    dummy = _DummyProvider()
    pushover = _RecordingPushover()
    adapter = _adapter_with(dummy, pushover)
    refs = [{"provider": "pushover", "target": "phone"}, {"provider": "dummy", "target": "default"}]

    results = await adapter.send_notification(message="Hi", providers=refs, priority=1, pushover={"url": "https://obs.example", "ttl": 60})

    assert [result.ok for result in results] == [True, True]
    assert pushover.send.await_args.kwargs["context"]["pushover"] == {"url": "https://obs.example", "ttl": 60, "format": "text"}
    assert "pushover" not in dummy.send.await_args.kwargs["context"]

    dummy.send.reset_mock()
    pushover.send.reset_mock()
    results = await adapter.send_notification(
        message="Hi", providers=refs, priority=2, pushover={"image_url": "http://admin:camera-pass@192.168.1.50/snap.jpg"}
    )
    # Invalid Pushover options fail the Pushover target only; the order of results follows the refs.
    assert [(result.provider, result.ok) for result in results] == [("pushover", False), ("dummy", True)]
    assert "emergency priority (2) requires retry and expire" in results[0].detail
    assert "camera-pass" not in results[0].detail
    pushover.send.assert_not_awaited()
    dummy.send.assert_awaited_once()

    results = await adapter.send_notification(message="Hi", providers=[refs[0]], priority=2)
    assert [result.ok for result in results] == [False]


@pytest.mark.asyncio
async def test_send_notification_sanitizes_invalid_config(_restore_pushover):
    adapter = _adapter_with(_RecordingPushover())

    with pytest.raises(ValueError) as exc_info:
        await adapter.send_notification(message=" ", providers=[{"provider": "dummy", "target": "default"}])

    assert not isinstance(exc_info.value, ValidationError)
    assert "message must not be empty" in str(exc_info.value)


@pytest.mark.asyncio
async def test_raising_provider_is_reported_once_as_send_failed(caplog, _restore_pushover):
    pushover = _RecordingPushover()
    pushover.send = AsyncMock(side_effect=RuntimeError("boom"))
    adapter = _adapter_with(pushover)
    dp_id = uuid.uuid4()
    cfg = _message_binding(dp_id, providers=[{"provider": "pushover", "target": "phone"}]).config
    event = DataValueEvent(datapoint_id=dp_id, value=1, quality="good", source_adapter="test")
    caplog.set_level(logging.WARNING)

    results = await adapter._send_to_targets(adapter.binding_config_schema(**cfg), _message_binding(dp_id), event, "body")

    assert [(result.ok, result.detail) for result in results] == [(False, "send failed")]
    assert "failed: send failed" not in caplog.text


@pytest.mark.asyncio
async def test_image_is_fetched_once_per_message_for_several_targets(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = {"status": 1}
    _FakeAsyncClient.status_code = 200
    monkeypatch.setattr("obs.adapters.message.providers.pushover.httpx.AsyncClient", _FakeAsyncClient)
    fetch = AsyncMock(return_value=PushoverImage("snap.jpg", b"\xff\xd8\xff", "image/jpeg"))
    monkeypatch.setattr("obs.adapters.message.providers.pushover.fetch_pushover_image", fetch)
    adapter = MessageAdapter(
        event_bus=AsyncMock(),
        config={"providers": {"pushover": {"enabled": True, "api_token": "app", "targets": {"a": {"user_key": "u1"}, "b": {"user_key": "u2"}}}}},
    )
    refs = [{"provider": "pushover", "target": "a"}, {"provider": "pushover", "target": "b"}]

    results = await adapter.send_notification(message="Hi", providers=refs, pushover={"image_url": "https://camera.example/snap.jpg"})

    assert [result.ok for result in results] == [True, True]
    fetch.assert_awaited_once()
    assert [call[1]["files"]["attachment"][0] for call in _FakeAsyncClient.calls] == ["snap.jpg", "snap.jpg"]


@pytest.mark.asyncio
async def test_failed_image_is_not_refetched_for_further_targets(monkeypatch):
    from obs.adapters.message.providers.pushover_attachment import PushoverAttachmentError

    _FakeAsyncClient.calls = []
    monkeypatch.setattr("obs.adapters.message.providers.pushover.httpx.AsyncClient", _FakeAsyncClient)
    fetch = AsyncMock(side_effect=PushoverAttachmentError("timeout", "image download from camera.example timed out"))
    monkeypatch.setattr("obs.adapters.message.providers.pushover.fetch_pushover_image", fetch)
    cache: dict = {}

    for target in ("a", "b"):
        result = await PushoverProvider().send(
            provider_config={"enabled": True, "api_token": "app", "targets": {}},
            target_name=target,
            target_config={"user_key": "user"},
            title=None,
            message="Hi",
            context={"pushover": {"image_url": "https://camera.example/snap.jpg"}, "attachment_cache": cache},
        )
        assert result.detail == "image attachment failed (timeout): image download from camera.example timed out"

    fetch.assert_awaited_once()
    assert _FakeAsyncClient.calls == []


def test_non_sending_binding_does_not_validate_pushover_combinations():
    cfg = MessageBindingConfig(
        providers=[{"provider": "pushover", "target": "phone"}], priority=2, archive_strategy="archive_only", archive_id="alarms"
    )
    assert cfg.priority == 2


@pytest.mark.asyncio
async def test_send_notification_sanitizes_malformed_target_refs(_restore_pushover):
    adapter = _adapter_with(_RecordingPushover())

    with pytest.raises(ValueError) as exc_info:
        await adapter.send_notification(message="Hi", providers=[{"provider": "pushover", "target_x": "admin:secret@x"}])

    assert not isinstance(exc_info.value, ValidationError)
    assert "secret" not in str(exc_info.value)
    assert "target" in str(exc_info.value)


@pytest.mark.asyncio
async def test_archive_only_binding_skips_option_rendering(monkeypatch, _restore_pushover):
    pushover = _RecordingPushover()
    adapter = _adapter_with(pushover)
    monkeypatch.setattr(
        "obs.adapters.message.adapter._datetime_settings",
        AsyncMock(
            return_value={
                "timezone": "UTC",
                "date_format": "DD.MM.YYYY",
                "time_format": "HH:mm",
                "language": "de",
                "region_format": "de-CH",
                "currency": "CHF",
            }
        ),
    )
    captured = []

    async def _record(binding, event, cfg, rendered, state, reset_version, pushover_message=None):
        captured.append(cfg)
        state.in_flight = False

    monkeypatch.setattr(adapter, "_send_and_record", _record)
    dp_id = uuid.uuid4()
    binding = _message_binding(
        dp_id,
        operator="any",
        providers=[{"provider": "pushover", "target": "phone"}],
        archive_strategy="archive_only",
        archive_id="alarms",
        pushover={"url": "https://obs.example/###DPI###"},
    )
    adapter._bindings = [binding]
    await adapter._on_bindings_reloaded()

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=1, quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    assert captured[0].pushover["url"] == "https://obs.example/###DPI###"


def test_url_value_encoding_keeps_url_delimiters():
    from obs.core.url_values import encode_url_value

    assert encode_url_value("https://cam.example/snap.jpg?x=1") == "https://cam.example/snap.jpg?x=1"
    assert encode_url_value("Haustür vorne+1") == "Haust%C3%BCr%20vorne%2B1"
    assert encode_url_value("100% done") == "100%25%20done"
    assert encode_url_value("55%") == "55%25"
    assert encode_url_value("https://cam.example/a%20b.jpg") == "https://cam.example/a%20b.jpg"


@pytest.mark.asyncio
async def test_html_binding_escapes_inserted_values_for_pushover_only(monkeypatch, _restore_pushover):
    dummy = _DummyProvider()
    pushover = _RecordingPushover()
    adapter = _adapter_with(dummy, pushover)
    monkeypatch.setattr(
        "obs.adapters.message.adapter._datetime_settings",
        AsyncMock(
            return_value={
                "timezone": "UTC",
                "date_format": "DD.MM.YYYY",
                "time_format": "HH:mm",
                "language": "de",
                "region_format": "de-CH",
                "currency": "CHF",
            }
        ),
    )
    dp_id = uuid.uuid4()
    binding = _message_binding(
        dp_id,
        operator="any",
        message="<b>Alarm</b>: ###DP###",
        providers=[{"provider": "dummy", "target": "default"}, {"provider": "pushover", "target": "phone"}],
        pushover={"format": "html"},
    )
    adapter._bindings = [binding]
    await adapter._on_bindings_reloaded()

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value='<a href="https://evil">x</a>', quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    assert pushover.send.await_args.kwargs["message"] == "<b>Alarm</b>: &lt;a href=&quot;https://evil&quot;&gt;x&lt;/a&gt;"
    assert dummy.send.await_args.kwargs["message"] == '<b>Alarm</b>: <a href="https://evil">x</a>'


@pytest.mark.asyncio
async def test_send_notification_uses_the_pushover_message_for_pushover_only(_restore_pushover):
    dummy = _DummyProvider()
    pushover = _RecordingPushover()
    adapter = _adapter_with(dummy, pushover)
    refs = [{"provider": "pushover", "target": "phone"}, {"provider": "dummy", "target": "default"}]

    await adapter.send_notification(message="a<b", providers=refs, pushover={"format": "html"}, pushover_message="a&lt;b")

    assert pushover.send.await_args.kwargs["message"] == "a&lt;b"
    assert dummy.send.await_args.kwargs["message"] == "a<b"


@pytest.mark.asyncio
async def test_plain_text_binding_sends_the_same_message_to_pushover(monkeypatch, _restore_pushover):
    pushover = _RecordingPushover()
    adapter = _adapter_with(pushover)
    monkeypatch.setattr(
        "obs.adapters.message.adapter._datetime_settings",
        AsyncMock(
            return_value={
                "timezone": "UTC",
                "date_format": "DD.MM.YYYY",
                "time_format": "HH:mm",
                "language": "de",
                "region_format": "de-CH",
                "currency": "CHF",
            }
        ),
    )
    dp_id = uuid.uuid4()
    binding = _message_binding(
        dp_id, operator="any", message="a<b ###DP###", providers=[{"provider": "pushover", "target": "phone"}], pushover={"format": "monospace"}
    )
    adapter._bindings = [binding]
    await adapter._on_bindings_reloaded()

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="<x>", quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    assert pushover.send.await_args.kwargs["message"] == "a<b <x>"


@pytest.mark.asyncio
async def test_url_title_without_url_does_not_block_other_providers(_restore_pushover):
    dummy = _DummyProvider()
    pushover = _RecordingPushover()
    adapter = _adapter_with(dummy, pushover)
    refs = [{"provider": "dummy", "target": "default"}, {"provider": "pushover", "target": "phone"}]

    results = await adapter.send_notification(message="Hi", providers=refs, pushover={"url_title": "Open", "url": ""})

    assert [result.ok for result in results] == [True, True]
    assert "url_title" not in pushover.send.await_args.kwargs["context"]["pushover"]
    dummy.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_own_option_priority_wins_over_the_top_level_one(_restore_pushover):
    pushover = _RecordingPushover()
    adapter = _adapter_with(pushover)

    await adapter.send_notification(message="Hi", providers=[{"provider": "pushover", "target": "phone"}], priority=0, pushover={"priority": -1})

    assert pushover.send.await_args.kwargs["context"]["pushover"]["priority"] == -1


@pytest.mark.asyncio
async def test_unencodable_value_does_not_leave_the_binding_stuck(monkeypatch, _restore_pushover):
    pushover = _RecordingPushover()
    adapter = _adapter_with(pushover)
    monkeypatch.setattr(
        "obs.adapters.message.adapter._datetime_settings",
        AsyncMock(
            return_value={
                "timezone": "UTC",
                "date_format": "DD.MM.YYYY",
                "time_format": "HH:mm",
                "language": "de",
                "region_format": "de-CH",
                "currency": "CHF",
            }
        ),
    )
    dp_id = uuid.uuid4()
    binding = _message_binding(
        dp_id, operator="any", providers=[{"provider": "pushover", "target": "phone"}], pushover={"url": "https://x/?v=###DP###"}
    )
    adapter._bindings = [binding]
    await adapter._on_bindings_reloaded()

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="a\ud800b", quality="good", source_adapter="test"))
    await _drain_sends(adapter)
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="next", quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    urls = [call.kwargs["context"]["pushover"]["url"] for call in pushover.send.await_args_list]
    assert urls == ["https://x/?v=a%EF%BF%BDb", "https://x/?v=next"]
    assert adapter._states[binding.id].in_flight is False


def test_disabled_binding_is_not_blocked_by_invalid_options():
    cfg = MessageBindingConfig(providers=[{"provider": "pushover", "target": "phone"}], priority=2, pushover={"retry": 10}, enabled=False)
    assert cfg.enabled is False
