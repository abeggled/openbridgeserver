"""seven.io SMS and Voice MESSAGE provider."""

from __future__ import annotations

import json
from enum import Enum
from typing import Any

import httpx
from pydantic import BaseModel, Field, model_validator

from obs.adapters.message.providers.base import MessageSendResult


class SevenIoChannel(str, Enum):
    SMS = "sms"
    VOICE = "voice"


class SevenIoConfig(BaseModel):
    enabled: bool = False
    api_key: str = Field(default="", json_schema_extra={"format": "password"})
    sender: str | None = None
    targets: dict[str, "SevenIoTarget"] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_enabled_provider(self) -> "SevenIoConfig":
        if self.enabled and not self.api_key.strip():
            raise ValueError("seven.io api_key is required when provider is enabled")
        return self


class SevenIoTarget(BaseModel):
    to: str
    channel: SevenIoChannel = SevenIoChannel.SMS
    sender: str | None = None

    @model_validator(mode="after")
    def _validate_target(self) -> "SevenIoTarget":
        if not self.to.strip():
            raise ValueError("seven.io to is required")
        return self


class SevenIoProvider:
    provider_type = "seven.io"
    config_schema = SevenIoConfig
    target_schema = SevenIoTarget

    async def send(
        self,
        *,
        provider_config: dict[str, Any],
        target_name: str,
        target_config: dict[str, Any],
        title: str | None,
        message: str,
        context: dict[str, Any],
    ) -> MessageSendResult:
        cfg = SevenIoConfig(**provider_config)
        target = SevenIoTarget(**target_config)
        text = f"{title}: {message}" if title else message
        payload: dict[str, Any] = {"to": target.to, "text": text}
        sender = target.sender or cfg.sender
        if sender:
            payload["from"] = sender
        endpoint = "voice" if target.channel == SevenIoChannel.VOICE else "sms"
        headers = {"X-Api-Key": cfg.api_key, "Accept": "application/json"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(f"https://gateway.seven.io/api/{endpoint}", data=payload, headers=headers)
        if response.status_code >= 400:
            return MessageSendResult("seven.io", target_name, False, f"HTTP {response.status_code}")
        ok, detail = _sevenio_response_ok(response)
        if not ok:
            return MessageSendResult("seven.io", target_name, False, detail)
        return MessageSendResult("seven.io", target_name, True)


def _sevenio_response_ok(response: httpx.Response) -> tuple[bool, str]:
    try:
        body = response.json()
    except Exception:
        body_text = (getattr(response, "text", "") or "").strip()
        if not body_text:
            return True, ""
        code = body_text.split()[0].strip()
        if code in {"1", "100"}:
            return True, ""
        try:
            body = json.loads(body_text)
        except json.JSONDecodeError:
            return (code == "100", f"seven.io code {code}" if code != "100" else "")

    if not isinstance(body, dict | list):
        code = str(body).strip()
        return (code == "100", f"seven.io code {code}" if code != "100" else "")

    success_values = _collect_success_values(body)
    if success_values and not all(_is_success_value(value) for value in success_values):
        return False, "seven.io response success=false"
    return True, ""


def _collect_success_values(value: Any) -> list[Any]:
    if isinstance(value, dict):
        values = []
        if "success" in value:
            values.append(value["success"])
        for nested in value.values():
            values.extend(_collect_success_values(nested))
        return values
    if isinstance(value, list):
        values = []
        for nested in value:
            values.extend(_collect_success_values(nested))
        return values
    return []


def _is_success_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return value == 100
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized == "100":
            return True
        if normalized in {"true", "ok", "success"}:
            return True
        if normalized in {"", "0", "false", "no", "failed", "error"}:
            return False
        if normalized.isdecimal():
            return False
        return True
    return bool(value)
