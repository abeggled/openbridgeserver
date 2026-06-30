"""MESSAGE adapter.

Observes DataValueEvent updates and sends notifications through provider
plugins when a per-binding value condition is fulfilled.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import uuid
from collections import deque
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

from obs.adapters.base import AdapterBase
from obs.adapters.message import providers as message_providers
from obs.adapters.message.providers.base import MessageSendResult
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent
from obs.core.json import json_dumps

logger = logging.getLogger(__name__)

MessageOperator = Literal["any", "=", "==", "<", "<=", ">", ">=", "!=", "contains", "contains not", "starts with", "ends with"]
MAX_PENDING_EVENTS_PER_BINDING = 100
_NO_PENDING_COALESCE = object()
_PLACEHOLDER_PATTERN = re.compile("###(?:DP|DPU|DPN|DPI|TS)###")


class ProviderTargetRef(BaseModel):
    provider: str
    target: str


class MessageAdapterConfig(BaseModel):
    providers: dict[str, dict[str, Any]] = Field(
        default_factory=dict,
        title="Provider",
        description="Provider-Konfigurationen, z. B. pushover, telegram oder seven.io mit targets.",
    )

    @model_validator(mode="after")
    def _validate_providers(self) -> "MessageAdapterConfig":
        for provider_type, provider_config in self.providers.items():
            provider = message_providers.get_provider(provider_type)
            if provider is None:
                raise ValueError(f"Unknown MESSAGE provider: {provider_type}")
            parsed_without_targets = provider.config_schema(**{**provider_config, "targets": {}})
            if getattr(parsed_without_targets, "enabled", False):
                provider.config_schema(**provider_config)
        return self


class MessageBindingConfig(BaseModel):
    operator: MessageOperator = Field(default="==")
    compare_value: Any = None
    message: str = Field(default="###DPN###: ###DP### ###DPU###")
    title: str | None = None
    providers: list[ProviderTargetRef] = Field(default_factory=list)
    priority: int | None = None
    send_on_change: bool = True
    cooldown_seconds: int = Field(default=0, ge=0)
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_targets(self) -> "MessageBindingConfig":
        if not self.message.strip():
            raise ValueError("MESSAGE binding message must not be empty")
        if self.enabled and not self.providers:
            raise ValueError("MESSAGE binding requires at least one target")
        seen_targets: set[tuple[str, str]] = set()
        for ref in self.providers:
            key = (ref.provider, ref.target)
            if key in seen_targets:
                raise ValueError(f"Duplicate MESSAGE target: {ref.provider}/{ref.target}")
            seen_targets.add(key)
        if self.priority is not None and any(ref.provider == "pushover" for ref in self.providers):
            if self.priority == 2:
                raise ValueError("Pushover emergency priority is not supported")
            if self.priority not in {-2, -1, 0, 1}:
                raise ValueError("Pushover priority must be one of -2, -1, 0 or 1")
        return self


class _BindingState:
    def __init__(self) -> None:
        self.last_condition: bool = False
        self.last_sent_monotonic: float | None = None
        self.last_value: Any = object()
        self.in_flight: bool = False
        self.in_flight_key: Any = _NO_PENDING_COALESCE
        self.reset_version: int = 0
        self.pending_events: deque[tuple[Any, DataValueEvent, Any]] = deque()

    def queue_pending_event(self, binding: Any, event: DataValueEvent, coalesce_key: Any = _NO_PENDING_COALESCE) -> None:
        if coalesce_key is not _NO_PENDING_COALESCE:
            if not self.pending_events and _values_equal(coalesce_key, self.in_flight_key):
                return
            for index, (_binding, _event, pending_key) in enumerate(self.pending_events):
                if _values_equal(coalesce_key, pending_key):
                    self.pending_events[index] = (binding, event, coalesce_key)
                    return
        if len(self.pending_events) >= MAX_PENDING_EVENTS_PER_BINDING:
            self.pending_events.popleft()
        self.pending_events.append((binding, event, coalesce_key))


def _binding_config(binding: Any) -> MessageBindingConfig:
    return MessageBindingConfig(**{**binding.config, "enabled": bool(getattr(binding, "enabled", True))})


def _as_number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _values_equal(left: Any, right: Any) -> bool:
    try:
        return left == right
    except Exception:
        return False


def _pending_coalesce_key(cfg: MessageBindingConfig, event: DataValueEvent) -> Any:
    if not cfg.send_on_change:
        return _NO_PENDING_COALESCE
    return event.value if cfg.operator == "any" else True


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off"}:
            return False
    return None


def evaluate_condition(value: Any, operator: str, compare_value: Any) -> bool:
    """Evaluate a MESSAGE binding condition without raising for bad input."""
    op = "==" if operator == "=" else operator
    if op == "any":
        return True
    if op in {"==", "!="}:
        left_bool = _as_bool(value)
        right_bool = _as_bool(compare_value)
        if left_bool is not None and right_bool is not None:
            equal = left_bool is right_bool
        else:
            left_number = _as_number(value)
            right_number = _as_number(compare_value)
            if left_number is not None and right_number is not None:
                equal = left_number == right_number
            else:
                equal = str(value) == str(compare_value)
        return equal if op == "==" else not equal

    if op in {"<", "<=", ">", ">="}:
        left_number = _as_number(value)
        right_number = _as_number(compare_value)
        if left_number is None or right_number is None:
            return False
        if op == "<":
            return left_number < right_number
        if op == "<=":
            return left_number <= right_number
        if op == ">":
            return left_number > right_number
        return left_number >= right_number

    left = "" if value is None else str(value)
    right = "" if compare_value is None else str(compare_value)
    if op == "contains":
        return right in left
    if op == "contains not":
        return right not in left
    if op == "starts with":
        return left.startswith(right)
    if op == "ends with":
        return left.endswith(right)
    return False


def _format_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    return json_dumps(value)


def render_message(template: str, *, value: Any, unit: str | None, name: str, datapoint_id: uuid.UUID, ts: datetime) -> str:
    replacements = {
        "###DP###": _format_value(value),
        "###DPU###": unit or "",
        "###DPN###": name,
        "###DPI###": str(datapoint_id),
        "###TS###": ts.isoformat(),
    }
    return _PLACEHOLDER_PATTERN.sub(lambda match: replacements[match.group(0)], template)


@register
class MessageAdapter(AdapterBase):
    adapter_type = "MESSAGE"
    config_schema = MessageAdapterConfig
    binding_config_schema = MessageBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._cfg = MessageAdapterConfig(**self._config)
        self._binding_map: dict[uuid.UUID, list[Any]] = {}
        self._states: dict[uuid.UUID, _BindingState] = {}
        self._send_tasks: set[asyncio.Task] = set()
        self._subscribed = False

    async def connect(self) -> None:
        if not self._subscribed:
            self._bus.subscribe(DataValueEvent, self._on_value_event)
            self._subscribed = True
        await self._publish_status(True, "MESSAGE adapter ready", code="connected")

    async def disconnect(self) -> None:
        if self._subscribed:
            self._bus.unsubscribe(DataValueEvent, self._on_value_event)
            self._subscribed = False
        for task in self._send_tasks:
            task.cancel()
        if self._send_tasks:
            await asyncio.gather(*self._send_tasks, return_exceptions=True)
            self._send_tasks.clear()
        self._binding_map.clear()
        await self._publish_status(False, "Disconnected", code="disconnected")

    async def _on_bindings_reloaded(self) -> None:
        self._binding_map.clear()
        active_ids: set[uuid.UUID] = set()
        for binding in self._bindings:
            if binding.direction != "SOURCE":
                continue
            try:
                cfg = _binding_config(binding)
            except Exception:
                logger.warning("Invalid MESSAGE binding config for %s skipped", binding.id)
                continue
            if not cfg.enabled:
                continue
            active_ids.add(binding.id)
            self._binding_map.setdefault(binding.datapoint_id, []).append(binding)
            self._states[binding.id] = _BindingState()
        for binding_id in list(self._states):
            if binding_id not in active_ids:
                self._states.pop(binding_id, None)

    async def read(self, binding: Any) -> Any:
        return None

    async def write(self, binding: Any, value: Any) -> None:
        event = DataValueEvent(
            datapoint_id=binding.datapoint_id,
            value=value,
            quality="good",
            source_adapter=self.adapter_type,
            binding_id=binding.id,
        )
        await self._handle_binding_event(binding, event, ignore_repetition=True)

    async def _on_value_event(self, event: DataValueEvent) -> None:
        if event.quality != "good":
            return
        for binding in self._binding_map.get(event.datapoint_id, []):
            await self._handle_binding_event(binding, event)

    async def _handle_binding_event(self, binding: Any, event: DataValueEvent, *, ignore_repetition: bool = False) -> None:
        cfg = _binding_config(binding)
        condition = evaluate_condition(event.value, cfg.operator, cfg.compare_value)
        state = self._states.setdefault(binding.id, _BindingState())
        if not condition:
            state.last_condition = False
            state.in_flight_key = _NO_PENDING_COALESCE
            state.reset_version += 1
            state.pending_events.clear()
            return

        now = time.monotonic()
        if not ignore_repetition:
            if cfg.send_on_change and cfg.operator == "any" and event.value == state.last_value:
                return
            if cfg.send_on_change and cfg.operator != "any" and state.last_condition:
                return
            if cfg.cooldown_seconds and state.last_sent_monotonic is not None:
                elapsed = now - state.last_sent_monotonic
                if elapsed < cfg.cooldown_seconds:
                    state.last_condition = True
                    return

        dp = _lookup_datapoint(event.datapoint_id)
        rendered = render_message(
            cfg.message,
            value=event.value,
            unit=getattr(dp, "unit", None),
            name=getattr(dp, "name", str(event.datapoint_id)),
            datapoint_id=event.datapoint_id,
            ts=event.ts if event.ts.tzinfo else event.ts.replace(tzinfo=UTC),
        )
        reset_version = state.reset_version
        if state.in_flight and not ignore_repetition:
            state.queue_pending_event(binding, event, _pending_coalesce_key(cfg, event))
            return
        state.in_flight = True
        state.in_flight_key = _pending_coalesce_key(cfg, event) if not ignore_repetition else _NO_PENDING_COALESCE
        task = asyncio.create_task(self._send_and_record(binding, event, cfg, rendered, state, reset_version))
        self._send_tasks.add(task)
        task.add_done_callback(self._send_tasks.discard)

    async def _send_and_record(
        self,
        binding: Any,
        event: DataValueEvent,
        cfg: MessageBindingConfig,
        rendered: str,
        state: _BindingState,
        reset_version: int,
    ) -> None:
        try:
            results = await self._send_to_targets(cfg, binding, event, rendered)
        except Exception as exc:  # pragma: no cover - defensive guard for unexpected task errors
            logger.exception("MESSAGE send task failed unexpectedly")
            results = [MessageSendResult("message", "internal", False, str(exc))]

        success = bool(results) and all(result.ok for result in results)
        partial_success = bool(results) and any(result.ok for result in results)
        if success or partial_success:
            state.last_sent_monotonic = time.monotonic()
            if state.reset_version == reset_version:
                state.last_condition = True
                state.last_value = event.value

        failures = [result for result in results if not result.ok]
        if failures:
            detail = f"MESSAGE provider failures: {len(failures)} target(s)"
            await self._publish_status(True, detail, severity="warning", code="messageProviderFailures", params={"count": len(failures)})
        elif results:
            await self._publish_status(True, "MESSAGE sent", code="messageSent")

        state.in_flight = False
        state.in_flight_key = _NO_PENDING_COALESCE
        while state.pending_events:
            pending = state.pending_events.popleft()
            pending_binding, pending_event, _pending_key = pending
            if not self._is_current_binding(pending_binding):
                continue
            await self._handle_binding_event(pending_binding, pending_event)
            if state.in_flight:
                break

    def _is_current_binding(self, binding: Any) -> bool:
        return any(current is binding for current in self._binding_map.get(binding.datapoint_id, []))

    async def _send_to_targets(
        self,
        cfg: MessageBindingConfig,
        binding: Any,
        event: DataValueEvent,
        rendered: str,
    ) -> list[MessageSendResult]:
        results: list[MessageSendResult] = []
        context = {
            "datapoint_id": str(event.datapoint_id),
            "binding_id": str(binding.id),
            "value": event.value,
            "ts": event.ts.isoformat(),
            "priority": cfg.priority,
        }
        for ref in cfg.providers:
            provider = message_providers.get_provider(ref.provider)
            if provider is None:
                logger.warning("MESSAGE provider '%s' is not registered", ref.provider)
                results.append(MessageSendResult(ref.provider, ref.target, False, "provider not registered"))
                continue
            provider_config = self._cfg.providers.get(ref.provider)
            if not provider_config:
                results.append(MessageSendResult(ref.provider, ref.target, False, "provider not configured"))
                continue
            try:
                parsed_provider_config = provider.config_schema(**provider_config)
            except Exception:
                logger.exception("MESSAGE provider '%s' config is invalid", ref.provider)
                results.append(MessageSendResult(ref.provider, ref.target, False, "provider config invalid"))
                continue
            if not getattr(parsed_provider_config, "enabled", False):
                results.append(MessageSendResult(ref.provider, ref.target, False, "provider disabled"))
                continue
            targets = getattr(parsed_provider_config, "targets", {}) or {}
            target_config = targets.get(ref.target)
            if target_config is None:
                results.append(MessageSendResult(ref.provider, ref.target, False, "target not configured"))
                continue
            try:
                result = await provider.send(
                    provider_config=provider_config,
                    target_name=ref.target,
                    target_config=_model_dump(target_config),
                    title=cfg.title,
                    message=rendered,
                    context=context,
                )
                results.append(result)
            except Exception:
                logger.exception("MESSAGE provider '%s' target '%s' failed", ref.provider, ref.target)
                results.append(MessageSendResult(ref.provider, ref.target, False, "send failed"))
        return results


def _model_dump(value: Any) -> dict[str, Any]:
    if isinstance(value, BaseModel):
        return value.model_dump()
    return dict(value)


def _lookup_datapoint(datapoint_id: uuid.UUID) -> Any | None:
    try:
        from obs.core.registry import get_registry

        return get_registry().get(datapoint_id)
    except Exception:
        return None
