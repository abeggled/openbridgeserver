"""
Adapter Registry — Phase 5 (Multi-Instance)

Jeder Adapter-Typ (KNX, MQTT, MODBUS_TCP …) kann mehrfach instanziert werden.
Instanzen werden durch eine UUID identifiziert (adapter_instances Tabelle in DB).

Rückwärtskompatibel: get_instance(adapter_type) liefert die erste laufende Instanz.
"""
from __future__ import annotations

import json
import logging
import uuid
from typing import Any

logger = logging.getLogger(__name__)

_adapters: dict[str, type] = {}    # adapter_type → AdapterBase Klasse
_instances: dict[str, Any] = {}    # instance_id (str) → laufende Instanz


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
    Alle aktivierten Adapter-Instanzen aus adapter_instances laden,
    verbinden und Bindings laden.
    """
    rows = await db.fetchall(
        "SELECT * FROM adapter_instances WHERE enabled=1"
    )
    if not rows:
        logger.info("Keine Adapter-Instanzen in DB gefunden")
        return

    for row in rows:
        instance_id = row["id"]
        adapter_type = row["adapter_type"]
        cls = _adapters.get(adapter_type)
        if cls is None:
            logger.warning(
                "Adapter-Klasse '%s' nicht registriert — Instanz %s übersprungen",
                adapter_type, instance_id,
            )
            continue

        try:
            config_dict: dict = json.loads(row["config"]) if row["config"] else {}
            instance = cls(
                event_bus=event_bus,
                config=config_dict,
                instance_id=uuid.UUID(instance_id),
                name=row["name"],
            )
            await instance.connect()
            _instances[instance_id] = instance

            binding_rows = await db.fetchall(
                "SELECT * FROM adapter_bindings WHERE adapter_instance_id=? AND enabled=1",
                (instance_id,),
            )
            bindings = [_row_to_binding(r) for r in binding_rows]
            await instance.reload_bindings(bindings)

            logger.info(
                "Adapter gestartet: %s '%s' (%d Bindings)",
                adapter_type, row["name"], len(bindings),
            )
        except Exception:
            logger.exception(
                "Fehler beim Starten von Adapter %s '%s'", adapter_type, row["name"]
            )


async def stop_all() -> None:
    """Alle laufenden Adapter-Instanzen trennen."""
    for instance_id, instance in list(_instances.items()):
        try:
            await instance.disconnect()
            logger.info(
                "Adapter gestoppt: %s '%s'",
                instance.adapter_type, instance._instance_name,
            )
        except Exception:
            logger.exception("Fehler beim Stoppen von Instanz %s", instance_id)
    _instances.clear()


# ---------------------------------------------------------------------------
# Lookup by ID / type
# ---------------------------------------------------------------------------

def get_instance_by_id(instance_id: str | uuid.UUID) -> Any | None:
    """Instanz anhand der UUID zurückgeben."""
    return _instances.get(str(instance_id))


def get_instance(adapter_type: str) -> Any | None:
    """
    Rückwärtskompatibel: erste laufende Instanz des angegebenen Typs.
    Für Multi-Instance: get_instance_by_id() verwenden.
    """
    for instance in _instances.values():
        if instance.adapter_type == adapter_type:
            return instance
    return None


def get_all_instances() -> dict[str, Any]:
    """Alle laufenden Instanzen zurückgeben (instance_id → Instanz)."""
    return dict(_instances)


def get_status() -> dict[str, dict]:
    """Status aller registrierten Adapter-Typen (für /system/adapters)."""
    result = {}
    for adapter_type, cls in _adapters.items():
        instance = get_instance(adapter_type)
        result[adapter_type] = {
            "registered": True,
            "running": instance is not None,
            "connected": instance.connected if instance else False,
            "hidden": getattr(cls, "hidden", False),
        }
    return result


# ---------------------------------------------------------------------------
# Hot-Reload einer einzelnen Instanz (für API: PATCH /instances/{id})
# ---------------------------------------------------------------------------

async def restart_instance(instance_id: str, event_bus: Any, db: Any) -> Any | None:
    """
    Laufende Instanz stoppen, neu aus DB laden und starten.
    Gibt die neue Instanz zurück, oder None bei Fehler.
    """
    # Alte Instanz stoppen
    old = _instances.pop(instance_id, None)
    if old:
        try:
            await old.disconnect()
        except Exception:
            logger.exception("Fehler beim Stoppen von Instanz %s", instance_id)

    # Neu aus DB laden
    row = await db.fetchone(
        "SELECT * FROM adapter_instances WHERE id=? AND enabled=1", (instance_id,)
    )
    if row is None:
        logger.warning("Instanz %s nicht in DB oder deaktiviert", instance_id)
        return None

    cls = _adapters.get(row["adapter_type"])
    if cls is None:
        logger.warning("Adapter-Klasse '%s' nicht registriert", row["adapter_type"])
        return None

    try:
        config_dict = json.loads(row["config"]) if row["config"] else {}
        instance = cls(
            event_bus=event_bus,
            config=config_dict,
            instance_id=uuid.UUID(instance_id),
            name=row["name"],
        )
        await instance.connect()
        _instances[instance_id] = instance

        binding_rows = await db.fetchall(
            "SELECT * FROM adapter_bindings WHERE adapter_instance_id=? AND enabled=1",
            (instance_id,),
        )
        bindings = [_row_to_binding(r) for r in binding_rows]
        await instance.reload_bindings(bindings)

        logger.info(
            "Adapter neu gestartet: %s '%s' (%d Bindings)",
            row["adapter_type"], row["name"], len(bindings),
        )
        return instance
    except Exception:
        logger.exception(
            "Fehler beim Neustart von Adapter %s '%s'", row["adapter_type"], row["name"]
        )
        return None


async def start_instance(instance_id: str, event_bus: Any, db: Any) -> Any | None:
    """Neue Instanz aus DB laden und starten."""
    return await restart_instance(instance_id, event_bus, db)


async def stop_instance(instance_id: str) -> None:
    """Einzelne Instanz stoppen und aus Registry entfernen."""
    instance = _instances.pop(instance_id, None)
    if instance:
        try:
            await instance.disconnect()
            logger.info(
                "Adapter gestoppt: %s '%s'",
                instance.adapter_type, instance._instance_name,
            )
        except Exception:
            logger.exception("Fehler beim Stoppen von Instanz %s", instance_id)


# ---------------------------------------------------------------------------
# Bindings neu laden für eine Instanz
# ---------------------------------------------------------------------------

async def reload_instance_bindings(instance_id: str, db: Any) -> None:
    """Bindings einer laufenden Instanz aus DB neu laden."""
    instance = _instances.get(instance_id)
    if instance is None:
        return
    binding_rows = await db.fetchall(
        "SELECT * FROM adapter_bindings WHERE adapter_instance_id=? AND enabled=1",
        (instance_id,),
    )
    bindings = [_row_to_binding(r) for r in binding_rows]
    await instance.reload_bindings(bindings)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _row_to_binding(row: Any) -> Any:
    from opentws.models.binding import AdapterBinding
    from datetime import datetime
    instance_id_str = row["adapter_instance_id"] if row["adapter_instance_id"] else None
    throttle    = row["send_throttle_ms"]
    min_delta   = row["send_min_delta"]
    min_delta_p = row["send_min_delta_pct"]
    return AdapterBinding(
        id=uuid.UUID(row["id"]),
        datapoint_id=uuid.UUID(row["datapoint_id"]),
        adapter_type=row["adapter_type"],
        adapter_instance_id=uuid.UUID(instance_id_str) if instance_id_str else None,
        direction=row["direction"],
        config=json.loads(row["config"]),
        enabled=bool(row["enabled"]),
        send_throttle_ms=int(throttle) if throttle is not None else None,
        send_on_change=bool(row["send_on_change"]),
        send_min_delta=float(min_delta) if min_delta is not None else None,
        send_min_delta_pct=float(min_delta_p) if min_delta_p is not None else None,
        value_formula=row["value_formula"] or None,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )
