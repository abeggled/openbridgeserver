"""Shared building blocks for built-in node definitions.

Only helpers that every node module may depend on belong here. This module must
stay free of node-specific knowledge — it may not import any concrete node
module, category package or the registry.
"""

from __future__ import annotations

from obs.logic.models import NodeTypePort


def port(id_: str, label: str, type_: str = "value") -> NodeTypePort:
    """Declare a node port.

    ``type_`` is one of ``value``, ``trigger``, ``string``, ``boolean`` or
    ``number`` — the set the catalogue contract test accepts.
    """
    return NodeTypePort(id=id_, label=label, type=type_)
