"""Raster icons (PNG/JPEG) in the Icons Library (obs.api.v1.icons).

PNG/JPEG uploads are wrapped into a minimal SVG that embeds the image as a base64 data URI on an
<image> element, and the SVG sanitizer keeps exactly such raster data URIs — and only on <image>.
"""

from __future__ import annotations

import base64
import io
import struct
import xml.etree.ElementTree as ET
import zipfile
import zlib

import pytest
from fastapi import HTTPException

import obs.api.v1.icons as icons_api
from obs.api.v1.icons import _jpeg_size, _png_size, _raster_to_svg, _sanitize_svg

_SVG_NS = "{http://www.w3.org/2000/svg}"
_SIMPLE_SVG = b'<svg xmlns="http://www.w3.org/2000/svg"><path d="M0 0"/></svg>'


def _png(width: int, height: int) -> bytes:
    """A complete, decodable 8-bit greyscale PNG of the given size."""

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    raw = b"".join(b"\x00" + b"\x80" * width for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )


_JPEG_SOS = b"\xff\xda" + struct.pack(">H", 8) + b"\x01\x01\x00\x00\x3f\x00"


def _jpeg(width: int, height: int, *, prefix: bytes = b"", scan: bool = True) -> bytes:
    """SOI, an APP0 segment, optional extra bytes, a baseline SOF0 header with the size, then
    (unless ``scan`` is False) the start of scan with some image data, and EOI."""
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    sof0 = b"\xff\xc0" + struct.pack(">HBHHB", 11, 8, height, width, 1) + b"\x01\x11\x00"
    body = _JPEG_SOS + b"\x12\x34" if scan else b""
    return b"\xff\xd8" + app0 + prefix + sof0 + body + b"\xff\xd9"


def _svg_with_href(element: str, value: str, *, xlink: bool = False) -> bytes:
    attr = "xlink:href" if xlink else "href"
    return (f'<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink"><{element} {attr}="{value}"/></svg>').encode()


def _href_of(svg: bytes, element: str) -> str | None:
    el = ET.fromstring(svg).find(f"{_SVG_NS}{element}")
    assert el is not None
    return el.get("href") or el.get("{http://www.w3.org/1999/xlink}href")


class _FakeUpload:
    def __init__(self, filename: str, content: bytes, content_type: str = "application/octet-stream"):
        self.filename = filename
        self.content_type = content_type
        self._content = content

    async def read(self) -> bytes:
        return self._content


def _zip(members: list[tuple[str, bytes]]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in members:
            zf.writestr(name, data)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# _sanitize_svg — raster data URIs on <image>
# ---------------------------------------------------------------------------

_PNG_URI = "data:image/png;base64," + base64.b64encode(_png(2, 1)).decode()


class TestSanitizeRasterDataUri:
    @pytest.mark.parametrize("mime", ["png", "jpeg", "gif", "webp"])
    def test_keeps_raster_data_uri_on_image(self, mime):
        value = f"data:image/{mime};base64,iVBORw0KGgo="
        assert _href_of(_sanitize_svg(_svg_with_href("image", value)), "image") == value

    def test_keeps_namespaced_raster_href_as_written_by_inkscape(self):
        # Inkscape writes xlink:href and wraps the base64 payload across lines.
        value = "data:image/png;base64,iVBORw0K&#10;Ggo="
        out = _sanitize_svg(_svg_with_href("image", value, xlink=True))
        assert _href_of(out, "image") == "data:image/png;base64,iVBORw0K\nGgo="

    def test_keeps_real_png_payload(self):
        assert _href_of(_sanitize_svg(_svg_with_href("image", _PNG_URI)), "image") == _PNG_URI

    @pytest.mark.parametrize(
        "value",
        [
            "data:image/svg+xml;base64,PHN2Zy8+",
            "data:image/svg+xml,%3Csvg%3E%3C/svg%3E",
            "data:image/png,rawbytes",  # not base64
            "data:image/png;base64,abc!def",  # not a base64 payload
            "data:text/html;base64,PHNjcmlwdD4=",
            "data:image/bmp;base64,Qk0=",
            "javascript:alert(1)",
            "java&#10;script:alert(1)",
        ],
    )
    def test_strips_other_schemes_on_image(self, value):
        out = _sanitize_svg(_svg_with_href("image", value))
        assert _href_of(out, "image") is None

    @pytest.mark.parametrize("element", ["a", "use", "feImage", "pattern"])
    @pytest.mark.parametrize("xlink", [False, True])
    def test_strips_raster_data_uri_on_other_elements(self, element, xlink):
        out = _sanitize_svg(_svg_with_href(element, _PNG_URI, xlink=xlink))
        assert _href_of(out, element) is None

    def test_strips_embedded_raster_over_the_size_cap(self, monkeypatch):
        monkeypatch.setattr(icons_api, "_RASTER_MAX_BYTES", len(_png(2, 1)))
        assert _href_of(_sanitize_svg(_svg_with_href("image", _PNG_URI)), "image") == _PNG_URI
        big_uri = "data:image/png;base64," + base64.b64encode(_png(2, 1) + b"\x00").decode()
        assert _href_of(_sanitize_svg(_svg_with_href("image", big_uri)), "image") is None

    def test_base64_size_matches_the_decoded_payload(self):
        for data in (b"", b"a", b"ab", b"abc", _png(3, 2)):
            assert icons_api._base64_size(base64.b64encode(data).decode()) == len(data)

    def test_still_strips_event_handlers_on_image(self):
        payload = f'<svg xmlns="http://www.w3.org/2000/svg"><image href="{_PNG_URI}" onload="alert(1)"/></svg>'.encode()
        out = _sanitize_svg(payload).decode()
        assert "onload" not in out
        assert _PNG_URI in out


# ---------------------------------------------------------------------------
# _png_size / _jpeg_size
# ---------------------------------------------------------------------------


class TestRasterSize:
    def test_png_size(self):
        assert _png_size(_png(393, 207)) == (393, 207)

    def test_png_without_ihdr(self):
        assert _png_size(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16) is None

    def test_png_truncated(self):
        assert _png_size(b"\x89PNG\r\n\x1a\n") is None

    def test_jpeg_size(self):
        assert _jpeg_size(_jpeg(640, 480)) == (640, 480)

    def test_jpeg_progressive_sof2(self):
        data = _jpeg(32, 16).replace(b"\xff\xc0", b"\xff\xc2", 1)
        assert _jpeg_size(data) == (32, 16)

    def test_jpeg_skips_fill_bytes_and_standalone_markers(self):
        assert _jpeg_size(_jpeg(10, 20, prefix=b"\xff\xff\xd0\xff\x01")) == (10, 20)

    def test_jpeg_skips_dht_segment(self):
        dht = b"\xff\xc4" + struct.pack(">H", 4) + b"\x00\x00"
        assert _jpeg_size(_jpeg(7, 9, prefix=dht)) == (7, 9)

    def test_jpeg_garbage_between_segments(self):
        assert _jpeg_size(_jpeg(10, 20, prefix=b"\x00")) is None

    def test_jpeg_truncated_sof(self):
        assert _jpeg_size(b"\xff\xd8\xff\xc0\x00\x11\x08") is None

    def test_jpeg_without_sof(self):
        assert _jpeg_size(b"\xff\xd8\xff\xd9") is None

    def test_jpeg_with_headers_only(self):
        assert _jpeg_size(_jpeg(640, 480, scan=False)) is None

    def test_jpeg_scan_before_any_frame_header(self):
        assert _jpeg_size(b"\xff\xd8" + _JPEG_SOS + b"\x12\x34\xff\xd9") is None


# ---------------------------------------------------------------------------
# _raster_to_svg
# ---------------------------------------------------------------------------


class TestRasterToSvg:
    def test_png_is_wrapped_with_its_dimensions(self):
        png = _png(393, 207)
        svg = _raster_to_svg(png, "car.png")
        root = ET.fromstring(svg)
        assert root.tag == f"{_SVG_NS}svg"
        assert (root.get("width"), root.get("height"), root.get("viewBox")) == ("393", "207", "0 0 393 207")
        image = root.find(f"{_SVG_NS}image")
        assert (image.get("width"), image.get("height")) == ("393", "207")
        assert image.get("href") == "data:image/png;base64," + base64.b64encode(png).decode()

    def test_jpeg_is_wrapped_with_its_dimensions(self):
        jpeg = _jpeg(640, 480)
        root = ET.fromstring(_raster_to_svg(jpeg, "photo.jpg"))
        assert root.get("viewBox") == "0 0 640 480"
        assert root.find(f"{_SVG_NS}image").get("href") == "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()

    @pytest.mark.parametrize("content", [_SIMPLE_SVG, b"GIF89a", b"", b"\x89PNG"])
    def test_non_raster_returns_none(self, content):
        assert _raster_to_svg(content, "x") is None

    def test_too_large_image_is_rejected(self, monkeypatch):
        monkeypatch.setattr(icons_api, "_RASTER_MAX_BYTES", 64)
        with pytest.raises(HTTPException) as exc_info:
            _raster_to_svg(_png(64, 64) + b"\x00" * 100, "big.png")
        assert exc_info.value.status_code == 422
        assert "big.png" in exc_info.value.detail

    @pytest.mark.parametrize(
        "content",
        [
            b"\x89PNG\r\n\x1a\n" + b"\x00" * 16,  # no IHDR
            _png(1, 1)[:16] + struct.pack(">II", 0, 5) + _png(1, 1)[24:],  # zero width
            _png(1, 1)[:16] + struct.pack(">II", 5, 16385) + _png(1, 1)[24:],  # too high
            b"\xff\xd8\xff\xd9",  # JPEG without SOF
            _jpeg(8, 8, scan=False),  # JPEG that ends after its headers
        ],
    )
    def test_unreadable_image_is_rejected(self, content):
        with pytest.raises(HTTPException) as exc_info:
            _raster_to_svg(content, "bad.png")
        assert exc_info.value.status_code == 422


# ---------------------------------------------------------------------------
# import_icons — PNG/JPEG uploads
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_import_single_png(tmp_path, monkeypatch):
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    result = await icons_api.import_icons(files=[_FakeUpload("Car Front.PNG", _png(393, 207), "image/png")], _user="admin")
    assert result.imported == 1
    assert result.names == ["car_front"]
    stored = (tmp_path / "car_front.svg").read_bytes()
    assert ET.fromstring(stored).get("viewBox") == "0 0 393 207"
    # What is stored is also what is served: re-sanitizing keeps the embedded image.
    assert _sanitize_svg(stored) == stored


@pytest.mark.asyncio
async def test_import_single_jpeg(tmp_path, monkeypatch):
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    result = await icons_api.import_icons(files=[_FakeUpload("photo.jpeg", _jpeg(20, 10), "image/jpeg")], _user="admin")
    assert result.names == ["photo"]
    assert "data:image/jpeg;base64," in (tmp_path / "photo.svg").read_text()


@pytest.mark.asyncio
async def test_import_single_png_detected_by_content_not_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    result = await icons_api.import_icons(files=[_FakeUpload("lamp.svg", _png(4, 4))], _user="admin")
    assert result.names == ["lamp"]
    assert "data:image/png;base64," in (tmp_path / "lamp.svg").read_text()


@pytest.mark.asyncio
async def test_import_single_file_neither_svg_nor_raster_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    with pytest.raises(HTTPException) as exc_info:
        await icons_api.import_icons(files=[_FakeUpload("notes.txt", b"hello")], _user="admin")
    assert exc_info.value.status_code == 422


@pytest.mark.asyncio
async def test_import_single_png_with_unsafe_name_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    result = await icons_api.import_icons(files=[_FakeUpload(".hidden.png", _png(1, 1))], _user="admin")
    assert result.skipped == 1
    assert list(tmp_path.iterdir()) == []


@pytest.mark.asyncio
async def test_import_inkscape_svg_with_embedded_png_keeps_the_image(tmp_path, monkeypatch):
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    inkscape_svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" viewBox="0 0 2 1">'
        f'<g><image width="2" height="1" xlink:href="{_PNG_URI}"/></g></svg>'
    ).encode()
    await icons_api.import_icons(files=[_FakeUpload("drawing.svg", inkscape_svg, "image/svg+xml")], _user="admin")
    assert _PNG_URI in (tmp_path / "drawing.svg").read_text()


@pytest.mark.asyncio
async def test_import_zip_with_png_jpeg_and_svg(tmp_path, monkeypatch):
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    zdata = _zip(
        [
            ("set/car.png", _png(393, 207)),
            ("set/photo.JPG", _jpeg(8, 8)),
            ("set/noext", _png(2, 2)),
            ("set/home.svg", _SIMPLE_SVG),
            ("set/readme.txt", b"hello"),
            ("set/fake.png", b"not a png"),
        ],
    )
    result = await icons_api.import_icons(files=[_FakeUpload("icons.zip", zdata, "application/zip")], _user="admin")
    assert sorted(result.names) == ["car", "home", "noext", "photo"]
    assert result.skipped == 2
    assert ET.fromstring((tmp_path / "car.svg").read_bytes()).get("viewBox") == "0 0 393 207"


@pytest.mark.asyncio
async def test_import_zip_png_with_unsafe_name_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    zdata = _zip([(".hidden.png", _png(1, 1))])
    result = await icons_api.import_icons(files=[_FakeUpload("icons.zip", zdata, "application/zip")], _user="admin")
    assert (result.imported, result.skipped) == (0, 1)


@pytest.mark.asyncio
async def test_import_zip_with_oversized_png_is_atomic(tmp_path, monkeypatch):
    monkeypatch.setattr(icons_api, "_icons_dir", lambda: tmp_path)
    monkeypatch.setattr(icons_api, "_RASTER_MAX_BYTES", 200)
    zdata = _zip([("ok.svg", _SIMPLE_SVG), ("big.png", _png(64, 64) + b"\x00" * 300)])
    with pytest.raises(HTTPException) as exc_info:
        await icons_api.import_icons(files=[_FakeUpload("icons.zip", zdata, "application/zip")], _user="admin")
    assert exc_info.value.status_code == 422
    assert list(tmp_path.iterdir()) == []
