"""Integration Test Fixtures — Ebene 2

Session-scoped setup:
  1. mosquitto  — Eclipse Mosquitto in Docker (anonymous, port 18830)
  2. app        — FastAPI app with lifespan, SQLite :memory:, test MQTT port
  3. client     — httpx.AsyncClient via ASGITransport
  4. auth_headers — Bearer token from admin/admin login

Requirements (install alongside regular deps):
  pip install pytest-asyncio asgi-lifespan httpx
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import time

import pytest
import pytest_asyncio
from asgi_lifespan import LifespanManager
from httpx import ASGITransport, AsyncClient

# ---------------------------------------------------------------------------
# Mosquitto Docker fixture (sync — subprocess only)
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def mosquitto_port():
    """Start eclipse-mosquitto in Docker with anonymous access enabled.
    Yields the host port (18830). Stops/removes the container on teardown.
    """
    port = 18830

    # Write a minimal mosquitto config that allows anonymous connections
    cfg = tempfile.NamedTemporaryFile(mode="w", suffix=".conf", delete=False, prefix="obs_test_mosquitto_")
    cfg.write("listener 1883\nallow_anonymous true\n")
    cfg.flush()
    cfg.close()

    cid = (
        subprocess.check_output(
            [
                "docker",
                "run",
                "-d",
                "-p",
                f"{port}:1883",
                "-v",
                f"{cfg.name}:/mosquitto/config/mosquitto.conf:ro",
                "eclipse-mosquitto",
            ],
        )
        .decode()
        .strip()
    )

    # Give the broker a moment to start accepting connections
    time.sleep(1.5)

    yield port

    subprocess.run(["docker", "stop", cid], check=False, capture_output=True)
    subprocess.run(["docker", "rm", cid], check=False, capture_output=True)
    try:
        os.unlink(cfg.name)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# App + Client (async session fixtures)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture(scope="session")
async def app(mosquitto_port):
    """Create the FastAPI app with:
      - SQLite :memory: (fresh for this test session)
      - MQTT pointing at the test Mosquitto container
      - JWT secret long enough to pass validation
      - Mosquitto passwd file in /tmp (no reload needed in tests)

    Lifespan is managed by asgi_lifespan.LifespanManager so startup/shutdown
    hooks run exactly once for the whole test session.
    """
    from obs.config import (
        DatabaseSettings,
        MosquittoSettings,
        MqttSettings,
        RingBufferSettings,
        SecuritySettings,
        Settings,
        override_settings,
    )

    override_settings(
        Settings(
            database=DatabaseSettings(
                path=":memory:",
                history_plugin="sqlite",
            ),
            mqtt=MqttSettings(
                host="localhost",
                port=mosquitto_port,
                username=None,
                password=None,
            ),
            security=SecuritySettings(
                jwt_secret="integration-test-secret-32-chars-xx",
                jwt_expire_minutes=60,
            ),
            mosquitto=MosquittoSettings(
                passwd_file="/tmp/obs_integration_test_passwd",
                reload_pid=None,
                reload_command=None,
                service_username="obs",
                service_password="test",
            ),
            ringbuffer=RingBufferSettings(
                storage="memory",
                max_entries=1000,
            ),
        ),
    )

    from obs.main import create_app

    _app = create_app()

    async with LifespanManager(_app):
        yield _app

    # Reset all singletons so the test session ends in a clean state.
    # Each import is attempted individually so that a missing reset helper
    # (e.g. on a server whose code is slightly behind) does not abort the
    # teardown and cause a noisy ERROR report for every other test.
    _reset_targets = [
        ("obs.api.v1.websocket", "reset_ws_manager"),
        ("obs.core.write_router", "reset_write_router"),
        ("obs.core.mqtt_client", "reset_mqtt_client"),
        ("obs.history.factory", "reset_history_plugin"),
        ("obs.ringbuffer.ringbuffer", "reset_ringbuffer"),
        ("obs.core.registry", "reset_registry"),
        ("obs.core.event_bus", "reset_event_bus"),
        ("obs.db.database", "reset_db"),
    ]
    for module_path, fn_name in _reset_targets:
        try:
            import importlib

            mod = importlib.import_module(module_path)
            getattr(mod, fn_name)()
        except Exception:
            pass  # best-effort — never fail teardown


@pytest_asyncio.fixture(scope="session")
async def client(app):
    """httpx.AsyncClient wired to the in-process FastAPI app."""
    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    ) as c:
        yield c


@pytest_asyncio.fixture(scope="session")
async def auth_headers(client):
    """Login once as admin and return the Authorization header dict."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "admin"},
    )
    assert resp.status_code == 200, f"Login failed: {resp.text}"
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Response shape assertion helpers
# ---------------------------------------------------------------------------
# These catch regressions where a FastAPI/Pydantic upgrade silently drops or
# renames a field from a serialized response model.


_DATAPOINT_OUT_FIELDS = {
    "id",
    "name",
    "data_type",
    "unit",
    "tags",
    "mqtt_topic",
    "mqtt_alias",
    "persist_value",
    "record_history",
    "created_at",
    "updated_at",
    "value",
    "quality",
}

_DATAPOINT_PAGE_FIELDS = {"items", "total", "page", "size", "pages"}

_VALUE_OUT_FIELDS = {"id", "value", "unit", "quality", "ts"}

_AUTH_TOKEN_FIELDS = {"access_token", "refresh_token", "token_type"}


def assert_datapoint_shape(body: dict) -> None:
    """Assert that a DataPointOut response body contains all expected fields."""
    missing = _DATAPOINT_OUT_FIELDS - set(body.keys())
    assert not missing, (
        f"DataPointOut response is missing fields: {missing}. A FastAPI or Pydantic upgrade may have changed the serialization of DataPointOut."
    )


def assert_datapoint_page_shape(body: dict) -> None:
    """Assert that a DataPointPage response body contains all expected fields."""
    missing = _DATAPOINT_PAGE_FIELDS - set(body.keys())
    assert not missing, (
        f"DataPointPage response is missing fields: {missing}. A FastAPI or Pydantic upgrade may have changed the serialization of DataPointPage."
    )


def assert_value_out_shape(body: dict) -> None:
    """Assert that a ValueOut response body contains all expected fields."""
    missing = _VALUE_OUT_FIELDS - set(body.keys())
    assert not missing, (
        f"ValueOut response is missing fields: {missing}. A FastAPI or Pydantic upgrade may have changed the serialization of ValueOut."
    )


def assert_auth_token_shape(body: dict) -> None:
    """Assert that a login response body contains all expected token fields."""
    missing = _AUTH_TOKEN_FIELDS - set(body.keys())
    assert not missing, (
        f"Auth token response is missing fields: {missing}. A FastAPI or Pydantic upgrade may have changed the serialization of the login response."
    )
