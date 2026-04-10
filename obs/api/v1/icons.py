"""
Icons Library API

GET    /icons/            — list all installed SVG icons
POST   /icons/import      — upload SVG file(s) or ZIP containing SVGs
POST   /icons/export      — export selected or all icons as ZIP (JSON body, kein URL-Limit)
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
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile, status
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

    Path-traversal characters ("..", "/", "\\") are checked on the ORIGINAL
    filename before Path().stem is extracted — so _safe_name("../evil.svg")
    returns None instead of "evil".
    """
    if not filename or ".." in filename or "/" in filename or "\\" in filename:
        return None
    stem = Path(filename).stem
    # Reject hidden files (".svg" → stem ".svg") and empty stems
    if not stem or stem.startswith("."):
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
                    status.HTTP_422_UNPROCESSABLE_CONTENT,
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


class ExportRequest(BaseModel):
    names: list[str] = []   # leer = alle exportieren


def _build_export_zip(icons_dir: Path, names: list[str]) -> io.BytesIO:
    """Erstelle einen In-Memory-ZIP aus den angegebenen Icons (leer = alle)."""
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
    return buf


@router.post("/export")
async def export_icons_post(
    body: ExportRequest,
    _user: str = Depends(get_current_user),
) -> StreamingResponse:
    """
    Export Icons als ZIP (POST-Variante, empfohlen).
    Übergibt die Namen im JSON-Body — kein URL-Längenlimit.
    Leere Namen-Liste = alle Icons exportieren.
    """
    buf = _build_export_zip(_icons_dir(), body.names)
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


_FA_GRAPHQL_URL = "https://api.fontawesome.com"
_FA_CDN = "https://unpkg.com/@fortawesome/fontawesome-free@7.2.0/svgs"


async def _fa_exchange_token(http: httpx.AsyncClient, api_key: str) -> str | None:
    """
    Tauscht einen FontAwesome API-Key gegen einen kurzlebigen Access-Token.
    POST https://api.fontawesome.com/token  (OAuth2 Bearer)
    Gibt None zurück wenn der Austausch scheitert.
    """
    try:
        resp = await http.post(
            f"{_FA_GRAPHQL_URL}/token",
            headers={"Authorization": f"Bearer {api_key}"},
        )
        if resp.status_code == 200:
            return resp.json().get("access_token")
    except Exception:
        pass
    return None


async def _fa_graphql_svg(
    http: httpx.AsyncClient,
    access_token: str,
    icon_name: str,
    style: str,
) -> bytes | None:
    """
    Ruft das fertige SVG-HTML eines Icons über die FontAwesome GraphQL API ab.
    Gibt None zurück wenn das Icon nicht gefunden wurde oder der Scope fehlt.

    Die `html`-Field liefert ein komplettes <svg>…</svg>-Element.
    Ohne ausreichende Scopes (svg_icons_free / svg_icons_pro) gibt FA null zurück.
    """
    # familyStyle: z.B. {"family": "classic", "style": "solid"}
    family_style_map = {
        "solid":   {"family": "CLASSIC", "style": "SOLID"},
        "regular": {"family": "CLASSIC", "style": "REGULAR"},
        "brands":  {"family": "BRANDS",  "style": "BRANDS"},
        "light":   {"family": "CLASSIC", "style": "LIGHT"},
        "thin":    {"family": "CLASSIC", "style": "THIN"},
        "duotone": {"family": "DUOTONE", "style": "SOLID"},
    }
    fs = family_style_map.get(style, {"family": "CLASSIC", "style": "SOLID"})

    query = """
    query GetIcon($id: String!, $family: String!, $style: String!) {
      release {
        icon(id: $id) {
          svgs(familyStyle: {family: $family, style: $style}) {
            html
          }
        }
      }
    }
    """
    try:
        resp = await http.post(
            f"{_FA_GRAPHQL_URL}/graphql",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": {"id": icon_name, **fs}},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        svgs = (
            data.get("data", {})
            .get("release", {})
            .get("icon", {})
            .get("svgs", [])
        )
        if svgs and svgs[0].get("html"):
            return svgs[0]["html"].encode()
    except Exception:
        pass
    return None


async def _fa_cdn_svg(
    http: httpx.AsyncClient,
    icon_name: str,
    style: str,
) -> bytes | None:
    """
    Lädt ein Icon vom öffentlichen unpkg-CDN (FontAwesome Free).
    Versucht automatisch den FA5→FA6-Alias wenn der erste Aufruf fehlschlägt.
    """
    style_path = {"solid": "solid", "regular": "regular", "brands": "brands"}.get(style, "solid")

    async def _fetch(name: str) -> bytes | None:
        try:
            r = await http.get(f"{_FA_CDN}/{style_path}/{name}.svg")
            if r.status_code == 200 and _is_svg(r.content):
                return r.content
        except Exception:
            pass
        return None

    svg = await _fetch(icon_name)
    if svg is None and icon_name in _FA5_TO_FA6:
        svg = await _fetch(_FA5_TO_FA6[icon_name])
    return svg


@router.post("/fontawesome", response_model=ImportResult)
async def import_fontawesome(
    body: FontAwesomeRequest,
    _user: str = Depends(get_current_user),
) -> ImportResult:
    """
    Icons von FontAwesome importieren.

    Ohne api_key: Free-CDN (unpkg, FontAwesome 7 Free).
    Mit api_key:  1. Token-Exchange gegen api.fontawesome.com/token
                  2. GraphQL-Abfrage für das Icon (PRO + Free je nach Scope)
                  3. Fallback auf Free-CDN wenn GraphQL kein Ergebnis liefert
                     (z.B. api_key hat nur svg_icons_free-Scope)
    """
    if not body.icons:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "Keine Icons angegeben")

    icons_dir = _icons_dir()
    imported: list[str] = []
    skipped = 0
    valid_styles = {"solid", "regular", "brands", "light", "thin", "duotone"}
    style = body.style if body.style in valid_styles else "solid"

    async with httpx.AsyncClient(timeout=15.0) as http:
        # PRO: einmalig Token tauschen (nicht pro Icon)
        access_token: str | None = None
        if body.api_key:
            access_token = await _fa_exchange_token(http, body.api_key)

        for icon_name in body.icons:
            safe = _safe_name(icon_name)
            if not safe:
                skipped += 1
                continue

            svg_bytes: bytes | None = None

            # 1. Versuch: GraphQL (wenn Access-Token vorhanden)
            if access_token:
                svg_bytes = await _fa_graphql_svg(http, access_token, icon_name, style)
                # FA5-Alias-Fallback für GraphQL
                if svg_bytes is None and icon_name in _FA5_TO_FA6:
                    svg_bytes = await _fa_graphql_svg(http, access_token, _FA5_TO_FA6[icon_name], style)

            # 2. Versuch: Free-CDN (immer, auch wenn api_key gesetzt aber GraphQL erfolglos)
            if svg_bytes is None:
                svg_bytes = await _fa_cdn_svg(http, icon_name, style)

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
