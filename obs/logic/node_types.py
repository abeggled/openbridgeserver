"""Backward-compatible facade for the built-in node catalogue.

The catalogue itself now lives in :mod:`obs.logic.registry`, assembled from the
per-node modules under ``obs/logic/nodes/``. This module only re-exports the
long-standing public names so that existing imports (and branches developed in
parallel) keep working.

New code should import from :mod:`obs.logic.registry` directly.
"""

from __future__ import annotations

from obs.logic.registry import (
    BUILTIN_NODE_TYPES,
    NODE_TYPE_REGISTRY,
    get_node_type,
    list_node_types,
)

__all__ = [
    "BUILTIN_NODE_TYPES",
    "NODE_TYPE_REGISTRY",
    "get_node_type",
    "list_node_types",
]
