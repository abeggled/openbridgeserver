"""Public API for OBS logic block plugins.

A minimal plugin looks like this::

    # my_plugin.py  (drop into plugins/ dir, or package as obs-plugin-*)
    from obs.logic.plugin_api import LogicNodePlugin, NodeTypeDef, NodeTypePort, register_node_type

    @register_node_type
    class ShadowControl(LogicNodePlugin):
        type_name = "shadow_control"

        @classmethod
        def node_type_def(cls) -> NodeTypeDef:
            return NodeTypeDef(
                type="shadow_control",
                label="Beschattungssteuerung",
                category="integration",
                description="Berechnet Jalousieposition aus Sonnenhöhe und Raumtemperatur.",
                inputs=[
                    NodeTypePort(id="sun_elevation", label="Sonnenhöhe"),
                    NodeTypePort(id="indoor_temp",   label="Innentemperatur"),
                ],
                outputs=[NodeTypePort(id="position", label="Position (0–100)")],
                config_schema={
                    "threshold_elevation": {
                        "type": "number", "default": 20, "label": "Mindest-Sonnenhöhe (°)"
                    },
                },
                color="#d97706",
            )

        @classmethod
        def evaluate(cls, node_id, inputs, config, state):
            elevation = float(inputs.get("sun_elevation") or 0)
            threshold = float(config.get("threshold_elevation") or 20)
            if elevation < threshold:
                position = 0.0
            else:
                position = min(100.0, (elevation - threshold) * 2)
            return {"position": position}, state

For pip-installable plugins, declare the entry point in pyproject.toml::

    [project.entry-points."obs.logic_blocks"]
    shadow_control = "my_package.plugin"

The module is imported at OBS startup — the @register_node_type decorator
runs automatically and adds the node type to the GUI palette.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

# Re-export for plugin authors so they only need one import
from obs.logic.models import NodeTypeDef, NodeTypePort
from obs.logic.plugin_registry import _register


class LogicNodePlugin(ABC):
    """Base class for all OBS logic block plugins.

    Subclass this, set ``type_name``, implement ``node_type_def`` and
    ``evaluate``, then decorate with ``@register_node_type``.
    """

    #: Unique node type identifier — must match NodeTypeDef.type and be globally unique.
    type_name: str

    @classmethod
    @abstractmethod
    def node_type_def(cls) -> NodeTypeDef:
        """Return the UI metadata (label, ports, config schema, color) for this node."""

    @classmethod
    @abstractmethod
    def evaluate(
        cls,
        node_id: str,
        inputs: dict[str, Any],
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Evaluate the node synchronously.

        Parameters
        ----------
        node_id:
            Stable node ID within the graph — use as a key if you need to store
            per-node data inside ``state``.
        inputs:
            Resolved upstream values, keyed by port ID (as declared in
            ``node_type_def().inputs``).  Missing connections arrive as ``None``.
        config:
            The node's data dict from the GUI editor (matches ``config_schema``).
        state:
            Mutable persistent dict that survives between graph executions
            (e.g. for hysteresis, counters, moving averages).
            The dict is shared by reference — mutate it directly or return an
            updated copy as the second return value.

        Returns
        -------
        tuple[outputs, new_state]
            ``outputs`` is a dict keyed by output port ID.
            ``new_state`` replaces the previous state for this node; return the
            same ``state`` object if nothing changed.
        """


def register_node_type(cls: type[LogicNodePlugin]) -> type[LogicNodePlugin]:
    """Class decorator — registers a LogicNodePlugin with OBS.

    Usage::

        @register_node_type
        class MyBlock(LogicNodePlugin):
            type_name = "my_block"
            ...
    """
    return _register(cls)


__all__ = ["LogicNodePlugin", "NodeTypeDef", "NodeTypePort", "register_node_type"]
