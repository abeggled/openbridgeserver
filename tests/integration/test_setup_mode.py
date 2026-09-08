"""Setup mode against the real app (#1229) — what the HTTP surface still answers.

The session app is a configured installation (its owner is seeded in conftest),
so setup mode is switched on around each test here: that is exactly the state a
fresh Docker or LXC installation boots into, and it lets the middleware be
exercised without a second app instance fighting over the global singletons.
"""

from __future__ import annotations

import pytest

from obs.api.setup import set_setup_required, setup_required


@pytest.fixture
def in_setup_mode():
    previous = setup_required()
    set_setup_required(True)
    yield
    set_setup_required(previous)


async def test_setup_status_is_public_and_reports_the_mode(client, in_setup_mode):
    resp = await client.get("/api/v1/setup/status")

    assert resp.status_code == 200
    assert resp.json() == {"setup_required": True}


async def test_health_stays_reachable(client, in_setup_mode):
    """The container health check must not report unhealthy while awaiting setup."""
    resp = await client.get("/api/v1/system/health")

    assert resp.status_code == 200


@pytest.mark.parametrize(
    "path",
    ["/api/v1/datapoints", "/api/v1/adapters/", "/api/v1/system/info", "/api/v1/auth/users"],
)
async def test_api_is_refused_while_awaiting_setup(client, in_setup_mode, path):
    resp = await client.get(path)

    assert resp.status_code == 503
    assert resp.json()["setup_required"] is True


async def test_login_is_refused_while_awaiting_setup(client, in_setup_mode):
    """No effective login: the credentials of an existing user must not work either."""
    resp = await client.post(
        "/api/v1/auth/login",
        json={"username": "admin", "password": "integration-test-password"},
    )

    assert resp.status_code == 503


@pytest.mark.parametrize("path", ["/", "/login", "/datapoints", "/visu/"])
async def test_browser_navigation_is_sent_to_the_setup_page(client, in_setup_mode, path):
    resp = await client.get(path)

    assert resp.status_code == 303
    assert resp.headers["location"] == "/setup"


async def test_cors_preflight_still_gets_an_answer(client, in_setup_mode):
    """The gate is outermost, so it has to let the CORS layer answer preflights."""
    resp = await client.options(
        "/api/v1/datapoints/",
        headers={"Origin": "http://localhost:5173", "Access-Control-Request-Method": "GET"},
    )

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"


async def test_setup_page_itself_is_not_redirected(client, in_setup_mode):
    resp = await client.get("/setup")

    assert resp.status_code != 303


async def test_claiming_a_configured_installation_is_refused(client, in_setup_mode):
    """A stale flag must not let anyone add a second owner to a live installation."""
    resp = await client.post(
        "/api/v1/setup/owner",
        json={"username": "intruder", "password": "an-intruders-password"},
    )

    assert resp.status_code == 409
    assert setup_required() is False


async def test_normal_operation_once_setup_is_done(client):
    """Outside setup mode nothing is intercepted — auth decides again, not the gate."""
    status_resp = await client.get("/api/v1/setup/status")
    unauthenticated = await client.get("/api/v1/datapoints/")

    assert status_resp.json() == {"setup_required": False}
    assert unauthenticated.status_code in (401, 403)
