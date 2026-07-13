"""VISU background image catalog API.

Endpoints:
  GET    /visu/backgrounds         - list catalog entries (auth required)
  POST   /visu/backgrounds/import  - upload image file(s) (auth required)
  GET    /visu/backgrounds/{name}  - get image by logical name (public)
  DELETE /visu/backgrounds         - delete one or more entries (auth required)
"""

from __future__ import annotations

import re
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, Request, UploadFile, status
from fastapi.responses import Response
from pydantic import BaseModel

from obs.api.auth import get_admin_user, get_current_user
from obs.api.v1.application_audit import audit_application_contract, write_application_success
from obs.config import get_settings
from obs.db.database import Database, get_db

router = APIRouter(tags=["visu", "backgrounds"])

_ALLOWED_EXTENSIONS = ("png", "jpg", "jpeg", "webp")


class BackgroundOut(BaseModel):
    name: str
    filename: str
    size: int
    mime_type: str
    url: str


class BackgroundListOut(BaseModel):
    total: int
    backgrounds: list[BackgroundOut]


class ImportResult(BaseModel):
    imported: int
    skipped: int
    names: list[str]
    message: str


class DeleteRequest(BaseModel):
    names: list[str]


def _secure_filename(filename: str) -> str:
    filename = filename.strip().replace("/", "_").replace("\\", "_").replace("\x00", "")
    filename = re.sub(r"[^\w.\-]", "_", filename, flags=re.ASCII)
    filename = filename.lstrip("._")
    return filename


def _safe_stem(filename: str) -> str | None:
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return None
    stem = Path(filename).stem
    if not stem or stem.startswith("."):
        return None
    clean = re.sub(r"[^\w\-]", "_", stem, flags=re.ASCII).lower().strip("_")
    return clean or None


def _backgrounds_dir() -> Path:
    settings = get_settings()
    db_path = settings.database.path
    if db_path in (":memory:", "file::memory:?cache=shared"):
        target = Path("/tmp/obs_visu_backgrounds_test")
    else:
        target = Path(db_path).parent / "visu_backgrounds"
    target.mkdir(parents=True, exist_ok=True)
    return target


def _detect_image_type(content: bytes) -> tuple[str, str] | None:
    """Return (extension, mime_type) if content looks like a supported image."""
    head = content[:2048]
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if len(head) >= 3 and head[0:3] == b"\xff\xd8\xff":
        return "jpg", "image/jpeg"
    if len(head) >= 12 and head[0:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp", "image/webp"
    return None


def _guess_mime_type(path: Path) -> str:
    ext = path.suffix.lower().lstrip(".")
    if ext == "png":
        return "image/png"
    if ext in ("jpg", "jpeg"):
        return "image/jpeg"
    if ext == "webp":
        return "image/webp"
    return "application/octet-stream"


def _find_background_file(name: str, directory: Path) -> Path | None:
    for ext in _ALLOWED_EXTENSIONS:
        candidate = directory / f"{name}.{ext}"
        if candidate.exists():
            return candidate
    return None


@router.get("", response_model=BackgroundListOut)
async def list_backgrounds(_user: str = Depends(get_current_user)) -> BackgroundListOut:
    directory = _backgrounds_dir()
    items: list[BackgroundOut] = []

    for file in sorted(directory.iterdir()):
        if not file.is_file():
            continue
        ext = file.suffix.lower().lstrip(".")
        if ext not in _ALLOWED_EXTENSIONS:
            continue
        try:
            size = file.stat().st_size
            mime_type = _guess_mime_type(file)
            items.append(
                BackgroundOut(
                    name=file.stem,
                    filename=file.name,
                    size=size,
                    mime_type=mime_type,
                    url=f"/api/v1/visu/backgrounds/{file.stem}",
                ),
            )
        except OSError:
            continue

    return BackgroundListOut(total=len(items), backgrounds=items)


@router.post("/import", response_model=ImportResult)
@audit_application_contract("POST", "/api/v1/visu/backgrounds/import", principal_param="_user")
async def import_backgrounds(
    request: Request = None,
    files: list[UploadFile] = File(...),
    _user: str = Depends(get_admin_user),
    db: Database = Depends(get_db),
) -> ImportResult:
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Dateien empfangen")

    directory = _backgrounds_dir()
    imported_names: list[str] = []
    prepared: list[tuple[str, str, bytes]] = []
    skipped = 0

    # Validate the complete batch before touching the catalog. A malformed
    # later upload must not leave earlier files imported.
    for upload in files:
        content = await upload.read()
        filename = upload.filename or ""

        detected = _detect_image_type(content)
        if detected is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                f"'{filename}' enthält kein gültiges Bildformat (erlaubt: PNG, JPG, WEBP)",
            )

        name = _safe_stem(filename)
        if not name:
            skipped += 1
            continue

        ext, _ = detected
        prepared.append((name, ext, content))
        if name not in imported_names:
            imported_names.append(name)

    for name, ext, content in prepared:
        target = directory / f"{name}.{ext}"
        target.write_bytes(content)

        # Remove stale variants with other extensions to keep one canonical file per name.
        for other_ext in _ALLOWED_EXTENSIONS:
            if other_ext == ext:
                continue
            alt = directory / f"{name}.{other_ext}"
            if alt.exists():
                alt.unlink()

    result = ImportResult(
        imported=len(imported_names),
        skipped=skipped,
        names=imported_names,
        message=(f"{len(imported_names)} Hintergrundbild(er) importiert" + (f", {skipped} übersprungen" if skipped else "")),
    )
    if isinstance(db, Database):
        await write_application_success(
            db,
            request,
            _user,
            "POST",
            "/api/v1/visu/backgrounds/import",
            details={"imported_count": result.imported, "skipped_count": result.skipped},
            commit=True,
        )
    return result


@router.get("/{name}")
async def get_background(name: str) -> Response:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültiger Hintergrund-Name")

    safe_name = _secure_filename(name)
    if not safe_name or safe_name != name:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültiger Hintergrund-Name")

    directory = _backgrounds_dir().resolve()
    target = _find_background_file(safe_name, directory)
    if not target:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Hintergrund '{name}' nicht gefunden")

    resolved = target.resolve()
    if not resolved.is_relative_to(directory):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Ungültiger Hintergrund-Pfad")

    return Response(content=resolved.read_bytes(), media_type=_guess_mime_type(resolved))


@router.delete("", status_code=status.HTTP_200_OK)
@audit_application_contract("DELETE", "/api/v1/visu/backgrounds", principal_param="_user")
async def delete_backgrounds(
    body: DeleteRequest,
    request: Request = None,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> dict:
    directory = _backgrounds_dir().resolve()
    deleted: list[str] = []
    not_found: list[str] = []
    prepared: list[tuple[str, Path | None]] = []
    seen: set[str] = set()

    # Resolve and validate the complete request before deleting the first file.
    for name in body.names:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ungültiger Hintergrund-Name: {name!r}")
        safe_name = _secure_filename(name)
        if not safe_name or safe_name != name:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ungültiger Hintergrund-Name: {name!r}")
        if name in seen:
            not_found.append(name)
            prepared.append((name, None))
            continue
        seen.add(name)

        target = _find_background_file(safe_name, directory)
        if target is None:
            not_found.append(name)
            prepared.append((name, None))
            continue

        resolved = target.resolve()
        if not resolved.is_relative_to(directory):
            raise HTTPException(status.HTTP_400_BAD_REQUEST, f"Ungültiger Hintergrund-Name: {name!r}")

        prepared.append((name, resolved))

    for name, resolved in prepared:
        if resolved is None:
            continue
        resolved.unlink()
        deleted.append(name)

    if isinstance(db, Database):
        await write_application_success(
            db,
            request,
            _user,
            "DELETE",
            "/api/v1/visu/backgrounds",
            details={
                "deleted_count": len(deleted),
                "not_found_count": len(not_found),
                "requested_count": len(body.names),
            },
            commit=True,
        )
    return {"deleted": len(deleted), "names": deleted, "not_found": not_found}
