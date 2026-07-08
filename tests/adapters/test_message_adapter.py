"""Unit tests for the MESSAGE adapter."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from pydantic import BaseModel

from obs.adapters.message.adapter import (
    MessageAdapter,
    MessageAdapterConfig,
    MessageBindingConfig,
    evaluate_condition,
    render_message,
)
from obs.adapters.message.providers.base import MessageSendResult
from obs.adapters.message.providers.pushover import PushoverProvider
from obs.adapters.message.providers.registry import register_provider
from obs.adapters.message.providers.sevenio import SevenIoProvider
from obs.adapters.message.providers.telegram import TelegramProvider
from obs.core.event_bus import DataValueEvent
from tests.adapters.conftest import make_binding


class _DummyConfig(BaseModel):
    enabled: bool = True
    targets: dict[str, dict] = {}


class _DummyProvider:
    provider_type = "dummy"
    config_schema = _DummyConfig
    target_schema = BaseModel

    def __init__(self) -> None:
        self.send = AsyncMock(return_value=MessageSendResult("dummy", "default", True))


class _Dp:
    def __init__(self, dp_id: uuid.UUID, name: str = "Temperatur", unit: str | None = "°C") -> None:
        self.id = dp_id
        self.name = name
        self.unit = unit


class _Registry:
    def __init__(self, dp: _Dp) -> None:
        self._dp = dp

    def get(self, dp_id: uuid.UUID) -> _Dp | None:
        return self._dp if dp_id == self._dp.id else None


class _FakeResponse:
    def __init__(self, status_code: int = 200, text: str = "100", json_body=None) -> None:
        self.status_code = status_code
        self.text = text
        self._json_body = json_body

    def json(self):
        if self._json_body is None:
            raise ValueError("not json")
        return self._json_body


class _FakeAsyncClient:
    calls: list[tuple[str, dict, float | None]] = []
    json_body = None
    status_code = 200
    text = "100"

    def __init__(self, timeout: float | None = None) -> None:
        self.timeout = timeout

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, **kwargs):
        self.calls.append((url, kwargs, self.timeout))
        return _FakeResponse(self.status_code, self.text, self.json_body)


@pytest.mark.parametrize(
    ("value", "operator", "compare_value", "expected"),
    [
        ("anything", "any", None, True),
        (None, "any", "ignored", True),
        (29.4, ">=", 28, True),
        ("29.4", "<", "30", True),
        ("bad", "<", 30, False),
        ("open", "=", "open", True),
        (1, "==", "1", True),
        (True, "==", "true", True),
        (False, "==", "false", True),
        (True, "!=", "false", True),
        ("abc", "!=", "def", True),
        ("hello world", "contains", "world", True),
        ("hello world", "contains not", "mars", True),
        ("sensor/temp", "starts with", "sensor", True),
        ("sensor/temp", "ends with", "temp", True),
    ],
)
def test_evaluate_condition(value, operator, compare_value, expected):
    assert evaluate_condition(value, operator, compare_value) is expected


def test_render_message_replaces_value_unit_and_metadata():
    dp_id = uuid.uuid4()
    ts = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)

    rendered = render_message(
        "###DPN### ###DPI### ###DP### ###DPU### ###TS###",
        value=29.4,
        unit="°C",
        name="Temperatur",
        datapoint_id=dp_id,
        ts=ts,
    )

    assert rendered == f"Temperatur {dp_id} 29.4 °C 2026-06-28T12:00:00+00:00"


def test_render_message_does_not_reprocess_inserted_placeholder_text():
    dp_id = uuid.uuid4()
    ts = datetime(2026, 6, 28, 12, 0, tzinfo=UTC)

    rendered = render_message(
        "value=###DP### ts=###TS###",
        value="###TS###",
        unit=None,
        name="Sensor",
        datapoint_id=dp_id,
        ts=ts,
    )

    assert rendered == "value=###TS### ts=2026-06-28T12:00:00+00:00"


def test_disabled_provider_allows_incomplete_hidden_targets():
    cfg = MessageAdapterConfig(providers={"telegram": {"enabled": False, "targets": {"default": {}}}})

    assert cfg.providers["telegram"]["enabled"] is False


def test_string_disabled_provider_allows_incomplete_hidden_targets():
    cfg = MessageAdapterConfig(providers={"telegram": {"enabled": "false", "targets": {"default": {}}}})

    assert cfg.providers["telegram"]["enabled"] == "false"


@pytest.mark.parametrize(
    ("provider", "config", "error"),
    [
        ("pushover", {"enabled": True, "api_token": "", "targets": {}}, "api_token"),
        ("telegram", {"enabled": True, "bot_token": " ", "targets": {}}, "bot_token"),
        ("seven.io", {"enabled": True, "api_key": "", "targets": {}}, "api_key"),
        ("pushover", {"enabled": True, "api_token": "app", "targets": {"default": {"user_key": ""}}}, "user_key"),
        ("telegram", {"enabled": True, "bot_token": "token", "targets": {"default": {"chat_id": " "}}}, "chat_id"),
        ("seven.io", {"enabled": True, "api_key": "key", "targets": {"default": {"to": ""}}}, "to"),
    ],
)
def test_enabled_provider_rejects_empty_credentials_and_recipients(provider, config, error):
    with pytest.raises(ValueError, match=error):
        MessageAdapterConfig(providers={provider: config})


def test_enabled_binding_requires_message_target():
    with pytest.raises(ValueError, match="at least one target"):
        MessageBindingConfig(providers=[])

    cfg = MessageBindingConfig(enabled=False, providers=[])

    assert cfg.enabled is False


def test_binding_rejects_blank_message_body():
    with pytest.raises(ValueError, match="message must not be empty"):
        MessageBindingConfig(
            message="   ",
            providers=[{"provider": "telegram", "target": "default"}],
        )


def test_binding_rejects_duplicate_message_targets():
    with pytest.raises(ValueError, match="Duplicate MESSAGE target"):
        MessageBindingConfig(
            providers=[
                {"provider": "telegram", "target": "default"},
                {"provider": "telegram", "target": "default"},
            ]
        )


def test_binding_rejects_pushover_emergency_priority_without_required_fields():
    with pytest.raises(ValueError, match="Pushover emergency priority"):
        MessageBindingConfig(
            priority=2,
            providers=[{"provider": "pushover", "target": "default"}],
        )


def test_binding_rejects_out_of_range_pushover_priority():
    with pytest.raises(ValueError, match="Pushover priority"):
        MessageBindingConfig(
            priority=3,
            providers=[{"provider": "pushover", "target": "default"}],
        )


@pytest.fixture
def dummy_provider():
    provider = _DummyProvider()
    register_provider(provider)
    return provider


@pytest.fixture
def bus():
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


def _message_binding(dp_id: uuid.UUID, **config):
    binding = make_binding(
        {
            "operator": ">=",
            "compare_value": 28,
            "message": "Temperatur kritisch: ###DP### ###DPU###",
            "title": "OBS Alarm",
            "providers": [{"provider": "dummy", "target": "default"}],
            "send_on_change": True,
            **config,
        },
        direction="SOURCE",
    )
    binding.datapoint_id = dp_id
    return binding


async def _drain_sends(adapter: MessageAdapter) -> None:
    while adapter._send_tasks:
        await asyncio.gather(*list(adapter._send_tasks))
        await asyncio.sleep(0)


@pytest.mark.asyncio
async def test_datapoint_update_sends_message_to_provider(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {"id": "x"}}}}},
    )
    binding = _message_binding(dp_id)
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=29.4, quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    dummy_provider.send.assert_awaited_once()
    kwargs = dummy_provider.send.await_args.kwargs
    assert kwargs["title"] == "OBS Alarm"
    assert kwargs["message"] == "Temperatur kritisch: 29.4 °C"
    assert kwargs["target_name"] == "default"


@pytest.mark.asyncio
async def test_send_on_change_suppresses_repeated_true_condition(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id)
    await adapter.reload_bindings([binding])

    event = DataValueEvent(datapoint_id=dp_id, value=29.4, quality="good", source_adapter="test")
    await adapter._on_value_event(event)
    await _drain_sends(adapter)
    await adapter._on_value_event(event)
    await _drain_sends(adapter)

    dummy_provider.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_cooldown_suppresses_repeated_sends(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id, send_on_change=False, cooldown_seconds=300)
    await adapter.reload_bindings([binding])

    event = DataValueEvent(datapoint_id=dp_id, value=29.4, quality="good", source_adapter="test")
    await adapter._on_value_event(event)
    await _drain_sends(adapter)
    await adapter._on_value_event(event)
    await _drain_sends(adapter)

    dummy_provider.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_condition_reset_allows_next_true_transition(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id)
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=29, quality="good", source_adapter="test"))
    await _drain_sends(adapter)
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=20, quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=30, quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    assert dummy_provider.send.await_count == 2


@pytest.mark.asyncio
async def test_any_operator_sends_for_each_changed_value(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id, operator="any", compare_value=None, send_on_change=True)
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=False, quality="good", source_adapter="test"))
    await _drain_sends(adapter)
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=False, quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=True, quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    assert dummy_provider.send.await_count == 2


@pytest.mark.asyncio
async def test_any_operator_queues_each_changed_value_during_in_flight_send(bus, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id, unit=None)))
    release = asyncio.Event()
    messages: list[str] = []

    class _SlowProvider(_DummyProvider):
        provider_type = "slow-any"

        def __init__(self) -> None:
            pass

        async def send(self, **kwargs):
            messages.append(kwargs["message"])
            await release.wait()
            return MessageSendResult("slow-any", "default", True)

    provider = _SlowProvider()
    register_provider(provider)
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"slow-any": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(
        dp_id,
        operator="any",
        compare_value=None,
        message="###DP###",
        providers=[{"provider": "slow-any", "target": "default"}],
    )
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="A", quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="B", quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="C", quality="good", source_adapter="test"))

    release.set()
    await _drain_sends(adapter)

    assert messages == ["A", "B", "C"]


@pytest.mark.asyncio
async def test_any_operator_continues_draining_after_suppressed_pending_duplicate(bus, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id, unit=None)))
    release = asyncio.Event()
    messages: list[str] = []

    class _SlowProvider(_DummyProvider):
        provider_type = "slow-any-duplicate"

        def __init__(self) -> None:
            pass

        async def send(self, **kwargs):
            messages.append(kwargs["message"])
            await release.wait()
            return MessageSendResult("slow-any-duplicate", "default", True)

    provider = _SlowProvider()
    register_provider(provider)
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"slow-any-duplicate": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(
        dp_id,
        operator="any",
        compare_value=None,
        message="###DP###",
        providers=[{"provider": "slow-any-duplicate", "target": "default"}],
    )
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="A", quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="A", quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="B", quality="good", source_adapter="test"))

    release.set()
    await _drain_sends(adapter)

    assert messages == ["A", "B"]


@pytest.mark.asyncio
async def test_any_operator_preserves_return_to_in_flight_value_after_change(bus, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id, unit=None)))
    release = asyncio.Event()
    messages: list[str] = []

    class _SlowProvider(_DummyProvider):
        provider_type = "slow-any-return"

        def __init__(self) -> None:
            pass

        async def send(self, **kwargs):
            messages.append(kwargs["message"])
            await release.wait()
            return MessageSendResult("slow-any-return", "default", True)

    provider = _SlowProvider()
    register_provider(provider)
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"slow-any-return": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(
        dp_id,
        operator="any",
        compare_value=None,
        message="###DP###",
        providers=[{"provider": "slow-any-return", "target": "default"}],
    )
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="A", quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="B", quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="A", quality="good", source_adapter="test"))

    release.set()
    await _drain_sends(adapter)

    assert messages == ["A", "B", "A"]


@pytest.mark.asyncio
async def test_send_on_change_coalesces_duplicate_pending_failure_retries(bus, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id, unit=None)))
    release = asyncio.Event()
    messages: list[str] = []

    class _FailingSlowProvider(_DummyProvider):
        provider_type = "slow-failing-duplicate"

        def __init__(self) -> None:
            pass

        async def send(self, **kwargs):
            messages.append(kwargs["message"])
            await release.wait()
            return MessageSendResult("slow-failing-duplicate", "default", False, "down")

    provider = _FailingSlowProvider()
    register_provider(provider)
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"slow-failing-duplicate": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(
        dp_id,
        operator="any",
        compare_value=None,
        message="###DP###",
        providers=[{"provider": "slow-failing-duplicate", "target": "default"}],
    )
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="A", quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="A", quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value="A", quality="good", source_adapter="test"))

    release.set()
    await _drain_sends(adapter)

    assert messages == ["A"]


@pytest.mark.asyncio
async def test_in_flight_pending_events_are_bounded_to_newest_values(bus, monkeypatch):
    monkeypatch.setattr("obs.adapters.message.adapter.MAX_PENDING_EVENTS_PER_BINDING", 2)
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id, unit=None)))
    release = asyncio.Event()
    messages: list[str] = []

    class _SlowProvider(_DummyProvider):
        provider_type = "slow-bounded"

        def __init__(self) -> None:
            pass

        async def send(self, **kwargs):
            messages.append(kwargs["message"])
            await release.wait()
            return MessageSendResult("slow-bounded", "default", True)

    provider = _SlowProvider()
    register_provider(provider)
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"slow-bounded": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(
        dp_id,
        operator="any",
        compare_value=None,
        message="###DP###",
        providers=[{"provider": "slow-bounded", "target": "default"}],
        send_on_change=False,
    )
    await adapter.reload_bindings([binding])

    for value in ["A", "B", "C", "D", "E"]:
        await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=value, quality="good", source_adapter="test"))

    state = adapter._states[binding.id]
    assert len(state.pending_events) == 2

    release.set()
    await _drain_sends(adapter)

    assert messages == ["A", "D", "E"]


@pytest.mark.asyncio
async def test_write_path_sends_message_to_provider(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id, unit=None)))
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id, operator="any")

    await adapter.write(binding, "manual")
    await _drain_sends(adapter)

    dummy_provider.send.assert_awaited_once()
    assert dummy_provider.send.await_args.kwargs["message"] == "Temperatur kritisch: manual "


@pytest.mark.asyncio
async def test_bad_quality_event_is_ignored(bus, dummy_provider):
    dp_id = uuid.uuid4()
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id)
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="bad", source_adapter="test"))

    dummy_provider.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_disabled_binding_is_not_reloaded(bus, dummy_provider):
    dp_id = uuid.uuid4()
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id, enabled=False)
    binding.enabled = False

    await adapter.reload_bindings([binding])
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))

    dummy_provider.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_binding_reload_uses_top_level_enabled_flag(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id, enabled=False)
    binding.enabled = True

    await adapter.reload_bindings([binding])
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    dummy_provider.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_both_binding_is_not_observed(bus, dummy_provider):
    dp_id = uuid.uuid4()
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id)
    binding.direction = "BOTH"

    await adapter.reload_bindings([binding])
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    dummy_provider.send.assert_not_awaited()


@pytest.mark.asyncio
async def test_provider_failures_publish_warning(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    dummy_provider.send.return_value = MessageSendResult("dummy", "default", False, "boom")
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id, send_on_change=False)
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    assert any(getattr(call.args[0], "severity", None) == "warning" for call in bus.publish.call_args_list)


@pytest.mark.asyncio
async def test_complete_provider_failure_is_retried_for_same_condition(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    dummy_provider.send.return_value = MessageSendResult("dummy", "default", False, "temporary")
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id)
    await adapter.reload_bindings([binding])
    event = DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test")

    await adapter._on_value_event(event)
    await _drain_sends(adapter)
    await adapter._on_value_event(event)
    await _drain_sends(adapter)

    assert dummy_provider.send.await_count == 2


@pytest.mark.asyncio
async def test_partial_provider_failure_records_condition_for_same_value(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    dummy_provider.send.side_effect = [
        MessageSendResult("dummy", "ok", True),
        MessageSendResult("dummy", "fail", False, "temporary"),
        MessageSendResult("dummy", "ok", True),
        MessageSendResult("dummy", "fail", False, "temporary"),
    ]
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"ok": {}, "fail": {}}}}},
    )
    binding = _message_binding(
        dp_id,
        providers=[
            {"provider": "dummy", "target": "ok"},
            {"provider": "dummy", "target": "fail"},
        ],
    )
    await adapter.reload_bindings([binding])
    event = DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test")

    await adapter._on_value_event(event)
    await _drain_sends(adapter)
    await adapter._on_value_event(event)
    await _drain_sends(adapter)

    assert dummy_provider.send.await_count == 2


@pytest.mark.asyncio
async def test_value_event_does_not_wait_for_provider_http_call(bus, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    release = asyncio.Event()

    class _SlowProvider(_DummyProvider):
        provider_type = "slow"

        def __init__(self) -> None:
            pass

        async def send(self, **kwargs):
            await release.wait()
            return MessageSendResult("slow", "default", True)

    provider = _SlowProvider()
    register_provider(provider)
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"slow": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id, providers=[{"provider": "slow", "target": "default"}])
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))

    assert len(adapter._send_tasks) == 1
    release.set()
    await _drain_sends(adapter)


@pytest.mark.asyncio
async def test_false_true_transition_during_in_flight_send_is_retried(bus, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    release = asyncio.Event()
    calls = 0

    class _SlowProvider(_DummyProvider):
        provider_type = "slow-transition"

        def __init__(self) -> None:
            pass

        async def send(self, **kwargs):
            nonlocal calls
            calls += 1
            await release.wait()
            return MessageSendResult("slow-transition", "default", True)

    provider = _SlowProvider()
    register_provider(provider)
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"slow-transition": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id, providers=[{"provider": "slow-transition", "target": "default"}])
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=20, quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))

    release.set()
    await _drain_sends(adapter)

    assert calls == 2


@pytest.mark.asyncio
async def test_condition_reset_clears_pending_in_flight_send(bus, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    release = asyncio.Event()
    calls = 0

    class _SlowProvider(_DummyProvider):
        provider_type = "slow-reset"

        def __init__(self) -> None:
            pass

        async def send(self, **kwargs):
            nonlocal calls
            calls += 1
            await release.wait()
            return MessageSendResult("slow-reset", "default", True)

    provider = _SlowProvider()
    register_provider(provider)
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"slow-reset": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id, providers=[{"provider": "slow-reset", "target": "default"}])
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=20, quality="good", source_adapter="test"))

    release.set()
    await _drain_sends(adapter)

    assert calls == 1


@pytest.mark.asyncio
async def test_cooldown_is_recorded_when_condition_resets_during_in_flight_send(bus, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    release = asyncio.Event()
    calls = 0

    class _SlowProvider(_DummyProvider):
        provider_type = "slow-cooldown"

        def __init__(self) -> None:
            pass

        async def send(self, **kwargs):
            nonlocal calls
            calls += 1
            await release.wait()
            return MessageSendResult("slow-cooldown", "default", True)

    provider = _SlowProvider()
    register_provider(provider)
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"slow-cooldown": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(
        dp_id,
        cooldown_seconds=300,
        providers=[{"provider": "slow-cooldown", "target": "default"}],
    )
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=20, quality="good", source_adapter="test"))
    release.set()
    await _drain_sends(adapter)
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    assert calls == 1


@pytest.mark.asyncio
async def test_binding_reload_drops_stale_pending_in_flight_send(bus, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    release = asyncio.Event()
    calls = 0

    class _SlowProvider(_DummyProvider):
        provider_type = "slow-reload"

        def __init__(self) -> None:
            pass

        async def send(self, **kwargs):
            nonlocal calls
            calls += 1
            await release.wait()
            return MessageSendResult("slow-reload", "default", True)

    provider = _SlowProvider()
    register_provider(provider)
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"slow-reload": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id, providers=[{"provider": "slow-reload", "target": "default"}])
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=100, quality="good", source_adapter="test"))
    await adapter.reload_bindings([])

    release.set()
    await _drain_sends(adapter)

    assert calls == 1


@pytest.mark.asyncio
async def test_binding_reload_resets_previous_condition_state(bus, dummy_provider, monkeypatch):
    dp_id = uuid.uuid4()
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Dp(dp_id)))
    adapter = MessageAdapter(
        event_bus=bus,
        config={"providers": {"dummy": {"enabled": True, "targets": {"default": {}}}}},
    )
    binding = _message_binding(dp_id)
    await adapter.reload_bindings([binding])

    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await _drain_sends(adapter)
    await adapter.reload_bindings([binding])
    await adapter._on_value_event(DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test"))
    await _drain_sends(adapter)

    assert dummy_provider.send.await_count == 2


@pytest.mark.asyncio
async def test_send_to_targets_reports_missing_disabled_and_unknown_providers(bus):
    dp_id = uuid.uuid4()
    disabled = _DummyProvider()
    disabled.provider_type = "disabled"
    missing = _DummyProvider()
    missing.provider_type = "missing"
    register_provider(disabled)
    register_provider(missing)
    adapter = MessageAdapter(
        event_bus=bus,
        config={
            "providers": {
                "disabled": {"enabled": False, "targets": {"default": {}}},
                "missing": {"enabled": True, "targets": {}},
            },
        },
    )
    cfg = _message_binding(
        dp_id,
        providers=[
            {"provider": "unknown", "target": "default"},
            {"provider": "disabled", "target": "default"},
            {"provider": "missing", "target": "default"},
        ],
    ).config
    binding = _message_binding(dp_id)
    event = DataValueEvent(datapoint_id=dp_id, value=99, quality="good", source_adapter="test")

    results = await adapter._send_to_targets(adapter.binding_config_schema(**cfg), binding, event, "body")

    assert [result.detail for result in results] == ["provider not registered", "provider disabled", "target not configured"]


@pytest.mark.asyncio
async def test_pushover_provider_posts_payload(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = {"status": 1}
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.text = ""
    monkeypatch.setattr("obs.adapters.message.providers.pushover.httpx.AsyncClient", _FakeAsyncClient)

    result = await PushoverProvider().send(
        provider_config={"enabled": True, "api_token": "app", "targets": {}},
        target_name="phone",
        target_config={"user_key": "user", "device": "iphone", "sound": "pushover"},
        title="Alarm",
        message="Window open",
        context={"priority": 1},
    )

    assert result.ok is True
    url, kwargs, timeout = _FakeAsyncClient.calls[0]
    assert url == "https://api.pushover.net/1/messages.json"
    assert timeout == 10.0
    assert kwargs["data"] == {
        "token": "app",
        "user": "user",
        "message": "Window open",
        "title": "Alarm",
        "device": "iphone",
        "sound": "pushover",
        "priority": 1,
    }


@pytest.mark.asyncio
async def test_pushover_provider_reports_http_error(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = None
    _FakeAsyncClient.status_code = 500
    _FakeAsyncClient.text = "100"
    monkeypatch.setattr("obs.adapters.message.providers.pushover.httpx.AsyncClient", _FakeAsyncClient)

    result = await PushoverProvider().send(
        provider_config={"enabled": True, "api_token": "app", "targets": {}},
        target_name="phone",
        target_config={"user_key": "user"},
        title=None,
        message="Body",
        context={},
    )

    assert result.ok is False
    assert result.detail == "HTTP 500"


@pytest.mark.asyncio
async def test_pushover_provider_reports_body_failure(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = {"status": 0, "errors": ["application token is invalid"]}
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.text = ""
    monkeypatch.setattr("obs.adapters.message.providers.pushover.httpx.AsyncClient", _FakeAsyncClient)

    result = await PushoverProvider().send(
        provider_config={"enabled": True, "api_token": "app", "targets": {}},
        target_name="phone",
        target_config={"user_key": "user"},
        title=None,
        message="Body",
        context={},
    )

    assert result.ok is False
    assert result.detail == "application token is invalid"


@pytest.mark.asyncio
async def test_pushover_provider_rejects_priority_two_before_posting(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = {"status": 1}
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.text = ""
    monkeypatch.setattr("obs.adapters.message.providers.pushover.httpx.AsyncClient", _FakeAsyncClient)

    result = await PushoverProvider().send(
        provider_config={"enabled": True, "api_token": "app", "targets": {}},
        target_name="phone",
        target_config={"user_key": "user"},
        title=None,
        message="Body",
        context={"priority": 2},
    )

    assert result.ok is False
    assert result.detail == "pushover priority=2 requires retry and expire"
    assert _FakeAsyncClient.calls == []


@pytest.mark.asyncio
async def test_telegram_provider_posts_message(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = {"ok": True}
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.text = ""
    monkeypatch.setattr("obs.adapters.message.providers.telegram.httpx.AsyncClient", _FakeAsyncClient)

    result = await TelegramProvider().send(
        provider_config={"enabled": True, "bot_token": "secret", "targets": {}},
        target_name="chat",
        target_config={"chat_id": "123", "disable_notification": True},
        title="OBS",
        message="Hello",
        context={},
    )

    assert result.ok is True
    url, kwargs, _timeout = _FakeAsyncClient.calls[0]
    assert url == "https://api.telegram.org/botsecret/sendMessage"
    assert kwargs["json"] == {"chat_id": "123", "text": "OBS\nHello", "disable_notification": True}


@pytest.mark.asyncio
async def test_telegram_provider_reports_body_failure(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = {"ok": False, "description": "Bad Request: chat not found"}
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.text = ""
    monkeypatch.setattr("obs.adapters.message.providers.telegram.httpx.AsyncClient", _FakeAsyncClient)

    result = await TelegramProvider().send(
        provider_config={"enabled": True, "bot_token": "secret", "targets": {}},
        target_name="chat",
        target_config={"chat_id": "123"},
        title=None,
        message="Hello",
        context={},
    )

    assert result.ok is False
    assert result.detail == "Bad Request: chat not found"


@pytest.mark.asyncio
async def test_sevenio_provider_posts_voice_payload(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.json_body = None
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.text = "100"
    monkeypatch.setattr("obs.adapters.message.providers.sevenio.httpx.AsyncClient", _FakeAsyncClient)

    result = await SevenIoProvider().send(
        provider_config={"enabled": True, "api_key": "key", "sender": "OBS", "targets": {}},
        target_name="voice",
        target_config={"to": "+4100000000", "channel": "voice", "sender": "Home"},
        title="Alarm",
        message="Door",
        context={},
    )

    assert result.ok is True
    url, kwargs, _timeout = _FakeAsyncClient.calls[0]
    assert url == "https://gateway.seven.io/api/voice"
    assert kwargs["headers"] == {"X-Api-Key": "key", "Accept": "application/json"}
    assert kwargs["data"] == {"to": "+4100000000", "text": "Alarm: Door", "from": "Home"}


@pytest.mark.asyncio
async def test_sevenio_provider_reports_body_failure(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.text = "101"
    _FakeAsyncClient.json_body = None
    monkeypatch.setattr("obs.adapters.message.providers.sevenio.httpx.AsyncClient", _FakeAsyncClient)

    result = await SevenIoProvider().send(
        provider_config={"enabled": True, "api_key": "key", "targets": {}},
        target_name="sms",
        target_config={"to": "+4100000000", "channel": "sms"},
        title=None,
        message="Door",
        context={},
    )

    assert result.ok is False
    assert result.detail == "seven.io code 101"


@pytest.mark.asyncio
async def test_sevenio_provider_reports_json_success_false(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.text = ""
    _FakeAsyncClient.json_body = {"messages": [{"success": True}, {"success": False}]}
    monkeypatch.setattr("obs.adapters.message.providers.sevenio.httpx.AsyncClient", _FakeAsyncClient)

    result = await SevenIoProvider().send(
        provider_config={"enabled": True, "api_key": "key", "targets": {}},
        target_name="sms",
        target_config={"to": "+4100000000", "channel": "sms"},
        title=None,
        message="Door",
        context={},
    )

    assert result.ok is False
    assert result.detail == "seven.io response success=false"


@pytest.mark.asyncio
async def test_sevenio_provider_reports_json_success_error_code(monkeypatch):
    _FakeAsyncClient.calls = []
    _FakeAsyncClient.status_code = 200
    _FakeAsyncClient.text = ""
    _FakeAsyncClient.json_body = {"success": "101"}
    monkeypatch.setattr("obs.adapters.message.providers.sevenio.httpx.AsyncClient", _FakeAsyncClient)

    result = await SevenIoProvider().send(
        provider_config={"enabled": True, "api_key": "key", "targets": {}},
        target_name="sms",
        target_config={"to": "+4100000000", "channel": "sms"},
        title=None,
        message="Door",
        context={},
    )

    assert result.ok is False
    assert result.detail == "seven.io response success=false"
