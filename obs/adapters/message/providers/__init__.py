"""Built-in MESSAGE provider registrations."""

from obs.adapters.message.providers.pushover import PushoverProvider
from obs.adapters.message.providers.sevenio import SevenIoProvider
from obs.adapters.message.providers.telegram import TelegramProvider
from obs.adapters.message.providers.registry import all_providers, get_provider, register_provider

register_provider(PushoverProvider())
register_provider(TelegramProvider())
register_provider(SevenIoProvider())

__all__ = [
    "PushoverProvider",
    "SevenIoProvider",
    "TelegramProvider",
    "all_providers",
    "get_provider",
    "register_provider",
]
