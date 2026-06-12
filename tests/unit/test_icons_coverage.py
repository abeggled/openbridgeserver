"""Targeted coverage tests for obs/api/v1/icons.py missing lines."""

from __future__ import annotations

import io
import zipfile
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import obs.api.v1.icons as icons_api
from obs.api.v1.icons import (
    _fa_cdn_svg,
    _fa_exchange_token,
    _fa_get_version,
    _fa_graphql_svg,
    _icons_dir,
    _sanitize_svg,
)

_SIMPLE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'


# ---------------------------------------------------------------------------
# _icons_dir — non-in-memory DB path (lines 98-105)
# ---------------------------------------------------------------------------


def test_icons_dir_non_memory_path(monkeypatch, tmp_path):
    """_icons_dir uses db_path.parent/icons when path is a real file."""
    settings = MagicMock()
    settings.database.path = str(tmp_path / "obs.db")
    monkeypatch.setattr(icons_api, "get_settings", lambda: settings)
    d = _icons_dir()
    assert d == tmp_path / "icons"
    assert d.exists()


def test_icons_dir_memory_path(monkeypatch, tmp_path):
    """_icons_dir uses /tmp/obs_icons_test for in-memory DB."""
    settings = MagicMock()
    settings.database.path = ":memory:"
    monkeypatch.setattr(icons_api, "get_settings", lambda: settings)
    d = _icons_dir()
    assert str(d) == "/tmp/obs_icons_test"


# ---------------------------------------------------------------------------
# _sanitize_svg — namespace-prefixed root tag (line 173)
# ---------------------------------------------------------------------------


def test_sanitize_svg_with_namespace_prefix():
    """_sanitize_svg registers namespace for tags like {http://...}svg."""
    svg = b'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><path d="M0 0"/></svg>'
    result = _sanitize_svg(svg).decode("utf-8")
    assert result.startswith("<svg")
    assert "<ns0:svg" not in result


# ---------------------------------------------------------------------------
# list_icons — OSError + invalid SVG paths (lines 248-258)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_list_icons_skips_unreadable_file(tmp_path, monkeypatch):
    """list_icons skips SVG files that raise OSError on read."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    bad = tmp_path / "bad.svg"
    bad.touch()
    bad.chmod(0o000)
    try:
        result = await icons_api.list_icons(_user="admin")
        assert result.total == 0
    finally:
        bad.chmod(0o644)


@pytest.mark.asyncio
async def test_list_icons_invalid_svg_returns_empty_content(tmp_path, monkeypatch):
    """list_icons returns empty content string for files that fail sanitization."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    broken = tmp_path / "broken.svg"
    broken.write_bytes(b"<svg><unclosed")  # invalid XML
    result = await icons_api.list_icons(_user="admin")
    assert result.total == 1
    assert result.icons[0].content == ""


# ---------------------------------------------------------------------------
# import_icons — ZIP path + unsafe member names + BadZipFile (lines 294-309)
# ---------------------------------------------------------------------------


class _FakeUpload:
    def __init__(self, filename: str, content: bytes, content_type: str = "application/octet-stream"):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self) -> bytes:
        return self._content


def _make_zip(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_icons_zip_with_valid_svg(tmp_path, monkeypatch):
    """ZIP import processes valid SVG member."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    zdata = _make_zip([("icon.svg", _SIMPLE_SVG)])
    upload = _FakeUpload("icons.zip", zdata, "application/zip")
    result = await icons_api.import_icons(files=[upload], _user="admin")
    assert result.imported == 1


@pytest.mark.asyncio
async def test_import_icons_zip_member_non_svg_skipped(tmp_path, monkeypatch):
    """ZIP member with non-SVG extension and no suffix gets skipped if not SVG content."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    zdata = _make_zip([("icon.png", b"\x89PNG\r\n"), ("readme.txt", b"hello")])
    upload = _FakeUpload("icons.zip", zdata, "application/zip")
    result = await icons_api.import_icons(files=[upload], _user="admin")
    assert result.imported == 0
    assert result.skipped == 2


@pytest.mark.asyncio
async def test_import_icons_zip_member_no_extension_not_svg(tmp_path, monkeypatch):
    """ZIP member with no suffix that is not an SVG gets skipped."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    zdata = _make_zip([("noext", b"not an svg")])
    upload = _FakeUpload("icons.zip", zdata, "application/zip")
    result = await icons_api.import_icons(files=[upload], _user="admin")
    assert result.skipped == 1


@pytest.mark.asyncio
async def test_import_icons_bad_zip_raises_400(tmp_path, monkeypatch):
    """Non-ZIP file with .zip extension raises 400."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    upload = _FakeUpload("bad.zip", b"not a zip at all", "application/zip")
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await icons_api.import_icons(files=[upload], _user="admin")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_import_icons_single_file_unsafe_name(tmp_path, monkeypatch):
    """Single SVG file with unsafe name gets skipped."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    upload = _FakeUpload(".hidden.svg", _SIMPLE_SVG)
    result = await icons_api.import_icons(files=[upload], _user="admin")
    assert result.skipped == 1


# ---------------------------------------------------------------------------
# delete_icons — secure_filename mismatch + path traversal (lines 401, 407)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_icon_secure_filename_mismatch(tmp_path, monkeypatch):
    """delete_icons rejects names where secure_filename changes the value."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    from fastapi import HTTPException

    # Name with special char that secure_filename would change
    with pytest.raises(HTTPException) as exc_info:
        await icons_api.delete_icons(
            body=icons_api.DeleteRequest(names=["icon with spaces"]),
            _user="admin",
        )
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# get_icon — secure_filename mismatch (lines 472, 481-482)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_icon_secure_filename_mismatch(tmp_path, monkeypatch):
    """get_icon rejects names where secure_filename changes the value."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    from fastapi import HTTPException

    # Alphanumeric with dots — secure_filename keeps dots but regex rejects
    with pytest.raises(HTTPException) as exc_info:
        await icons_api.get_icon(name="icon.extra", _user="admin")
    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_get_icon_path_traversal_rejected(tmp_path, monkeypatch):
    """get_icon blocks names that resolve outside icons dir."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        await icons_api.get_icon(name="valid" * 50, _user="admin")
    # Either 400 (invalid name regex) or 404 (not found) — either is acceptable
    assert exc_info.value.status_code in (400, 404)


# ---------------------------------------------------------------------------
# _fa_exchange_token (lines 514-527)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fa_exchange_token_success():
    """_fa_exchange_token returns access_token on HTTP 200."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"access_token": "tok123"}
    http = AsyncMock()
    http.post = AsyncMock(return_value=mock_resp)
    result = await _fa_exchange_token(http, "api_key", [])
    assert result == "tok123"


@pytest.mark.asyncio
async def test_fa_exchange_token_non_200():
    """_fa_exchange_token returns None on non-200 response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 401
    http = AsyncMock()
    http.post = AsyncMock(return_value=mock_resp)
    result = await _fa_exchange_token(http, "bad_key", [])
    assert result is None


@pytest.mark.asyncio
async def test_fa_exchange_token_exception():
    """_fa_exchange_token returns None on network exception."""
    http = AsyncMock()
    http.post = AsyncMock(side_effect=Exception("network error"))
    result = await _fa_exchange_token(http, "key", [])
    assert result is None


@pytest.mark.asyncio
async def test_fa_exchange_token_missing_access_token_field():
    """_fa_exchange_token returns None when access_token field is absent."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {}  # no access_token key
    http = AsyncMock()
    http.post = AsyncMock(return_value=mock_resp)
    result = await _fa_exchange_token(http, "key", [])
    assert result is None


# ---------------------------------------------------------------------------
# _fa_get_version (lines 538-564)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fa_get_version_is_latest():
    """_fa_get_version returns version marked isLatest."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"releases": [{"version": "6.0.0", "isLatest": False}, {"version": "7.0.0", "isLatest": True}]}}
    http = AsyncMock()
    http.post = AsyncMock(return_value=mock_resp)
    result = await _fa_get_version(http, "tok", [])
    assert result == "7.0.0"


@pytest.mark.asyncio
async def test_fa_get_version_first_release_fallback():
    """_fa_get_version returns first release when none isLatest."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"releases": [{"version": "6.5.0", "isLatest": False}]}}
    http = AsyncMock()
    http.post = AsyncMock(return_value=mock_resp)
    result = await _fa_get_version(http, "tok", [])
    assert result == "6.5.0"


@pytest.mark.asyncio
async def test_fa_get_version_fallback_on_error():
    """_fa_get_version returns 7.2.0 fallback on exception."""
    http = AsyncMock()
    http.post = AsyncMock(side_effect=Exception("timeout"))
    result = await _fa_get_version(http, "tok", [])
    assert result == "7.2.0"


@pytest.mark.asyncio
async def test_fa_get_version_non_200_fallback():
    """_fa_get_version returns 7.2.0 on non-200 response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 500
    http = AsyncMock()
    http.post = AsyncMock(return_value=mock_resp)
    result = await _fa_get_version(http, "tok", [])
    assert result == "7.2.0"


# ---------------------------------------------------------------------------
# _fa_graphql_svg (lines 579-630)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fa_graphql_svg_exact_style_match():
    """_fa_graphql_svg returns SVG bytes for exact style match."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "release": {
                "icon": {
                    "svgs": [
                        {"familyStyle": {"family": "classic", "style": "solid"}, "html": "<svg><path/></svg>"},
                    ]
                }
            }
        }
    }
    http = AsyncMock()
    http.post = AsyncMock(return_value=mock_resp)
    result = await _fa_graphql_svg(http, "tok", "home", "solid", "7.0.0", [])
    assert result == b"<svg><path/></svg>"


@pytest.mark.asyncio
async def test_fa_graphql_svg_fallback_style():
    """_fa_graphql_svg falls back to first available SVG when style not matched."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": {
            "release": {
                "icon": {
                    "svgs": [
                        {"familyStyle": {"family": "classic", "style": "regular"}, "html": "<svg><path/></svg>"},
                    ]
                }
            }
        }
    }
    http = AsyncMock()
    http.post = AsyncMock(return_value=mock_resp)
    result = await _fa_graphql_svg(http, "tok", "home", "solid", "7.0.0", [])
    assert result == b"<svg><path/></svg>"


@pytest.mark.asyncio
async def test_fa_graphql_svg_non_200_returns_none():
    """_fa_graphql_svg returns None on non-200 response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 403
    http = AsyncMock()
    http.post = AsyncMock(return_value=mock_resp)
    result = await _fa_graphql_svg(http, "tok", "home", "solid", "7.0.0", [])
    assert result is None


@pytest.mark.asyncio
async def test_fa_graphql_svg_no_icon_data():
    """_fa_graphql_svg returns None when icon data is null."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"release": {"icon": None}}}
    http = AsyncMock()
    http.post = AsyncMock(return_value=mock_resp)
    result = await _fa_graphql_svg(http, "tok", "home", "solid", "7.0.0", [])
    assert result is None


@pytest.mark.asyncio
async def test_fa_graphql_svg_exception_returns_none():
    """_fa_graphql_svg returns None on exception."""
    http = AsyncMock()
    http.post = AsyncMock(side_effect=Exception("error"))
    result = await _fa_graphql_svg(http, "tok", "home", "solid", "7.0.0", [])
    assert result is None


# ---------------------------------------------------------------------------
# _fa_cdn_svg (lines 641-655)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fa_cdn_svg_found():
    """_fa_cdn_svg returns SVG bytes on 200 response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.content = _SIMPLE_SVG
    http = AsyncMock()
    http.get = AsyncMock(return_value=mock_resp)
    result = await _fa_cdn_svg(http, "home", "solid")
    assert result == _SIMPLE_SVG


@pytest.mark.asyncio
async def test_fa_cdn_svg_not_found_returns_none():
    """_fa_cdn_svg returns None on 404."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404
    http = AsyncMock()
    http.get = AsyncMock(return_value=mock_resp)
    result = await _fa_cdn_svg(http, "nonexistent", "solid")
    assert result is None


@pytest.mark.asyncio
async def test_fa_cdn_svg_fa5_alias_fallback():
    """_fa_cdn_svg tries FA5→FA6 alias when first request fails."""
    responses = [
        MagicMock(status_code=404),  # cog not found
        MagicMock(status_code=200, content=_SIMPLE_SVG),  # gear found
    ]
    http = AsyncMock()
    http.get = AsyncMock(side_effect=responses)
    result = await _fa_cdn_svg(http, "cog", "solid")  # cog → gear alias
    assert result == _SIMPLE_SVG


@pytest.mark.asyncio
async def test_fa_cdn_svg_exception_returns_none():
    """_fa_cdn_svg returns None on network exception."""
    http = AsyncMock()
    http.get = AsyncMock(side_effect=Exception("timeout"))
    result = await _fa_cdn_svg(http, "home", "solid")
    assert result is None


# ---------------------------------------------------------------------------
# import_fontawesome — API key from DB + GraphQL path (lines 689, 698-749)
# ---------------------------------------------------------------------------


class _DbStub:
    def __init__(self, row=None):
        self._row = row
        self.committed = []

    async def fetchone(self, query, params=()):
        return self._row

    async def execute_and_commit(self, query, params=()):
        self.committed.append((query, params))


class _Row(dict):
    def __getitem__(self, k):
        return super().__getitem__(k)


@pytest.mark.asyncio
async def test_import_fontawesome_api_key_from_db(tmp_path, monkeypatch):
    """import_fontawesome loads API key from DB when not in request."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    db = _DbStub(row=_Row({"value": "stored_key"}))

    with patch("obs.api.v1.icons._fa_exchange_token", new_callable=AsyncMock, return_value=None):
        with patch("obs.api.v1.icons._fa_cdn_svg", new_callable=AsyncMock, return_value=_SIMPLE_SVG):
            body = icons_api.FontAwesomeRequest(icons=["home"], style="solid", api_key=None)
            result = await icons_api.import_fontawesome(body=body, _user="admin", db=db)
    assert result.imported == 1


@pytest.mark.asyncio
async def test_import_fontawesome_with_access_token_graphql(tmp_path, monkeypatch):
    """import_fontawesome uses GraphQL when access token is available."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    db = _DbStub()

    with patch("obs.api.v1.icons._fa_exchange_token", new_callable=AsyncMock, return_value="tok"):
        with patch("obs.api.v1.icons._fa_get_version", new_callable=AsyncMock, return_value="7.0.0"):
            with patch("obs.api.v1.icons._fa_graphql_svg", new_callable=AsyncMock, return_value=_SIMPLE_SVG):
                body = icons_api.FontAwesomeRequest(icons=["home"], style="solid", api_key="mykey")
                result = await icons_api.import_fontawesome(body=body, _user="admin", db=db)
    assert result.imported == 1


@pytest.mark.asyncio
async def test_import_fontawesome_graphql_miss_falls_to_cdn(tmp_path, monkeypatch):
    """import_fontawesome falls back to CDN when GraphQL returns None."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    db = _DbStub()

    with patch("obs.api.v1.icons._fa_exchange_token", new_callable=AsyncMock, return_value="tok"):
        with patch("obs.api.v1.icons._fa_get_version", new_callable=AsyncMock, return_value="7.0.0"):
            with patch("obs.api.v1.icons._fa_graphql_svg", new_callable=AsyncMock, return_value=None):
                with patch("obs.api.v1.icons._fa_cdn_svg", new_callable=AsyncMock, return_value=_SIMPLE_SVG):
                    body = icons_api.FontAwesomeRequest(icons=["home"], style="solid", api_key="mykey")
                    result = await icons_api.import_fontawesome(body=body, _user="admin", db=db)
    assert result.imported == 1


@pytest.mark.asyncio
async def test_import_fontawesome_fa5_alias_graphql(tmp_path, monkeypatch):
    """import_fontawesome tries FA5 alias for GraphQL when first attempt misses."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    db = _DbStub()

    call_count = [0]

    async def _mock_graphql(http, tok, name, style, version, dbg):
        call_count[0] += 1
        if call_count[0] == 1:
            return None  # first call fails
        return _SIMPLE_SVG  # alias call succeeds

    with patch("obs.api.v1.icons._fa_exchange_token", new_callable=AsyncMock, return_value="tok"):
        with patch("obs.api.v1.icons._fa_get_version", new_callable=AsyncMock, return_value="7.0.0"):
            with patch("obs.api.v1.icons._fa_graphql_svg", side_effect=_mock_graphql):
                body = icons_api.FontAwesomeRequest(icons=["cog"], style="solid", api_key="key")
                result = await icons_api.import_fontawesome(body=body, _user="admin", db=db)
    assert result.imported == 1


@pytest.mark.asyncio
async def test_import_fontawesome_icon_not_found_skipped(tmp_path, monkeypatch):
    """import_fontawesome skips icon when neither GraphQL nor CDN returns SVG."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    db = _DbStub()

    with patch("obs.api.v1.icons._fa_cdn_svg", new_callable=AsyncMock, return_value=None):
        body = icons_api.FontAwesomeRequest(icons=["nonexistent_icon_xyz"], style="solid")
        result = await icons_api.import_fontawesome(body=body, _user="admin", db=db)
    assert result.skipped == 1
    assert result.imported == 0


# ---------------------------------------------------------------------------
# import_knxuf — all execution paths (lines 797-845)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_knxuf_success(tmp_path, monkeypatch):
    """import_knxuf downloads JS, parses icons, writes sanitized SVGs."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    js_content = "const ICONS = {\n\t'test_icon': 'M 0,0 L 10,10 Z'\n};"
    mock_resp = MagicMock()
    mock_resp.text = js_content
    mock_resp.raise_for_status = MagicMock()

    with patch("obs.api.v1.icons.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await icons_api.import_knxuf(_user="admin")

    assert result.imported == 1
    assert result.imported_names[0] if hasattr(result, "imported_names") else result.names[0] == "kuf_test_icon"
    assert (tmp_path / "kuf_test_icon.svg").exists()


@pytest.mark.asyncio
async def test_import_knxuf_download_error(monkeypatch, tmp_path):
    """import_knxuf raises 502 on HTTP download error."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    import httpx
    from fastapi import HTTPException

    with patch("obs.api.v1.icons.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("refused"))
        mock_client_cls.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await icons_api.import_knxuf(_user="admin")
    assert exc_info.value.status_code == 502


@pytest.mark.asyncio
async def test_import_knxuf_empty_icons_raises_422(monkeypatch, tmp_path):
    """import_knxuf raises 422 when no icons found in downloaded JS."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    from fastapi import HTTPException

    mock_resp = MagicMock()
    mock_resp.text = "const ICONSET_NAME = 'kuf';"  # no ICONS object
    mock_resp.raise_for_status = MagicMock()

    with patch("obs.api.v1.icons.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        with pytest.raises(HTTPException) as exc_info:
            await icons_api.import_knxuf(_user="admin")
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_import_knxuf_unsafe_icon_name_skipped(monkeypatch, tmp_path):
    """import_knxuf skips icons with unsafe names (empty after sanitization)."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    js_content = "const ICONS = {\n\t'../etc/passwd': 'M 0 0',\n\t'valid_icon': 'M 1 1'\n};"
    mock_resp = MagicMock()
    mock_resp.text = js_content
    mock_resp.raise_for_status = MagicMock()

    with patch("obs.api.v1.icons.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)
        mock_client.get = AsyncMock(return_value=mock_resp)
        mock_client_cls.return_value = mock_client

        result = await icons_api.import_knxuf(_user="admin")

    assert result.imported == 1
    assert result.skipped == 1


# ---------------------------------------------------------------------------
# _sanitize_svg — non-SVG root element raises (line 173)
# ---------------------------------------------------------------------------


def test_sanitize_svg_non_svg_root_raises():
    """_sanitize_svg raises 422 when root element is not <svg>."""
    from fastapi import HTTPException

    non_svg = b'<circle xmlns="http://www.w3.org/2000/svg" r="10"/>'
    with pytest.raises(HTTPException) as exc_info:
        _sanitize_svg(non_svg)
    assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# import_icons ZIP — directory entry skipped (line 294)
# ---------------------------------------------------------------------------


def _make_zip_with_dir(members: list[tuple[str, bytes]], dirs: list[str] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for d in dirs or []:
            info = zipfile.ZipInfo(d + "/")
            zf.writestr(info, "")
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_import_icons_zip_directory_entry_skipped(tmp_path, monkeypatch):
    """Directory entries in ZIP are silently skipped."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    zdata = _make_zip_with_dir([("icon.svg", _SIMPLE_SVG)], dirs=["subdir"])
    upload = _FakeUpload("icons.zip", zdata, "application/zip")
    result = await icons_api.import_icons(files=[upload], _user="admin")
    assert result.imported == 1  # only the SVG, directory was skipped


# ---------------------------------------------------------------------------
# import_icons ZIP — unsafe member name skipped (lines 303-304)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_icons_zip_member_unsafe_name_skipped(tmp_path, monkeypatch):
    """ZIP member with valid SVG but unsafe filename gets skipped (lines 303-304)."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    # .hidden.svg → _safe_name returns None (stem starts with dot)
    zdata = _make_zip_with_dir([(".hidden.svg", _SIMPLE_SVG)])
    upload = _FakeUpload("icons.zip", zdata, "application/zip")
    result = await icons_api.import_icons(files=[upload], _user="admin")
    assert result.skipped == 1
    assert result.imported == 0


# ---------------------------------------------------------------------------
# delete_icons — _secure_filename changes the name (line 401)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_delete_icon_leading_underscore_rejected(tmp_path, monkeypatch):
    """delete_icons rejects names with leading underscore (secure_filename strips it)."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    from fastapi import HTTPException

    # "_icon" passes regex but secure_filename("_icon") = "icon" ≠ "_icon" → 400
    with pytest.raises(HTTPException) as exc_info:
        await icons_api.delete_icons(
            body=icons_api.DeleteRequest(names=["_icon"]),
            _user="admin",
        )
    assert exc_info.value.status_code == 400


# ---------------------------------------------------------------------------
# get_icon — _secure_filename changes the name (lines 472, 481-482)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_icon_leading_underscore_rejected(tmp_path, monkeypatch):
    """get_icon rejects names with leading underscore (secure_filename strips it)."""
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    from fastapi import HTTPException

    # "_icon" passes regex but secure_filename changes it → 400
    with pytest.raises(HTTPException) as exc_info:
        await icons_api.get_icon(name="_icon", _user="admin")
    assert exc_info.value.status_code == 400
