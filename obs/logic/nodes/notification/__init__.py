"""Notification and message archive function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.notification.message_archive import NODE_TYPE as MESSAGE_ARCHIVE
from obs.logic.nodes.notification.notify_message import NODE_TYPE as NOTIFY_MESSAGE
from obs.logic.nodes.notification.notify_pushover import NODE_TYPE as NOTIFY_PUSHOVER
from obs.logic.nodes.notification.notify_sms import NODE_TYPE as NOTIFY_SMS

NODE_TYPES: tuple[NodeTypeDef, ...] = (
    NOTIFY_MESSAGE,
    NOTIFY_PUSHOVER,
    NOTIFY_SMS,
    MESSAGE_ARCHIVE,
)

__all__ = ["NODE_TYPES"]
