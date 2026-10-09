"""Integration tests for the WEBHOOK adapter (issue #1256).

Exercises the real supported entry points end to end against the live FastAPI
app: the device-facing trigger URL below the instance path prefix, the two
management routes under ``/api/v1/adapters/instances/...``, and the webhook
rules the generic binding routes enforce.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime

import pytest

pytestmark = pytest.mark.integration

_MISSING_ID = "00000000-0000-0000-0000-000000000000"


async def _create_dp(client, auth_headers, *, data_type: str = "BOOLEAN", control_class: str = "room_local") -> dict:
    resp = await client.post(
        "/api/v1/datapoints/",
        json={
            "name": f"WebhookTest-{uuid.uuid4().hex[:8]}",
            "data_type": data_type,
            "control_class": control_class,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_instance(client, auth_headers, *, config: dict | None = None, enabled: bool = True) -> dict:
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "WEBHOOK",
            "name": f"Hook-{uuid.uuid4().hex[:6]}",
            "config": config or {},
            "enabled": enabled,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _create_binding(client, auth_headers, dp_id: str, instance_id: str, config: dict) -> dict:
    resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/bindings",
        json={"adapter_instance_id": instance_id, "direction": "SOURCE", "config": config},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


async def _dp_value(client, auth_headers, dp_id: str):
    resp = await client.get(f"/api/v1/datapoints/{dp_id}/value", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()["value"]


async def _delete_instance(client, auth_headers, instance_id: str) -> None:
    resp = await client.delete(f"/api/v1/adapters/instances/{instance_id}", headers=auth_headers)
    assert resp.status_code in (200, 204), resp.text


async def _webhook_overview(client, auth_headers, instance_id: str) -> dict:
    resp = await client.get(f"/api/v1/adapters/instances/{instance_id}/webhook/bindings", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _webhook_bindings(client, auth_headers, instance_id: str) -> list[dict]:
    return (await _webhook_overview(client, auth_headers, instance_id))["bindings"]


# ---------------------------------------------------------------------------
# Binding lifecycle
# ---------------------------------------------------------------------------


async def test_create_binding_generates_a_token_and_redacts_it_in_the_generic_listing(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        created = await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "bell-one"})
        assert created["config"]["token"] == "[redacted]"

        listed = await client.get(f"/api/v1/datapoints/{dp['id']}/bindings", headers=auth_headers)
        assert listed.status_code == 200, listed.text
        assert listed.json()[0]["config"]["token"] == "[redacted]"

        entries = await _webhook_bindings(client, auth_headers, instance["id"])
        assert len(entries) == 1
        entry = entries[0]
        assert entry["slug"] == "bell-one"
        assert len(entry["token"]) >= 40
        assert entry["call_path"] == f"/hook/bell-one?token={entry['token']}"
        assert entry["call_path_token_in_path"] == f"/hook/bell-one/{entry['token']}"
        assert entry["datapoint_id"] == dp["id"]
        assert entry["datapoint_name"] == dp["name"]
        assert entry["methods"] == ["GET"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_a_client_supplied_token_is_ignored_on_create_and_update(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        created = await _create_binding(
            client,
            auth_headers,
            dp["id"],
            instance["id"],
            {"slug": "bell-two", "token": "attacker-chosen"},
        )
        issued = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]["token"]
        assert issued != "attacker-chosen"

        patched = await client.patch(
            f"/api/v1/datapoints/{dp['id']}/bindings/{created['id']}",
            json={"config": {"slug": "bell-two", "token": "attacker-chosen", "debounce_ms": 500}},
            headers=auth_headers,
        )
        assert patched.status_code == 200, patched.text
        after = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert after["token"] == issued
        assert after["debounce_ms"] == 500
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_webhook_bindings_must_be_source(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        resp = await client.post(
            f"/api/v1/datapoints/{dp['id']}/bindings",
            json={"adapter_instance_id": instance["id"], "direction": "DEST", "config": {"slug": "bell-three"}},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
        assert "SOURCE" in resp.json()["detail"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_central_plant_datapoints_are_refused(client, auth_headers):
    dp = await _create_dp(client, auth_headers, control_class="central_plant")
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        resp = await client.post(
            f"/api/v1/datapoints/{dp['id']}/bindings",
            json={"adapter_instance_id": instance["id"], "direction": "SOURCE", "config": {"slug": "plant-hook"}},
            headers=auth_headers,
        )
        assert resp.status_code == 403, resp.text
        assert "central_plant" in resp.json()["detail"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_duplicate_slug_in_one_instance_is_refused(client, auth_headers):
    first_dp = await _create_dp(client, auth_headers)
    second_dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        await _create_binding(client, auth_headers, first_dp["id"], instance["id"], {"slug": "shared-slug"})
        resp = await client.post(
            f"/api/v1/datapoints/{second_dp['id']}/bindings",
            json={"adapter_instance_id": instance["id"], "direction": "SOURCE", "config": {"slug": "shared-slug"}},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
        assert "shared-slug" in resp.json()["detail"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_patching_a_binding_onto_an_existing_slug_is_refused(client, auth_headers):
    first_dp = await _create_dp(client, auth_headers)
    second_dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        await _create_binding(client, auth_headers, first_dp["id"], instance["id"], {"slug": "taken-slug"})
        other = await _create_binding(client, auth_headers, second_dp["id"], instance["id"], {"slug": "free-slug"})

        collide = await client.patch(
            f"/api/v1/datapoints/{second_dp['id']}/bindings/{other['id']}",
            json={"config": {"slug": "taken-slug"}},
            headers=auth_headers,
        )
        assert collide.status_code == 422, collide.text

        keep = await client.patch(
            f"/api/v1/datapoints/{second_dp['id']}/bindings/{other['id']}",
            json={"config": {"slug": "free-slug", "debounce_ms": 10}},
            headers=auth_headers,
        )
        assert keep.status_code == 200, keep.text
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_invalid_binding_config_is_rejected(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        resp = await client.post(
            f"/api/v1/datapoints/{dp['id']}/bindings",
            json={"adapter_instance_id": instance["id"], "direction": "SOURCE", "config": {"slug": "not a slug"}},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


# ---------------------------------------------------------------------------
# Instance configuration
# ---------------------------------------------------------------------------


async def test_instance_rejects_a_reserved_path_prefix(client, auth_headers):
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "WEBHOOK",
            "name": f"Hook-{uuid.uuid4().hex[:6]}",
            "config": {"path_prefix": "/api"},
            "enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_binding_schema_is_served_for_the_new_adapter_type(client, auth_headers):
    resp = await client.get("/api/v1/adapters/WEBHOOK/binding-schema", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    binding_properties = set(resp.json()["properties"])
    assert binding_properties >= {"slug", "token", "methods", "allowed_networks", "value_source", "fixed_value", "value_param", "debounce_ms"}

    schema = await client.get("/api/v1/adapters/WEBHOOK/schema", headers=auth_headers)
    assert schema.status_code == 200, schema.text
    instance_properties = set(schema.json()["properties"])
    assert instance_properties == {"path_prefix", "trust_forwarded_for", "rate_limit_per_minute"}
    # The allowlist belongs to the binding; the instance must not offer one.
    assert "allowed_networks" not in instance_properties


# ---------------------------------------------------------------------------
# Trigger endpoint — the device-facing URL
# ---------------------------------------------------------------------------


async def test_get_trigger_sets_the_datapoint_value(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, config={"path_prefix": "/hook"})
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "doorbell"})
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        resp = await client.get(entry["call_path"])
        assert resp.status_code == 204, resp.text

        value = await client.get(f"/api/v1/datapoints/{dp['id']}/value", headers=auth_headers)
        assert value.status_code == 200, value.text
        assert value.json()["value"] is True

        stats = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert stats["call_count"] == 1
        assert stats["publish_count"] == 1
        assert stats["last_status"] == 204
        assert stats["last_called"] is not None
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_token_in_path_variant_works(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "bell-path"})
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        resp = await client.get(entry["call_path_token_in_path"])
        assert resp.status_code == 204, resp.text
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_post_with_a_json_body_sets_the_value(client, auth_headers):
    dp = await _create_dp(client, auth_headers, data_type="INTEGER")
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(
            client,
            auth_headers,
            dp["id"],
            instance["id"],
            {"slug": "bell-post", "methods": ["POST"], "value_source": "request"},
        )
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        resp = await client.post(f"/hook/bell-post?token={entry['token']}", json={"value": 42})
        assert resp.status_code == 204, resp.text

        value = await client.get(f"/api/v1/datapoints/{dp['id']}/value", headers=auth_headers)
        assert value.json()["value"] == 42
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_wrong_token_and_unknown_slug_both_return_json_404(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "bell-404"})

        wrong = await client.get("/hook/bell-404?token=nope")
        unknown = await client.get("/hook/does-not-exist?token=nope")

        for resp in (wrong, unknown):
            assert resp.status_code == 404
            assert resp.json() == {"detail": "Not found"}
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_unsupported_method_on_a_claimed_prefix_returns_404(client, auth_headers):
    instance = await _create_instance(client, auth_headers)
    try:
        resp = await client.put("/hook/anything")
        assert resp.status_code == 404
        assert resp.json() == {"detail": "Not found"}
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_options_preflight_passes_through_to_the_cors_layer(client, auth_headers):
    """The webhook gate must not swallow OPTIONS, or CORS preflights break."""
    instance = await _create_instance(client, auth_headers)
    try:
        resp = await client.options(
            "/hook/anything",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.headers["access-control-allow-origin"] == "http://localhost:5173"
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_custom_path_prefix_is_honoured(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, config={"path_prefix": "/iot/trigger"})
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "bell-prefix"})
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert entry["call_path"].startswith("/iot/trigger/bell-prefix?token=")

        # The default prefix is not claimed by this instance, so the call never
        # reaches the adapter.  It falls through to the normal request stack,
        # which answers an unknown non-API path with the Admin-GUI shell — what
        # matters is that no value was published.
        await client.get(f"/hook/bell-prefix?token={entry['token']}")
        assert await _dp_value(client, auth_headers, dp["id"]) is None
        assert (await _webhook_bindings(client, auth_headers, instance["id"]))[0]["call_count"] == 0

        assert (await client.get(entry["call_path"])).status_code == 204
        assert await _dp_value(client, auth_headers, dp["id"]) is True
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_allowlist_blocks_the_test_client(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(
            client,
            auth_headers,
            dp["id"],
            instance["id"],
            {"slug": "bell-binding-allowlist", "allowed_networks": ["203.0.113.0/24"]},
        )
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert entry["allowed_networks"] == ["203.0.113.0/24"]

        resp = await client.get(entry["call_path"])

        assert resp.status_code == 404
        assert await _dp_value(client, auth_headers, dp["id"]) is None
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_a_binding_allowlist_that_covers_the_caller_lets_it_through(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(
            client,
            auth_headers,
            dp["id"],
            instance["id"],
            {"slug": "bell-allowed", "allowed_networks": ["127.0.0.0/8", "::1"]},
        )
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        assert (await client.get(entry["call_path"])).status_code == 204
        assert await _dp_value(client, auth_headers, dp["id"]) is True
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_autoreset_turns_the_webhook_into_a_trigger(client, auth_headers):
    """One call, two values on the bus — so the next press is a fresh edge."""
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(
            client,
            auth_headers,
            dp["id"],
            instance["id"],
            {"slug": "bell-trigger", "autoreset": True, "autoreset_value": "false", "autoreset_delay_ms": 0},
        )
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert entry["autoreset"] is True
        assert entry["autoreset_value"] == "false"
        assert entry["autoreset_delay_ms"] == 0

        assert (await client.get(entry["call_path"])).status_code == 204

        # The reset runs on its own task; give the loop a turn to finish it.
        for _ in range(50):
            if await _dp_value(client, auth_headers, dp["id"]) is False:
                break
            await asyncio.sleep(0.01)

        assert await _dp_value(client, auth_headers, dp["id"]) is False
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_autoreset_is_off_by_default(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "bell-no-reset"})
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert entry["autoreset"] is False

        assert (await client.get(entry["call_path"])).status_code == 204
        await asyncio.sleep(0.05)
        assert await _dp_value(client, auth_headers, dp["id"]) is True
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_rejections_are_reported_with_reason_and_address(client, auth_headers):
    """The diagnostics that turn an opaque 404 into something actionable."""
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(
            client,
            auth_headers,
            dp["id"],
            instance["id"],
            {"slug": "bell-diag", "allowed_networks": ["203.0.113.0/24"]},
        )
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        assert (await client.get(entry["call_path"])).status_code == 404

        overview = await _webhook_overview(client, auth_headers, instance["id"])
        assert "allowed_networks" not in overview
        assert overview["running"] is True
        rejections = overview["rejections"]
        assert rejections["total"] == 1
        assert rejections["counts"] == {"address_blocked": 1}
        assert rejections["last_reason"] == "address_blocked"
        assert rejections["last_client_ip"]
        assert rejections["last_at"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_a_rejection_is_reported_on_the_binding_that_caused_it(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(
            client,
            auth_headers,
            dp["id"],
            instance["id"],
            {"slug": "bell-binding-diag", "allowed_networks": ["203.0.113.0/24"]},
        )
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert (await client.get(entry["call_path"])).status_code == 404

        after = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert after["rejections"]["counts"] == {"address_blocked": 1}
        assert after["rejections"]["last_reason"] == "address_blocked"
        assert after["last_status"] == 404
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_a_legacy_comma_separated_allowlist_still_loads(client, auth_headers):
    """A binding stored before the field became a list must keep working."""
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        await _create_binding(
            client,
            auth_headers,
            dp["id"],
            instance["id"],
            {"slug": "bell-legacy", "allowed_networks": "10.0.0.0/8, 192.168.1.5"},
        )
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert entry["allowed_networks"] == ["10.0.0.0/8", "192.168.1.5/32"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_rate_limit_answers_429(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, config={"rate_limit_per_minute": 1})
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "bell-rate"})
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        assert (await client.get(entry["call_path"])).status_code == 204
        limited = await client.get(entry["call_path"])
        assert limited.status_code == 429
        assert limited.json() == {"detail": "Too many requests"}
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_missing_request_value_answers_400(client, auth_headers):
    dp = await _create_dp(client, auth_headers, data_type="INTEGER")
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(
            client,
            auth_headers,
            dp["id"],
            instance["id"],
            {"slug": "bell-novalue", "value_source": "request"},
        )
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        resp = await client.get(entry["call_path"])
        assert resp.status_code == 400
        assert "value" in resp.json()["detail"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_a_disabled_instance_claims_no_prefix(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "bell-disabled"})
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        # The instance is not running, but its prefix is still configured: the
        # call is answered with the same 404 as any unavailable webhook (and not
        # with the Admin-GUI shell), and no value is published.
        resp = await client.get(entry["call_path"])
        assert (resp.status_code, resp.json()) == (404, {"detail": "Not found"})
        assert await _dp_value(client, auth_headers, dp["id"]) is None
        assert entry["call_count"] == 0
        assert entry["last_status"] is None
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


# ---------------------------------------------------------------------------
# Token rotation
# ---------------------------------------------------------------------------


async def test_rotating_a_token_revokes_the_old_url(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        binding = await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "bell-rotate"})
        old = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        rotated = await client.post(
            f"/api/v1/adapters/instances/{instance['id']}/webhook/bindings/{binding['id']}/rotate-token",
            headers=auth_headers,
        )
        assert rotated.status_code == 200, rotated.text
        body = rotated.json()
        assert body["slug"] == "bell-rotate"
        assert body["token"] != old["token"]
        assert body["call_path"] == f"/hook/bell-rotate?token={body['token']}"
        assert body["call_path_token_in_path"] == f"/hook/bell-rotate/{body['token']}"

        assert (await client.get(old["call_path"])).status_code == 404
        assert (await client.get(body["call_path"])).status_code == 204
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_rotation_writes_an_audit_entry_without_the_token(client, auth_headers):
    from obs.db.database import get_db

    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        binding = await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "bell-audit"})
        rotated = await client.post(
            f"/api/v1/adapters/instances/{instance['id']}/webhook/bindings/{binding['id']}/rotate-token",
            headers=auth_headers,
        )
        assert rotated.status_code == 200, rotated.text
        token = rotated.json()["token"]

        row = await get_db().fetchone(
            """
            SELECT actor, action, resource_type, resource_id, details_json
            FROM audit_log_entries
            WHERE action = 'adapter.webhook.token_rotated'
            ORDER BY id DESC
            LIMIT 1
            """
        )
        assert row is not None
        assert row["actor"] == "admin"
        assert row["resource_type"] == "binding"
        assert row["resource_id"] == binding["id"]
        assert token not in (row["details_json"] or "")
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_rotation_rejects_unknown_instances_and_bindings(client, auth_headers):
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        missing_instance = await client.post(
            f"/api/v1/adapters/instances/{_MISSING_ID}/webhook/bindings/{_MISSING_ID}/rotate-token",
            headers=auth_headers,
        )
        assert missing_instance.status_code == 404

        missing_binding = await client.post(
            f"/api/v1/adapters/instances/{instance['id']}/webhook/bindings/{_MISSING_ID}/rotate-token",
            headers=auth_headers,
        )
        assert missing_binding.status_code == 404
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_webhook_routes_reject_a_non_webhook_instance(client, auth_headers):
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "ANWESENHEITSSIMULATION",
            "name": f"NotHook-{uuid.uuid4().hex[:6]}",
            "config": {},
            "enabled": False,
        },
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    instance_id = resp.json()["id"]
    try:
        listing = await client.get(f"/api/v1/adapters/instances/{instance_id}/webhook/bindings", headers=auth_headers)
        assert listing.status_code == 400

        rotation = await client.post(
            f"/api/v1/adapters/instances/{instance_id}/webhook/bindings/{_MISSING_ID}/rotate-token",
            headers=auth_headers,
        )
        assert rotation.status_code == 400

        missing = await client.get(f"/api/v1/adapters/instances/{_MISSING_ID}/webhook/bindings", headers=auth_headers)
        assert missing.status_code == 404
    finally:
        await _delete_instance(client, auth_headers, instance_id)


async def test_webhook_management_routes_require_authentication(client):
    instance_id = _MISSING_ID
    assert (await client.get(f"/api/v1/adapters/instances/{instance_id}/webhook/bindings")).status_code == 401
    assert (await client.post(f"/api/v1/adapters/instances/{instance_id}/webhook/bindings/{_MISSING_ID}/rotate-token")).status_code == 401


# ---------------------------------------------------------------------------
# Review follow-ups
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["/docs", "/redoc", "/openapi.json", "/favicon.svg"])
async def test_instance_rejects_prefixes_that_would_shadow_application_surfaces(client, auth_headers, prefix):
    resp = await client.post(
        "/api/v1/adapters/instances",
        json={"adapter_type": "WEBHOOK", "name": f"Hook-{uuid.uuid4().hex[:6]}", "config": {"path_prefix": prefix}, "enabled": False},
        headers=auth_headers,
    )
    assert resp.status_code == 422, resp.text


async def test_an_oversized_post_is_cut_off_before_authentication(client, auth_headers):
    instance = await _create_instance(client, auth_headers)
    try:
        declared = await client.post("/hook/does-not-exist", content=b"x" * (64 * 1024 + 1))
        assert declared.status_code == 413
        assert declared.json() == {"detail": "Request body is too large"}

        async def chunks():
            for _ in range(65):
                yield b"x" * 1024

        undeclared = await client.post("/hook/does-not-exist", content=chunks())
        assert undeclared.status_code == 413

        small = await client.post("/hook/does-not-exist", content=b"{}")
        assert small.status_code == 404
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_a_binding_on_a_reclassified_datapoint_can_be_disabled_but_not_re_enabled(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        binding = await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "reclassified"})
        reclass = await client.patch(f"/api/v1/datapoints/{dp['id']}", json={"control_class": "central_plant"}, headers=auth_headers)
        assert reclass.status_code == 200, reclass.text

        off = await client.patch(f"/api/v1/datapoints/{dp['id']}/bindings/{binding['id']}", json={"enabled": False}, headers=auth_headers)
        assert off.status_code == 200, off.text

        on = await client.patch(f"/api/v1/datapoints/{dp['id']}/bindings/{binding['id']}", json={"enabled": True}, headers=auth_headers)
        assert on.status_code == 403, on.text
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_migration_refuses_a_slug_the_target_instance_already_owns(client, auth_headers):
    source_dp = await _create_dp(client, auth_headers)
    target_dp = await _create_dp(client, auth_headers)
    source = await _create_instance(client, auth_headers, enabled=False)
    target = await _create_instance(client, auth_headers, enabled=False)
    try:
        moving = await _create_binding(client, auth_headers, source_dp["id"], source["id"], {"slug": "bell"})
        await _create_binding(client, auth_headers, target_dp["id"], target["id"], {"slug": "bell"})

        resp = await client.post(
            f"/api/v1/adapters/instances/{source['id']}/bindings/migrate",
            json={"target_instance_id": target["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
        assert "bell" in resp.json()["detail"]

        still_there = await _webhook_bindings(client, auth_headers, source["id"])
        assert [entry["binding_id"] for entry in still_there] == [moving["id"]]
    finally:
        await _delete_instance(client, auth_headers, source["id"])
        await _delete_instance(client, auth_headers, target["id"])


async def test_migration_moves_webhook_bindings_with_distinct_slugs(client, auth_headers):
    source_dp = await _create_dp(client, auth_headers)
    target_dp = await _create_dp(client, auth_headers)
    source = await _create_instance(client, auth_headers, enabled=False)
    target = await _create_instance(client, auth_headers, enabled=False)
    try:
        moving = await _create_binding(client, auth_headers, source_dp["id"], source["id"], {"slug": "moves"})
        await _create_binding(client, auth_headers, target_dp["id"], target["id"], {"slug": "stays"})

        resp = await client.post(
            f"/api/v1/adapters/instances/{source['id']}/bindings/migrate",
            json={"target_instance_id": target["id"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["migrated"] == 1

        moved = await _webhook_bindings(client, auth_headers, target["id"])
        assert moving["id"] in [entry["binding_id"] for entry in moved]
    finally:
        await _delete_instance(client, auth_headers, source["id"])
        await _delete_instance(client, auth_headers, target["id"])


async def test_a_save_that_raced_a_rotation_does_not_restore_the_old_token(client, auth_headers, monkeypatch):
    from obs.api.v1 import bindings as bindings_api
    from obs.db.database import get_db

    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        binding = await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "racy"})
        rotated_token = "rotated-while-the-save-was-in-flight"
        original = bindings_api._ensure_webhook_slug_free

        async def rotate_then_check(db, *args, **kwargs):
            # Lands after the PATCH read the old token and before it writes.
            row = await db.fetchone("SELECT config FROM adapter_bindings WHERE id=?", (binding["id"],))
            config = json.loads(row["config"])
            config["token"] = rotated_token
            await db.execute_and_commit("UPDATE adapter_bindings SET config=? WHERE id=?", (json.dumps(config), binding["id"]))
            return await original(db, *args, **kwargs)

        monkeypatch.setattr(bindings_api, "_ensure_webhook_slug_free", rotate_then_check)
        resp = await client.patch(
            f"/api/v1/datapoints/{dp['id']}/bindings/{binding['id']}",
            json={"config": {"slug": "racy", "debounce_ms": 10}},
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text

        row = await get_db().fetchone("SELECT config FROM adapter_bindings WHERE id=?", (binding["id"],))
        assert json.loads(row["config"])["token"] == rotated_token
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_duplicating_a_datapoint_does_not_clone_its_webhook_binding(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "original"})

        resp = await client.post(f"/api/v1/datapoints/{dp['id']}/duplicate", json={"name": f"Copy-{uuid.uuid4().hex[:6]}"}, headers=auth_headers)
        assert resp.status_code == 201, resp.text

        bindings = await _webhook_bindings(client, auth_headers, instance["id"])
        assert [entry["slug"] for entry in bindings] == ["original"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_a_non_ascii_token_is_a_plain_404_for_an_existing_slug(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "unicode-bell"})

        known = await client.get("/hook/unicode-bell", params={"token": "é"})
        unknown = await client.get("/hook/no-such-slug", params={"token": "é"})

        assert (known.status_code, known.json()) == (404, {"detail": "Not found"})
        assert (unknown.status_code, unknown.json()) == (404, {"detail": "Not found"})
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_config_import_refuses_a_second_webhook_binding_with_the_same_slug(client, auth_headers):
    first_dp = await _create_dp(client, auth_headers)
    second_dp = await _create_dp(client, auth_headers)
    third_dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)

    def exported(dp_id: str, slug: str) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "datapoint_id": dp_id,
            "adapter_type": "WEBHOOK",
            "adapter_instance_id": instance["id"],
            "direction": "SOURCE",
            "config": {"slug": slug, "token": f"token-{slug}-{uuid.uuid4().hex}"},
            "enabled": True,
        }

    try:
        resp = await client.post(
            "/api/v1/config/import",
            json={
                "obs_version": "5",
                "exported_at": "2024-01-01T00:00:00",
                "datapoints": [],
                "bindings": [exported(first_dp["id"], "bell"), exported(second_dp["id"], "Bell"), exported(third_dp["id"], "gate")],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bindings_created"] == 2
        assert any("bell" in error for error in body["errors"] if error.startswith("Binding "))

        slugs = sorted(entry["slug"] for entry in await _webhook_bindings(client, auth_headers, instance["id"]))
        assert slugs == ["bell", "gate"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_a_disabled_instance_only_claims_its_own_prefix(client, auth_headers):
    instance = await _create_instance(client, auth_headers, config={"path_prefix": "/off-hook"}, enabled=False)
    try:
        claimed = await client.get("/off-hook/anything")
        assert (claimed.status_code, claimed.json()) == (404, {"detail": "Not found"})
        assert claimed.headers["cache-control"] == "no-store"

        # Whether the application answers an unknown path with the Admin-GUI shell
        # or its own JSON 404 depends on whether a GUI build is present, so tell
        # the two apart by the header only the webhook middleware sets.
        other = await client.get("/off-hookish")
        assert other.headers.get("cache-control") != "no-store"
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_config_import_stores_the_webhook_slug_normalised(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        resp = await client.post(
            "/api/v1/config/import",
            json={
                "obs_version": "5",
                "exported_at": "2024-01-01T00:00:00",
                "datapoints": [],
                "bindings": [
                    {
                        "id": str(uuid.uuid4()),
                        "datapoint_id": dp["id"],
                        "adapter_type": "WEBHOOK",
                        "adapter_instance_id": instance["id"],
                        "direction": "SOURCE",
                        "config": {"slug": "Mixed-Case", "token": "imported-token"},
                        "enabled": True,
                    }
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["errors"] == []

        assert [entry["slug"] for entry in await _webhook_bindings(client, auth_headers, instance["id"])] == ["mixed-case"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_a_trigger_response_carries_the_cors_header_of_its_preflight(client, auth_headers):
    from obs.config import get_settings

    allowed = get_settings().cors.origins
    if "*" in allowed:
        origin = "http://browser.example"
    else:
        origin = allowed[0]
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "cors-bell"})
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        headers = {"Origin": origin}

        preflight = await client.options("/hook/cors-bell", headers={**headers, "Access-Control-Request-Method": "GET"})
        trigger = await client.get(entry["call_path"], headers=headers)

        assert trigger.status_code == 204
        assert preflight.headers["access-control-allow-origin"] == trigger.headers["access-control-allow-origin"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_config_import_issues_a_token_for_a_tokenless_webhook_binding(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    binding_id = str(uuid.uuid4())

    def document() -> dict:
        return {
            "obs_version": "5",
            "exported_at": "2024-01-01T00:00:00",
            "datapoints": [],
            "bindings": [
                {
                    "id": binding_id,
                    "datapoint_id": dp["id"],
                    "adapter_type": "WEBHOOK",
                    "adapter_instance_id": instance["id"],
                    "direction": "SOURCE",
                    "config": {"slug": "tokenless"},
                    "enabled": True,
                }
            ],
        }

    try:
        first = await client.post("/api/v1/config/import", json=document(), headers=auth_headers)
        assert first.status_code == 200 and first.json()["errors"] == [], first.text
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert entry["token"]
        assert (await client.get(entry["call_path"])).status_code == 204

        # Importing the same tokenless document again keeps the token that was issued.
        second = await client.post("/api/v1/config/import", json=document(), headers=auth_headers)
        assert second.status_code == 200 and second.json()["errors"] == [], second.text
        again = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]
        assert again["token"] == entry["token"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_oversized_posts_spend_rate_limit_budget(client, auth_headers):
    instance = await _create_instance(client, auth_headers, config={"rate_limit_per_minute": 1})
    try:
        first = await client.post("/hook/bell", content=b"x" * (64 * 1024 + 1))
        second = await client.post("/hook/bell", content=b"x" * (64 * 1024 + 1))

        assert first.status_code == 413
        assert (second.status_code, second.json()) == (429, {"detail": "Too many requests"})
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_the_credential_parameter_cannot_be_used_as_the_value_parameter(client, auth_headers):
    dp = await _create_dp(client, auth_headers, data_type="STRING")
    instance = await _create_instance(client, auth_headers, enabled=False)
    try:
        resp = await client.post(
            f"/api/v1/datapoints/{dp['id']}/bindings",
            json={
                "adapter_instance_id": instance["id"],
                "direction": "SOURCE",
                "config": {"slug": "leaky", "value_source": "request", "value_param": "token"},
            },
            headers=auth_headers,
        )
        assert resp.status_code == 422, resp.text
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_config_import_refuses_an_enabled_webhook_on_a_central_plant_datapoint(client, auth_headers):
    plant = await _create_dp(client, auth_headers, control_class="central_plant")
    room = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, enabled=False)

    def binding(dp_id: str, slug: str, enabled: bool) -> dict:
        return {
            "id": str(uuid.uuid4()),
            "datapoint_id": dp_id,
            "adapter_type": "WEBHOOK",
            "adapter_instance_id": instance["id"],
            "direction": "SOURCE",
            "config": {"slug": slug, "token": f"token-{slug}"},
            "enabled": enabled,
        }

    try:
        resp = await client.post(
            "/api/v1/config/import",
            json={
                "obs_version": "5",
                "exported_at": "2024-01-01T00:00:00",
                "datapoints": [],
                "bindings": [
                    binding(plant["id"], "plant-live", True),
                    binding(plant["id"], "plant-off", False),
                    binding(room["id"], "room-live", True),
                ],
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["bindings_created"] == 2
        assert [error for error in body["errors"] if "central_plant" in error]

        slugs = sorted(entry["slug"] for entry in await _webhook_bindings(client, auth_headers, instance["id"]))
        assert slugs == ["plant-off", "room-live"]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_every_handled_trigger_response_is_marked_non_cacheable(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers, config={"rate_limit_per_minute": 2})
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "no-cache", "methods": ["GET", "POST"]})
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        responses = {
            204: await client.get(entry["call_path"]),
            404: await client.put("/hook/anything"),
            413: await client.post("/hook/no-cache", content=b"x" * (64 * 1024 + 1)),
            429: await client.get(entry["call_path"]),
        }

        for status_code, response in responses.items():
            assert response.status_code == status_code
            assert response.headers["cache-control"] == "no-store", status_code
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


# ---------------------------------------------------------------------------
# Monitor path — triggered values reach the RingBuffer (issue #1310)
# ---------------------------------------------------------------------------


async def _monitor_entries(client, auth_headers, dp_id: str, *, adapters: list[str] | None = None) -> list[dict]:
    """Query the RingBuffer the way the Monitor view does (``POST /ringbuffer/query``), oldest first."""
    filters: dict = {"datapoints": {"ids": [dp_id]}}
    if adapters is not None:
        filters["adapters"] = {"any_of": adapters}
    resp = await client.post(
        "/api/v1/ringbuffer/query",
        json={"filters": filters, "sort": {"field": "id", "order": "asc"}, "pagination": {"limit": 100}},
        headers=auth_headers,
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


async def _wait_for_monitor_entries(client, auth_headers, dp_id: str, count: int, *, adapters: list[str] | None = None, timeout: float = 5.0):
    """Poll until at least *count* entries are recorded; the RingBuffer is fed asynchronously from the bus."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while True:
        entries = await _monitor_entries(client, auth_headers, dp_id, adapters=adapters)
        if len(entries) >= count or loop.time() >= deadline:
            return entries
        await asyncio.sleep(0.02)


def _ts_seconds(ts: str) -> float:
    return datetime.fromisoformat(ts).timestamp()


async def test_trigger_and_autoreset_are_recorded_in_the_monitor_as_webhook_entries(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(
            client,
            auth_headers,
            dp["id"],
            instance["id"],
            {"slug": "monitor-bell", "autoreset": True, "autoreset_value": "false", "autoreset_delay_ms": 200},
        )
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        # A value from another source on the same DataPoint, so the adapter filter has something to exclude.
        written = await client.post(f"/api/v1/datapoints/{dp['id']}/value", json={"value": False}, headers=auth_headers)
        assert written.status_code == 204, written.text
        baseline = await _wait_for_monitor_entries(client, auth_headers, dp["id"], 1)
        assert [e["source_adapter"] for e in baseline] == ["api"]

        assert (await client.get(entry["call_path"])).status_code == 204

        recorded = await _wait_for_monitor_entries(client, auth_headers, dp["id"], 3)
        assert len(recorded) == 3, recorded
        trigger, reset = recorded[1], recorded[2]

        assert trigger["new_value"] is True
        assert trigger["source_adapter"] == "WEBHOOK"
        assert reset["new_value"] is False
        assert reset["old_value"] is True
        assert reset["source_adapter"] == "WEBHOOK"
        # The reset stands for the configured delay after the trigger, it is not published with it.
        assert _ts_seconds(reset["ts"]) - _ts_seconds(trigger["ts"]) >= 0.15

        # The RingBuffer does not persist the binding id; the entry's metadata snapshot attributes the
        # value to the WEBHOOK binding of this instance, which is the only binding on the DataPoint.
        for recorded_entry in (trigger, reset):
            assert recorded_entry["datapoint_id"] == dp["id"]
            assert recorded_entry["metadata"]["source"] == {"adapter": "WEBHOOK"}
            assert [(b["adapter_type"], b["adapter_instance_id"], b["direction"]) for b in recorded_entry["metadata"]["bindings"]] == [
                ("WEBHOOK", instance["id"], "SOURCE")
            ]

        # The Monitor's adapter filter returns exactly the two webhook values — in either casing.
        for adapter_filter in (["WEBHOOK"], ["webhook"]):
            filtered = await _monitor_entries(client, auth_headers, dp["id"], adapters=adapter_filter)
            assert [e["id"] for e in filtered] == [trigger["id"], reset["id"]], adapter_filter
        assert [e["id"] for e in await _monitor_entries(client, auth_headers, dp["id"], adapters=["api"])] == [baseline[0]["id"]]
    finally:
        await _delete_instance(client, auth_headers, instance["id"])


async def test_a_rejected_call_leaves_no_monitor_entry(client, auth_headers):
    dp = await _create_dp(client, auth_headers)
    instance = await _create_instance(client, auth_headers)
    try:
        await _create_binding(client, auth_headers, dp["id"], instance["id"], {"slug": "monitor-reject"})
        entry = (await _webhook_bindings(client, auth_headers, instance["id"]))[0]

        rejected = await client.get("/hook/monitor-reject?token=wrong-token")
        assert rejected.status_code == 404, rejected.text

        # A valid call afterwards is the barrier: the bus delivers in order, so once its entry is
        # recorded, anything the rejected call had published would already be there too.
        assert (await client.get(entry["call_path"])).status_code == 204
        recorded = await _wait_for_monitor_entries(client, auth_headers, dp["id"], 1)
        assert len(recorded) == 1, recorded
        assert recorded[0]["new_value"] is True
        assert recorded[0]["source_adapter"] == "WEBHOOK"
    finally:
        await _delete_instance(client, auth_headers, instance["id"])
