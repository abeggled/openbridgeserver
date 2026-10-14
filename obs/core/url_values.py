"""Encoding of values inserted into URL templates."""

from __future__ import annotations

import re
from urllib.parse import quote

_LONE_PERCENT = re.compile(r"%(?![0-9A-Fa-f]{2})")
_LONE_SURROGATE = re.compile("[\ud800-\udfff]")


def encode_url_value(text: str) -> str:
    """Percent-encode a value inserted into a URL template (spaces, '+', '%', non-ASCII …).

    URL delimiters and existing percent-escapes stay, so a placeholder may still
    supply a complete URL; a value containing '&' or '#' therefore keeps its
    delimiter meaning (documented).
    """
    # A lone surrogate (e.g. from a JSON payload) cannot be UTF-8 encoded;
    # it becomes U+FFFD instead of raising out of the send path.
    text = _LONE_SURROGATE.sub("\ufffd", text)
    return quote(_LONE_PERCENT.sub("%25", text), safe="/:?#[]@!$&'()*,;=%~")
