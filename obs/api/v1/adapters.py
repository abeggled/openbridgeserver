"""Adapters API — Phase 5 (Multi-Instance)

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
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from obs.adapters import registry as adapter_registry
from obs.adapters.knx.dpt_registry import DPTRegistry
from obs.api.auth import get_current_user
from obs.db.database import Database, get_db

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
    registered: bool  # Typ-Klasse geladen?
    running: bool
    connected: bool
    severity: str = "ok"  # "ok" | "warning" | "error" — last AdapterStatusEvent
    status_detail: str = ""
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


class IoBrokerStateOut(BaseModel):
    id: str
    name: str | None = None
    type: str | None = None
    role: str | None = None
    read: bool = True
    write: bool = False
    value: Any = None
    unit: str | None = None


class IoBrokerImportRequest(BaseModel):
    prefix: str = ""
    states: list[str] = []
    direction: str = "auto"
    tags: list[str] = []
    persist_value: bool = True
    record_history: bool = True
    limit: int = 300


class IoBrokerImportItem(BaseModel):
    state_id: str
    name: str
    data_type: str
    unit: str | None = None
    direction: str
    tags: list[str]
    exists: bool = False
    reason: str | None = None


class IoBrokerImportResult(BaseModel):
    preview: list[IoBrokerImportItem] = []
    created_datapoints: int = 0
    created_bindings: int = 0
    skipped_existing: int = 0
    errors: list[str] = []


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
        severity=getattr(instance, "last_severity", "ok") if instance else "ok",
        status_detail=getattr(instance, "last_detail", "") if instance else "",
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
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Adapter-Typ '{body.adapter_type}' nicht registriert",
        )
    # Config validieren
    try:
        cls.config_schema(**body.config)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Config-Validierungsfehler: {exc}",
        ) from exc

    instance_id = str(uuid.uuid4())
    now = datetime.now(UTC).isoformat()

    await db.execute_and_commit(
        """INSERT INTO adapter_instances
           (id, adapter_type, name, config, enabled, created_at, updated_at)
           VALUES (?,?,?,?,?,?,?)""",
        (
            instance_id,
            body.adapter_type,
            body.name,
            json.dumps(body.config),
            int(body.enabled),
            now,
            now,
        ),
    )

    # Hot-start wenn enabled
    if body.enabled:
        from obs.core.event_bus import get_event_bus

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
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
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
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")

    # Neue Werte bestimmen
    name_new = body.name if body.name is not None else row["name"]
    enabled_new = body.enabled if body.enabled is not None else bool(row["enabled"])
    config_raw = row["config"]
    if body.config is not None:
        cls = adapter_registry.get_class(row["adapter_type"])
        if cls:
            try:
                cls.config_schema(**body.config)
            except Exception as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
                    f"Config-Validierungsfehler: {exc}",
                ) from exc
        config_raw = json.dumps(body.config)

    now = datetime.now(UTC).isoformat()
    await db.execute_and_commit(
        """UPDATE adapter_instances
           SET name=?, config=?, enabled=?, updated_at=?
           WHERE id=?""",
        (name_new, config_raw, int(enabled_new), now, str(instance_id)),
    )

    # Hot-reload: Instanz neu starten
    from obs.core.event_bus import get_event_bus

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
    row = await db.fetchone("SELECT id FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")

    await adapter_registry.stop_instance(str(instance_id))
    # Bindings werden per DB (ON DELETE CASCADE via Trigger oder manuell) gelöscht
    await db.execute_and_commit("DELETE FROM adapter_bindings WHERE adapter_instance_id=?", (str(instance_id),))
    await db.execute_and_commit("DELETE FROM adapter_instances WHERE id=?", (str(instance_id),))


@router.post("/instances/{instance_id}/test", response_model=TestResult)
async def test_instance(
    instance_id: uuid.UUID,
    body: TestRequest | None = None,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> TestResult:
    """Verbindungstest mit aktuellem oder gegebenem Config (ephemer, kein Persist)."""
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")

    cls = adapter_registry.get_class(row["adapter_type"])
    if cls is None:
        return TestResult(
            success=False,
            detail=f"Adapter-Typ '{row['adapter_type']}' nicht registriert",
        )

    if body and body.config:
        config_dict = body.config  # bereits dict durch Pydantic
    else:
        raw = row["config"] or "{}"
        config_dict = json.loads(raw) if isinstance(raw, str) else raw

    try:
        cls.config_schema(**config_dict)
    except Exception as exc:
        return TestResult(success=False, detail=f"Config-Fehler: {exc}")

    from obs.core.event_bus import EventBus

    dummy_bus = EventBus()
    test_instance = cls(event_bus=dummy_bus, config=config_dict)
    try:
        await test_instance.connect()
        # Some adapters (e.g. MQTT) establish the connection in a background task
        # started by connect(). Poll briefly so that task gets a chance to run.
        deadline = asyncio.get_event_loop().time() + 5.0
        while not test_instance.connected and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.1)
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
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")

    from obs.core.event_bus import get_event_bus

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


class HolidayEntry(BaseModel):
    date: str
    name: str


@router.get("/instances/{instance_id}/holidays", response_model=list[HolidayEntry])
async def list_instance_holidays(
    instance_id: uuid.UUID,
    year: int = Query(default=0, description="Jahr (0 = aktuelles Jahr)"),
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[HolidayEntry]:
    """Alle Feiertage einer Zeitschaltuhr-Instanz für das angegebene Jahr (Library + benutzerdefiniert)."""
    from datetime import datetime as _dt

    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "ZEITSCHALTUHR":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für Zeitschaltuhr-Instanzen verfügbar")

    target_year = year if year > 0 else _dt.now().year

    instance = adapter_registry.get_instance_by_id(str(instance_id))
    if instance is not None and hasattr(instance, "get_holidays_for_year"):
        holidays = instance.get_holidays_for_year(target_year)
    else:
        # Instance not running — reconstruct adapter to query holidays
        from obs.adapters.zeitschaltuhr.adapter import ZeitschaltuhrAdapter

        raw_config = row["config"] or "{}"
        config_dict = json.loads(raw_config) if isinstance(raw_config, str) else raw_config
        from obs.core.event_bus import EventBus

        dummy = ZeitschaltuhrAdapter(event_bus=EventBus(), config=config_dict)
        holidays = dummy.get_holidays_for_year(target_year)

    return [HolidayEntry(date=h["date"], name=h["name"]) for h in holidays]


def _build_tls_context(cfg: Any) -> Any:
    """Return an ssl.SSLContext if cfg.tls is True, else None."""
    if not getattr(cfg, "tls", False):
        return None
    import ssl

    ctx = ssl.create_default_context()
    if getattr(cfg, "tls_insecure", False):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


@router.get("/instances/{instance_id}/mqtt/browse", response_model=list[str])
async def mqtt_browse_topics(
    instance_id: uuid.UUID,
    timeout: int = 5,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[str]:
    """Subscribe to # for up to `timeout` seconds (max 10) and return observed topics."""
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "MQTT":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für MQTT-Instanzen verfügbar")

    raw_config = row["config"] or "{}"
    config_dict = json.loads(raw_config) if isinstance(raw_config, str) else raw_config

    from obs.adapters.mqtt.adapter import MqttAdapterConfig

    try:
        cfg = MqttAdapterConfig(**config_dict)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Config-Fehler: {exc}") from exc

    try:
        import aiomqtt
    except ImportError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "aiomqtt nicht installiert")

    scan_secs = min(max(timeout, 1), 10)
    tls_context = _build_tls_context(cfg)
    browse_id = f"obs-mqtt-{instance_id.hex[:8]}-browse"
    topics: set[str] = set()
    try:
        async with aiomqtt.Client(
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username,
            password=cfg.password,
            identifier=browse_id,
            tls_context=tls_context,
        ) as client:
            await client.subscribe("#")
            try:
                async with asyncio.timeout(scan_secs):
                    async for message in client.messages:
                        topics.add(str(message.topic))
            except TimeoutError:
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
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "MQTT":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für MQTT-Instanzen verfügbar")

    raw_config = row["config"] or "{}"
    config_dict = json.loads(raw_config) if isinstance(raw_config, str) else raw_config

    from obs.adapters.mqtt.adapter import MqttAdapterConfig

    try:
        cfg = MqttAdapterConfig(**config_dict)
    except Exception as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Config-Fehler: {exc}") from exc

    try:
        import aiomqtt
    except ImportError:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "aiomqtt nicht installiert")

    scan_secs = min(max(timeout, 1), 10)
    tls_context = _build_tls_context(cfg)
    sample_id = f"obs-mqtt-{instance_id.hex[:8]}-sample"
    try:
        async with aiomqtt.Client(
            hostname=cfg.host,
            port=cfg.port,
            username=cfg.username,
            password=cfg.password,
            identifier=sample_id,
            tls_context=tls_context,
        ) as client:
            await client.subscribe(topic)
            try:
                async with asyncio.timeout(scan_secs):
                    async for message in client.messages:
                        return {
                            "topic": str(message.topic),
                            "payload": message.payload.decode("utf-8", errors="replace"),
                        }
            except TimeoutError:
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


@router.get("/instances/{instance_id}/iobroker/states", response_model=list[IoBrokerStateOut])
async def iobroker_browse_states(
    instance_id: uuid.UUID,
    q: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=100),
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[IoBrokerStateOut]:
    """Durchsuchbare ioBroker-State-Liste für Binding-Auswahl."""
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "IOBROKER":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für IOBROKER-Instanzen verfügbar")

    instance = adapter_registry.get_instance_by_id(str(instance_id))
    if instance is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ioBroker-Instanz ist nicht verbunden")
    if not hasattr(instance, "browse_states"):
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "ioBroker-State-Browser nicht verfügbar")

    try:
        return [IoBrokerStateOut(**item) for item in await instance.browse_states(q, limit)]
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"ioBroker-State-Suche fehlgeschlagen: {exc}",
        ) from exc


def _iobroker_obs_type(state_type: str | None) -> tuple[str, str | None]:
    t = (state_type or "").lower()
    if t == "boolean":
        return "BOOLEAN", "bool"
    if t == "number":
        return "FLOAT", "float"
    if t == "string":
        return "STRING", "string"
    return "STRING", None


def _iobroker_source_type(data_type: str) -> str | None:
    return {
        "BOOLEAN": "bool",
        "FLOAT": "float",
        "INTEGER": "int",
        "STRING": "string",
    }.get(data_type)


def _iobroker_direction(item: dict[str, Any], requested: str) -> str:
    if requested in ("SOURCE", "DEST", "BOTH"):
        return requested
    return "BOTH" if item.get("read", True) and item.get("write", False) else "SOURCE"


def _iobroker_name(item: dict[str, Any]) -> str:
    name = item.get("name")
    if name:
        return str(name)
    return str(item.get("id", "")).split(".")[-1] or str(item.get("id", "ioBroker State"))


def _iobroker_tags(item: dict[str, Any], extra_tags: list[str]) -> list[str]:
    parts = str(item.get("id", "")).split(".")
    tags = ["iobroker"]
    if parts:
        tags.append(parts[0])
    for key in ("role", "type"):
        if item.get(key):
            tags.append(str(item[key]))
    tags.extend(t.strip() for t in extra_tags if t.strip())
    seen: set[str] = set()
    return [t for t in tags if not (t in seen or seen.add(t))]


async def _iobroker_candidates(
    instance_id: str,
    body: IoBrokerImportRequest,
    db: Database,
) -> list[IoBrokerImportItem]:
    instance = adapter_registry.get_instance_by_id(instance_id)
    if instance is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "ioBroker-Instanz ist nicht verbunden")
    states = await instance.browse_states(body.prefix, min(max(body.limit, 1), 500))
    selected = set(body.states)
    if selected:
        states = [s for s in states if s["id"] in selected]

    rows = await db.fetchall(
        "SELECT config FROM adapter_bindings WHERE adapter_instance_id=?",
        (instance_id,),
    )
    existing_states: set[str] = set()
    for row in rows:
        try:
            cfg = json.loads(row["config"] or "{}")
            if cfg.get("state_id"):
                existing_states.add(str(cfg["state_id"]))
        except Exception:
            pass

    result: list[IoBrokerImportItem] = []
    for state in states:
        dp_type, _source_type = _iobroker_obs_type(state.get("type"))
        exists = state["id"] in existing_states
        result.append(
            IoBrokerImportItem(
                state_id=state["id"],
                name=_iobroker_name(state),
                data_type=dp_type,
                unit=state.get("unit"),
                direction=_iobroker_direction(state, body.direction),
                tags=_iobroker_tags(state, body.tags),
                exists=exists,
                reason="Binding existiert bereits" if exists else None,
            ),
        )
    return result


@router.post(
    "/instances/{instance_id}/iobroker/import-preview",
    response_model=IoBrokerImportResult,
)
async def iobroker_import_preview(
    instance_id: uuid.UUID,
    body: IoBrokerImportRequest,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> IoBrokerImportResult:
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "IOBROKER":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für IOBROKER-Instanzen verfügbar")
    return IoBrokerImportResult(preview=await _iobroker_candidates(str(instance_id), body, db))


@router.post("/instances/{instance_id}/iobroker/import", response_model=IoBrokerImportResult)
async def iobroker_import_states(
    instance_id: uuid.UUID,
    body: IoBrokerImportRequest,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> IoBrokerImportResult:
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "IOBROKER":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für IOBROKER-Instanzen verfügbar")

    from obs.api.v1.bindings import create_binding
    from obs.core.registry import get_registry
    from obs.models.binding import AdapterBindingCreate
    from obs.models.datapoint import DataPointCreate

    candidates = await _iobroker_candidates(str(instance_id), body, db)
    result = IoBrokerImportResult(preview=candidates)
    registry = get_registry()

    for item in candidates:
        if item.exists:
            result.skipped_existing += 1
            continue
        try:
            source_type = _iobroker_source_type(item.data_type)
            dp = await registry.create(
                DataPointCreate(
                    name=item.name,
                    data_type=item.data_type,
                    unit=item.unit,
                    tags=item.tags,
                    persist_value=body.persist_value,
                    record_history=body.record_history,
                ),
            )
            result.created_datapoints += 1
            config: dict[str, Any] = {"state_id": item.state_id}
            if source_type:
                config["source_data_type"] = source_type
            await create_binding(
                dp.id,
                AdapterBindingCreate(
                    adapter_instance_id=instance_id,
                    direction=item.direction,
                    config=config,
                    enabled=True,
                ),
                _user,
                db,
            )
            result.created_bindings += 1
        except Exception as exc:
            result.errors.append(f"{item.state_id}: {exc}")
    return result


# ---------------------------------------------------------------------------
# Anwesenheitssimulation: Datenpunkt-Selektor + Binding-Sync
# ---------------------------------------------------------------------------


class AnwesenheitHealthResult(BaseModel):
    healthy: bool
    message: str
    bindings_total: int = 0
    bindings_with_data: int = 0


@router.get("/instances/{instance_id}/anwesenheit/health", response_model=AnwesenheitHealthResult)
async def anwesenheit_health(
    instance_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> AnwesenheitHealthResult:
    """Check whether history data is available for the configured offset window."""
    from datetime import timedelta, timezone
    from datetime import datetime as _dt

    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "ANWESENHEITSSIMULATION":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für ANWESENHEITSSIMULATION-Instanzen verfügbar")

    raw_config = row["config"] or "{}"
    config_dict = json.loads(raw_config) if isinstance(raw_config, str) else raw_config

    from obs.adapters.anwesenheit.adapter import AnwesenheitssimulationConfig

    try:
        cfg = AnwesenheitssimulationConfig(**config_dict)
    except Exception as exc:
        return AnwesenheitHealthResult(healthy=False, message=f"Config-Fehler: {exc}")

    try:
        from obs.history.factory import get_history_plugin

        history = get_history_plugin()
    except RuntimeError:
        return AnwesenheitHealthResult(
            healthy=False, message="History-Plugin nicht verfügbar — bitte History-Backend in den Einstellungen konfigurieren"
        )

    binding_rows = await db.fetchall(
        "SELECT id, datapoint_id FROM adapter_bindings WHERE adapter_instance_id=? AND direction='SOURCE' AND enabled=1",
        (str(instance_id),),
    )
    total = len(binding_rows)
    if total == 0:
        return AnwesenheitHealthResult(
            healthy=False,
            message="Keine aktiven Verknüpfungen konfiguriert — bitte zuerst Objekte über 'Objekte verwalten' hinzufügen",
            bindings_total=0,
            bindings_with_data=0,
        )

    now = _dt.now(tz=timezone.utc)
    delta = timedelta(days=cfg.offset_days)
    hist_from = now - delta - timedelta(hours=12)
    hist_to = now - delta + timedelta(hours=12)

    with_data = 0
    for b_row in binding_rows:
        try:
            dp_id = uuid.UUID(b_row["datapoint_id"])
            records = await history.query(dp_id, hist_from, hist_to, limit=1)
            if records:
                with_data += 1
        except Exception:
            pass

    healthy = with_data > 0
    if healthy:
        msg = (
            f"{with_data} von {total} Objekt(en) haben historische Daten für den Versatz von {cfg.offset_days} Tag(en). "
            f"Die Simulation wird heute Ereignisse aus dem {cfg.offset_days}-Tage-Fenster wiedergeben."
        )
    else:
        msg = (
            f"Keine historischen Daten für den Versatz von {cfg.offset_days} Tag(en) gefunden. "
            f"Stellen Sie sicher, dass für die verknüpften Objekte die Historisierung aktiviert ist "
            f"und Aufzeichnungen aus dem Zeitraum vor {cfg.offset_days + 1} Tagen vorhanden sind."
        )

    return AnwesenheitHealthResult(
        healthy=healthy,
        message=msg,
        bindings_total=total,
        bindings_with_data=with_data,
    )


class AnwesenheitDatapointEntry(BaseModel):
    id: str
    name: str
    data_type: str
    has_binding: bool
    binding_id: str | None


class AnwesenheitSyncRequest(BaseModel):
    datapoint_ids: list[str]


class AnwesenheitSyncResult(BaseModel):
    created: int = 0
    removed: int = 0
    errors: list[str] = []


@router.get(
    "/instances/{instance_id}/anwesenheit/datapoints",
    response_model=list[AnwesenheitDatapointEntry],
)
async def anwesenheit_list_datapoints(
    instance_id: uuid.UUID,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[AnwesenheitDatapointEntry]:
    """List all Boolean/Integer DataPoints with their binding status for this instance."""
    row = await db.fetchone("SELECT adapter_type FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "ANWESENHEITSSIMULATION":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für ANWESENHEIT-Instanzen verfügbar")

    from obs.core.registry import get_registry

    registry = get_registry()
    all_dps = registry.all()

    # Existing bindings for this instance
    binding_rows = await db.fetchall(
        "SELECT id, datapoint_id FROM adapter_bindings WHERE adapter_instance_id=?",
        (str(instance_id),),
    )
    bound_map: dict[str, str] = {r["datapoint_id"]: r["id"] for r in binding_rows}

    result: list[AnwesenheitDatapointEntry] = []
    for dp in sorted(all_dps, key=lambda d: d.name.lower()):
        if dp.data_type not in ("BOOLEAN", "INTEGER"):
            continue
        dp_id_str = str(dp.id)
        result.append(
            AnwesenheitDatapointEntry(
                id=dp_id_str,
                name=dp.name,
                data_type=dp.data_type,
                has_binding=dp_id_str in bound_map,
                binding_id=bound_map.get(dp_id_str),
            )
        )
    return result


@router.post(
    "/instances/{instance_id}/anwesenheit/sync-bindings",
    response_model=AnwesenheitSyncResult,
)
async def anwesenheit_sync_bindings(
    instance_id: uuid.UUID,
    body: AnwesenheitSyncRequest,
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> AnwesenheitSyncResult:
    """Create missing bindings for selected DataPoints and remove bindings for deselected ones."""
    row = await db.fetchone("SELECT adapter_type FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "ANWESENHEITSSIMULATION":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für ANWESENHEIT-Instanzen verfügbar")

    from obs.api.v1.bindings import create_binding, delete_binding
    from obs.models.binding import AdapterBindingCreate

    # Current bindings for this instance
    binding_rows = await db.fetchall(
        "SELECT id, datapoint_id FROM adapter_bindings WHERE adapter_instance_id=?",
        (str(instance_id),),
    )
    current: dict[str, str] = {r["datapoint_id"]: r["id"] for r in binding_rows}

    desired_ids = set(body.datapoint_ids)
    current_ids = set(current.keys())

    result = AnwesenheitSyncResult()

    # Create missing bindings
    for dp_id_str in desired_ids - current_ids:
        try:
            dp_uuid = uuid.UUID(dp_id_str)
            await create_binding(
                dp_uuid,
                AdapterBindingCreate(
                    adapter_instance_id=instance_id,
                    direction="SOURCE",
                    config={},
                    enabled=True,
                ),
                _user,
                db,
            )
            result.created += 1
        except Exception as exc:
            result.errors.append(f"{dp_id_str}: {exc}")

    # Remove bindings for deselected DataPoints
    for dp_id_str in current_ids - desired_ids:
        try:
            binding_uuid = uuid.UUID(current[dp_id_str])
            dp_uuid = uuid.UUID(dp_id_str)
            await delete_binding(dp_uuid, binding_uuid, _user, db)
            result.removed += 1
        except Exception as exc:
            result.errors.append(f"{dp_id_str}: {exc}")

    # Reload adapter bindings if the instance is running
    try:
        inst = adapter_registry.get_instance_by_id(instance_id)
        if inst is not None:
            await adapter_registry.reload_instance_bindings(instance_id, db)
    except Exception:
        pass  # non-critical — bindings take effect on next restart

    return result


# ---------------------------------------------------------------------------
# SNMP Walk (Discovery)
# ---------------------------------------------------------------------------


class SnmpWalkEntry(BaseModel):
    oid: str
    value: str
    type: str


@router.get("/instances/{instance_id}/snmp/walk", response_model=list[SnmpWalkEntry])
async def snmp_walk(
    instance_id: uuid.UUID,
    host: str = Query(..., description="IP-Adresse oder DNS-Name des SNMP-Geräts"),
    oid: str = Query(default="1.3.6.1.2.1", description="Subtree-Root OID"),
    port: int = Query(default=161, ge=1, le=65535, description="UDP-Port"),
    timeout: float = Query(default=5.0, ge=0.5, le=30.0, description="Timeout pro Request (s)"),
    max_results: int = Query(default=50, ge=1, le=500, description="Einträge pro Seite"),
    start_oid: str | None = Query(default=None, description="Cursor für Paginierung (letzter OID der Vorseite)"),
    _user: str = Depends(get_current_user),
    db: Database = Depends(lambda: get_db()),
) -> list[SnmpWalkEntry]:
    """SNMP-Walk über einen OID-Teilbaum — nützlich für OID-Discovery beim Binding-Anlegen.

    Paginierung: start_oid auf den letzten OID der Vorseite setzen um weitere Einträge zu laden.
    """
    row = await db.fetchone("SELECT * FROM adapter_instances WHERE id=?", (str(instance_id),))
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Instanz nicht gefunden")
    if row["adapter_type"] != "SNMP":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Nur für SNMP-Instanzen verfügbar")

    instance = adapter_registry.get_instance_by_id(str(instance_id))
    if instance is None or not instance.connected:
        from obs.adapters.snmp.adapter import SnmpAdapter
        from obs.core.event_bus import EventBus
        import json as _json

        raw_config = row["config"] or "{}"
        config_dict = _json.loads(raw_config) if isinstance(raw_config, str) else raw_config
        dummy_bus = EventBus()
        instance = SnmpAdapter(event_bus=dummy_bus, config=config_dict)
        await instance.connect()
        if not instance.connected:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                "SNMP Adapter konnte nicht verbunden werden (pysnmp installiert?)",
            )

    if not hasattr(instance, "snmp_walk"):
        raise HTTPException(status.HTTP_501_NOT_IMPLEMENTED, "snmp_walk nicht verfügbar")

    try:
        entries = await instance.snmp_walk(
            host=host,
            oid=oid,
            port=port,
            timeout=timeout,
            max_results=max_results,
            start_oid=start_oid,
        )
        return [SnmpWalkEntry(**e) for e in entries]
    except Exception as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            f"SNMP Walk fehlgeschlagen: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Typ-Routen (unverändert — Schema-Abfragen + Legacy-Config)
# ---------------------------------------------------------------------------


@router.get("/", response_model=list[AdapterStatusOut])
async def list_adapters(
    _user: str = Depends(get_current_user),
) -> list[AdapterStatusOut]:
    status_map = adapter_registry.get_status()
    return [AdapterStatusOut(adapter_type=k, **v) for k, v in status_map.items()]


@router.get("/knx/dpts")
async def list_knx_dpts(
    _user: str = Depends(get_current_user),
) -> list[dict]:
    """Alle registrierten KNX DPTs — gruppiert nach Familie (DPT1, DPT9, …)."""
    return [
        {
            "dpt_id": d.dpt_id,
            "name": d.name,
            "data_type": d.data_type,
            "unit": d.unit,
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

    from obs.core.event_bus import EventBus

    dummy_bus = EventBus()
    test_instance = cls(event_bus=dummy_bus, config=body.config)
    try:
        await test_instance.connect()
        # Some adapters (e.g. MQTT) establish the connection in a background task
        # started by connect(). Poll briefly so that task gets a chance to run.
        deadline = asyncio.get_event_loop().time() + 5.0
        while not test_instance.connected and asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(0.1)
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
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Config-Validierungsfehler: {exc}",
        ) from exc

    now = datetime.now(UTC).isoformat()
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
    row = await db.fetchone("SELECT * FROM adapter_configs WHERE adapter_type=?", (adapter_type,))
    if row is None:
        return AdapterConfigOut(adapter_type=adapter_type, config={}, enabled=True, updated_at=None)
    return AdapterConfigOut(
        adapter_type=adapter_type,
        config=json.loads(row["config"]),
        enabled=bool(row["enabled"]),
        updated_at=row["updated_at"],
    )
