"""
Unit tests for the Icons Library helpers (obs.api.v1.icons).

These tests exercise the pure utility functions that do not require a running
FastAPI app or database connection.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from obs.api.v1.icons import _is_svg, _safe_name


# ---------------------------------------------------------------------------
# _is_svg
# ---------------------------------------------------------------------------

class TestIsSvg:
    def test_simple_svg(self):
        content = b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24"><path d="M1 1"/></svg>'
        assert _is_svg(content) is True

    def test_svg_with_spaces(self):
        content = b'<?xml version="1.0"?>\n<svg \nxmlns="http://www.w3.org/2000/svg">'
        assert _is_svg(content) is True

    def test_svg_case_insensitive(self):
        content = b'<SVG xmlns="...">'
        assert _is_svg(content) is True

    def test_svg_with_closing_bracket(self):
        content = b'<svg>'
        assert _is_svg(content) is True

    def test_not_svg_png(self):
        # PNG magic bytes
        content = b'\x89PNG\r\n\x1a\n' + b'\x00' * 100
        assert _is_svg(content) is False

    def test_not_svg_json(self):
        content = b'{"icon": "home"}'
        assert _is_svg(content) is False

    def test_not_svg_html(self):
        content = b'<html><body><p>not an svg</p></body></html>'
        assert _is_svg(content) is False

    def test_not_svg_empty(self):
        assert _is_svg(b'') is False

    def test_svg_detected_within_first_2kb(self):
        # Prefix muss exakt >= 2048 Bytes sein, damit <svg> ausserhalb des
        # [:2048]-Fensters liegt. 2000 < 2048 → würde fälschlicherweise True liefern.
        prefix = b'X' * 2048
        content = prefix + b'<svg>'
        assert _is_svg(content) is False

    def test_svg_detected_just_before_limit(self):
        prefix = b'X' * 2040
        content = prefix + b'<svg>'
        # Within first 2048 characters? prefix is 2040, tag starts at 2040 < 2048
        assert _is_svg(content) is True


# ---------------------------------------------------------------------------
# _safe_name
# ---------------------------------------------------------------------------

class TestSafeName:
    def test_simple_name(self):
        assert _safe_name("home.svg") == "home"

    def test_name_with_uppercase(self):
        assert _safe_name("MyIcon.SVG") == "myicon"

    def test_name_with_spaces(self):
        result = _safe_name("my icon.svg")
        assert result == "my_icon"

    def test_name_with_hyphens(self):
        assert _safe_name("arrow-right.svg") == "arrow-right"

    def test_name_with_underscores(self):
        assert _safe_name("my_icon.svg") == "my_icon"

    def test_path_traversal_dotdot(self):
        assert _safe_name("../evil.svg") is None

    def test_path_traversal_slash(self):
        assert _safe_name("/etc/passwd") is None

    def test_path_traversal_backslash(self):
        assert _safe_name("sub\\evil.svg") is None

    def test_empty_stem(self):
        assert _safe_name(".svg") is None

    def test_empty_string(self):
        assert _safe_name("") is None

    def test_special_chars_replaced(self):
        result = _safe_name("icon!@#$.svg")
        assert result is not None
        # Should contain only alphanumeric, hyphens, underscores
        import re
        assert re.match(r'^[\w\-]+$', result)

    def test_no_extension(self):
        assert _safe_name("justname") == "justname"

    def test_nested_path_in_zip_direct(self):
        # Slash im Dateinamen → direkt abgelehnt
        assert _safe_name("icons/home.svg") is None

    def test_zip_member_basename(self):
        from pathlib import Path
        # Der ZIP-Handler ruft _safe_name(Path(member).name) auf, nicht
        # _safe_name(member) — dadurch wird der Slash vorher entfernt.
        member = "folder/home.svg"
        assert _safe_name(Path(member).name) == "home"
