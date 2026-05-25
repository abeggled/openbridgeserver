"""Internal registry for plugin-contributed logic node types.

Do not import this module directly from plugin code — use plugin_api.py instead.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from obs.logic.models import NodeTypeDef

logger = logging.getLogger(__name__)

_registry: dict[str, type[Any]] = {}  # type_name -> LogicNodePlugin subclass


def _register(cls: type[Any]) -> type[Any]:
    type_name: str | None = getattr(cls, "type_name", None)
    if not type_name:
        raise ValueError(f"LogicNodePlugin subclass {cls.__name__} must define a non-empty type_name")
    if type_name in _registry:
        logger.warning("Plugin node type %r already registered — overwriting with %s", type_name, cls.__name__)
    _registry[type_name] = cls
    logger.debug("Registered plugin node type: %r (%s)", type_name, cls.__name__)
    return cls


def _unregister(type_name: str) -> bool:
    """Remove a plugin type from the registry. Returns True if it existed."""
    if type_name in _registry:
        del _registry[type_name]
        logger.debug("Unregistered plugin node type: %r", type_name)
        return True
    return False


def get_plugin_node_type(type_name: str) -> type[Any] | None:
    return _registry.get(type_name)


def get_all_plugin_node_type_defs() -> list[NodeTypeDef]:
    defs = []
    for cls in _registry.values():
        try:
            defs.append(cls.node_type_def())
        except Exception:
            logger.exception("Plugin %s: node_type_def() raised an exception", cls.__name__)
    return defs
