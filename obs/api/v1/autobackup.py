"""Autobackup — /api/v1/config/autobackup/...

Täglich automatische JSON-Sicherung aller Konfigurationsdaten (Adapter, Objekte,
Logikmodul, Visu, NavLinks, AppSettings, Hierarchy, Icons).

Einstellungen werden in app_settings gespeichert:
  autobackup.enabled        = "1" | "0"
  autobackup.hour           = "0" … "23"  (Uhrzeit der täglichen Sicherung)
  autobackup.retention_days = "1" … "30"  (Anzahl Sicherungen aufbewahren)

Sicherungsdateien liegen in {db_dir}/autobackup/YYYYMMDD-HHMM.json
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from obs.api.auth import get_admin_user
from obs.db.database import Database, get_db

logger = logging.getLogger(__name__)

router = APIRouter(tags=["config"])

# ---------------------------------------------------------------------------
# Modelle
# ---------------------------------------------------------------------------


class AutobackupConfig(BaseModel):
    enabled: bool
    hour: int  # 0–23
    retention_days: int  # 1–30


class AutobackupEntry(BaseModel):
    name: str  # z.B. "20240506-0300"
    created_at: str  # ISO-Timestamp aus dem Dateinamen
    size_bytes: int


# ---------------------------------------------------------------------------
# Hilfsfunktionen
# ---------------------------------------------------------------------------


def _autobackup_dir() -> Path:
    from obs.config import get_settings

    db_path = Path(get_settings().database.path)
    backup_dir = db_path.parent / "autobackup"
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


async def _load_config(db: Database) -> AutobackupConfig:
    rows = await db.fetchall("SELECT key, value FROM app_settings WHERE key LIKE 'autobackup.%'")
    settings = {r["key"]: r["value"] for r in rows}
    return AutobackupConfig(
        enabled=settings.get("autobackup.enabled", "0") == "1",
        hour=int(settings.get("autobackup.hour", "3")),
        retention_days=int(settings.get("autobackup.retention_days", "7")),
    )


async def _save_config(db: Database, cfg: AutobackupConfig) -> None:
    for key, value in [
        ("autobackup.enabled", "1" if cfg.enabled else "0"),
        ("autobackup.hour", str(cfg.hour)),
        ("autobackup.retention_days", str(cfg.retention_days)),
    ]:
        await db.execute_and_commit(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?,?)",
            (key, value),
        )


def _list_backups() -> list[AutobackupEntry]:
    backup_dir = _autobackup_dir()
    entries: list[AutobackupEntry] = []
    for f in sorted(backup_dir.glob("*.json"), reverse=True):
        stem = f.stem  # z.B. "20240506-0300"
        try:
            dt = datetime.strptime(stem, "%Y%m%d-%H%M")
            created_at = dt.isoformat()
        except ValueError:
            created_at = stem
        entries.append(AutobackupEntry(name=stem, created_at=created_at, size_bytes=f.stat().st_size))
    return entries


def _prune_old_backups(retention_days: int) -> int:
    backup_dir = _autobackup_dir()
    files = sorted(backup_dir.glob("*.json"), reverse=True)
    deleted = 0
    for f in files[retention_days:]:
        try:
            f.unlink()
            deleted += 1
        except OSError:
            pass
    return deleted


async def _create_backup_now(db: Database) -> str:
    """Erstellt eine JSON-Sicherung und gibt den Dateinamen zurück."""
    from obs.api.v1.config import export_config

    # Export-Funktion direkt aufrufen (ohne HTTP-Request)
    export = await export_config(_user="autobackup", db=db)

    ts = datetime.now(UTC).strftime("%Y%m%d-%H%M")
    backup_path = _autobackup_dir() / f"{ts}.json"
    backup_path.write_text(json.dumps(export.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Autobackup erstellt: %s (%d Bytes)", backup_path.name, backup_path.stat().st_size)
    return ts


# ---------------------------------------------------------------------------
# API-Endpunkte
# ---------------------------------------------------------------------------


@router.get("/autobackup/config", response_model=AutobackupConfig)
async def get_autobackup_config(
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> AutobackupConfig:
    return await _load_config(db)


@router.put("/autobackup/config", response_model=AutobackupConfig)
async def set_autobackup_config(
    body: AutobackupConfig,
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> AutobackupConfig:
    if not (0 <= body.hour <= 23):
        raise HTTPException(status_code=400, detail="hour muss zwischen 0 und 23 liegen.")
    if not (1 <= body.retention_days <= 30):
        raise HTTPException(status_code=400, detail="retention_days muss zwischen 1 und 30 liegen.")
    await _save_config(db, body)
    # Scheduler über Konfigurationsänderung informieren
    _notify_config_change()
    return body


@router.get("/autobackup/list", response_model=list[AutobackupEntry])
async def list_autobackups(
    _admin: str = Depends(get_admin_user),
) -> list[AutobackupEntry]:
    return _list_backups()


@router.post("/autobackup/run", status_code=status.HTTP_200_OK)
async def run_autobackup_now(
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> dict:
    """Autobackup sofort manuell auslösen."""
    name = await _create_backup_now(db)
    cfg = await _load_config(db)
    deleted = _prune_old_backups(cfg.retention_days)
    return {"ok": True, "name": name, "old_backups_deleted": deleted}


@router.post("/autobackup/restore/{name}", status_code=status.HTTP_200_OK)
async def restore_autobackup(
    name: str,
    _admin: str = Depends(get_admin_user),
    db: Database = Depends(lambda: get_db()),
) -> dict:
    """Autobackup-Sicherung wiederherstellen (Upsert-Semantik, wie JSON-Import)."""
    # Dateinamen strikt validieren (erwartetes Format: YYYYMMDD-HHMM)
    safe_name = Path(name).name
    if not re.fullmatch(r"\d{8}-\d{4}", safe_name):
        raise HTTPException(status_code=400, detail="Ungültiger Sicherungsname.")

    base_dir = _autobackup_dir().resolve()
    allowed_backups = {p.stem: p for p in base_dir.glob("*.json") if p.is_file() and re.fullmatch(r"\d{8}-\d{4}", p.stem)}
    backup_path = allowed_backups.get(safe_name)
    if backup_path is None:
        raise HTTPException(status_code=404, detail=f"Sicherung '{safe_name}' nicht gefunden.")

    try:
        content = json.loads(backup_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Sicherungsdatei ungültig: {exc}") from exc

    from obs.api.v1.config import ConfigExport, ImportResult, import_config

    try:
        body = ConfigExport.model_validate(content)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Sicherungsformat ungültig: {exc}") from exc

    result: ImportResult = await import_config(body=body, _user="autobackup-restore", db=db)
    return {
        "ok": True,
        "name": safe_name,
        "datapoints": result.datapoints_created + result.datapoints_updated,
        "bindings": result.bindings_created + result.bindings_updated,
        "visu_nodes": result.visu_nodes_upserted,
        "errors": result.errors,
    }


@router.delete("/autobackup/{name}", status_code=status.HTTP_200_OK)
async def delete_autobackup(
    name: str,
    _admin: str = Depends(get_admin_user),
) -> dict:
    """Eine einzelne Autobackup-Sicherung löschen."""
    safe_name = Path(name).name
    if not re.fullmatch(r"\d{8}-\d{4}", safe_name):
        raise HTTPException(status_code=400, detail="Ungültiger Sicherungsname.")

    # Allowlist: nur Namen löschen, die als vorhandene Backups gelistet sind.
    existing_backups = _list_backups()
    matched = next((entry for entry in existing_backups if entry.name == safe_name), None)
    if matched is None:
        raise HTTPException(status_code=404, detail=f"Sicherung '{safe_name}' nicht gefunden.")

    base_dir = _autobackup_dir().resolve()
    backup_path = (base_dir / f"{matched.name}.json").resolve()
    try:
        backup_path.relative_to(base_dir)
    except ValueError:
        raise HTTPException(status_code=400, detail="Ungültiger Sicherungspfad.")

    if not backup_path.exists():
        raise HTTPException(status_code=404, detail=f"Sicherung '{safe_name}' nicht gefunden.")
    backup_path.unlink()
    return {"ok": True, "name": safe_name}


# ---------------------------------------------------------------------------
# Hintergrund-Scheduler
# ---------------------------------------------------------------------------

_config_changed_event: asyncio.Event | None = None


def _notify_config_change() -> None:
    if _config_changed_event is not None:
        _config_changed_event.set()


class AutobackupScheduler:
    def __init__(self, db: Database) -> None:
        self._db = db
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        global _config_changed_event
        _config_changed_event = asyncio.Event()
        self._task = asyncio.create_task(self._loop(), name="autobackup-scheduler")
        logger.info("Autobackup-Scheduler gestartet.")

    async def stop(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Autobackup-Scheduler gestoppt.")

    async def _loop(self) -> None:
        last_backup_day: int | None = None

        while True:
            try:
                global _config_changed_event
                cfg = await _load_config(self._db)

                if not cfg.enabled:
                    # Deaktiviert: alle 5 Minuten prüfen ob Konfiguration geändert wurde
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(_config_changed_event.wait()),
                            timeout=300,
                        )
                        _config_changed_event.clear()
                    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                        if isinstance(exc, asyncio.CancelledError):
                            raise
                    continue

                now = datetime.now(UTC)
                today = now.day

                # Prüfen ob heute schon gesichert wurde
                if last_backup_day == today and now.hour >= cfg.hour:
                    # Warten bis morgen um cfg.hour
                    tomorrow = datetime(now.year, now.month, now.day, cfg.hour, 0, tzinfo=UTC)
                    if tomorrow <= now:
                        # Nächsten Monat / Jahr berücksichtigen
                        import calendar

                        days_in_month = calendar.monthrange(now.year, now.month)[1]
                        if now.day < days_in_month:
                            tomorrow = datetime(now.year, now.month, now.day + 1, cfg.hour, 0, tzinfo=UTC)
                        elif now.month < 12:
                            tomorrow = datetime(now.year, now.month + 1, 1, cfg.hour, 0, tzinfo=UTC)
                        else:
                            tomorrow = datetime(now.year + 1, 1, 1, cfg.hour, 0, tzinfo=UTC)

                    wait_s = max(60.0, (tomorrow - now).total_seconds())
                    logger.debug("Autobackup: nächste Sicherung in %.0fs um %s", wait_s, tomorrow.isoformat())
                    try:
                        await asyncio.wait_for(
                            asyncio.shield(_config_changed_event.wait()),
                            timeout=min(wait_s, 270),
                        )
                        _config_changed_event.clear()
                    except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                        if isinstance(exc, asyncio.CancelledError):
                            raise
                    continue

                # Ist es Zeit für die Sicherung?
                if now.hour >= cfg.hour and last_backup_day != today:
                    try:
                        await _create_backup_now(self._db)
                        deleted = _prune_old_backups(cfg.retention_days)
                        last_backup_day = today
                        if deleted:
                            logger.info("Autobackup: %d alte Sicherung(en) gelöscht.", deleted)
                    except Exception as exc:
                        logger.error("Autobackup fehlgeschlagen: %s", exc)

                # Jede Minute prüfen (oder auf Konfigurationsänderung warten)
                try:
                    await asyncio.wait_for(
                        asyncio.shield(_config_changed_event.wait()),
                        timeout=60,
                    )
                    _config_changed_event.clear()
                except (asyncio.TimeoutError, asyncio.CancelledError) as exc:
                    if isinstance(exc, asyncio.CancelledError):
                        raise

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error("Autobackup-Scheduler Fehler: %s", exc)
                await asyncio.sleep(60)


_scheduler: AutobackupScheduler | None = None


def init_autobackup_scheduler(db: Database) -> AutobackupScheduler:
    global _scheduler
    _scheduler = AutobackupScheduler(db)
    _scheduler.start()
    return _scheduler


def get_autobackup_scheduler() -> AutobackupScheduler:
    if _scheduler is None:
        raise RuntimeError("AutobackupScheduler nicht initialisiert.")
    return _scheduler
