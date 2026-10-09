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


async def test_webhook_trigger_is_refused_while_awaiting_setup(client, auth_headers, in_setup_mode):
    """An unclaimed installation exposes no entry point, token or not (#1256).

    The webhook gate is registered before the setup gate, so the setup gate
    stays the outermost middleware and sees the request first. Pinned here
    because reversing that registration order would silently open a write path
    on an installation that has no owner yet.
    """
    import uuid as _uuid

    # A running WEBHOOK instance claims its path prefix process-wide, so it has
    # to be removed even when an assertion below fails — otherwise every later
    # webhook test in the session would hit a prefix conflict instead of its
    # own instance.
    instance_id = None
    set_setup_required(False)
    try:
        dp = await client.post(
            "/api/v1/datapoints/",
            json={"name": f"SetupHook-{_uuid.uuid4().hex[:8]}", "data_type": "BOOLEAN"},
            headers=auth_headers,
        )
        assert dp.status_code == 201, dp.text
        instance = await client.post(
            "/api/v1/adapters/instances",
            json={"adapter_type": "WEBHOOK", "name": f"SetupHook-{_uuid.uuid4().hex[:6]}", "config": {}, "enabled": True},
            headers=auth_headers,
        )
        assert instance.status_code == 201, instance.text
        instance_id = instance.json()["id"]
        binding = await client.post(
            f"/api/v1/datapoints/{dp.json()['id']}/bindings",
            json={"adapter_instance_id": instance_id, "direction": "SOURCE", "config": {"slug": "setup-gate-bell"}},
            headers=auth_headers,
        )
        assert binding.status_code == 201, binding.text
        overview = await client.get(f"/api/v1/adapters/instances/{instance_id}/webhook/bindings", headers=auth_headers)
        assert overview.status_code == 200, overview.text
        call_path = overview.json()["bindings"][0]["call_path"]

        set_setup_required(True)
        blocked = await client.get(call_path)
        assert blocked.status_code == 303
        assert blocked.headers["location"] == "/setup"

        set_setup_required(False)
        assert (await client.get(call_path)).status_code == 204
    finally:
        set_setup_required(False)
        if instance_id is not None:
            await client.delete(f"/api/v1/adapters/instances/{instance_id}", headers=auth_headers)
        set_setup_required(True)


async def test_normal_operation_once_setup_is_done(client):
    """Outside setup mode nothing is intercepted — auth decides again, not the gate."""
    status_resp = await client.get("/api/v1/setup/status")
    unauthenticated = await client.get("/api/v1/datapoints/")

    assert status_resp.json() == {"setup_required": False}
    assert unauthenticated.status_code in (401, 403)
