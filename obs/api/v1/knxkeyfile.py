"""KNX API

GET    /api/v1/knx/scan              — KNX/IP-Geräte im Netzwerk suchen
POST   /api/v1/knx/keyfile           — .knxkeys hochladen, Tunnel-Liste zurückgeben
DELETE /api/v1/knx/keyfile/{file_id} — gespeichertes Keyfile löschen
"""

from __future__ import annotations

import logging
import uuid as uuid_mod
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import BaseModel

from obs.api.auth import get_current_user
from obs.config import get_settings

logger = logging.getLogger(__name__)
router = APIRouter(tags=["knx"])


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------


class TunnelInfo(BaseModel):
    individual_address: str
    host: str | None
    user_id: int | None
    secure_ga_count: int


class BackboneInfo(BaseModel):
    multicast_address: str | None
    latency_ms: int | None


class KeyfileParseResult(BaseModel):
    file_id: str
    file_path: str
    project_name: str
    tunnels: list[TunnelInfo]
    backbone: BackboneInfo | None


class GatewayScanResult(BaseModel):
    name: str
    ip_addr: str
    port: int
    local_ip: str
    local_interface: str
    individual_address: str | None
    supports_tunnelling: bool
    supports_tunnelling_tcp: bool
    supports_routing: bool
    supports_secure: bool
    tunnelling_requires_secure: bool | None
    routing_requires_secure: bool | None


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _keyfiles_dir() -> Path:
    """Gibt das Verzeichnis für gespeicherte .knxkeys Dateien zurück."""
    db_path = Path(get_settings().database.path)
    d = db_path.parent / "knxkeys"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _parse_keyring(path: Path, password: str) -> Any:
    """Lädt und entschlüsselt ein .knxkeys File synchron."""
    try:
        from xknx.secure.keyring import sync_load_keyring
    except ImportError as exc:
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "xknx nicht installiert",
        ) from exc

    try:
        return sync_load_keyring(path, password)
    except Exception as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"Keyfile konnte nicht geladen werden: {exc}",
        ) from exc


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/scan", response_model=list[GatewayScanResult])
async def scan_knx_gateways(
    timeout: float = 4.0,
    local_ip: str | None = None,
    _user: str = Depends(get_current_user),
) -> list[GatewayScanResult]:
    """KNX/IP-Geräte im lokalen Netzwerk suchen (GatewayScanner).

    Sendet UDP-Multicast SearchRequest-Frames und gibt alle gefundenen
    KNX/IP-Interfaces mit ihren Fähigkeiten zurück.
    """
    try:
        from xknx import XKNX
        from xknx.io.gateway_scanner import GatewayScanner
    except ImportError as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "xknx nicht installiert") from exc

    xknx = XKNX()
    scanner = GatewayScanner(xknx, local_ip=local_ip, timeout_in_seconds=timeout)
    try:
        gateways = await scanner.scan()
    except Exception as exc:
        logger.warning("KNX GatewayScanner Fehler: %s", exc)
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR, f"Scan fehlgeschlagen: {exc}") from exc

    logger.info("KNX Scan: %d Gerät(e) gefunden", len(gateways))
    return [
        GatewayScanResult(
            name=gw.name,
            ip_addr=gw.ip_addr,
            port=gw.port,
            local_ip=gw.local_ip,
            local_interface=gw.local_interface,
            individual_address=str(gw.individual_address) if gw.individual_address else None,
            supports_tunnelling=gw.supports_tunnelling,
            supports_tunnelling_tcp=gw.supports_tunnelling_tcp,
            supports_routing=gw.supports_routing,
            supports_secure=gw.supports_secure,
            tunnelling_requires_secure=gw.tunnelling_requires_secure,
            routing_requires_secure=gw.routing_requires_secure,
        )
        for gw in gateways
    ]


@router.post("/keyfile", response_model=KeyfileParseResult)
async def upload_keyfile(
    file: UploadFile = File(...),
    password: str = Form(...),
    _user: str = Depends(get_current_user),
) -> KeyfileParseResult:
    """.knxkeys Datei hochladen, entschlüsseln und verfügbare Tunnel zurückgeben.

    Das Keyfile wird serverseitig gespeichert. Der zurückgegebene `file_path`
    wird im KNX-Adapter unter `knxkeys_file_path` eingetragen.
    Der `individual_address`-Wert des gewünschten Tunnels wird als
    `individual_address` in der Adapter-Konfiguration gesetzt.
    """
    if not file.filename or not file.filename.lower().endswith(".knxkeys"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Nur .knxkeys Dateien werden akzeptiert",
        )

    content = await file.read()
    if not content:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Datei ist leer")

    # Speichern
    file_id = str(uuid_mod.uuid4())
    keyfile_path = _keyfiles_dir() / f"{file_id}.knxkeys"
    keyfile_path.write_bytes(content)

    # Parsen — bei Fehler gespeicherte Datei wieder löschen
    try:
        keyring = _parse_keyring(keyfile_path, password)
    except HTTPException:
        keyfile_path.unlink(missing_ok=True)
        raise

    # Tunneling-Interfaces extrahieren
    try:
        from xknx.secure.keyring import InterfaceType
    except ImportError as exc:
        keyfile_path.unlink(missing_ok=True)
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "xknx nicht installiert") from exc

    tunnels: list[TunnelInfo] = [
        TunnelInfo(
            individual_address=str(iface.individual_address),
            host=str(iface.host) if iface.host else None,
            user_id=iface.user_id,
            secure_ga_count=len(iface.group_addresses),
        )
        for iface in keyring.interfaces
        if iface.type is InterfaceType.TUNNELING
    ]

    # Backbone-Info (für Routing Secure)
    backbone: BackboneInfo | None = None
    if keyring.backbone is not None:
        backbone = BackboneInfo(
            multicast_address=keyring.backbone.multicast_address,
            latency_ms=keyring.backbone.latency,
        )

    logger.info(
        "KNX Keyfile importiert: project=%r tunnels=%d backbone=%s file_id=%s",
        keyring.project_name,
        len(tunnels),
        backbone is not None,
        file_id,
    )

    return KeyfileParseResult(
        file_id=file_id,
        file_path=str(keyfile_path),
        project_name=keyring.project_name,
        tunnels=tunnels,
        backbone=backbone,
    )


@router.delete("/keyfile/{file_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_keyfile(
    file_id: str,
    _user: str = Depends(get_current_user),
) -> None:
    """Gespeichertes .knxkeys File löschen."""
    # Nur UUID-artige file_ids erlauben (Pfad-Traversal verhindern)
    try:
        file_uuid = uuid_mod.UUID(file_id)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültige file_id") from exc

    safe_file_id = str(file_uuid)

    base_dir = _keyfiles_dir().resolve()
    keyfile_path = (base_dir / f"{safe_file_id}.knxkeys").resolve()
    try:
        keyfile_path.relative_to(base_dir)
    except ValueError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültige file_id") from exc

    if not keyfile_path.exists():
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Keyfile nicht gefunden")

    keyfile_path.unlink()
    logger.info("KNX Keyfile gelöscht: file_id=%s", file_id)
