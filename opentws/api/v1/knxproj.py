"""
KNX Project Import API

POST /api/v1/knxproj/import          — .knxproj hochladen, GAs importieren
GET  /api/v1/knxproj/group-addresses — importierte GAs abfragen (Suche)
DELETE /api/v1/knxproj/group-addresses — alle GAs löschen
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from pydantic import BaseModel

from opentws.api.auth import get_current_user
from opentws.db.database import Database, get_db
from opentws.knxproj.parser import parse_knxproj

router = APIRouter(tags=["knxproj"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class ImportResult(BaseModel):
    imported: int
    message:  str


class GroupAddressOut(BaseModel):
    address:     str
    name:        str
    description: str
    dpt:         str | None
    imported_at: str


class GroupAddressPage(BaseModel):
    total:   int
    items:   list[GroupAddressOut]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/import", response_model=ImportResult)
async def import_knxproj_file(
    file:     UploadFile = File(...),
    password: str | None = Form(None),
    _user:    str        = Depends(get_current_user),
    db:       Database   = Depends(get_db),
) -> ImportResult:
    """
    .knxproj Datei hochladen und Gruppenadressen in die DB importieren.
    Bestehende Einträge werden mit UPSERT-Semantik aktualisiert.
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
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "Keine Gruppenadressen gefunden. "
            "Bitte prüfe ob du das richtige ETS-Projekt exportiert hast: "
            "In ETS unter 'Datei → Speichern unter' oder 'Projekt exportieren'. "
            "Eine Produktdatenbank (nur M-XXXX/ Ordner) enthält keine Gruppenadressen.",
        )

    now = datetime.now(timezone.utc).isoformat()

    await db.executemany(
        """INSERT INTO knx_group_addresses (address, name, description, dpt, imported_at)
           VALUES (?, ?, ?, ?, ?)
           ON CONFLICT(address) DO UPDATE SET
               name        = excluded.name,
               description = excluded.description,
               dpt         = excluded.dpt,
               imported_at = excluded.imported_at""",
        [
            (r.address, r.name, r.description, r.dpt, now)
            for r in records
        ],
    )
    await db.commit()

    return ImportResult(
        imported=len(records),
        message=f"{len(records)} Gruppenadressen erfolgreich importiert",
    )


@router.get("/group-addresses", response_model=GroupAddressPage)
async def list_group_addresses(
    q:     str = Query("", description="Suche in Adresse, Name oder Beschreibung"),
    page:  int = Query(0, ge=0),
    size:  int = Query(100, ge=1, le=500),
    _user: str = Depends(get_current_user),
    db:    Database = Depends(get_db),
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
    _user: str      = Depends(get_current_user),
    db:    Database = Depends(get_db),
) -> None:
    """Alle importierten KNX Gruppenadressen löschen."""
    await db.execute_and_commit("DELETE FROM knx_group_addresses")
