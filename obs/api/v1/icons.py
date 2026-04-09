"""
Icons Library API

GET    /icons/            — list all installed SVG icons
POST   /icons/import      — upload SVG file(s) or ZIP containing SVGs
GET    /icons/export      — export selected or all icons as ZIP
GET    /icons/{name}      — get raw SVG content of a single icon
DELETE /icons/            — delete one or multiple icons by name
POST   /icons/fontawesome — import icons from FontAwesome
"""
from __future__ import annotations

import io
import re
import zipfile
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile, status
from fastapi.responses import Response, StreamingResponse
from pydantic import BaseModel

from obs.api.auth import get_current_user
from obs.config import get_settings

router = APIRouter(tags=["icons"])

_SVG_RE = re.compile(rb"<svg[\s>]", re.IGNORECASE)

# ---------------------------------------------------------------------------
# FontAwesome 5 → FontAwesome 6 icon name aliases
# Many FA5 icons were renamed in FA6 (word order reversed for shape-based names).
# The backend tries the user-supplied name first, then falls back to these aliases.
# ---------------------------------------------------------------------------
_FA5_TO_FA6: dict[str, str] = {
    "question-circle":       "circle-question",
    "check-circle":          "circle-check",
    "times-circle":          "circle-xmark",
    "exclamation-circle":    "circle-exclamation",
    "info-circle":           "circle-info",
    "plus-circle":           "circle-plus",
    "minus-circle":          "circle-minus",
    "dot-circle":            "circle-dot",
    "play-circle":           "circle-play",
    "pause-circle":          "circle-pause",
    "stop-circle":           "circle-stop",
    "arrow-circle-left":     "circle-arrow-left",
    "arrow-circle-right":    "circle-arrow-right",
    "arrow-circle-up":       "circle-arrow-up",
    "arrow-circle-down":     "circle-arrow-down",
    "arrow-alt-circle-left":  "circle-left",
    "arrow-alt-circle-right": "circle-right",
    "arrow-alt-circle-up":    "circle-up",
    "arrow-alt-circle-down":  "circle-down",
    "cog":                   "gear",
    "cogs":                  "gears",
    "home":                  "house",
    "times":                 "xmark",
    "trash-alt":             "trash-can",
    "edit":                  "pen-to-square",
    "external-link-alt":     "arrow-up-right-from-square",
    "sign-out-alt":          "right-from-bracket",
    "sign-in-alt":           "right-to-bracket",
    "save":                  "floppy-disk",
    "search":                "magnifying-glass",
    "phone-alt":             "phone-flip",
    "calendar-alt":          "calendar-days",
    "map-marker-alt":        "location-dot",
    "thumbtack":             "thumbtack",  # unchanged — explicit for clarity
    "sort-up":               "sort-up",    # unchanged
    "sort-down":             "sort-down",  # unchanged
}


# ---------------------------------------------------------------------------
# Storage helpers
# ---------------------------------------------------------------------------

def _icons_dir() -> Path:
    """Return (and create) the directory where SVG icon files are stored."""
    settings = get_settings()
    db_path = settings.database.path
    if db_path in (":memory:", "file::memory:?cache=shared"):
        icons = Path("/tmp/obs_icons_test")
    else:
        icons = Path(db_path).parent / "icons"
    icons.mkdir(parents=True, exist_ok=True)
    return icons


def _is_svg(content: bytes) -> bool:
    """Quick check: does the first 2 KB contain an <svg tag?"""
    return bool(_SVG_RE.search(content[:2048]))


def _safe_name(filename: str) -> str | None:
    """
    Return a sanitised icon name (stem only, alphanumeric + hyphen/underscore,
    lowercase). Returns None if the name cannot be made safe.
    """
    stem = Path(filename).stem
    if not stem or ".." in stem or "/" in stem or "\\" in stem:
        return None
    clean = re.sub(r"[^\w\-]", "_", stem, flags=re.ASCII).lower().strip("_")
    return clean if clean else None


# ---------------------------------------------------------------------------
# Response models
# ---------------------------------------------------------------------------

class IconOut(BaseModel):
    name: str
    size: int
    content: str  # inline SVG UTF-8


class IconListOut(BaseModel):
    total: int
    icons: list[IconOut]


class ImportResult(BaseModel):
    imported: int
    skipped: int
    names: list[str]
    message: str


class DeleteRequest(BaseModel):
    names: list[str]


class FontAwesomeRequest(BaseModel):
    icons: list[str]             # icon names, e.g. ["home", "star"]
    style: str = "solid"         # solid | regular | brands
    api_key: str | None = None   # None → free CDN


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/", response_model=IconListOut)
async def list_icons(
    _user: str = Depends(get_current_user),
) -> IconListOut:
    """List all installed SVG icons (name, file size, inline SVG content)."""
    icons_dir = _icons_dir()
    items: list[IconOut] = []
    for svg_file in sorted(icons_dir.glob("*.svg")):
        try:
            raw = svg_file.read_bytes()
            items.append(IconOut(
                name=svg_file.stem,
                size=len(raw),
                content=raw.decode("utf-8", errors="replace"),
            ))
        except OSError:
            pass
    return IconListOut(total=len(items), icons=items)


@router.post("/import", response_model=ImportResult)
async def import_icons(
    files: list[UploadFile] = File(...),
    _user: str = Depends(get_current_user),
) -> ImportResult:
    """
    Upload one or more SVG files or a ZIP archive containing SVGs.
    Each file is validated to confirm it actually contains SVG markup,
    regardless of its file extension.
    """
    if not files:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Dateien empfangen")

    icons_dir = _icons_dir()
    imported: list[str] = []
    skipped = 0

    for upload in files:
        content = await upload.read()
        filename = upload.filename or ""
        lower = filename.lower()

        if lower.endswith(".zip") or upload.content_type in (
            "application/zip",
            "application/x-zip-compressed",
        ):
            # --- ZIP: extract and validate each member ---
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    for member in zf.namelist():
                        # Skip directories and obviously non-SVG entries
                        if member.endswith("/"):
                            continue
                        member_lower = member.lower()
                        if member_lower.endswith(".svg") or not Path(member).suffix:
                            member_bytes = zf.read(member)
                            if not _is_svg(member_bytes):
                                skipped += 1
                                continue
                            name = _safe_name(Path(member).name)
                            if not name:
                                skipped += 1
                                continue
                            (icons_dir / f"{name}.svg").write_bytes(member_bytes)
                            if name not in imported:
                                imported.append(name)
                        else:
                            skipped += 1
            except zipfile.BadZipFile:
                raise HTTPException(
                    status.HTTP_400_BAD_REQUEST,
                    f"'{filename}' ist kein gültiges ZIP-Archiv",
                )
        else:
            # --- Single file: validate as SVG ---
            if not _is_svg(content):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"'{filename}' enthält kein gültiges SVG (kein <svg> Tag gefunden)",
                )
            name = _safe_name(filename)
            if not name:
                skipped += 1
                continue
            (icons_dir / f"{name}.svg").write_bytes(content)
            if name not in imported:
                imported.append(name)

    return ImportResult(
        imported=len(imported),
        skipped=skipped,
        names=imported,
        message=(
            f"{len(imported)} Icon(s) importiert"
            + (f", {skipped} übersprungen" if skipped else "")
        ),
    )


@router.get("/export")
async def export_icons(
    names: list[str] = Query(default=[]),
    _user: str = Depends(get_current_user),
) -> StreamingResponse:
    """Export selected icons as ZIP. Exports all icons if no names are given."""
    icons_dir = _icons_dir()

    if names:
        files = [p for n in names if (p := icons_dir / f"{n}.svg").exists()]
    else:
        files = sorted(icons_dir.glob("*.svg"))

    if not files:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Keine Icons zum Exportieren gefunden",
        )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for svg_file in files:
            zf.write(svg_file, svg_file.name)
    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={"Content-Disposition": "attachment; filename=obs_icons.zip"},
    )


@router.delete("/", status_code=status.HTTP_200_OK)
async def delete_icons(
    body: DeleteRequest,
    _user: str = Depends(get_current_user),
) -> dict:
    """Delete one or multiple icons by name."""
    icons_dir = _icons_dir()
    deleted: list[str] = []
    not_found: list[str] = []
    for name in body.names:
        svg_file = icons_dir / f"{name}.svg"
        if svg_file.exists():
            svg_file.unlink()
            deleted.append(name)
        else:
            not_found.append(name)
    return {"deleted": len(deleted), "names": deleted, "not_found": not_found}


@router.get("/{name}")
async def get_icon(
    name: str,
    _user: str = Depends(get_current_user),
) -> Response:
    """Return the raw SVG content of a single icon."""
    icons_dir = _icons_dir()
    svg_file = icons_dir / f"{name}.svg"
    if not svg_file.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Icon '{name}' nicht gefunden",
        )
    return Response(content=svg_file.read_bytes(), media_type="image/svg+xml")


@router.post("/fontawesome", response_model=ImportResult)
async def import_fontawesome(
    body: FontAwesomeRequest,
    _user: str = Depends(get_current_user),
) -> ImportResult:
    """
    Import icons from FontAwesome.

    Free mode (no api_key): fetches from the public FontAwesome CDN.
    PRO mode (api_key provided): uses the FontAwesome Pro API.
    """
    if not body.icons:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Icons angegeben")

    icons_dir = _icons_dir()
    imported: list[str] = []
    skipped = 0
    valid_styles = {"solid", "regular", "brands", "light", "thin", "duotone"}
    style = body.style if body.style in valid_styles else "solid"

    async with httpx.AsyncClient(timeout=15.0) as http:
        for icon_name in body.icons:
            safe = _safe_name(icon_name)
            if not safe:
                skipped += 1
                continue

            svg_bytes: bytes | None = None

            if body.api_key:
                # --- PRO API ---
                headers = {
                    "Authorization": f"Bearer {body.api_key}",
                    "Accept": "application/json",
                }
                url = f"https://api.fontawesome.com/icons/{icon_name}"
                try:
                    resp = await http.get(url, headers=headers, params={"style": style})
                    if resp.status_code == 200:
                        data = resp.json()
                        icon_data = data.get("icon", {})
                        svg_path_data = icon_data.get("svgPathData", "")
                        viewbox = icon_data.get("viewBox", "0 0 512 512")
                        if svg_path_data:
                            svg_bytes = (
                                f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{viewbox}">'
                                f'<path d="{svg_path_data}"/></svg>'
                            ).encode()
                except Exception:
                    pass
            else:
                # --- Free CDN: unpkg (@fortawesome/fontawesome-free) ---
                style_path = {"solid": "solid", "regular": "regular", "brands": "brands"}.get(
                    style, "solid"
                )
                _CDN = "https://unpkg.com/@fortawesome/fontawesome-free@7.2.0/svgs"

                async def _fetch_fa_svg(name: str) -> bytes | None:
                    url = f"{_CDN}/{style_path}/{name}.svg"
                    try:
                        r = await http.get(url)
                        if r.status_code == 200 and _is_svg(r.content):
                            return r.content
                    except Exception:
                        pass
                    return None

                # 1st attempt: exact name as supplied
                svg_bytes = await _fetch_fa_svg(icon_name)

                # 2nd attempt: FA5 → FA6 alias fallback
                if svg_bytes is None and icon_name in _FA5_TO_FA6:
                    svg_bytes = await _fetch_fa_svg(_FA5_TO_FA6[icon_name])

            if svg_bytes and _is_svg(svg_bytes):
                (icons_dir / f"{safe}.svg").write_bytes(svg_bytes)
                imported.append(safe)
            else:
                skipped += 1

    return ImportResult(
        imported=len(imported),
        skipped=skipped,
        names=imported,
        message=(
            f"{len(imported)} FontAwesome Icon(s) importiert"
            + (f", {skipped} nicht gefunden/übersprungen" if skipped else "")
        ),
    )
