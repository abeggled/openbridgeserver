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


# The operator vocabulary a "condition" rule accepts — shared by ``decision``
# and ``value_mapping``, whose rule lists are both matched by
# ``GraphExecutor._condition_matches``. Kept as one constant so the two node
# modules' published ``config_schema`` cannot drift apart from each other or
# from the executor's actual accepted operators.
CONDITION_OPERATORS: tuple[str, ...] = (
    "eq",
    "ne",
    "gt",
    "lt",
    "gte",
    "lte",
    "range",
    "text_eq",
    "contains",
    "starts_with",
    "ends_with",
    "regex",
)
