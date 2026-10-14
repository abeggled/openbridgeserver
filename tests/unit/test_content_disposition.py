"""Content-Disposition header builder for downloads named after user data (#1284)."""

from __future__ import annotations

import pytest

from obs.api.v1.content_disposition import attachment_header


def test_ascii_name_keeps_plain_quoted_filename():
    assert attachment_header("Room_-_Action.json") == 'attachment; filename="Room_-_Action.json"'


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        # em dash: not Latin-1, previously a 500 when encoding the header
        ("Room_—_Action.json", "attachment; filename=\"Room___Action.json\"; filename*=UTF-8''Room_%E2%80%94_Action.json"),
        # umlaut: accent dropped in the fallback, exact name in filename*
        ("Küche.json", "attachment; filename=\"Kuche.json\"; filename*=UTF-8''K%C3%BCche.json"),
        # characters that would break or escape the quoted-string
        ('a"b\\c.json', "attachment; filename=\"a_b_c.json\"; filename*=UTF-8''a%22b%5Cc.json"),
        # control characters (header injection) are neutralised in both parts
        ("a\r\nb.json", "attachment; filename=\"a__b.json\"; filename*=UTF-8''a%0D%0Ab.json"),
    ],
)
def test_non_ascii_or_unsafe_name_gets_fallback_and_rfc5987_filename(name, expected):
    assert attachment_header(name) == expected


def test_header_value_is_latin1_encodable():
    # Starlette encodes header values as Latin-1; this is what failed in #1284.
    attachment_header("日本 — Ω.json").encode("latin-1")
