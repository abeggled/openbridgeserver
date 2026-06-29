"""Pushover MESSAGE provider."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field, model_validator

from obs.adapters.message.providers.base import MessageSendResult


class PushoverConfig(BaseModel):
    enabled: bool = False
    api_token: str = Field(default="", json_schema_extra={"format": "password"})
    targets: dict[str, "PushoverTarget"] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_enabled_provider(self) -> "PushoverConfig":
        if self.enabled and not self.api_token.strip():
            raise ValueError("Pushover api_token is required when provider is enabled")
        return self


class PushoverTarget(BaseModel):
    user_key: str = Field(default="", json_schema_extra={"format": "password"})
    device: str | None = None
    sound: str | None = None

    @model_validator(mode="after")
    def _validate_target(self) -> "PushoverTarget":
        if not self.user_key.strip():
            raise ValueError("Pushover user_key is required")
        return self


class PushoverProvider:
    provider_type = "pushover"
    config_schema = PushoverConfig
    target_schema = PushoverTarget

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
        cfg = PushoverConfig(**provider_config)
        target = PushoverTarget(**target_config)
        payload: dict[str, Any] = {
            "token": cfg.api_token,
            "user": target.user_key,
            "message": message,
        }
        if title:
            payload["title"] = title
        if target.device:
            payload["device"] = target.device
        if target.sound:
            payload["sound"] = target.sound
        priority = context.get("priority")
        if priority is not None:
            if priority == 2:
                return MessageSendResult("pushover", target_name, False, "pushover priority=2 requires retry and expire")
            payload["priority"] = priority

        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post("https://api.pushover.net/1/messages.json", data=payload)
        if response.status_code >= 400:
            return MessageSendResult("pushover", target_name, False, f"HTTP {response.status_code}")
        ok, detail = _pushover_response_ok(response)
        if not ok:
            return MessageSendResult("pushover", target_name, False, detail)
        return MessageSendResult("pushover", target_name, True)


def _pushover_response_ok(response: httpx.Response) -> tuple[bool, str]:
    try:
        body = response.json()
    except Exception:
        return True, ""

    if not isinstance(body, dict) or "status" not in body:
        return True, ""

    status = body["status"]
    if status is True or status == 1 or (isinstance(status, str) and status.strip().lower() in {"1", "true"}):
        return True, ""

    errors = body.get("errors")
    if isinstance(errors, list) and errors:
        return False, "; ".join(str(error) for error in errors)
    return False, f"pushover status={status}"
