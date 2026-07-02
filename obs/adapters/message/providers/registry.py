"""Provider registry for the MESSAGE adapter."""

from __future__ import annotations

from obs.adapters.message.providers.base import MessageProvider

_providers: dict[str, MessageProvider] = {}


def register_provider(provider: MessageProvider) -> MessageProvider:
    if not provider.provider_type:
        raise ValueError("MESSAGE provider_type must not be empty")
    _providers[provider.provider_type] = provider
    return provider


def get_provider(provider_type: str) -> MessageProvider | None:
    return _providers.get(provider_type)


def all_providers() -> dict[str, MessageProvider]:
    return dict(_providers)
