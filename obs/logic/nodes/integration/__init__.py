"""External system integration function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.integration.api_client import NODE_TYPE as API_CLIENT
from obs.logic.nodes.integration.host_check import NODE_TYPE as HOST_CHECK
from obs.logic.nodes.integration.ical import NODE_TYPE as ICAL
from obs.logic.nodes.integration.json_extractor import NODE_TYPE as JSON_EXTRACTOR
from obs.logic.nodes.integration.substring_extractor import NODE_TYPE as SUBSTRING_EXTRACTOR
from obs.logic.nodes.integration.wake_on_lan import NODE_TYPE as WAKE_ON_LAN
from obs.logic.nodes.integration.xml_extractor import NODE_TYPE as XML_EXTRACTOR

NODE_TYPES: tuple[NodeTypeDef, ...] = (
    WAKE_ON_LAN,
    HOST_CHECK,
    JSON_EXTRACTOR,
    XML_EXTRACTOR,
    SUBSTRING_EXTRACTOR,
    ICAL,
    API_CLIENT,
)

__all__ = ["NODE_TYPES"]
