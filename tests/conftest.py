"""Top-level pytest configuration shared by all test suites.

Provides autouse fixtures that prevent test-state leakage across the full
``pytest tests/`` run, regardless of which test files or suites are collected.
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _restore_settings():
    """Capture the obs.config singleton before each test and restore it afterwards.

    Tests that call override_settings() without teardown leave _settings pointing
    at a minimal test object.  On Python 3.14 (CI, no config.yaml) this succeeds
    silently and the leaked singleton corrupts subsequent tests — in particular
    test_api_v1_route_classification_registry which ends up seeing an empty router
    because later imports resolve settings differently.
    """
    import obs.config

    saved = obs.config._settings
    yield
    obs.config._settings = saved
