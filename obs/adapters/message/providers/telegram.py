"""Telegram Bot API MESSAGE provider."""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import BaseModel, Field, model_validator

from obs.adapters.message.providers.base import MessageSendResult


class TelegramConfig(BaseModel):
    enabled: bool = False
    bot_token: str = Field(default="", json_schema_extra={"format": "password"})
    targets: dict[str, "TelegramTarget"] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _validate_enabled_provider(self) -> "TelegramConfig":
        if self.enabled and not self.bot_token.strip():
            raise ValueError("Telegram bot_token is required when provider is enabled")
        return self


class TelegramTarget(BaseModel):
    chat_id: str
    disable_notification: bool = False

    @model_validator(mode="after")
    def _validate_target(self) -> "TelegramTarget":
        if not self.chat_id.strip():
            raise ValueError("Telegram chat_id is required")
        return self


class TelegramProvider:
    provider_type = "telegram"
    config_schema = TelegramConfig
    target_schema = TelegramTarget

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
        cfg = TelegramConfig(**provider_config)
        target = TelegramTarget(**target_config)
        text = f"{title}\n{message}" if title else message
        payload = {
            "chat_id": target.chat_id,
            "text": text,
            "disable_notification": target.disable_notification,
        }
        url = f"https://api.telegram.org/bot{cfg.bot_token}/sendMessage"
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            return MessageSendResult("telegram", target_name, False, f"HTTP {response.status_code}")
        ok, detail = _telegram_response_ok(response)
        if not ok:
            return MessageSendResult("telegram", target_name, False, detail)
        return MessageSendResult("telegram", target_name, True)


def _telegram_response_ok(response: httpx.Response) -> tuple[bool, str]:
    try:
        body = response.json()
    except Exception:
        return True, ""

    if not isinstance(body, dict) or "ok" not in body:
        return True, ""
    if body["ok"] is True:
        return True, ""

    description = body.get("description")
    if description:
        return False, str(description)
    return False, "telegram ok=false"
