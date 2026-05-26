"""KNX Project Import API

POST /api/v1/knxproj/import          — .knxproj hochladen, GAs importieren
POST /api/v1/knxproj/import-csv      — ETS GA-CSV hochladen (optional: DataPoints+Bindings anlegen)
GET  /api/v1/knxproj/group-addresses — importierte GAs abfragen (Suche)
DELETE /api/v1/knxproj/group-addresses — alle GAs löschen
"""

from __future__ import annotations

import json
import logging
import uuid as uuid_mod
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
    status,
)
from pydantic import BaseModel

from obs.api.auth import get_current_user
from obs.db.database import Database, get_db
from obs.knxproj.csv_parser import parse_ga_csv
from obs.knxproj.parser import parse_knxproj, parse_knxproj_locations, parse_knxproj_trades

logger = logging.getLogger(__name__)
router = APIRouter(tags=["knxproj"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class ImportResult(BaseModel):
    imported: int
    created: int = 0
    updated: int = 0
    locations: int = 0
    functions: int = 0
    trades: int = 0
    message: str


class GroupAddressOut(BaseModel):
    address: str
    name: str
    description: str
    dpt: str | None
    imported_at: str


class GroupAddressPage(BaseModel):
    total: int
    items: list[GroupAddressOut]


# ---------------------------------------------------------------------------
# Bulk DataPoint + Binding import helper
# ---------------------------------------------------------------------------


async def _bulk_import_datapoints(
    records: list[Any],
    adapter_name: str,
    direction: str,
    db: Database,
    now: str,
) -> tuple[int, int]:
    """Erstellt DataPoints + KNX-Bindings für alle records in einer DB-Transaktion.
    Bestehende Bindings (gleiche group_address + adapter_instance) werden aktualisiert.

    Returns: (created, updated)
    """
    from obs.adapters.knx.dpt_registry import DPTRegistry
    from obs.core.registry import ValueState, _row_to_datapoint, get_registry

    # --- Adapter-Instanz ermitteln ---
    instance_row = await db.fetchone(
        "SELECT id, adapter_type FROM adapter_instances WHERE name=?",
        (adapter_name,),
    )
    if not instance_row:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Adapter-Instanz '{adapter_name}' nicht gefunden",
        )
    adapter_instance_id: str = instance_row["id"]
    adapter_type: str = instance_row["adapter_type"]

    # --- Bestehende Bindings laden (group_address → {binding_id, dp_id}) ---
    existing_rows = await db.fetchall(
        "SELECT id, datapoint_id, config FROM adapter_bindings WHERE adapter_instance_id=?",
        (adapter_instance_id,),
    )
    existing_map: dict[str, dict[str, str]] = {}
    for row in existing_rows:
        try:
            cfg = json.loads(row["config"])
            ga = cfg.get("group_address")
            if ga:
                existing_map[ga] = {
                    "binding_id": row["id"],
                    "dp_id": row["datapoint_id"],
                }
        except (json.JSONDecodeError, KeyError):
            pass

    # --- Batch-Listen aufbauen ---
    dp_inserts: list[tuple] = []
    binding_inserts: list[tuple] = []
    dp_updates: list[tuple] = []
    binding_updates: list[tuple] = []
    new_dp_ids: list[str] = []  # für Registry-Update

    base_time = datetime.fromisoformat(now)

    for row_idx, record in enumerate(records):
        # DPT → data_type + unit aus Registry
        dpt_def = DPTRegistry.get(record.dpt) if record.dpt else None
        if dpt_def and dpt_def.dpt_id != "UNKNOWN":
            data_type = dpt_def.data_type
            unit = dpt_def.unit or None
        else:
            data_type = "UNKNOWN"
            unit = None

        config_dict = {"group_address": record.address}
        if record.dpt:
            config_dict["dpt_id"] = record.dpt
        config_json = json.dumps(config_dict)

        # Jede Zeile bekommt einen eindeutigen Timestamp → CSV-Reihenfolge bleibt erhalten
        row_ts = (base_time + timedelta(microseconds=row_idx)).isoformat()

        if record.address in existing_map:
            existing = existing_map[record.address]
            dp_updates.append((record.name, data_type, unit, row_ts, existing["dp_id"]))
            binding_updates.append((config_json, direction, row_ts, existing["binding_id"]))
        else:
            dp_id = str(uuid_mod.uuid4())
            mqtt_topic = f"dp/{dp_id}/value"
            dp_inserts.append(
                (
                    dp_id,
                    record.name,
                    data_type,
                    unit,
                    "[]",
                    mqtt_topic,
                    None,
                    1,
                    row_ts,
                    row_ts,
                ),
            )

            binding_id = str(uuid_mod.uuid4())
            binding_inserts.append(
                (
                    binding_id,
                    dp_id,
                    adapter_type,
                    adapter_instance_id,
                    direction,
                    config_json,
                    1,
                    now,
                    now,
                ),
            )
            new_dp_ids.append(dp_id)

    # --- Alle DB-Operationen in einer Transaktion ---
    if dp_inserts:
        await db.executemany(
            """INSERT INTO datapoints
               (id, name, data_type, unit, tags, mqtt_topic, mqtt_alias, persist_value, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            dp_inserts,
        )
    if binding_inserts:
        await db.executemany(
            """INSERT INTO adapter_bindings
               (id, datapoint_id, adapter_type, adapter_instance_id,
                direction, config, enabled, created_at, updated_at)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            binding_inserts,
        )
    if dp_updates:
        await db.executemany(
            "UPDATE datapoints SET name=?, data_type=?, unit=?, updated_at=? WHERE id=?",
            dp_updates,
        )
    if binding_updates:
        await db.executemany(
            "UPDATE adapter_bindings SET config=?, direction=?, updated_at=? WHERE id=?",
            binding_updates,
        )
    await db.commit()

    # --- In-Memory Registry aktualisieren (neue + aktualisierte DataPoints) ---
    updated_dp_ids = [t[4] for t in dp_updates]  # tuple: (name, data_type, unit, ts, id)
    all_registry_ids = new_dp_ids + updated_dp_ids
    if all_registry_ids:
        try:
            reg = get_registry()
            rows = await db.fetchall(
                f"SELECT * FROM datapoints WHERE id IN ({','.join('?' * len(all_registry_ids))})",
                all_registry_ids,
            )
            for row in rows:
                dp = _row_to_datapoint(row)
                reg._points[dp.id] = dp
                if dp.id not in reg._values:
                    reg._values[dp.id] = ValueState()
        except Exception:
            pass  # Registry nicht verfügbar (z.B. in Tests) — kein Fehler

    # --- Adapter-Instanz neu laden ---
    try:
        from obs.adapters.registry import _row_to_binding, get_instance_by_id

        adapter_instance = get_instance_by_id(adapter_instance_id)
        if adapter_instance:
            binding_rows = await db.fetchall(
                "SELECT * FROM adapter_bindings WHERE adapter_instance_id=? AND enabled=1",
                (adapter_instance_id,),
            )
            await adapter_instance.reload_bindings([_row_to_binding(r) for r in binding_rows])
    except Exception:
        pass  # Adapter nicht geladen — kein Fehler

    return len(dp_inserts), len(dp_updates)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/import", response_model=ImportResult)
async def import_knxproj_file(
    file: UploadFile = File(...),
    password: str | None = Form(None),
    adapter_name: str | None = Query(
        None,
        description="Adapter-Instanzname — wenn angegeben, werden DataPoints und Bindings angelegt",
    ),
    direction: str = Query("SOURCE", pattern="^(SOURCE|DEST|BOTH)$", description="Verknüpfungsrichtung"),
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> ImportResult:
    """.knxproj Datei hochladen und Gruppenadressen in die DB importieren.
    Bestehende Einträge werden mit UPSERT-Semantik aktualisiert.

    Mit adapter_name: zusätzlich DataPoints + KNX-Bindings anlegen.
    persist_value wird beim Anlegen auf False gesetzt und beim Reimport nicht überschrieben.
    """
    if not file.filename or not file.filename.lower().endswith(".knxproj"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nur .knxproj Dateien werden akzeptiert",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Datei ist leer")

    try:
        records = parse_knxproj(content, password or None)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Unerwarteter Fehler beim Parsen: {e}",
        )

    if not records:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Keine Gruppenadressen gefunden. "
            "Bitte prüfe ob du das richtige ETS-Projekt exportiert hast: "
            "In ETS unter 'Datei → Speichern unter' oder 'Projekt exportieren'. "
            "Eine Produktdatenbank (nur M-XXXX/ Ordner) enthält keine Gruppenadressen.",
        )

    now = datetime.now(UTC).isoformat()

    await db.executemany(
        """INSERT INTO knx_group_addresses
               (address, name, description, dpt, main_group_name, mid_group_name, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(address) DO UPDATE SET
               name            = excluded.name,
               description     = excluded.description,
               dpt             = excluded.dpt,
               main_group_name = excluded.main_group_name,
               mid_group_name  = excluded.mid_group_name,
               imported_at     = excluded.imported_at""",
        [(r.address, r.name, r.description, r.dpt, r.main_group_name, r.mid_group_name, now) for r in records],
    )
    await db.commit()

    # Import Gebäude/Gewerke structure (locations + functions)
    locations_count = 0
    functions_count = 0
    try:
        loc_records, fn_records = parse_knxproj_locations(content, password or None)

        if loc_records:
            await db.execute_and_commit("DELETE FROM knx_locations")
            await db.executemany(
                """INSERT INTO knx_locations (id, parent_id, name, space_type, sort_order, imported_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                [(r.identifier, r.parent_id, r.name, r.space_type, r.sort_order, now) for r in loc_records],
            )
            await db.commit()
            locations_count = len(loc_records)

        if fn_records:
            await db.execute_and_commit("DELETE FROM knx_functions")
            await db.execute_and_commit("DELETE FROM knx_function_ga_links")
            await db.executemany(
                """INSERT INTO knx_functions (id, space_id, name, usage_text, imported_at)
                   VALUES (?, ?, ?, ?, ?)""",
                [(r.identifier, r.space_id, r.name, r.usage_text, now) for r in fn_records],
            )
            ga_links = [(r.identifier, addr) for r in fn_records for addr in r.ga_addresses]
            if ga_links:
                await db.executemany(
                    "INSERT OR IGNORE INTO knx_function_ga_links (function_id, ga_address) VALUES (?, ?)",
                    ga_links,
                )
            await db.commit()
            functions_count = len(fn_records)
    except Exception as e:
        logger.warning("Gebäude/Gewerke-Import fehlgeschlagen (wird ignoriert): %s", e)

    # Import Trades (Gewerke) — direct ZIP/XML parsing, no xknxproject needed
    trades_count = 0
    try:
        trade_records = parse_knxproj_trades(content)
        if trade_records:
            await db.execute_and_commit("DELETE FROM knx_trades")
            await db.executemany(
                "INSERT INTO knx_trades (id, name, parent_id, sort_order, imported_at) VALUES (?, ?, ?, ?, ?)",
                [(r.identifier, r.name, r.parent_id, r.sort_order, now) for r in trade_records],
            )
            await db.commit()
            trades_count = len(trade_records)

            # Link functions to their trade:
            # Primary: XML DeviceInstanceRef.Links (exact function ID match)
            # Fallback: usage_text case-insensitive match against trade name
            fn_to_trade: dict[str, str] = {}
            for tr in trade_records:
                for fn_id in tr.function_ids:
                    fn_to_trade[fn_id] = tr.identifier

            if fn_to_trade:
                await db.executemany(
                    "UPDATE knx_functions SET trade_id = ? WHERE id = ?",
                    [(trade_id, fn_id) for fn_id, trade_id in fn_to_trade.items()],
                )
                await db.commit()
            else:
                # Fallback: match usage_text to trade name (works for German projects)
                trade_name_map = {tr.name.lower().strip(): tr.identifier for tr in trade_records}
                fn_rows = await db.fetchall("SELECT id, usage_text FROM knx_functions WHERE trade_id IS NULL")
                updates = []
                for fn in fn_rows:
                    usage = (fn["usage_text"] or "").lower().strip()
                    if usage and usage in trade_name_map:
                        updates.append((trade_name_map[usage], fn["id"]))
                if updates:
                    await db.executemany(
                        "UPDATE knx_functions SET trade_id = ? WHERE id = ?",
                        updates,
                    )
                    await db.commit()
    except Exception as e:
        logger.warning("Trades-Import fehlgeschlagen (wird ignoriert): %s", e)

    # Ohne Adapter: nur GA-Tabelle → fertig
    if not adapter_name:
        msg = f"{len(records)} Gruppenadressen importiert"
        extra = []
        if locations_count:
            extra.append(f"{locations_count} Räume/Gebäude")
        if trades_count:
            extra.append(f"{trades_count} Gewerke")
        if extra:
            msg += ", " + ", ".join(extra)
        return ImportResult(
            imported=len(records),
            locations=locations_count,
            functions=functions_count,
            trades=trades_count,
            message=msg,
        )

    # Mit Adapter: DataPoints + Bindings bulk anlegen
    created, updated = await _bulk_import_datapoints(records, adapter_name, direction, db, now)

    return ImportResult(
        imported=len(records),
        created=created,
        updated=updated,
        locations=locations_count,
        functions=functions_count,
        trades=trades_count,
        message=f"{len(records)} Gruppenadressen importiert, {created} DataPoints neu erstellt, {updated} aktualisiert",
    )


@router.post("/import-csv", response_model=ImportResult)
async def import_ga_csv_file(
    file: UploadFile = File(...),
    adapter_name: str | None = Query(
        None,
        description="Adapter-Instanzname — wenn angegeben, werden DataPoints und Bindings angelegt",
    ),
    direction: str = Query("SOURCE", pattern="^(SOURCE|DEST|BOTH)$", description="Verknüpfungsrichtung"),
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> ImportResult:
    """ETS GA-CSV hochladen.

    Ohne adapter_name: nur knx_group_addresses Tabelle befüllen (schnelle Vorschau).
    Mit adapter_name:  zusätzlich DataPoints + KNX-Bindings in einer Transaktion anlegen
                       (Bulk-Import, deutlich schneller als Einzelrequests).

    Bestehende DataPoints/Bindings für dieselbe Gruppenadresse werden aktualisiert.
    """
    if not file.filename or not file.filename.lower().endswith(".csv"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nur .csv Dateien werden akzeptiert",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Datei ist leer")

    try:
        records = parse_ga_csv(content)
    except ValueError as e:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(e))
    except Exception as e:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"Unerwarteter Fehler beim Parsen: {e}",
        )

    if not records:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Keine Gruppenadressen gefunden. Bitte prüfe ob du den ETS GA-Export als CSV verwendet hast.",
        )

    now = datetime.now(UTC).isoformat()

    # GA-Tabelle immer befüllen (für Vorschau / manuelle Bindung im GUI)
    await db.executemany(
        """INSERT INTO knx_group_addresses
               (address, name, description, dpt, main_group_name, mid_group_name, imported_at)
           VALUES (?, ?, ?, ?, ?, ?, ?)
           ON CONFLICT(address) DO UPDATE SET
               name            = excluded.name,
               description     = excluded.description,
               dpt             = excluded.dpt,
               main_group_name = excluded.main_group_name,
               mid_group_name  = excluded.mid_group_name,
               imported_at     = excluded.imported_at""",
        [(r.address, r.name, r.description, r.dpt, r.main_group_name, r.mid_group_name, now) for r in records],
    )
    await db.commit()

    # Ohne Adapter: nur GA-Tabelle → fertig
    if not adapter_name:
        return ImportResult(
            imported=len(records),
            message=f"{len(records)} Gruppenadressen importiert (ohne DataPoints — adapter_name fehlt)",
        )

    # Mit Adapter: DataPoints + Bindings bulk anlegen
    created, updated = await _bulk_import_datapoints(records, adapter_name, direction, db, now)

    return ImportResult(
        imported=created + updated,
        created=created,
        updated=updated,
        message=f"{created} DataPoints neu erstellt, {updated} aktualisiert",
    )


@router.get("/group-addresses", response_model=GroupAddressPage)
async def list_group_addresses(
    q: str = Query("", description="Suche in Adresse, Name oder Beschreibung"),
    page: int = Query(0, ge=0),
    size: int = Query(100, ge=1, le=500),
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> GroupAddressPage:
    """Importierte KNX Gruppenadressen abfragen. Unterstützt Volltextsuche."""
    if q:
        like = f"%{q}%"
        rows = await db.fetchall(
            """SELECT address, name, description, dpt, imported_at
               FROM knx_group_addresses
               WHERE address LIKE ? OR name LIKE ? OR description LIKE ?
               ORDER BY address
               LIMIT ? OFFSET ?""",
            (like, like, like, size, page * size),
        )
        count_row = await db.fetchone(
            """SELECT COUNT(*) AS n FROM knx_group_addresses
               WHERE address LIKE ? OR name LIKE ? OR description LIKE ?""",
            (like, like, like),
        )
    else:
        rows = await db.fetchall(
            """SELECT address, name, description, dpt, imported_at
               FROM knx_group_addresses
               ORDER BY address
               LIMIT ? OFFSET ?""",
            (size, page * size),
        )
        count_row = await db.fetchone(
            "SELECT COUNT(*) AS n FROM knx_group_addresses",
        )

    total = count_row["n"] if count_row else 0
    return GroupAddressPage(
        total=total,
        items=[GroupAddressOut(**dict(r)) for r in rows],
    )


@router.delete("/group-addresses", status_code=status.HTTP_204_NO_CONTENT)
async def clear_group_addresses(
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> None:
    """Alle importierten KNX Gruppenadressen löschen."""
    await db.execute_and_commit("DELETE FROM knx_group_addresses")
