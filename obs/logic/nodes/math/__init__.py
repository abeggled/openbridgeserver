"""Arithmetic, statistics and counter function blocks.

Registration only — see ``docs/architecture/logic-nodes.md``. Add a new block
by creating its module next to this file and listing it in ``NODE_TYPES``.
"""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.math.avg_multi import NODE_TYPE as AVG_MULTI
from obs.logic.nodes.math.clamp import NODE_TYPE as CLAMP
from obs.logic.nodes.math.consumption_counter import NODE_TYPE as CONSUMPTION_COUNTER
from obs.logic.nodes.math.formula import NODE_TYPE as MATH_FORMULA
from obs.logic.nodes.math.heating_circuit import NODE_TYPE as HEATING_CIRCUIT
from obs.logic.nodes.math.min_max_tracker import NODE_TYPE as MIN_MAX_TRACKER
from obs.logic.nodes.math.random_value import NODE_TYPE as RANDOM_VALUE
from obs.logic.nodes.math.scale import NODE_TYPE as MATH_MAP
from obs.logic.nodes.math.statistics import NODE_TYPE as STATISTICS

NODE_TYPES: tuple[NodeTypeDef, ...] = (
    MATH_FORMULA,
    MATH_MAP,
    CLAMP,
    RANDOM_VALUE,
    STATISTICS,
    AVG_MULTI,
    HEATING_CIRCUIT,
    MIN_MAX_TRACKER,
    CONSUMPTION_COUNTER,
)

__all__ = ["NODE_TYPES"]
