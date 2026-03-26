"""
Adapter Registry — Phase 2/3

Self-registering pattern: adapters decorate their class with @register.
start_all() lädt Adapter-Konfigurationen und Bindings aus der DB.
"""
from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

_adapters: dict[str, type] = {}   # adapter_type → AdapterBase subclass
_instances: dict[str, Any] = {}   # adapter_type → live instance


# ---------------------------------------------------------------------------
# Registration (decorator)
# ---------------------------------------------------------------------------

def register(cls: type) -> type:
    """Class decorator: register an AdapterBase subclass."""
    if not hasattr(cls, "adapter_type") or not cls.adapter_type:
        raise TypeError(f"{cls.__name__} must define adapter_type")
    _adapters[cls.adapter_type] = cls
    logger.debug("Adapter registered: %s", cls.adapter_type)
    return cls


# ---------------------------------------------------------------------------
# Lookup
# ---------------------------------------------------------------------------

def get_class(adapter_type: str) -> type | None:
    return _adapters.get(adapter_type)


def all_types() -> list[str]:
    return list(_adapters.keys())


def all_classes() -> dict[str, type]:
    return dict(_adapters)


# ---------------------------------------------------------------------------
# Instance management (runtime)
# ---------------------------------------------------------------------------

async def start_all(event_bus: Any, db: Any) -> None:
    """
    Instantiate, connect, and load bindings for all registered adapters.

    For each registered adapter:
      1. Load config dict from adapter_configs table (or empty dict)
      2. Instantiate with (event_bus=bus, config=config_dict)
      3. Connect
      4. Load enabled bindings from DB and call reload_bindings()
    """
    from opentws.models.binding import AdapterBinding

    for adapter_type, cls in _adapters.items():
        try:
            # 1. Config
            row = await db.fetchone(
                "SELECT config FROM adapter_configs WHERE adapter_type=? AND enabled=1",
                (adapter_type,),
            )
            config_dict: dict = json.loads(row["config"]) if row else {}

            # 2. Instantiate
            instance = cls(event_bus=event_bus, config=config_dict)

            # 3. Connect
            await instance.connect()
            _instances[adapter_type] = instance

            # 4. Bindings
            binding_rows = await db.fetchall(
                "SELECT * FROM adapter_bindings WHERE adapter_type=? AND enabled=1",
                (adapter_type,),
            )
            bindings = [_row_to_binding(r) for r in binding_rows]
            await instance.reload_bindings(bindings)

            logger.info(
                "Adapter started: %s (%d bindings)", adapter_type, len(bindings)
            )
        except Exception:
            logger.exception("Failed to start adapter: %s", adapter_type)


async def stop_all() -> None:
    """Disconnect all running adapter instances."""
    for adapter_type, instance in list(_instances.items()):
        try:
            await instance.disconnect()
            logger.info("Adapter stopped: %s", adapter_type)
        except Exception:
            logger.exception("Error stopping adapter: %s", adapter_type)
    _instances.clear()


def get_instance(adapter_type: str) -> Any | None:
    return _instances.get(adapter_type)


def get_status() -> dict[str, dict]:
    """Return connection status for all registered adapters."""
    result = {}
    for adapter_type in _adapters:
        instance = _instances.get(adapter_type)
        result[adapter_type] = {
            "registered": True,
            "running": instance is not None,
            "connected": instance.connected if instance else False,
        }
    return result


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _row_to_binding(row: Any) -> Any:
    from opentws.models.binding import AdapterBinding
    import uuid
    from datetime import datetime
    return AdapterBinding(
        id=uuid.UUID(row["id"]),
        datapoint_id=uuid.UUID(row["datapoint_id"]),
        adapter_type=row["adapter_type"],
        direction=row["direction"],
        config=json.loads(row["config"]),
        enabled=bool(row["enabled"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
