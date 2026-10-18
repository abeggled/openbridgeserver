"""The application startup installs the webhook access-log filter (issue #1305)."""

from __future__ import annotations

import logging

import pytest

from obs.adapters.webhook.access_log import WebhookTokenAccessLogFilter

pytestmark = pytest.mark.integration


async def test_startup_installs_the_webhook_token_filter_on_the_access_logger(app):
    filters = logging.getLogger("uvicorn.access").filters
    assert sum(isinstance(existing, WebhookTokenAccessLogFilter) for existing in filters) == 1
