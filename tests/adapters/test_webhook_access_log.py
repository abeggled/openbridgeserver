"""Webhook tokens must not reach the uvicorn access log (issue #1305).

A webhook URL carries its credential either as ``?token=…`` or as the path
segment after the slug.  uvicorn logs every request line to stdout (journal,
``docker logs``), so the access-log filter replaces both forms with
``[redacted]`` while keeping the caller address, method, status and slug.
"""

from __future__ import annotations

import asyncio
import logging
import uuid

import httpx
import pytest
import uvicorn
from uvicorn.logging import AccessFormatter

from obs.adapters.webhook import adapter as webhook_module
from obs.adapters.webhook.access_log import (
    WebhookTokenAccessLogFilter,
    install_webhook_access_log_filter,
    redact_access_path,
)
from obs.adapters.webhook.adapter import WebhookAdapter, generate_token
from obs.api.v1.redaction import REDACTED

TOKEN = generate_token()
# uvicorn's own request-line format (uvicorn/protocols/http/*_impl.py).
UVICORN_ACCESS_MSG = '%s - "%s %s HTTP/%s" %d'


@pytest.fixture(autouse=True)
def _clean_dispatch_registry():
    webhook_module._instances_by_prefix.clear()
    yield
    webhook_module._instances_by_prefix.clear()


def _claim(prefix: str) -> None:
    assert webhook_module._register_prefix(prefix, WebhookAdapter(event_bus=None))


# ---------------------------------------------------------------------------
# Query variant
# ---------------------------------------------------------------------------


def test_query_token_is_redacted_below_an_active_prefix():
    _claim("/hook")
    assert redact_access_path(f"/hook/bell?token={TOKEN}") == f"/hook/bell?token={REDACTED}"


def test_query_token_is_redacted_without_any_running_instance():
    """A stopped or disabled instance must not turn the token back into clear text."""
    assert redact_access_path(f"/hook/bell?token={TOKEN}") == f"/hook/bell?token={REDACTED}"


def test_other_query_parameters_are_kept():
    _claim("/hook")
    assert redact_access_path(f"/hook/bell?value=21.5&token={TOKEN}&x=1") == f"/hook/bell?value=21.5&token={REDACTED}&x=1"


def test_percent_encoded_and_repeated_token_keys_are_redacted():
    """Starlette decodes ``%74oken`` to ``token``, so the filter must too."""
    redacted = redact_access_path(f"/hook/bell?%74oken={TOKEN}&token={TOKEN}")
    assert TOKEN not in redacted
    assert redacted == f"/hook/bell?%74oken={REDACTED}&token={REDACTED}"


# ---------------------------------------------------------------------------
# Path variant
# ---------------------------------------------------------------------------


def test_path_token_is_redacted_below_an_active_prefix():
    _claim("/hook")
    assert redact_access_path(f"/hook/bell/{TOKEN}") == f"/hook/bell/{REDACTED}"


def test_path_token_of_any_shape_is_redacted_below_a_multi_segment_prefix():
    """Below a claimed prefix the segment after the slug is the credential, whatever it looks like."""
    _claim("/home/hooks")
    assert redact_access_path("/home/hooks/bell/short-or-mistyped") == f"/home/hooks/bell/{REDACTED}"
    assert redact_access_path("/home/hooks//bell//abc/def") == f"/home/hooks//bell//{REDACTED}/{REDACTED}"


def test_path_token_is_redacted_without_any_running_instance():
    """Without a claim a trailing segment after a slug is treated as a credential."""
    assert redact_access_path(f"/hook/bell/{TOKEN}") == f"/hook/bell/{REDACTED}"
    assert redact_access_path(f"/home/hooks/bell/{TOKEN}?token={TOKEN}") == f"/home/hooks/bell/{REDACTED}?token={REDACTED}"


def test_slug_only_calls_under_an_active_prefix_are_unchanged():
    _claim("/hook")
    assert redact_access_path("/hook/bell") == "/hook/bell"
    assert redact_access_path("/hook/bell?value=1") == "/hook/bell?value=1"
    webhook_module._instances_by_prefix.clear()
    # Without a running instance this may also be a token URL below /home.
    assert redact_access_path("/home/hooks/bell") == f"/home/hooks/{REDACTED}"


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "",
        f"/api/v1/datapoints/{uuid.uuid4()}",
        f"/api/v1/something/{TOKEN}",
        "/assets/index-DdW0x9Qz.js",
        "/visu/page/abc?lang=de",
        "/datapoints",
        f"/hook/{TOKEN}",
    ],
)
def test_other_paths_are_unchanged(path):
    _claim("/elsewhere")
    assert redact_access_path(path) == path


# ---------------------------------------------------------------------------
# The logging filter on real uvicorn access records
# ---------------------------------------------------------------------------


def _access_record(path: str) -> logging.LogRecord:
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg=UVICORN_ACCESS_MSG,
        args=("192.168.1.50:51234", "GET", path, "1.1", 204),
        exc_info=None,
    )


def test_filter_rewrites_the_path_argument_of_an_access_record():
    _claim("/hook")
    record = _access_record(f"/hook/bell/{TOKEN}")

    assert WebhookTokenAccessLogFilter().filter(record) is True

    line = AccessFormatter(fmt='%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(record)
    assert TOKEN not in line
    assert line == f'192.168.1.50:51234 - "GET /hook/bell/{REDACTED} HTTP/1.1" 204 No Content'


def test_filter_masks_imported_token_when_instance_is_disabled():
    record = _access_record("/inactive-import/bell/legacy-imported-secret")
    record.args = (*record.args[:-1], 404)

    assert WebhookTokenAccessLogFilter().filter(record) is True
    line = AccessFormatter(fmt='%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False).format(record)
    assert line == f'192.168.1.50:51234 - "GET /inactive-import/bell/{REDACTED} HTTP/1.1" 404 Not Found'


def test_filter_leaves_unrelated_records_alone():
    record = _access_record("/api/v1/system/health")
    WebhookTokenAccessLogFilter().filter(record)
    assert record.args == ("192.168.1.50:51234", "GET", "/api/v1/system/health", "1.1", 204)

    odd = logging.LogRecord("uvicorn.access", logging.INFO, __file__, 1, "plain message", None, None)
    assert WebhookTokenAccessLogFilter().filter(odd) is True
    assert odd.getMessage() == "plain message"


def test_install_is_idempotent():
    access_logger = logging.getLogger("uvicorn.access")
    before = list(access_logger.filters)
    try:
        install_webhook_access_log_filter()
        install_webhook_access_log_filter()
        installed = [f for f in access_logger.filters if isinstance(f, WebhookTokenAccessLogFilter)]
        assert len(installed) == 1
    finally:
        access_logger.filters[:] = before


class _ListHandler(logging.Handler):
    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


async def test_real_uvicorn_access_log_lines_carry_no_token():
    """End to end: a real uvicorn server writes its own access records through the filter."""
    _claim("/hook")

    async def app(scope, receive, send):
        assert scope["type"] == "http"
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    access_logger = logging.getLogger("uvicorn.access")
    saved_filters, saved_handlers, saved_level, saved_propagate = (
        list(access_logger.filters),
        list(access_logger.handlers),
        access_logger.level,
        access_logger.propagate,
    )
    handler = _ListHandler()
    handler.setFormatter(AccessFormatter(fmt='%(client_addr)s - "%(request_line)s" %(status_code)s', use_colors=False))
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_config=None, log_level="info", lifespan="off"))
    try:
        access_logger.addHandler(handler)
        access_logger.propagate = False
        install_webhook_access_log_filter()
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.01)
        port = server.servers[0].sockets[0].getsockname()[1]
        async with httpx.AsyncClient(base_url=f"http://127.0.0.1:{port}") as client:
            assert (await client.get(f"/hook/bell?token={TOKEN}")).status_code == 204
            assert (await client.get(f"/hook/bell/{TOKEN}")).status_code == 204
            assert (await client.post("/api/v1/system/health")).status_code == 204
        server.should_exit = True
        await task
    finally:
        access_logger.filters[:] = saved_filters
        access_logger.handlers[:] = saved_handlers
        access_logger.setLevel(saved_level)
        access_logger.propagate = saved_propagate

    assert len(handler.lines) == 3
    assert all(TOKEN not in line for line in handler.lines)
    assert handler.lines[0].startswith("127.0.0.1:")
    assert handler.lines[0].endswith(f'"GET /hook/bell?token={REDACTED} HTTP/1.1" 204 No Content')
    assert handler.lines[1].endswith(f'"GET /hook/bell/{REDACTED} HTTP/1.1" 204 No Content')
    assert handler.lines[2].endswith('"POST /api/v1/system/health HTTP/1.1" 204 No Content')
