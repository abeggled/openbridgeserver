"""
Adapters API — Phase 5 (Multi-Instance)

Instanz-Routen (NEU):
  GET    /api/v1/adapters/instances                list all instances + status
  POST   /api/v1/adapters/instances                create new instance
  GET    /api/v1/adapters/instances/{id}           get one instance
  PATCH  /api/v1/adapters/instances/{id}           update config/name + hot-reload
  DELETE /api/v1/adapters/instances/{id}           stop + delete instance
  POST   /api/v1/adapters/instances/{id}/test      test connection (ephemeral)
  POST   /api/v1/adapters/instances/{id}/restart   stop + reconnect
  GET    /api/v1/adapters/instances/{id}/mqtt/browse  MQTT topic browser (scan broker)

Typ-Routen (unverändert):
  GET    /api/v1/adapters                          list registered types
  GET    /api/v1/adapters/{type}/schema            Pydantic JSON schema
  GET    /api/v1/adapters/{type}/binding-schema    Pydantic JSON schema
  POST   /api/v1/adapters/{type}/test              test with given config (legacy)
  PATCH  /api/v1/adapters/{type}/config            update legacy adapter_configs
"""
from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from opentws.api.auth import get_current_user
from opentws.adapters import registry as adapter_registry
from opentws.adapters.knx.dpt_registry import DPTRegistry
from opentws.db.database import get_db, Database

router = APIRouter(tags=["adapters"])


# ---------------------------------------------------------------------------
# Response / Request models
# ---------------------------------------------------------------------------

class AdapterInstanceOut(BaseModel):
    id: uuid.UUID
    adapter_type: str
    name: str
    config: dict
    enabled: bool
    registered: bool   # Typ-Klasse geladen?
    running: bool
    connected: bool
    bindings: int
    created_at: str
    updated_at: str


class InstanceBindingEntry(BaseModel):
    binding_id: uuid.UUID
    datapoint_id: uuid.UUID
    datapoint_name: str
    enabled: bool
    config: dict


class AdapterInstanceCreate(BaseModel):
    adapter_type: str
    name: str
    config: dict = {}
    enabled: bool = True


class AdapterInstanceUpdate(BaseModel):
    name: str | None = None
    config: dict | None = None
    enabled: bool | None = None


class AdapterStatusOut(BaseModel):
    adapter_type: str
    registered: bool
    running: bool
    connected: bool
    hidden: bool = False


class AdapterConfigOut(BaseModel):
    adapter_type: str
    config: dict
    enabled: bool
    updated_at: str | None


class TestRequest(BaseModel):
    config: dict


class TestResult(BaseModel):
    success: bool
    detail: str


class ConfigPatch(BaseModel):
    config: dict
    enabled: bool = True


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _instance_out(row: Any, instance: Any | None) -> AdapterInstanceOut:
    cls = adapter_registry.get_class(row["adapter_type"])
    return AdapterInstanceOut(
        id=uuid.UUID(row["id"]),
        adapter_type=row["adapter_type"],
        name=row["name"],
        config=json.loads(row["config"]) if row["config"] else {},
        enabled=bool(row["enabled"]),
        registered=cls is not None,
        running=instance is not None,
        connected=instance.connected if instance else False,
        bindings=len(instance.get_bindings()) if instance else 0,
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


# ---------------------------------------------------------------------------
# Instanz-Routen  (WICHTIG: vor /{adapter_type}/... registrieren!)
# ---------------------------------------------------------------------------

@router.get("/instances", response_model=list[AdapterInstanceOut])
async def list_instances(
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[AdapterInstanceOut]:
    rows = await db.fetchall("SELECT * FROM adapter_instances ORDER BY adapter_type, name")
    result = []
    for row in rows:
        instance = adapter_registry.get_instance_by_id(row["id"])
        result.append(_instance_out(row, instance))
    return result


@router.post(
    "/instances",
    response_model=AdapterInstanceOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_instance(
    body: AdapterInstanceCreate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> AdapterInstanceOut:
    cls = adapter_registry.get_class(body.adapter_type)
    if cls is None:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Adapter-Typ '{body.adapter_type}' nicht registriert",
        )
    # Config validieren
    try:
        cls.config_schema(**body.config)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Config-Validierungsfehler: {exc}",
        ) from exc

    instance_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    await db.execute_and_commit(
        """INSERT INTO adapter_instances
           (id, adapter_type, name, config, enabled, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (instance_id, body.adapter_type, body.name,
         json.dumps(body.config), int(body.enabled), now, now),
    )

    # Hot-start wenn enabled
    if body.enabled:
        from opentws.core.event_bus import get_event_bus
        try:
            await adapter_registry.start_instance(instance_id, get_event_bus(), db)
        except Exception:
            pass  # Verbindungsfehler → Instanz existiert, aber running=False

    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (instance_id,))
    instance = adapter_registry.get_instance_by_id(instance_id)
    return _instance_out(row, instance)


@router.get("/instances/{instance_id}", response_model=AdapterInstanceOut)
async def get_instance(
    instance_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> AdapterInstanceOut:
    row = await db.fetchone(
        "SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    instance = adapter_registry.get_instance_by_id(str(instance_id))
    return _instance_out(row, instance)


@router.patch("/instances/{instance_id}", response_model=AdapterInstanceOut)
async def update_instance(
    instance_id: uuid.UUID,
    body: AdapterInstanceUpdate,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> AdapterInstanceOut:
    row = await db.fetchone(
        "SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")

    # Neue Werte bestimmen
    name_new    = body.name    if body.name    is not None else row["name"]
    enabled_new = body.enabled if body.enabled is not None else bool(row["enabled"])
    config_raw  = row["config"]
    if body.config is not None:
        cls = adapter_registry.get_class(row["adapter_type"])
        if cls:
            try:
                cls.config_schema(**body.config)
            except Exception as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"Config-Validierungsfehler: {exc}",
                ) from exc
        config_raw = json.dumps(body.config)

    now = datetime.now(timezone.utc).isoformat()
    await db.execute_and_commit(
        """UPDATE adapter_instances
           SET name=?, config=?, enabled=?, updated_at=?
           WHERE id=?""",
        (name_new, config_raw, int(enabled_new), now, str(instance_id)),
    )

    # Hot-reload: Instanz neu starten
    from opentws.core.event_bus import get_event_bus
    if enabled_new:
        await adapter_registry.restart_instance(str(instance_id), get_event_bus(), db)
    else:
        await adapter_registry.stop_instance(str(instance_id))

    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    instance = adapter_registry.get_instance_by_id(str(instance_id))
    return _instance_out(row, instance)


@router.delete("/instances/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_instance(
    instance_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> None:
    row = await db.fetchone(
        "SELECT id FROM adapter_instances WHERE id=?", (str(instance_id),)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")

    await adapter_registry.stop_instance(str(instance_id))
    # Bindings werden per DB (ON DELETE CASCADE via Trigger oder manuell) gelöscht
    await db.execute_and_commit(
        "DELETE FROM adapter_bindings WHERE adapter_instance_id=?", (str(instance_id),)
    )
    await db.execute_and_commit(
        "DELETE FROM adapter_instances WHERE id=?", (str(instance_id),)
    )


@router.post("/instances/{instance_id}/test", response_model=TestResult)
async def test_instance(
    instance_id: uuid.UUID,
    body: TestRequest | None = None,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> TestResult:
    """Verbindungstest mit aktuellem oder gegebenem Config (ephemer, kein Persist)."""
    row = await db.fetchone(
        "SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")

    cls = adapter_registry.get_class(row["adapter_type"])
    if cls is None:
        return TestResult(success=False, detail=f"Adapter-Typ '{row['adapter_type']}' nicht registriert")

    if body and body.config:
        config_dict = body.config  # bereits dict durch Pydantic
    else:
        raw = row["config"] or "{}"
        config_dict = json.loads(raw) if isinstance(raw, str) else raw

    try:
        cls.config_schema(**config_dict)
    except Exception as exc:
        return TestResult(success=False, detail=f"Config-Fehler: {exc}")

    from opentws.core.event_bus import EventBus
    dummy_bus = EventBus()
    test_instance = cls(event_bus=dummy_bus, config=config_dict)
    try:
        await test_instance.connect()
        connected = test_instance.connected
        await test_instance.disconnect()
        if connected:
            return TestResult(success=True, detail=f"Verbindung zu {row['adapter_type']} erfolgreich")
        return TestResult(success=False, detail="Verbindungsversuch fehlgeschlagen")
    except Exception as exc:
        return TestResult(success=False, detail=str(exc))


@router.post("/instances/{instance_id}/restart", response_model=AdapterInstanceOut)
async def restart_instance_route(
    instance_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> AdapterInstanceOut:
    row = await db.fetchone(
        "SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")

    from opentws.core.event_bus import get_event_bus
    await adapter_registry.restart_instance(str(instance_id), get_event_bus(), db)

    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    instance = adapter_registry.get_instance_by_id(str(instance_id))
    return _instance_out(row, instance)


@router.get("/instances/{instance_id}/bindings", response_model=list[InstanceBindingEntry])
async def list_instance_bindings(
    instance_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[InstanceBindingEntry]:
    """Alle Bindings einer Adapter-Instanz, angereichert mit Datenpunkt-Namen."""
    rows = await db.fetchall(
        """SELECT ab.id, ab.datapoint_id, dp.name AS dp_name, ab.enabled, ab.config
           FROM adapter_bindings ab
           JOIN datapoints dp ON dp.id = ab.datapoint_id
           WHERE ab.adapter_instance_id = ?
           ORDER BY dp.name, ab.created_at""",
        (str(instance_id),),
    )
    return [
        InstanceBindingEntry(
            binding_id=uuid.UUID(row["id"]),
            datapoint_id=uuid.UUID(row["datapoint_id"]),
            datapoint_name=row["dp_name"],
            enabled=bool(row["enabled"]),
            config=json.loads(row["config"]) if row["config"] else {},
        )
        for row in rows
    ]


@router.get("/instances/{instance_id}/mqtt/browse", response_model=list[str])
async def mqtt_browse_topics(
    instance_id: uuid.UUID,
    timeout: int = 5,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[str]:
    """Subscribe to # for up to `timeout` seconds (max 10) and return observed topics."""
    row = await db.fetchone(
        "SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "MQTT":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für MQTT-Instanzen verfügbar")

    raw_config = row["config"] or "{}"
    config_dict = json.loads(raw_config) if isinstance(raw_config, str) else raw_config

    from opentws.adapters.mqtt.adapter import MqttAdapterConfig
    try:
        cfg = MqttAdapterConfig(**config_dict)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Config-Fehler: {exc}") from exc

    try:
        import aiomqtt
    except ImportError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "aiomqtt nicht installiert")

    scan_secs = min(max(timeout, 1), 10)
    topics: set[str] = set()
    try:
        async with aiomqtt.Client(
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username,
            password=cfg.password,
        ) as client:
            await client.subscribe("#")
            try:
                async with asyncio.timeout(scan_secs):
                    async for message in client.messages:
                        topics.add(str(message.topic))
            except asyncio.TimeoutError:
                pass
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"MQTT-Verbindung fehlgeschlagen: {exc}",
        )

    return sorted(topics)


@router.get("/instances/{instance_id}/mqtt/sample")
async def mqtt_sample_payload(
    instance_id: uuid.UUID,
    topic: str,
    timeout: int = 5,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> dict:
    """Subscribe to a specific topic and return the first received payload (useful for retained messages)."""
    row = await db.fetchone(
        "SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),)
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "MQTT":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für MQTT-Instanzen verfügbar")

    raw_config = row["config"] or "{}"
    config_dict = json.loads(raw_config) if isinstance(raw_config, str) else raw_config

    from opentws.adapters.mqtt.adapter import MqttAdapterConfig
    try:
        cfg = MqttAdapterConfig(**config_dict)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"Config-Fehler: {exc}") from exc

    try:
        import aiomqtt
    except ImportError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "aiomqtt nicht installiert")

    scan_secs = min(max(timeout, 1), 10)
    try:
        async with aiomqtt.Client(
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username,
            password=cfg.password,
        ) as client:
            await client.subscribe(topic)
            try:
                async with asyncio.timeout(scan_secs):
                    async for message in client.messages:
                        return {
                            "topic": str(message.topic),
                            "payload": message.payload.decode("utf-8", errors="replace"),
                        }
            except asyncio.TimeoutError:
                pass
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"MQTT-Verbindung fehlgeschlagen: {exc}",
        )

    raise HTTPException(
        status.HTTP_404_NOT_FOUND,
        f"Kein Payload auf Topic '{topic}' innerhalb von {scan_secs} s empfangen",
    )


# ---------------------------------------------------------------------------
# Typ-Routen (unverändert — Schema-Abfragen + Legacy-Config)
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[AdapterStatusOut])
async def list_adapters(
    _user: str = Depends(get_current_user),
) -> list[AdapterStatusOut]:
    status_map = adapter_registry.get_status()
    return [
        AdapterStatusOut(adapter_type=k, **v)
        for k, v in status_map.items()
    ]


@router.get("/knx/dpts")
async def list_knx_dpts(
    _user: str = Depends(get_current_user),
) -> list[dict]:
    """Alle registrierten KNX DPTs — gruppiert nach Familie (DPT1, DPT9, …)."""
    return [
        {
            "dpt_id":    d.dpt_id,
            "name":      d.name,
            "data_type": d.data_type,
            "unit":      d.unit,
        }
        for d in sorted(DPTRegistry.all().values(), key=lambda x: x.dpt_id)
    ]


@router.get("/{adapter_type}/schema")
async def get_adapter_schema(
    adapter_type: str,
    _user: str = Depends(get_current_user),
) -> dict:
    cls = adapter_registry.get_class(adapter_type)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Adapter '{adapter_type}' nicht registriert")
    schema = cls.config_schema.model_json_schema()
    schema["title"] = f"{adapter_type} Connection Config"
    return schema


@router.get("/{adapter_type}/binding-schema")
async def get_binding_schema(
    adapter_type: str,
    _user: str = Depends(get_current_user),
) -> dict:
    cls = adapter_registry.get_class(adapter_type)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Adapter '{adapter_type}' nicht registriert")
    if not hasattr(cls, "binding_config_schema"):
        return {}
    schema = cls.binding_config_schema.model_json_schema()
    schema["title"] = f"{adapter_type} Binding Config"
    return schema


@router.post("/{adapter_type}/test", response_model=TestResult)
async def test_adapter(
    adapter_type: str,
    body: TestRequest,
    _user: str = Depends(get_current_user),
) -> TestResult:
    cls = adapter_registry.get_class(adapter_type)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Adapter '{adapter_type}' nicht registriert")
    try:
        cls.config_schema(**body.config)
    except Exception as exc:
        return TestResult(success=False, detail=f"Config-Validierungsfehler: {exc}")

    from opentws.core.event_bus import EventBus
    dummy_bus = EventBus()
    test_instance = cls(event_bus=dummy_bus, config=body.config)
    try:
        await test_instance.connect()
        connected = test_instance.connected
        await test_instance.disconnect()
        if connected:
            return TestResult(success=True, detail=f"Verbindung zu {adapter_type} erfolgreich")
        return TestResult(success=False, detail="Verbindungsversuch fehlgeschlagen")
    except Exception as exc:
        return TestResult(success=False, detail=str(exc))


@router.patch("/{adapter_type}/config", response_model=AdapterConfigOut)
async def update_adapter_config(
    adapter_type: str,
    body: ConfigPatch,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> AdapterConfigOut:
    cls = adapter_registry.get_class(adapter_type)
    if cls is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Adapter '{adapter_type}' nicht registriert")
    try:
        cls.config_schema(**body.config)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"Config-Validierungsfehler: {exc}",
        ) from exc

    now = datetime.now(timezone.utc).isoformat()
    await db.execute_and_commit(
        """INSERT INTO adapter_configs (adapter_type, config, enabled, updated_at)
           VALUES (?,?,?,?)
           ON CONFLICT(adapter_type) DO UPDATE
           SET config=excluded.config, enabled=excluded.enabled, updated_at=excluded.updated_at""",
        (adapter_type, json.dumps(body.config), int(body.enabled), now),
    )
    return AdapterConfigOut(
        adapter_type=adapter_type,
        config=body.config,
        enabled=body.enabled,
        updated_at=now,
    )


@router.get("/{adapter_type}/config", response_model=AdapterConfigOut)
async def get_adapter_config(
    adapter_type: str,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> AdapterConfigOut:
    row = await db.fetchone(
        "SELECT * FROM adapter_configs WHERE adapter_type=?", (adapter_type,)
    )
    if row is None:
        return AdapterConfigOut(
            adapter_type=adapter_type, config={}, enabled=True, updated_at=None
        )
    return AdapterConfigOut(
        adapter_type=adapter_type,
        config=json.loads(row["config"]),
        enabled=bool(row["enabled"]),
        updated_at=row["updated_at"],
    )
