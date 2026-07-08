"""Provider protocol and shared result models for the MESSAGE adapter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel


@dataclass(frozen=True)
class MessageSendResult:
    provider: str
    target: str
    ok: bool
    detail: str = ""


class MessageProvider(Protocol):
    provider_type: str
    config_schema: type[BaseModel]
    target_schema: type[BaseModel]

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
        """Send a notification to one provider target."""
