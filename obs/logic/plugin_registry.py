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
    # A type name belongs to whoever registered it first. Overwriting would let
    # one plugin silently replace another, and the hot-reload bookkeeping would
    # then unregister the type when the wrong file goes away. A plugin's own
    # reload unregisters its old types first, so it never hits this check.
    from obs.logic.registry import NODE_TYPE_REGISTRY

    if type_name in NODE_TYPE_REGISTRY:
        logger.error("Plugin node type %r (%s) clashes with a built-in block — ignored", type_name, cls.__name__)
        return cls
    if type_name in _registry:
        owner = _registry[type_name]
        logger.error(
            "Plugin node type %r (%s.%s) is already provided by %s.%s — ignored",
            type_name,
            cls.__module__,
            cls.__name__,
            owner.__module__,
            owner.__name__,
        )
        return cls
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
