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
from obs.db.database import Database, get_db

router = APIRouter(tags=["icons"])

_SVG_RE = re.compile(rb"<svg[\s>]", re.IGNORECASE)


def _secure_filename(filename: str) -> str:
    """
    Minimal werkzeug-free secure_filename:
    strips path separators, keeps only alphanumeric, hyphens, underscores, dots,
    and strips leading dots/underscores. Returns '' for empty/unsafe input.
    """
    filename = filename.strip().replace("/", "_").replace("\\", "_").replace("\x00", "")
    filename = re.sub(r"[^\w.\-]", "_", filename, flags=re.ASCII)
    filename = filename.lstrip("._")
    return filename

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
    debug: list[str] = []   # temporäre Debug-Infos (Token-Exchange, GraphQL-Response)


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
    icons_dir_resolved = icons_dir.resolve()
    deleted: list[str] = []
    not_found: list[str] = []
    for name in body.names:
        if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Ungültiger Icon-Name: {name!r}",
            )
        safe_name = _secure_filename(name)
        if not safe_name or safe_name != name:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Ungültiger Icon-Name: {name!r}",
            )
        svg_file = (icons_dir / f"{safe_name}.svg").resolve()
        if not svg_file.is_relative_to(icons_dir_resolved):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"Ungültiger Icon-Name: {name!r}",
            )
        if svg_file.exists():
            svg_file.unlink()
            deleted.append(name)
        else:
            not_found.append(name)
    return {"deleted": len(deleted), "names": deleted, "not_found": not_found}


_FA_KEY_SETTING = "icons.fontawesome_api_key"


class IconsSettingsOut(BaseModel):
    fa_api_key: str | None = None       # None = kein Key gespeichert


class IconsSettingsIn(BaseModel):
    fa_api_key: str | None = None       # None / leer = Key löschen


@router.get("/settings", response_model=IconsSettingsOut)
async def get_icons_settings(
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> IconsSettingsOut:
    """Gibt die gespeicherten Icons-Einstellungen zurück (FA API Key)."""
    row = await db.fetchone(
        "SELECT value FROM app_settings WHERE key = ?", (_FA_KEY_SETTING,)
    )
    return IconsSettingsOut(fa_api_key=row["value"] if row else None)


@router.put("/settings", response_model=IconsSettingsOut)
async def update_icons_settings(
    body: IconsSettingsIn,
    _user: str = Depends(get_current_user),
    db: Database = Depends(get_db),
) -> IconsSettingsOut:
    """Speichert oder löscht den FontAwesome API Key."""
    key = (body.fa_api_key or "").strip()
    if key:
        await db.execute_and_commit(
            "INSERT OR REPLACE INTO app_settings (key, value) VALUES (?, ?)",
            (_FA_KEY_SETTING, key),
        )
        return IconsSettingsOut(fa_api_key=key)
    else:
        await db.execute_and_commit(
            "DELETE FROM app_settings WHERE key = ?", (_FA_KEY_SETTING,)
        )
        return IconsSettingsOut(fa_api_key=None)


@router.get("/{name}")
async def get_icon(
    name: str,
    _user: str = Depends(get_current_user),
) -> Response:
    """Return the raw SVG content of a single icon."""
    if not re.fullmatch(r"[A-Za-z0-9_-]+", name):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ungültiger Icon-Name",
        )

    safe_name = _secure_filename(name)
    if not safe_name or safe_name != name:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ungültiger Icon-Name",
        )

    icons_dir = _icons_dir().resolve()
    svg_file = (icons_dir / f"{safe_name}.svg").resolve()
    try:
        svg_file.relative_to(icons_dir)
    except ValueError:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "Ungültiger Icon-Pfad",
        )

    if not svg_file.exists():
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"Icon '{name}' nicht gefunden",
        )
    return Response(content=svg_file.read_bytes(), media_type="image/svg+xml")


_FA_GRAPHQL_URL = "https://api.fontawesome.com"
_FA_CDN = "https://unpkg.com/@fortawesome/fontawesome-free@7.2.0/svgs"


async def _fa_exchange_token(
    http: httpx.AsyncClient,
    api_key: str,
    dbg: list[str],
) -> str | None:
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
        # dbg.append(f"[token-exchange] HTTP {resp.status_code}: {resp.text[:500]}")
        if resp.status_code == 200:
            token = resp.json().get("access_token")
            # dbg.append(f"[token-exchange] access_token erhalten: {'ja' if token else 'NEIN (Feld fehlt)'}")
            return token
    except Exception as exc:
        # dbg.append(f"[token-exchange] Exception: {exc}")
        pass
    return None


async def _fa_get_version(
    http: httpx.AsyncClient,
    access_token: str,
    dbg: list[str],
) -> str:
    """
    Ermittelt die aktuellste FontAwesome Release-Version über die GraphQL API.
    Fallback: "7.2.0" (neueste bekannte Version).
    """
    query = "{ releases { version isLatest } }"
    try:
        resp = await http.post(
            f"{_FA_GRAPHQL_URL}/graphql",
            headers={"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"},
            json={"query": query},
        )
        # dbg.append(f"[version-discovery] HTTP {resp.status_code}: {resp.text[:400]}")
        if resp.status_code == 200:
            releases = resp.json().get("data", {}).get("releases") or []
            # isLatest == True bevorzugen, sonst neueste
            for r in releases:
                if r.get("isLatest"):
                    # dbg.append(f"[version-discovery] isLatest → {r['version']}")
                    return r["version"]
            if releases:
                v = releases[0]["version"]
                # dbg.append(f"[version-discovery] erstes Release → {v}")
                return v
    except Exception as exc:
        # dbg.append(f"[version-discovery] Exception: {exc}")
        pass
    # dbg.append("[version-discovery] Fallback → 7.2.0")
    return "7.2.0"


async def _fa_graphql_svg(
    http: httpx.AsyncClient,
    access_token: str,
    icon_name: str,
    style: str,
    version: str,
    dbg: list[str],
) -> bytes | None:
    """
    Ruft das fertige SVG-HTML eines Icons über die FontAwesome GraphQL API ab.
    Korrekte Signatur: release(version: $version) { icon(name: $name) { ... } }
    Filtert client-seitig nach Style (robuster als server-seitiger Enum-Filter).
    """
    query = """
    query GetIcon($version: String!, $name: String!) {
      release(version: $version) {
        icon(name: $name) {
          svgs {
            familyStyle {
              family
              style
            }
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
            json={"query": query, "variables": {"version": version, "name": icon_name}},
        )
        # dbg.append(f"[graphql:{icon_name}] HTTP {resp.status_code}: {resp.text[:800]}")
        if resp.status_code != 200:
            return None
        data = resp.json()
        icon_data = (
            data.get("data", {})
            .get("release", {})
            .get("icon")
        )
        if not icon_data:
            # dbg.append(f"[graphql:{icon_name}] icon=null (kein Icon unter dieser ID/Version)")
            return None
        svgs: list = icon_data.get("svgs") or []
        # dbg.append(f"[graphql:{icon_name}] {len(svgs)} SVG(s): {[s.get('familyStyle') for s in svgs]}")

        # 1. Exakter Style-Match (case-insensitive)
        target = style.lower()
        for item in svgs:
            fs = item.get("familyStyle") or {}
            if fs.get("style", "").lower() == target and item.get("html"):
                return item["html"].encode()

        # 2. Fallback: erstes verfügbares SVG des Icons
        for item in svgs:
            if item.get("html"):
                # dbg.append(f"[graphql:{icon_name}] kein '{style}' → Fallback auf {item.get('familyStyle')}")
                return item["html"].encode()

    except Exception as exc:
        # dbg.append(f"[graphql:{icon_name}] Exception: {exc}")
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
    db: Database = Depends(get_db),
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
    icons_dir_resolved = icons_dir.resolve()
    imported: list[str] = []
    skipped = 0
    valid_styles = {"solid", "regular", "brands", "light", "thin", "duotone"}
    style = body.style if body.style in valid_styles else "solid"

    dbg: list[str] = []

    # API Key: explizit übergeben > gespeichert in DB > keiner
    effective_key = (body.api_key or "").strip()
    if not effective_key:
        row = await db.fetchone(
            "SELECT value FROM app_settings WHERE key = ?", (_FA_KEY_SETTING,)
        )
        if row:
            effective_key = row["value"]
            # dbg.append(f"[config] api_key aus DB geladen (Länge {len(effective_key)})")

    async with httpx.AsyncClient(timeout=15.0) as http:
        # PRO: einmalig Token tauschen (nicht pro Icon)
        access_token: str | None = None
        fa_version: str = "7.2.0"
        if effective_key:
            # dbg.append(f"[config] api_key gesetzt (Länge {len(effective_key)}), starte Token-Exchange …")
            access_token = await _fa_exchange_token(http, effective_key, dbg)
            if access_token:
                fa_version = await _fa_get_version(http, access_token, dbg)
        else:
            pass  # dbg.append("[config] kein api_key → nur Free-CDN")

        for icon_name in body.icons:
            safe = _safe_name(icon_name)
            if not safe:
                skipped += 1
                # dbg.append(f"[icon:{icon_name}] ungültiger Name → übersprungen")
                continue

            svg_bytes: bytes | None = None

            # 1. Versuch: GraphQL (wenn Access-Token vorhanden)
            if access_token:
                svg_bytes = await _fa_graphql_svg(http, access_token, icon_name, style, fa_version, dbg)
                # FA5-Alias-Fallback für GraphQL
                if svg_bytes is None and icon_name in _FA5_TO_FA6:
                    svg_bytes = await _fa_graphql_svg(http, access_token, _FA5_TO_FA6[icon_name], style, fa_version, dbg)

            # 2. Versuch: Free-CDN (immer, auch wenn api_key gesetzt aber GraphQL erfolglos)
            if svg_bytes is None:
                cdn_result = await _fa_cdn_svg(http, icon_name, style)
                # dbg.append(f"[cdn:{icon_name}] {'gefunden' if cdn_result else 'NICHT gefunden'}")
                svg_bytes = cdn_result

            if svg_bytes and _is_svg(svg_bytes):
                # Dateiname enthält Style → kein gegenseitiges Überschreiben
                raw_filename = f"{safe}-{style}.svg"
                filename = _secure_filename(raw_filename)
                if not filename or not filename.endswith(".svg"):
                    skipped += 1
                    continue
                target_path = (icons_dir / filename).resolve()
                try:
                    target_path.relative_to(icons_dir_resolved)
                except ValueError:
                    skipped += 1
                    continue
                target_path.write_bytes(svg_bytes)
                imported.append(Path(filename).stem)  # z.B. "abacus-solid"
            else:
                skipped += 1

    return ImportResult(
        imported=len(imported),
        skipped=skipped,
        names=imported,
        debug=[],  # debug=dbg  ← Debug-Ausgabe bei Bedarf wieder aktivieren
        message=(
            f"{len(imported)} FontAwesome Icon(s) importiert"
            + (f", {skipped} nicht gefunden/übersprungen" if skipped else "")
        ),
    )
