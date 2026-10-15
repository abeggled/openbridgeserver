"""Serve the api_client node's streamed requests from request-style fakes (#530).

The node reads responses through ``client.stream(...)`` so it can stop at the
size limit. Most manager tests fake ``client.request`` with an object carrying
``status_code`` and ``text``; :func:`serve_via_request` routes ``client.stream``
through that fake, so those tests keep describing responses the simple way.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from typing import Any


class _StreamedFake:
    def __init__(self, fake: Any) -> None:
        self.status_code = fake.status_code
        self.headers: dict[str, str] = {}
        self.encoding = "utf-8"
        self._body = str(fake.text).encode("utf-8")

    async def aiter_bytes(self):
        yield self._body


def serve_via_request(client: Any) -> None:
    """Make ``client.stream`` answer with whatever ``client.request`` returns or raises."""

    @asynccontextmanager
    async def _stream(method: str, url: str, **kwargs: Any):
        yield _StreamedFake(await client.request(method, url, **kwargs))

    client.stream = _stream
