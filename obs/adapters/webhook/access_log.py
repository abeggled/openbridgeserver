"""Keep webhook tokens out of the uvicorn access log (issue #1305).

A webhook URL carries its credential either as ``?token=…`` or as the path
segment after the slug (``<prefix>/<slug>/<token>``).  uvicorn writes every
request line to stdout — the systemd journal on LXC, ``docker logs`` in a
container — so without this filter each device call would log its token in
clear text.  The filter rewrites only the request target; caller address,
method, status and slug stay in the line for diagnosis.

The filter runs synchronously inside ``logging`` and cannot ask the database
which prefixes are configured, yet a stopped or disabled instance must not
leak a token either.  Hence three rules, from broad to narrow:

* ``token`` is masked as a query parameter on **every** path.  It is the
  webhook's credential parameter by contract (a binding may not read its value
  from ``token``), no other OBS route uses it, and masking a credential that
  happens to share the name elsewhere is harmless.
* Below a prefix a **running** instance claims, every segment after the slug is
  masked whatever it looks like — that is exactly what the dispatcher reads as
  the token, so a mistyped or truncated token is covered as well.
* Below any **other** non-reserved first segment (a stopped or disabled
  instance), a trailing segment following a slug-shaped segment is masked,
  including imported tokens of arbitrary length.  Application-owned paths
  (``/api``, the SPAs, static files) are never touched.
"""

from __future__ import annotations

import logging
from urllib.parse import unquote, unquote_plus

from obs.adapters.webhook.adapter import _RESERVED_PREFIX_SEGMENTS, SLUG_PATTERN, active_prefixes
from obs.api.v1.redaction import REDACTED

ACCESS_LOGGER_NAME = "uvicorn.access"

# Index of the request target in uvicorn's ``'%s - "%s %s HTTP/%s" %d'`` record args.
_PATH_ARG = 2


def _redact_query(query: str) -> str:
    pairs = query.split("&")
    for index, pair in enumerate(pairs):
        key, separator, _value = pair.partition("=")
        if separator and unquote_plus(key) == "token":
            pairs[index] = f"{key}={REDACTED}"
    return "&".join(pairs)


def _redact_path(path: str) -> str:
    raw = path.split("/")
    positions = [index for index, segment in enumerate(raw) if segment]
    decoded = [unquote(raw[index]) for index in positions]
    if not decoded or decoded[0].lower() in _RESERVED_PREFIX_SEGMENTS:
        return path

    masked: list[int] = []
    for prefix in active_prefixes():
        prefix_segments = prefix.strip("/").split("/")
        if decoded[: len(prefix_segments)] == prefix_segments:
            # <prefix>/<slug>/<everything after the slug is the token>
            masked = positions[len(prefix_segments) + 1 :]
            break
    else:
        if len(decoded) >= 3 and SLUG_PATTERN.match(decoded[-2].lower()):
            masked = [positions[-1]]

    if not masked:
        return path
    for index in masked:
        raw[index] = REDACTED
    return "/".join(raw)


def redact_access_path(path_with_query: str) -> str:
    """Return *path_with_query* with every webhook token replaced by ``[redacted]``."""
    path, separator, query = path_with_query.partition("?")
    redacted_path = _redact_path(path)
    if not separator:
        return redacted_path
    return f"{redacted_path}?{_redact_query(query)}"


class WebhookTokenAccessLogFilter(logging.Filter):
    """Rewrite the request target of a uvicorn access record; never drops a record."""

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) > _PATH_ARG and isinstance(args[_PATH_ARG], str):
            redacted = redact_access_path(args[_PATH_ARG])
            if redacted != args[_PATH_ARG]:
                record.args = (*args[:_PATH_ARG], redacted, *args[_PATH_ARG + 1 :])
        return True


def install_webhook_access_log_filter() -> None:
    """Attach the filter to the uvicorn access logger once.

    A logger-level filter sees every record uvicorn emits on that logger,
    whichever handlers a ``log_config`` attached to it.
    """
    access_logger = logging.getLogger(ACCESS_LOGGER_NAME)
    if not any(isinstance(existing, WebhookTokenAccessLogFilter) for existing in access_logger.filters):
        access_logger.addFilter(WebhookTokenAccessLogFilter())
