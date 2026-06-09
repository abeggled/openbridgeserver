"""Adapter Registry — Phase 5 (Multi-Instance)

Jeder Adapter-Typ (KNX, MQTT, MODBUS_TCP …) kann mehrfach instanziert werden.
Instanzen werden durch eine UUID identifiziert (adapter_instances Tabelle in DB).

Rückwärtskompatibel: get_instance(adapter_type) liefert die erste laufende Instanz.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

_adapters: dict[str, type] = {}  # adapter_type → AdapterBase Klasse
_instances: dict[str, Any] = {}  # instance_id (str) → laufende Instanz
_BINDING_LOAD_DETAIL_PREFIX = "Invalid adapter bindings skipped"


@dataclass(frozen=True)
class BindingLoadIssue:
    binding_id: str
    adapter_instance_id: str | None
    reason: str


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


async def start_all(event_bus: Any, db: Any, value_getter: Any = None) -> None:
    """Alle aktivierten Adapter-Instanzen aus adapter_instances laden,
    verbinden und Bindings laden.
    """
    rows = await db.fetchall("SELECT * FROM adapter_instances WHERE enabled=1")
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
                adapter_type,
                instance_id,
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
            if value_getter and hasattr(instance, "set_value_getter"):
                instance.set_value_getter(value_getter)
            await instance.connect()
            _instances[instance_id] = instance

            binding_rows = await db.fetchall(
                "SELECT * FROM adapter_bindings WHERE adapter_instance_id=? AND enabled=1",
                (instance_id,),
            )
            bindings, issues = await reload_instance_from_rows(instance, binding_rows)

            logger.info(
                "Adapter gestartet: %s '%s' (%d Bindings, %d invalid skipped)",
                adapter_type,
                row["name"],
                len(bindings),
                len(issues),
            )
        except Exception:
            logger.exception("Fehler beim Starten von Adapter %s '%s'", adapter_type, row["name"])


async def stop_all() -> None:
    """Alle laufenden Adapter-Instanzen trennen."""
    for instance_id, instance in list(_instances.items()):
        try:
            await instance.disconnect()
            logger.info(
                "Adapter gestoppt: %s '%s'",
                instance.adapter_type,
                instance._instance_name,
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
    """Rückwärtskompatibel: erste laufende Instanz des angegebenen Typs.
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


async def restart_instance(instance_id: str, event_bus: Any, db: Any, value_getter: Any = None) -> Any | None:
    """Laufende Instanz stoppen, neu aus DB laden und starten.
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
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=? AND enabled=1", (instance_id,))
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
        if value_getter and hasattr(instance, "set_value_getter"):
            instance.set_value_getter(value_getter)
        await instance.connect()
        _instances[instance_id] = instance

        binding_rows = await db.fetchall(
            "SELECT * FROM adapter_bindings WHERE adapter_instance_id=? AND enabled=1",
            (instance_id,),
        )
        bindings, issues = await reload_instance_from_rows(instance, binding_rows)

        logger.info(
            "Adapter neu gestartet: %s '%s' (%d Bindings, %d invalid skipped)",
            row["adapter_type"],
            row["name"],
            len(bindings),
            len(issues),
        )
        return instance
    except Exception:
        logger.exception("Fehler beim Neustart von Adapter %s '%s'", row["adapter_type"], row["name"])
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
                instance.adapter_type,
                instance._instance_name,
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
    await reload_instance_from_rows(instance, binding_rows)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _row_to_binding(row: Any) -> Any:
    from datetime import datetime

    from obs.models.binding import AdapterBinding

    instance_id_str = row["adapter_instance_id"] if row["adapter_instance_id"] else None
    throttle = row["send_throttle_ms"]
    min_delta = row["send_min_delta"]
    min_delta_p = row["send_min_delta_pct"]
    value_map_raw = row["value_map"] if row["value_map"] else None
    value_map = json.loads(value_map_raw) if value_map_raw else None
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
        value_map=value_map,
        created_at=datetime.fromisoformat(row["created_at"]),
        updated_at=datetime.fromisoformat(row["updated_at"]),
    )


def load_valid_bindings(rows: list[Any]) -> tuple[list[Any], list[BindingLoadIssue]]:
    """Convert DB rows to bindings, skipping malformed rows one-by-one."""
    bindings: list[Any] = []
    issues: list[BindingLoadIssue] = []
    for row in rows:
        try:
            bindings.append(_row_to_binding(row))
        except Exception as exc:
            issue = BindingLoadIssue(
                binding_id=_row_value(row, "id") or "<unknown>",
                adapter_instance_id=_row_value(row, "adapter_instance_id"),
                reason=f"{type(exc).__name__}: {exc}",
            )
            issues.append(issue)
            logger.error(
                "Invalid adapter binding skipped: id=%s instance=%s reason=%s",
                issue.binding_id,
                issue.adapter_instance_id,
                issue.reason,
            )
    return bindings, issues


async def reload_instance_from_rows(instance: Any, rows: list[Any]) -> tuple[list[Any], list[BindingLoadIssue]]:
    bindings, issues = load_valid_bindings(rows)
    await instance.reload_bindings(bindings)
    await _publish_binding_load_status(instance, issues)
    return bindings, issues


async def _publish_binding_load_status(instance: Any, issues: list[BindingLoadIssue]) -> None:
    if not issues:
        if getattr(instance, "last_detail", "").startswith(_BINDING_LOAD_DETAIL_PREFIX):
            await _set_instance_status(instance, "ok", "")
        return

    detail = _format_binding_load_detail(issues)
    await _set_instance_status(instance, "warning", detail)


def _format_binding_load_detail(issues: list[BindingLoadIssue]) -> str:
    examples = "; ".join(f"{i.binding_id}: {i.reason}" for i in issues[:3])
    suffix = f"; +{len(issues) - 3} more" if len(issues) > 3 else ""
    return f"{_BINDING_LOAD_DETAIL_PREFIX}: {len(issues)} invalid binding(s) skipped ({examples}{suffix})"


async def _set_instance_status(instance: Any, severity: str, detail: str) -> None:
    publish = getattr(instance, "_publish_status", None)
    if publish is not None:
        await publish(getattr(instance, "connected", False), detail=detail, severity=severity)
        return
    instance._last_severity = severity
    instance._last_detail = detail


def _row_value(row: Any, key: str) -> str | None:
    try:
        value = row[key]
    except Exception:
        return None
    return str(value) if value is not None else None
