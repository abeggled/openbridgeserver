"""Unit tests for visu_backgrounds, camera, visu, and knxproj API modules.

Tests cover helper functions and pure logic without requiring a running FastAPI
app or database connection. FastAPI endpoints are tested by invoking the handler
functions directly after patching their dependencies.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from obs.security.url_targets import UrlTargetDecision


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeCursor:
    """Minimal async cursor stub."""

    def __init__(self, row=None, rows=None):
        self._row = row
        self._rows = rows or []

    async def fetchone(self):
        return self._row

    async def fetchall(self):
        return list(self._rows)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        pass


def _make_row(**kwargs):
    """Return a dict-like object that also supports attribute and key access."""
    return SimpleNamespace(**kwargs) if False else _Row(kwargs)


def _url_decision(
    *,
    allowed: bool,
    url: str = "http://example.test/",
    host: str = "example.test",
    resolved_ips: list[str] | None = None,
    blocked_ips: list[str] | None = None,
    reason: str = "test decision",
    allowlisted_by: str | None = None,
) -> UrlTargetDecision:
    return UrlTargetDecision(
        allowed=allowed,
        url=url,
        host=host,
        resolved_ips=resolved_ips or [],
        blocked_ips=blocked_ips or [],
        reason=reason,
        allowlisted_by=allowlisted_by,
        suggested_target=(blocked_ips or [None])[0],
    )


class _Row(dict):
    """dict subclass that also allows attribute access."""

    def __getattr__(self, item):
        try:
            return self[item]
        except KeyError:
            raise AttributeError(item)


def _make_db_conn(row=None, rows=None):
    """Return a mock aiosqlite connection whose execute() returns a _FakeCursor."""
    conn = MagicMock()
    cursor = _FakeCursor(row=row, rows=rows)
    conn.execute = MagicMock(return_value=cursor)
    conn.commit = AsyncMock()
    conn.executemany = AsyncMock()
    return conn


def _make_db(fetchone_result=None, fetchall_result=None):
    """Return a mock Database with async helpers."""
    db = MagicMock()
    db.fetchone = AsyncMock(return_value=fetchone_result)
    db.fetchall = AsyncMock(return_value=fetchall_result or [])
    db.execute_and_commit = AsyncMock()
    db.executemany = AsyncMock()
    db.commit = AsyncMock()
    db.rollback = AsyncMock()
    db.conn = _make_db_conn()
    return db


# ===========================================================================
# visu_backgrounds — helper functions
# ===========================================================================


from obs.api.v1.visu_backgrounds import (
    _detect_image_type,
    _find_background_file,
    _guess_mime_type,
    _safe_stem,
    _secure_filename,
)


class TestSecureFilename:
    def test_simple_name(self):
        assert _secure_filename("floor.png") == "floor.png"

    def test_strips_leading_dots(self):
        result = _secure_filename(".hidden.png")
        assert not result.startswith(".")

    def test_replaces_slashes(self):
        result = _secure_filename("path/to/file.png")
        assert "/" not in result

    def test_replaces_backslashes(self):
        result = _secure_filename("path\\to\\file.png")
        assert "\\" not in result

    def test_strips_null_bytes(self):
        result = _secure_filename("file\x00.png")
        assert "\x00" not in result

    def test_non_ascii_replaced(self):
        # Non-ASCII chars (beyond \w scope with ASCII flag) should become _
        result = _secure_filename("Küche.png")
        assert result  # should not be empty


class TestSafeStem:
    def test_normal_filename(self):
        assert _safe_stem("floor.png") == "floor"

    def test_empty_string(self):
        assert _safe_stem("") is None

    def test_dotdot_rejected(self):
        assert _safe_stem("../evil.png") is None

    def test_slash_rejected(self):
        assert _safe_stem("a/b.png") is None

    def test_backslash_rejected(self):
        assert _safe_stem("a\\b.png") is None

    def test_dot_stem_rejected(self):
        # A file named ".png" has stem "" which should return None
        assert _safe_stem(".png") is None

    def test_uppercase_lowercased(self):
        result = _safe_stem("Floor.PNG")
        assert result == result.lower()

    def test_special_chars_replaced(self):
        result = _safe_stem("my-image.png")
        assert result is not None
        assert "-" in result or "_" in result  # hyphens allowed

    def test_leading_underscore_stripped(self):
        result = _safe_stem("_image.png")
        # leading underscores are stripped by the regex strip
        assert result is not None and not result.startswith("_")

    def test_returns_none_for_all_special(self):
        # "!!!" becomes "___" then strip("_") → ""
        result = _safe_stem("!!!.png")
        assert result is None


class TestDetectImageType:
    def test_png(self):
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
        assert _detect_image_type(content) == ("png", "image/png")

    def test_jpg(self):
        content = b"\xff\xd8\xff" + b"\x00" * 100
        assert _detect_image_type(content) == ("jpg", "image/jpeg")

    def test_webp(self):
        content = b"RIFF\x00\x00\x00\x00WEBP" + b"\x00" * 100
        assert _detect_image_type(content) == ("webp", "image/webp")

    def test_unknown(self):
        content = b"GIF89a" + b"\x00" * 100
        assert _detect_image_type(content) is None

    def test_empty(self):
        assert _detect_image_type(b"") is None

    def test_too_short_for_webp(self):
        # Less than 12 bytes → no WEBP detection
        assert _detect_image_type(b"RIFF") is None


class TestGuessMimeType:
    def test_png(self, tmp_path):
        p = tmp_path / "image.png"
        assert _guess_mime_type(p) == "image/png"

    def test_jpg(self, tmp_path):
        p = tmp_path / "image.jpg"
        assert _guess_mime_type(p) == "image/jpeg"

    def test_jpeg(self, tmp_path):
        p = tmp_path / "image.jpeg"
        assert _guess_mime_type(p) == "image/jpeg"

    def test_webp(self, tmp_path):
        p = tmp_path / "image.webp"
        assert _guess_mime_type(p) == "image/webp"

    def test_unknown_extension(self, tmp_path):
        p = tmp_path / "image.bmp"
        assert _guess_mime_type(p) == "application/octet-stream"


class TestFindBackgroundFile:
    def test_finds_png(self, tmp_path):
        (tmp_path / "floor.png").write_bytes(b"data")
        result = _find_background_file("floor", tmp_path)
        assert result is not None
        assert result.name == "floor.png"

    def test_finds_jpg(self, tmp_path):
        (tmp_path / "floor.jpg").write_bytes(b"data")
        result = _find_background_file("floor", tmp_path)
        assert result is not None

    def test_not_found(self, tmp_path):
        assert _find_background_file("nonexistent", tmp_path) is None


# ===========================================================================
# visu_backgrounds — endpoint tests (via direct function call)
# ===========================================================================


from obs.api.v1.visu_backgrounds import delete_backgrounds, get_background, list_backgrounds


class TestListBackgrounds:
    @pytest.mark.asyncio
    async def test_empty_directory(self, tmp_path):
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            result = await list_backgrounds(_user="testuser")
        assert result.total == 0
        assert result.backgrounds == []

    @pytest.mark.asyncio
    async def test_lists_image_files(self, tmp_path):
        (tmp_path / "floor.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        (tmp_path / "notes.txt").write_bytes(b"text")  # should be ignored
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            result = await list_backgrounds(_user="testuser")
        assert result.total == 1
        assert result.backgrounds[0].name == "floor"

    @pytest.mark.asyncio
    async def test_url_format(self, tmp_path):
        (tmp_path / "ground.jpg").write_bytes(b"\xff\xd8\xff")
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            result = await list_backgrounds(_user="testuser")
        assert result.backgrounds[0].url == "/api/v1/visu/backgrounds/ground"


class TestGetBackground:
    @pytest.mark.asyncio
    async def test_invalid_name_raises_400(self):
        with pytest.raises(HTTPException) as exc:
            await get_background("../evil")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_not_found_raises_404(self, tmp_path):
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            with pytest.raises(HTTPException) as exc:
                await get_background("nonexistent")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_image_bytes(self, tmp_path):
        content = b"\x89PNG\r\n\x1a\n" + b"\x00" * 20
        (tmp_path / "floor.png").write_bytes(content)
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            response = await get_background("floor")
        assert response.body == content
        assert response.media_type == "image/png"


class TestDeleteBackgrounds:
    @pytest.mark.asyncio
    async def test_delete_existing(self, tmp_path):
        (tmp_path / "floor.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        from obs.api.v1.visu_backgrounds import DeleteRequest

        body = DeleteRequest(names=["floor"])
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            result = await delete_backgrounds(body=body, _user="admin")
        assert result["deleted"] == 1
        assert "floor" in result["names"]

    @pytest.mark.asyncio
    async def test_not_found_goes_to_not_found_list(self, tmp_path):
        from obs.api.v1.visu_backgrounds import DeleteRequest

        body = DeleteRequest(names=["nonexistent"])
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            result = await delete_backgrounds(body=body, _user="admin")
        assert result["deleted"] == 0
        assert "nonexistent" in result["not_found"]

    @pytest.mark.asyncio
    async def test_invalid_name_raises_400(self, tmp_path):
        from obs.api.v1.visu_backgrounds import DeleteRequest

        body = DeleteRequest(names=["../evil"])
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            with pytest.raises(HTTPException) as exc:
                await delete_backgrounds(body=body, _user="admin")
        assert exc.value.status_code == 400


# ===========================================================================
# visu_backgrounds — import_backgrounds endpoint
# ===========================================================================


from obs.api.v1.visu_backgrounds import import_backgrounds


class TestImportBackgrounds:
    @pytest.mark.asyncio
    async def test_no_files_raises_400(self):
        with pytest.raises(HTTPException) as exc:
            await import_backgrounds(files=[], _user="admin")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_invalid_image_content_raises_422(self, tmp_path):
        upload = AsyncMock()
        upload.filename = "test.png"
        upload.read = AsyncMock(return_value=b"not an image")
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            with pytest.raises(HTTPException) as exc:
                await import_backgrounds(files=[upload], _user="admin")
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_valid_png_import(self, tmp_path):
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        upload = AsyncMock()
        upload.filename = "floor.png"
        upload.read = AsyncMock(return_value=png_bytes)
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            result = await import_backgrounds(files=[upload], _user="admin")
        assert result.imported == 1
        assert "floor" in result.names

    @pytest.mark.asyncio
    async def test_invalid_stem_skipped(self, tmp_path):
        # Filename with no valid stem (e.g. only special chars)
        png_bytes = b"\x89PNG\r\n\x1a\n" + b"\x00" * 50
        upload = AsyncMock()
        upload.filename = "!!.png"  # _safe_stem returns None
        upload.read = AsyncMock(return_value=png_bytes)
        with patch("obs.api.v1.visu_backgrounds._backgrounds_dir", return_value=tmp_path):
            result = await import_backgrounds(files=[upload], _user="admin")
        assert result.skipped == 1
        assert result.imported == 0


# ===========================================================================
# camera — _check_ssrf
# ===========================================================================


from obs.api.v1.camera import _build_fetch_targets as _check_ssrf


class TestCheckSsrf:
    @pytest.mark.asyncio
    async def test_no_hostname_raises_400(self):
        with pytest.raises(HTTPException) as exc:
            await _check_ssrf("not-a-url")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_loopback_blocked(self):
        with patch(
            "obs.api.v1.camera.build_pinned_url_targets",
            side_effect=ValueError("Blocked URL target"),
        ):
            with pytest.raises(HTTPException) as exc:
                await _check_ssrf("http://localhost/stream")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_metadata_blocked(self):
        # 169.254.169.254 is the cloud metadata address
        with patch(
            "obs.api.v1.camera.build_pinned_url_targets",
            side_effect=ValueError("Blocked URL target"),
        ):
            with pytest.raises(HTTPException) as exc:
                await _check_ssrf("http://metadata.internal/")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_private_network_allowed(self):
        # Private 192.168.x.x cameras are allowed only after an explicit allowlist decision.
        with patch(
            "obs.api.v1.camera.build_pinned_url_targets",
            return_value=(["http://192.168.1.100/stream"], {}, {}),
        ):
            # Should not raise
            await _check_ssrf("http://192.168.1.100/stream")

    @pytest.mark.asyncio
    async def test_dns_failure_raises_502(self):
        with patch(
            "obs.api.v1.camera.build_pinned_url_targets",
            side_effect=ValueError("Hostname could not be resolved: no address"),
        ):
            with pytest.raises(HTTPException) as exc:
                await _check_ssrf("http://nonexistent.local/stream")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_ipv6_loopback_blocked(self):
        with patch(
            "obs.api.v1.camera.build_pinned_url_targets",
            side_effect=ValueError("Blocked URL target"),
        ):
            with pytest.raises(HTTPException) as exc:
                await _check_ssrf("http://[::1]/stream")
        assert exc.value.status_code == 400


# ===========================================================================
# camera — _camera_auth
# ===========================================================================


from obs.api.v1.camera import _camera_auth


class TestCameraAuth:
    @pytest.mark.asyncio
    async def test_missing_auth_raises_401(self):
        request = MagicMock()
        request.headers = {"Authorization": ""}
        with pytest.raises(HTTPException) as exc:
            await _camera_auth(request=request, _token="")
        assert exc.value.status_code == 401

    @pytest.mark.asyncio
    async def test_bearer_header_accepted(self):
        request = MagicMock()
        request.headers = {"Authorization": "Bearer mytoken"}
        with patch("obs.api.v1.camera.decode_token", return_value="testuser"):
            result = await _camera_auth(request=request, _token="")
        assert result == "testuser"

    @pytest.mark.asyncio
    async def test_query_token_accepted(self):
        request = MagicMock()
        request.headers = {}
        with patch("obs.api.v1.camera.decode_token", return_value="testuser"):
            result = await _camera_auth(request=request, _token="mytoken")
        assert result == "testuser"


# ===========================================================================
# visu — helper functions
# ===========================================================================


from obs.api.v1.visu import _now_iso, _resolve_access, _resolve_access_with_node, _row_to_node


class TestNowIso:
    def test_returns_string(self):
        result = _now_iso()
        assert isinstance(result, str)
        assert "T" in result  # ISO format marker

    def test_includes_timezone(self):
        result = _now_iso()
        # Should contain +00:00 or Z suffix for UTC
        assert "+" in result or result.endswith("Z")


class TestRowToNode:
    def _make_row(self, **overrides):
        defaults = {
            "id": "node-1",
            "parent_id": None,
            "name": "Home",
            "type": "PAGE",
            "node_order": 0,
            "icon": None,
            "access": None,
            "page_config": None,
            "created_at": "2024-01-01T00:00:00+00:00",
            "updated_at": "2024-01-01T00:00:00+00:00",
        }
        defaults.update(overrides)
        return _Row(defaults)

    def test_basic_row(self):
        row = self._make_row()
        node = _row_to_node(row)
        assert node.id == "node-1"
        assert node.name == "Home"
        assert node.page_config is None

    def test_access_pin_never_returned(self):
        row = self._make_row()
        node = _row_to_node(row)
        assert node.access_pin is None

    def test_page_config_parsed(self):
        pc = json.dumps({"grid_cols": 12, "grid_row_height": 80, "background": None, "widgets": []})
        row = self._make_row(page_config=pc)
        node = _row_to_node(row)
        assert node.page_config is not None
        assert node.page_config.grid_cols == 12

    def test_page_config_none_when_empty(self):
        row = self._make_row(page_config=None)
        node = _row_to_node(row)
        assert node.page_config is None


class TestResolveAccess:
    @pytest.mark.asyncio
    async def test_public_fallback_when_no_rows(self):
        db = MagicMock()
        cursor = _FakeCursor(row=None)
        db.conn.execute = MagicMock(return_value=cursor)
        result = await _resolve_access(db, "node-1")
        assert result == "public"

    @pytest.mark.asyncio
    async def test_returns_explicit_access(self):
        row = _Row({"access": "readonly", "parent_id": None})
        db = MagicMock()
        cursor = _FakeCursor(row=row)
        db.conn.execute = MagicMock(return_value=cursor)
        result = await _resolve_access(db, "node-1")
        assert result == "readonly"

    @pytest.mark.asyncio
    async def test_traverses_parents(self):
        # Child has access=None, parent has access="user"
        child_row = _Row({"access": None, "parent_id": "parent-1"})
        parent_row = _Row({"access": "user", "parent_id": None})

        cursors = iter([_FakeCursor(row=child_row), _FakeCursor(row=parent_row)])

        def mock_execute(query, params=()):
            return next(cursors)

        db = MagicMock()
        db.conn.execute = MagicMock(side_effect=mock_execute)
        result = await _resolve_access(db, "child-node")
        assert result == "user"


class TestResolveAccessWithNode:
    @pytest.mark.asyncio
    async def test_returns_public_none_when_no_rows(self):
        db = MagicMock()
        cursor = _FakeCursor(row=None)
        db.conn.execute = MagicMock(return_value=cursor)
        access, node_id = await _resolve_access_with_node(db, "node-1")
        assert access == "public"
        assert node_id is None

    @pytest.mark.asyncio
    async def test_returns_defining_node_id(self):
        row = _Row({"access": "protected", "parent_id": None})
        db = MagicMock()
        cursor = _FakeCursor(row=row)
        db.conn.execute = MagicMock(return_value=cursor)
        access, defining_id = await _resolve_access_with_node(db, "node-1")
        assert access == "protected"
        assert defining_id == "node-1"


# ===========================================================================
# visu — endpoint: _get_node_or_404
# ===========================================================================


from obs.api.v1.visu import _get_node_or_404


class TestGetNodeOr404:
    @pytest.mark.asyncio
    async def test_raises_404_when_not_found(self):
        db = MagicMock()
        cursor = _FakeCursor(row=None)
        db.conn.execute = MagicMock(return_value=cursor)
        with pytest.raises(HTTPException) as exc:
            await _get_node_or_404(db, "missing-id")
        assert exc.value.status_code == 404

    @pytest.mark.asyncio
    async def test_returns_node_when_found(self):
        row = _Row(
            {
                "id": "node-1",
                "parent_id": None,
                "name": "Root",
                "type": "LOCATION",
                "node_order": 0,
                "icon": None,
                "access": None,
                "page_config": None,
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
            }
        )
        db = MagicMock()
        cursor = _FakeCursor(row=row)
        db.conn.execute = MagicMock(return_value=cursor)
        node = await _get_node_or_404(db, "node-1")
        assert node.id == "node-1"
        assert node.name == "Root"


# ===========================================================================
# visu — endpoint: get_tree
# ===========================================================================


from obs.api.v1.visu import get_tree


class TestGetTree:
    @pytest.mark.asyncio
    async def test_empty_tree(self):
        db = MagicMock()
        cursor = _FakeCursor(rows=[])
        db.conn.execute = MagicMock(return_value=cursor)
        result = await get_tree(db=db)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_all_nodes(self):
        rows = [
            _Row(
                {
                    "id": f"node-{i}",
                    "parent_id": None,
                    "name": f"Node {i}",
                    "type": "PAGE",
                    "node_order": i,
                    "icon": None,
                    "access": None,
                    "page_config": None,
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                }
            )
            for i in range(3)
        ]
        db = MagicMock()
        cursor = _FakeCursor(rows=rows)
        db.conn.execute = MagicMock(return_value=cursor)
        result = await get_tree(db=db)
        assert len(result) == 3


# ===========================================================================
# visu — endpoint: get_children / get_breadcrumb
# ===========================================================================


from obs.api.v1.visu import get_breadcrumb, get_children


class TestGetChildren:
    @pytest.mark.asyncio
    async def test_empty_children(self):
        db = MagicMock()
        cursor = _FakeCursor(rows=[])
        db.conn.execute = MagicMock(return_value=cursor)
        result = await get_children(node_id="node-1", db=db)
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_children(self):
        rows = [
            _Row(
                {
                    "id": "child-1",
                    "parent_id": "parent-1",
                    "name": "Child",
                    "type": "PAGE",
                    "node_order": 0,
                    "icon": None,
                    "access": None,
                    "page_config": None,
                    "created_at": "2024-01-01T00:00:00+00:00",
                    "updated_at": "2024-01-01T00:00:00+00:00",
                }
            )
        ]
        db = MagicMock()
        cursor = _FakeCursor(rows=rows)
        db.conn.execute = MagicMock(return_value=cursor)
        result = await get_children(node_id="parent-1", db=db)
        assert len(result) == 1
        assert result[0].id == "child-1"


class TestGetBreadcrumb:
    @pytest.mark.asyncio
    async def test_empty_when_node_missing(self):
        db = MagicMock()
        cursor = _FakeCursor(row=None)
        db.conn.execute = MagicMock(return_value=cursor)
        result = await get_breadcrumb(node_id="missing", db=db)
        assert result == []

    @pytest.mark.asyncio
    async def test_single_node_breadcrumb(self):
        row = _Row(
            {
                "id": "node-1",
                "parent_id": None,
                "name": "Root",
                "type": "LOCATION",
                "node_order": 0,
                "icon": None,
                "access": None,
                "page_config": None,
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
            }
        )
        db = MagicMock()
        cursor = _FakeCursor(row=row)
        db.conn.execute = MagicMock(return_value=cursor)
        result = await get_breadcrumb(node_id="node-1", db=db)
        assert len(result) == 1
        assert result[0].id == "node-1"


# ===========================================================================
# visu — endpoint: get_page (page_config)
# ===========================================================================


from obs.api.v1.visu import get_page


class TestGetPage:
    def _make_page_node_row(self, access=None):
        return _Row(
            {
                "id": "page-1",
                "parent_id": None,
                "name": "Page 1",
                "type": "PAGE",
                "node_order": 0,
                "icon": None,
                "access": access,
                "page_config": json.dumps({"grid_cols": 12, "grid_row_height": 80, "background": None, "widgets": []}),
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
            }
        )

    @pytest.mark.asyncio
    async def test_non_page_raises_400(self):
        location_row = _Row(
            {
                "id": "loc-1",
                "parent_id": None,
                "name": "Location",
                "type": "LOCATION",
                "node_order": 0,
                "icon": None,
                "access": None,
                "page_config": None,
                "created_at": "2024-01-01T00:00:00+00:00",
                "updated_at": "2024-01-01T00:00:00+00:00",
            }
        )
        db = MagicMock()
        cursor = _FakeCursor(row=location_row)
        db.conn.execute = MagicMock(return_value=cursor)
        request = MagicMock()
        request.headers = {}
        with pytest.raises(HTTPException) as exc:
            await get_page(node_id="loc-1", request=request, db=db, user=None)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_public_page_accessible_without_auth(self):
        row = self._make_page_node_row(access="public")
        db = MagicMock()
        cursor = _FakeCursor(row=row)
        db.conn.execute = MagicMock(return_value=cursor)
        request = MagicMock()
        request.headers = {}
        pc = await get_page(node_id="page-1", request=request, db=db, user=None)
        assert pc is not None

    @pytest.mark.asyncio
    async def test_user_page_without_auth_raises_401(self):
        row = self._make_page_node_row(access="user")
        db = MagicMock()
        cursor = _FakeCursor(row=row)
        db.conn.execute = MagicMock(return_value=cursor)
        request = MagicMock()
        request.headers = {}
        with pytest.raises(HTTPException) as exc:
            await get_page(node_id="page-1", request=request, db=db, user=None)
        assert exc.value.status_code == 401


# ===========================================================================
# visu — import_nodes validation
# ===========================================================================


from obs.api.v1.visu import import_nodes


class TestImportNodes:
    @pytest.mark.asyncio
    async def test_invalid_format_raises_400(self):
        from obs.models.visu import VisuImportRequest

        body = VisuImportRequest(obs_export="wrong_format", version=1, nodes=[])
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await import_nodes(body=body, db=db, _user="admin")
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_empty_nodes_raises_400(self):
        from obs.models.visu import VisuImportRequest

        body = VisuImportRequest(obs_export="visu_subtree", version=1, nodes=[])
        db = MagicMock()
        with pytest.raises(HTTPException) as exc:
            await import_nodes(body=body, db=db, _user="admin")
        assert exc.value.status_code == 400


# ===========================================================================
# knxproj — response models
# ===========================================================================


from obs.api.v1.knxproj import GroupAddressOut, GroupAddressPage, HierarchyImportResult, ImportResult


class TestKnxprojModels:
    def test_import_result_defaults(self):
        r = ImportResult(imported=5, message="ok")
        assert r.imported == 5
        assert r.created == 0
        assert r.updated == 0
        assert r.locations == 0
        assert r.trades == 0
        assert r.hierarchies == []

    def test_import_result_with_hierarchy(self):
        r = ImportResult(
            imported=5,
            message="ok",
            hierarchies=[
                HierarchyImportResult(
                    mode="groups",
                    status="created",
                    tree_id="tree-1",
                    tree_name="ETS Gruppenadressen",
                    nodes_created=3,
                    links_created=1,
                    trees_replaced=1,
                    message="created",
                )
            ],
        )
        assert r.hierarchies[0].mode == "groups"
        assert r.hierarchies[0].nodes_created == 3
        assert r.hierarchies[0].trees_replaced == 1

    def test_group_address_out(self):
        ga = GroupAddressOut(
            address="1/1/1",
            name="Light",
            description="",
            dpt="1.001",
            imported_at="2024-01-01T00:00:00",
        )
        assert ga.address == "1/1/1"

    def test_group_address_page(self):
        page = GroupAddressPage(total=0, items=[])
        assert page.total == 0


# ===========================================================================
# knxproj — list_group_addresses
# ===========================================================================


from obs.api.v1.knxproj import list_group_addresses


class TestListGroupAddresses:
    @pytest.mark.asyncio
    async def test_empty_result(self):
        db = _make_db(
            fetchone_result=_Row({"n": 0}),
            fetchall_result=[],
        )
        result = await list_group_addresses(q="", page=0, size=100, _user="admin", db=db)
        assert result.total == 0
        assert result.items == []

    @pytest.mark.asyncio
    async def test_returns_addresses(self):
        rows = [_Row({"address": "1/1/1", "name": "Light", "description": "", "dpt": "1.001", "imported_at": "2024-01-01T00:00:00"})]
        db = _make_db(
            fetchone_result=_Row({"n": 1}),
            fetchall_result=rows,
        )
        result = await list_group_addresses(q="", page=0, size=100, _user="admin", db=db)
        assert result.total == 1
        assert result.items[0].address == "1/1/1"

    @pytest.mark.asyncio
    async def test_search_query(self):
        rows = [_Row({"address": "1/1/1", "name": "Light", "description": "", "dpt": None, "imported_at": "2024-01-01T00:00:00"})]
        db = _make_db(
            fetchone_result=_Row({"n": 1}),
            fetchall_result=rows,
        )
        result = await list_group_addresses(q="Light", page=0, size=100, _user="admin", db=db)
        assert result.total == 1

    @pytest.mark.asyncio
    async def test_count_row_none_returns_zero(self):
        db = _make_db(fetchone_result=None, fetchall_result=[])
        result = await list_group_addresses(q="", page=0, size=100, _user="admin", db=db)
        assert result.total == 0


# ===========================================================================
# knxproj — clear_group_addresses
# ===========================================================================


from obs.api.v1.knxproj import clear_group_addresses


class TestClearGroupAddresses:
    @pytest.mark.asyncio
    async def test_calls_delete(self):
        db = _make_db()
        await clear_group_addresses(_user="admin", db=db)
        db.execute_and_commit.assert_called_once_with("DELETE FROM knx_group_addresses")


# ===========================================================================
# knxproj — import_knxproj_file validation
# ===========================================================================


from obs.api.v1.knxproj import import_knxproj_file


class TestImportKnxprojFile:
    def test_normalize_hierarchy_modes_dedupes_csv_values(self):
        from obs.api.v1.knxproj import _normalize_hierarchy_modes

        assert _normalize_hierarchy_modes(["groups, buildings", "groups", "trades"]) == ["groups", "buildings", "trades"]
        assert _normalize_hierarchy_modes(None) == []

    @pytest.mark.asyncio
    async def test_create_requested_hierarchies_reports_service_errors(self):
        from obs.api.v1.knxproj import _create_requested_hierarchies

        async def fake_create(db_arg, request):
            if request.mode == "groups":
                raise HTTPException(status_code=422, detail="Keine Gruppenadressen")
            raise RuntimeError("boom")

        db = _make_db()
        with patch("obs.api.v1.knxproj.create_ets_hierarchy", side_effect=fake_create):
            results = await _create_requested_hierarchies(db, ["groups", "mid"], auto_link=False, replace_existing=True)

        assert [result.status for result in results] == ["failed", "failed"]
        assert results[0].message == "Keine Gruppenadressen"
        assert "boom" in results[1].message

    @pytest.mark.asyncio
    async def test_create_requested_hierarchies_preserves_unavailable_modes_when_replace_requested(self):
        from obs.api.v1.knxproj import _create_requested_hierarchies

        db = _make_db()
        with patch("obs.api.v1.knxproj.create_ets_hierarchy", new_callable=AsyncMock) as create_mock:
            results = await _create_requested_hierarchies(
                db,
                ["buildings"],
                auto_link=False,
                replace_existing=True,
                unavailable_messages={"buildings": "Keine Gebäude-Daten"},
            )

        create_mock.assert_not_awaited()
        assert results[0].status == "failed"
        assert results[0].trees_replaced == 0
        assert results[0].message == "Keine Gebäude-Daten"

    @pytest.mark.asyncio
    async def test_wrong_extension_raises_400(self):
        upload = AsyncMock()
        upload.filename = "project.zip"
        db = _make_db()
        with pytest.raises(HTTPException) as exc:
            await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_empty_file_raises_400(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"")
        db = _make_db()
        with pytest.raises(HTTPException) as exc:
            await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_parse_error_raises_400(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"garbage")
        db = _make_db()
        with (
            patch("obs.api.v1.knxproj.parse_knxproj", side_effect=ValueError("bad format")),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([], [])),
        ):
            result = await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert result.status_code == 400

    @pytest.mark.asyncio
    async def test_unexpected_parse_error_returns_500(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        db = _make_db()
        with (
            patch("obs.api.v1.knxproj.parse_knxproj", side_effect=RuntimeError("parser exploded")),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([], [])),
        ):
            result = await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert result.status_code == 500

    @pytest.mark.asyncio
    async def test_no_records_raises_422(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        db = _make_db()
        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([], [])),
        ):
            result = await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert result.status_code == 422

    @pytest.mark.asyncio
    async def test_successful_import_without_adapter(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        db = _make_db()
        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([], [])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[]),
        ):
            result = await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert result.imported == 1

    @pytest.mark.asyncio
    async def test_location_parse_failure_does_not_abort_group_address_import(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        db = _make_db()
        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", side_effect=RuntimeError("locations broken")),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[]),
        ):
            result = await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert result.imported == 1
        assert result.locations == 0
        assert result.functions == 0

    @pytest.mark.asyncio
    async def test_location_persistence_failure_does_not_abort_import(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        location = SimpleNamespace(identifier="loc-1", parent_id=None, name="Kitchen", space_type="Room", sort_order=1)
        db = _make_db()
        db.execute_and_commit = AsyncMock(side_effect=[RuntimeError("location write failed")])

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([location], [])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[]),
        ):
            result = await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)

        assert result.imported == 1
        assert result.locations == 0

    @pytest.mark.asyncio
    async def test_trades_parse_failure_does_not_abort_import(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        db = _make_db()

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([], [])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", side_effect=RuntimeError("trades broken")),
        ):
            result = await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)

        assert result.imported == 1
        assert result.trades == 0

    @pytest.mark.asyncio
    async def test_successful_import_links_trade_functions_from_parser_refs(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        trade = SimpleNamespace(identifier="trade-1", name="Lighting", parent_id=None, sort_order=1, function_ids=["fn-1"])
        db = _make_db()

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([], [])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[trade]),
        ):
            result = await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)

        assert result.trades == 1
        assert any(
            call.args[0] == "UPDATE knx_functions SET trade_id = ? WHERE id = ?" and call.args[1] == [("trade-1", "fn-1")]
            for call in db.executemany.await_args_list
        )

    @pytest.mark.asyncio
    async def test_successful_import_persists_locations_functions_and_trades(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        location = SimpleNamespace(identifier="loc-1", parent_id=None, name="Kitchen", space_type="Room", sort_order=1)
        function = SimpleNamespace(identifier="fn-1", space_id="loc-1", name="Light", usage_text="Lighting", ga_addresses=["1/1/1"])
        trade = SimpleNamespace(identifier="trade-1", name="Lighting", parent_id=None, sort_order=1, function_ids=[])
        db = _make_db(fetchall_result=[_Row({"id": "fn-1", "usage_text": "Lighting"})])
        captured_requests = []

        async def fake_create_hierarchy(db_arg, request):
            captured_requests.append(request)
            return SimpleNamespace(
                tree_id=f"tree-{request.mode}",
                tree_name=request.tree_name,
                nodes_created=2,
                links_created=0,
                trees_replaced=0,
                message="created",
            )

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([location], [function])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[trade]),
            patch("obs.api.v1.knxproj.create_ets_hierarchy", side_effect=fake_create_hierarchy),
        ):
            result = await import_knxproj_file(
                file=upload,
                password=None,
                adapter_name=None,
                direction="SOURCE",
                hierarchy_modes=["buildings", "trades"],
                hierarchy_auto_link=True,
                _user="admin",
                db=db,
            )

        assert result.locations == 1
        assert result.functions == 1
        assert result.trades == 1
        assert len(result.hierarchies) == 2
        assert [request.mode for request in captured_requests] == ["buildings", "trades"]
        assert "1 Räume/Gebäude" in result.message
        assert "1 Gewerke" in result.message
        assert "2 Hierarchien erstellt" in result.message

    @pytest.mark.asyncio
    async def test_successful_import_creates_requested_hierarchy(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        db = _make_db()
        captured_requests = []

        async def fake_create_hierarchy(db_arg, request):
            captured_requests.append(request)
            return SimpleNamespace(
                tree_id="tree-1",
                tree_name=request.tree_name,
                nodes_created=3,
                links_created=0,
                trees_replaced=1,
                message="created",
            )

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([], [])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[]),
            patch("obs.api.v1.knxproj.create_ets_hierarchy", side_effect=fake_create_hierarchy),
        ):
            result = await import_knxproj_file(
                file=upload,
                password=None,
                adapter_name=None,
                direction="SOURCE",
                hierarchy_modes=["groups"],
                hierarchy_auto_link=True,
                _user="admin",
                db=db,
            )

        assert result.imported == 1
        assert result.hierarchies[0].status == "created"
        assert result.hierarchies[0].nodes_created == 3
        assert captured_requests[0].mode == "groups"
        assert captured_requests[0].auto_link is False
        assert captured_requests[0].replace_existing is True
        assert captured_requests[0].group_addresses == ["1/1/1"]

    @pytest.mark.asyncio
    async def test_adapter_import_passes_auto_link_to_hierarchy_import(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        location = SimpleNamespace(identifier="loc-1", parent_id=None, name="Room", space_type="Room", sort_order=1)
        db = _make_db()
        captured_requests = []

        async def fake_create_hierarchy(db_arg, request):
            captured_requests.append(request)
            return SimpleNamespace(
                tree_id="tree-1",
                tree_name=request.tree_name,
                nodes_created=2,
                links_created=1,
                trees_replaced=0,
                message="created",
            )

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([location], [])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[]),
            patch("obs.api.v1.knxproj._bulk_import_datapoints", return_value=(1, 0)),
            patch("obs.api.v1.knxproj.create_ets_hierarchy", side_effect=fake_create_hierarchy),
        ):
            result = await import_knxproj_file(
                file=upload,
                password=None,
                adapter_name="KNX",
                direction="SOURCE",
                hierarchy_modes=["buildings"],
                hierarchy_auto_link=True,
                _user="admin",
                db=db,
            )

        assert result.created == 1
        assert result.hierarchies[0].links_created == 1
        assert captured_requests[0].auto_link is True

    @pytest.mark.asyncio
    async def test_import_clears_stale_functions_even_without_new_functions(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        location = SimpleNamespace(identifier="loc-1", parent_id=None, name="Room", space_type="Room", sort_order=1)
        db = _make_db()

        async def fake_create_hierarchy(db_arg, request):
            return SimpleNamespace(
                tree_id="tree-1",
                tree_name=request.tree_name,
                nodes_created=1,
                links_created=0,
                trees_replaced=0,
                message="created",
            )

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([location], [])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[]),
            patch("obs.api.v1.knxproj.create_ets_hierarchy", side_effect=fake_create_hierarchy),
        ):
            result = await import_knxproj_file(
                file=upload,
                password=None,
                adapter_name=None,
                direction="SOURCE",
                hierarchy_modes=["buildings"],
                hierarchy_auto_link=True,
                _user="admin",
                db=db,
            )

        assert result.hierarchies[0].status == "created"
        db.execute_and_commit.assert_any_call("DELETE FROM knx_function_ga_links")
        db.execute_and_commit.assert_any_call("DELETE FROM knx_functions")

    @pytest.mark.asyncio
    async def test_import_preserves_functions_when_optional_location_parse_fails(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        db = _make_db()

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", side_effect=RuntimeError("optional parser failed")),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[]),
        ):
            result = await import_knxproj_file(
                file=upload,
                password=None,
                adapter_name=None,
                direction="SOURCE",
                hierarchy_modes=["buildings"],
                hierarchy_auto_link=True,
                _user="admin",
                db=db,
            )

        assert result.imported == 1
        assert result.hierarchies[0].status == "failed"
        assert "Keine Gebäude-Daten" in result.hierarchies[0].message
        assert all(call.args[0] != "DELETE FROM knx_function_ga_links" for call in db.execute_and_commit.await_args_list)
        assert all(call.args[0] != "DELETE FROM knx_functions" for call in db.execute_and_commit.await_args_list)

    @pytest.mark.asyncio
    async def test_import_can_keep_existing_hierarchy_trees(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        db = _make_db()
        captured_requests = []

        async def fake_create_hierarchy(db_arg, request):
            captured_requests.append(request)
            return SimpleNamespace(
                tree_id="tree-1",
                tree_name=request.tree_name,
                nodes_created=3,
                links_created=0,
                trees_replaced=0,
                message="created",
            )

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([], [])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[]),
            patch("obs.api.v1.knxproj.create_ets_hierarchy", side_effect=fake_create_hierarchy),
        ):
            result = await import_knxproj_file(
                file=upload,
                password=None,
                adapter_name=None,
                direction="SOURCE",
                hierarchy_modes=["groups"],
                hierarchy_auto_link=True,
                hierarchy_replace_existing=False,
                _user="admin",
                db=db,
            )

        assert result.hierarchies[0].status == "created"
        assert captured_requests[0].replace_existing is False

    @pytest.mark.asyncio
    async def test_unavailable_hierarchy_mode_is_reported_without_aborting_import(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        db = _make_db()
        create_hierarchy = AsyncMock()

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([], [])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[]),
            patch("obs.api.v1.knxproj.create_ets_hierarchy", create_hierarchy),
        ):
            result = await import_knxproj_file(
                file=upload,
                password=None,
                adapter_name=None,
                direction="SOURCE",
                hierarchy_modes=["buildings"],
                hierarchy_auto_link=True,
                _user="admin",
                db=db,
            )

        assert result.imported == 1
        assert result.hierarchies[0].mode == "buildings"
        assert result.hierarchies[0].status == "failed"
        assert result.hierarchies[0].trees_replaced == 0
        assert "Keine Gebäude-Daten" in result.hierarchies[0].message
        create_hierarchy.assert_not_called()

    @pytest.mark.asyncio
    async def test_invalid_hierarchy_mode_raises_400(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        db = _make_db()

        with pytest.raises(HTTPException) as exc:
            await import_knxproj_file(
                file=upload,
                password=None,
                adapter_name=None,
                direction="SOURCE",
                hierarchy_modes=["unknown"],
                hierarchy_auto_link=True,
                _user="admin",
                db=db,
            )

        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_device_import_failure_is_swallowed_after_group_address_import(self):
        upload = AsyncMock()
        upload.filename = "project.knxproj"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt="1.001", main_group_name="G1", mid_group_name="M1")
        db = _make_db()

        with (
            patch("obs.api.v1.knxproj.parse_knxproj", return_value=[record]),
            patch("obs.api.v1.knxproj.parse_knxproj_locations", return_value=([], [])),
            patch("obs.api.v1.knxproj.parse_knxproj_trades", return_value=[]),
            patch("obs.api.v1.knxproj._import_knx_devices_and_comm_objects", side_effect=RuntimeError("boom")),
        ):
            result = await import_knxproj_file(file=upload, password=None, adapter_name=None, direction="SOURCE", _user="admin", db=db)

        assert result.imported == 1
        assert result.locations == 0
        assert result.functions == 0
        assert result.trades == 0
        assert len(result.hierarchies) == 0


# ===========================================================================
# knxproj — import_ga_csv_file validation
# ===========================================================================


from obs.api.v1.knxproj import import_ga_csv_file


class TestImportGaCsvFile:
    @pytest.mark.asyncio
    async def test_wrong_extension_raises_400(self):
        upload = AsyncMock()
        upload.filename = "data.xlsx"
        db = _make_db()
        with pytest.raises(HTTPException) as exc:
            await import_ga_csv_file(file=upload, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_empty_file_raises_400(self):
        upload = AsyncMock()
        upload.filename = "data.csv"
        upload.read = AsyncMock(return_value=b"")
        db = _make_db()
        with pytest.raises(HTTPException) as exc:
            await import_ga_csv_file(file=upload, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_parse_error_raises_400(self):
        upload = AsyncMock()
        upload.filename = "data.csv"
        upload.read = AsyncMock(return_value=b"garbage")
        db = _make_db()
        with patch("obs.api.v1.knxproj.parse_ga_csv", side_effect=ValueError("bad csv")):
            with pytest.raises(HTTPException) as exc:
                await import_ga_csv_file(file=upload, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert exc.value.status_code == 400

    @pytest.mark.asyncio
    async def test_no_records_raises_422(self):
        upload = AsyncMock()
        upload.filename = "data.csv"
        upload.read = AsyncMock(return_value=b"data")
        db = _make_db()
        with patch("obs.api.v1.knxproj.parse_ga_csv", return_value=[]):
            with pytest.raises(HTTPException) as exc:
                await import_ga_csv_file(file=upload, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_unexpected_parse_error_raises_500(self):
        upload = AsyncMock()
        upload.filename = "data.csv"
        upload.read = AsyncMock(return_value=b"data")
        db = _make_db()
        with patch("obs.api.v1.knxproj.parse_ga_csv", side_effect=RuntimeError("parser broken")):
            with pytest.raises(HTTPException) as exc:
                await import_ga_csv_file(file=upload, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert exc.value.status_code == 500

    @pytest.mark.asyncio
    async def test_successful_csv_import_without_adapter(self):
        upload = AsyncMock()
        upload.filename = "data.csv"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt=None, main_group_name="G1", mid_group_name="M1")
        db = _make_db()
        with patch("obs.api.v1.knxproj.parse_ga_csv", return_value=[record]):
            result = await import_ga_csv_file(file=upload, adapter_name=None, direction="SOURCE", _user="admin", db=db)
        assert result.imported == 1
        assert "ohne DataPoints" in result.message

    @pytest.mark.asyncio
    async def test_successful_csv_import_with_adapter_imports_datapoints(self):
        upload = AsyncMock()
        upload.filename = "data.csv"
        upload.read = AsyncMock(return_value=b"data")
        record = SimpleNamespace(address="1/1/1", name="Light", description="", dpt=None, main_group_name="G1", mid_group_name="M1")
        db = _make_db()
        with (
            patch("obs.api.v1.knxproj.parse_ga_csv", return_value=[record]),
            patch("obs.api.v1.knxproj._bulk_import_datapoints", return_value=(2, 1)),
        ):
            result = await import_ga_csv_file(file=upload, adapter_name="knx-main", direction="SOURCE", _user="admin", db=db)

        assert result.imported == 3
        assert result.created == 2
        assert result.updated == 1
        assert result.message == "2 DataPoints neu erstellt, 1 aktualisiert"


# ===========================================================================
# knxproj — _bulk_import_datapoints helper
# ===========================================================================


from obs.api.v1.knxproj import _bulk_import_datapoints


class TestBulkImportDatapoints:
    @pytest.mark.asyncio
    async def test_bulk_import_datapoints_updates_existing_and_adds_new_records(self):
        db = _make_db(
            fetchone_result={"id": "inst-1", "adapter_type": "KNX"},
            fetchall_result=[{"id": "binding-1", "datapoint_id": "dp-1", "config": '{"group_address":"1/1/1"}'}],
        )

        def _get_dpt(_dpt):
            return SimpleNamespace(dpt_id="DPT1.001", data_type="BOOLEAN", unit=None)

        async def _reload_instance_bindings(*_args, **_kwargs):
            return None

        def _raise_registry():
            raise RuntimeError("registry unavailable")

        with (
            patch("obs.adapters.knx.dpt_registry.DPTRegistry.get", _get_dpt),
            patch("obs.core.registry.get_registry", _raise_registry),
            patch("obs.adapters.registry.reload_instance_bindings", _reload_instance_bindings),
        ):
            created, updated = await _bulk_import_datapoints(
                records=[
                    SimpleNamespace(address="1/1/1", name="Existing", dpt="DPT1.001"),
                    SimpleNamespace(address="1/1/2", name="New", dpt=None),
                ],
                adapter_name="knx-main",
                direction="SOURCE",
                db=db,
                now="2026-06-10T00:00:00+00:00",
            )

        assert created == 1
        assert updated == 1
        assert db.executemany.await_count == 4
        assert db.commit.await_count == 1

    @pytest.mark.asyncio
    async def test_bulk_import_datapoints_fails_if_adapter_instance_missing(self):
        db = _make_db(fetchone_result=None)

        with pytest.raises(HTTPException) as exc_info:
            await _bulk_import_datapoints(
                records=[],
                adapter_name="missing",
                direction="SOURCE",
                db=db,
                now="2026-06-10T00:00:00+00:00",
            )

        assert exc_info.value.status_code == 404


# ===========================================================================
# config.py — Pydantic model instantiation (simple smoke tests)
# ===========================================================================


from obs.api.v1.config import (
    ConfigExport,
    ExportedAdapterConfig,
    ExportedAdapterInstance,
    ExportedBinding,
    ExportedDataPoint,
    ExportedKnxGroupAddress,
    ExportedLogicGraph,
    ExportedVisuNode,
    ImportResult as ConfigImportResult,
    ResetResult,
)


class TestConfigModels:
    def test_exported_datapoint(self):
        dp = ExportedDataPoint(id="abc", name="Test", data_type="FLOAT", unit=None, tags=[], mqtt_alias=None)
        assert dp.name == "Test"

    def test_exported_binding(self):
        b = ExportedBinding(
            id="b1",
            datapoint_id="dp1",
            adapter_type="knx",
            direction="SOURCE",
            config={"group_address": "1/1/1"},
            enabled=True,
        )
        assert b.direction == "SOURCE"

    def test_exported_adapter_instance(self):
        ai = ExportedAdapterInstance(id="i1", adapter_type="knx", name="KNX", config={}, enabled=True)
        assert ai.name == "KNX"

    def test_exported_adapter_config_legacy(self):
        ac = ExportedAdapterConfig(adapter_type="knx", config={}, enabled=True)
        assert ac.adapter_type == "knx"

    def test_exported_knx_group_address(self):
        ga = ExportedKnxGroupAddress(address="1/1/1", name="Light", description="", dpt=None)
        assert ga.address == "1/1/1"

    def test_exported_logic_graph(self):
        lg = ExportedLogicGraph(id="g1", name="Graph", description="", enabled=True, flow_data={})
        assert lg.name == "Graph"

    def test_exported_visu_node_defaults(self):
        vn = ExportedVisuNode(
            id="v1", parent_id=None, name="Room", type="LOCATION", node_order=0, icon=None, access=None, access_pin=None, page_config=None
        )
        assert vn.users == []

    def test_import_result_defaults(self):
        r = ConfigImportResult(
            datapoints_created=0,
            datapoints_updated=0,
            bindings_created=0,
            bindings_updated=0,
            adapter_instances_upserted=0,
            knx_group_addresses_upserted=0,
            logic_graphs_created=0,
            logic_graphs_updated=0,
            adapters_restarted=0,
            errors=[],
        )
        assert r.icons_imported == 0
        assert r.visu_nodes_upserted == 0

    def test_reset_result_defaults(self):
        r = ResetResult(
            datapoints_deleted=0,
            bindings_deleted=0,
            adapter_instances_deleted=0,
            knx_group_addresses_deleted=0,
            logic_graphs_deleted=0,
            errors=[],
        )
        assert r.visu_nodes_deleted == 0
        assert r.icons_deleted == 0

    def test_config_export_empty(self):
        export = ConfigExport(obs_version="5", exported_at="2024-01-01T00:00:00", datapoints=[], bindings=[])
        assert export.obs_version == "5"
        assert export.adapter_instances == []
        assert export.visu_nodes == []
