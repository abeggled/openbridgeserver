"""Build ``Content-Disposition`` headers for downloads named after user data."""

from __future__ import annotations

import unicodedata
from urllib.parse import quote


def _ascii_fallback(filename: str) -> str:
    """Approximate *filename* in printable ASCII that is safe inside a quoted-string."""
    decomposed = unicodedata.normalize("NFKD", filename)
    chars = []
    for ch in decomposed:
        if unicodedata.combining(ch):
            continue  # drop accents split off by NFKD ("ü" -> "u")
        if " " <= ch <= "~" and ch not in '"\\':
            chars.append(ch)
        else:
            chars.append("_")
    return "".join(chars)


def attachment_header(filename: str) -> str:
    """Return an ``attachment`` Content-Disposition value for *filename* (RFC 6266).

    HTTP header values are Latin-1, so a raw non-ASCII name (e.g. an em dash in a
    logic graph name, #1284) makes the response fail with a 500. Pure-ASCII names
    keep the plain ``filename="..."`` form; anything else gets an ASCII fallback
    plus the exact name as ``filename*=UTF-8''...`` (RFC 5987).
    """
    fallback = _ascii_fallback(filename)
    if fallback == filename:
        return f'attachment; filename="{filename}"'
    return f"attachment; filename=\"{fallback}\"; filename*=UTF-8''{quote(filename, safe='')}"
