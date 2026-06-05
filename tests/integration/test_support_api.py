"""Integration tests for the support diagnostics API."""

from __future__ import annotations

import logging
from unittest.mock import patch

import pytest

from obs.api.auth import create_access_token

pytestmark = pytest.mark.integration


def _headers_for(username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(username)}"}


async def _create_non_admin_user(client, auth_headers, username: str) -> dict[str, str]:
    resp = await client.post(
        "/api/v1/auth/users",
        json={"username": username, "password": "pw-12345678", "is_admin": False},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return _headers_for(username)


async def test_support_categories_requires_auth(client):
    resp = await client.get("/api/v1/support/categories")
    assert resp.status_code == 401


async def test_support_categories_show_export_contents(client, auth_headers):
    resp = await client.get("/api/v1/support/categories", headers=auth_headers)
    assert resp.status_code == 200
    keys = {entry["key"] for entry in resp.json()}
    assert {"installation", "adapters", "health", "history", "monitor", "logs"} <= keys


async def test_support_package_requires_auth(client):
    resp = await client.post("/api/v1/support/package")
    assert resp.status_code == 401


async def test_support_endpoints_require_admin(client, auth_headers):
    username = "support-non-admin-authz"
    user_headers = await _create_non_admin_user(client, auth_headers, username)
    try:
        categories = await client.get("/api/v1/support/categories", headers=user_headers)
        package = await client.post("/api/v1/support/package", headers=user_headers)
        debug_status = await client.get("/api/v1/support/debug-log", headers=user_headers)
        debug_enable = await client.post(
            "/api/v1/support/debug-log",
            json={"duration_seconds": 30, "level": "DEBUG"},
            headers=user_headers,
        )
        debug_disable = await client.delete("/api/v1/support/debug-log", headers=user_headers)

        assert categories.status_code == 403
        assert package.status_code == 403
        assert debug_status.status_code == 403
        assert debug_enable.status_code == 403
        assert debug_disable.status_code == 403
    finally:
        await client.delete(f"/api/v1/auth/users/{username}", headers=auth_headers)


async def test_support_package_contains_phase1_privacy_contract(client, auth_headers):
    resp = await client.post("/api/v1/support/package", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["schema_version"] == 1
    assert body["privacy"]["automatic_upload"] is False
    assert body["privacy"]["remote_access"] is False
    assert "installation" in body
    assert "runtime" in body
    assert "adapters" in body
    assert "history" in body
    assert "monitor" in body
    assert "health" in body
    assert isinstance(body["debug_log"], list)


async def test_support_package_sanitizes_adapter_config_and_counts(client, auth_headers):
    instance_resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MQTT",
            "name": "Support Sanitizer MQTT",
            "enabled": False,
            "config": {
                "host": "192.168.10.25",
                "port": 1883,
                "username": "support-user",
                "password": "top-secret",
                "client_id": "client-without-secret",
            },
        },
        headers=auth_headers,
    )
    assert instance_resp.status_code == 201, instance_resp.text
    instance_id = instance_resp.json()["id"]

    dp_resp = await client.post(
        "/api/v1/datapoints/",
        json={"name": "Support Sanitizer DP", "data_type": "STRING", "persist_value": False},
        headers=auth_headers,
    )
    assert dp_resp.status_code == 201, dp_resp.text
    dp_id = dp_resp.json()["id"]

    binding_resp = await client.post(
        f"/api/v1/datapoints/{dp_id}/bindings",
        json={
            "adapter_instance_id": instance_id,
            "direction": "SOURCE",
            "config": {"topic": "support/test"},
        },
        headers=auth_headers,
    )
    assert binding_resp.status_code == 201, binding_resp.text

    resp = await client.post("/api/v1/support/package", headers=auth_headers)
    assert resp.status_code == 200
    package = resp.json()
    adapter = next(entry for entry in package["adapters"] if entry["id"] == instance_id)
    assert adapter["config"]["host"] == "[REDACTED_ENDPOINT]"
    assert adapter["config"]["username"] == "[REDACTED]"
    assert adapter["config"]["password"] == "[REDACTED]"
    assert adapter["config"]["client_id"] == "client-without-secret"
    assert adapter["bindings"] == 1
    assert adapter["objects"] == 1
    assert adapter["transactions_per_second"] == 0.0
    assert adapter["metrics_available"] is True
    assert adapter["metrics_source"] == "ringbuffer_metadata_adapter_instance_60s"


async def test_support_package_reports_ringbuffer_tps_for_adapter(client, auth_headers):
    instance_resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MQTT",
            "name": "Support TPS MQTT",
            "enabled": False,
            "config": {"host": "localhost", "port": 1883},
        },
        headers=auth_headers,
    )
    assert instance_resp.status_code == 201, instance_resp.text
    instance_id = instance_resp.json()["id"]

    from obs.ringbuffer.ringbuffer import get_ringbuffer

    await get_ringbuffer().record(
        ts="2099-01-01T00:00:00.000Z",
        datapoint_id="support-tps",
        topic="support/tps",
        old_value=None,
        new_value=1,
        source_adapter="MQTT",
        quality="good",
        metadata={"bindings": [{"adapter_type": "MQTT", "adapter_instance_id": instance_id}]},
    )

    resp = await client.post("/api/v1/support/package", headers=auth_headers)
    assert resp.status_code == 200
    adapter = next(entry for entry in resp.json()["adapters"] if entry["id"] == instance_id)
    assert adapter["transactions_per_second"] > 0
    assert adapter["metrics_available"] is True


async def test_support_package_does_not_apply_type_tps_to_unrelated_instances(client, auth_headers):
    instance_resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MQTT",
            "name": "Support TPS Idle MQTT",
            "enabled": False,
            "config": {"host": "localhost", "port": 1883},
        },
        headers=auth_headers,
    )
    assert instance_resp.status_code == 201, instance_resp.text
    idle_instance_id = instance_resp.json()["id"]

    from obs.ringbuffer.ringbuffer import get_ringbuffer

    await get_ringbuffer().record(
        ts="2099-01-01T00:00:01.000Z",
        datapoint_id="support-tps-other",
        topic="support/tps/other",
        old_value=None,
        new_value=1,
        source_adapter="MQTT",
        quality="good",
        metadata={"bindings": [{"adapter_type": "MQTT", "adapter_instance_id": "other-instance"}]},
    )

    resp = await client.post("/api/v1/support/package", headers=auth_headers)
    assert resp.status_code == 200
    adapter = next(entry for entry in resp.json()["adapters"] if entry["id"] == idle_instance_id)
    assert adapter["adapter_type_transactions_per_second"] > 0
    assert adapter["transactions_per_second"] == 0.0


async def test_support_package_marks_tps_metrics_unavailable_without_ringbuffer(client, auth_headers):
    instance_resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MQTT",
            "name": "Support TPS unavailable MQTT",
            "enabled": False,
            "config": {"host": "localhost", "port": 1883},
        },
        headers=auth_headers,
    )
    assert instance_resp.status_code == 201, instance_resp.text
    instance_id = instance_resp.json()["id"]

    with patch("obs.ringbuffer.ringbuffer.get_ringbuffer", side_effect=RuntimeError("not initialized")):
        resp = await client.post("/api/v1/support/package", headers=auth_headers)

    assert resp.status_code == 200
    adapter = next(entry for entry in resp.json()["adapters"] if entry["id"] == instance_id)
    assert adapter["transactions_per_second"] is None
    assert adapter["metrics_available"] is False
    assert adapter["metrics_source"] is None
    assert adapter["adapter_type_transactions_per_second"] is None


async def test_support_package_contains_history_and_monitor_sections(client, auth_headers):
    resp = await client.post("/api/v1/support/package", headers=auth_headers)
    assert resp.status_code == 200
    body = resp.json()
    assert body["history"]["active_plugin"]
    assert "settings" in body["history"]
    assert body["history"]["settings"]["influx_token"] == "[REDACTED]"
    assert "sqlite_storage" in body["history"]
    assert body["monitor"]["available"] is True
    assert "stats" in body["monitor"]
    assert "recent_source_adapter_counts" in body["monitor"]


async def test_support_package_sanitizes_error_history(client, auth_headers):
    logging.getLogger("tests.support").error(
        'connection failed url=http://admin:secret@192.168.1.20/api?token=abc password=hidden '
        'Authorization: Bearer bearer-token X-API-Key: header-secret password: colon-secret '
        'Authorization: Basic basic-secret '
        '{"token":"json-token"}'
    )

    resp = await client.post("/api/v1/support/package", headers=auth_headers)
    assert resp.status_code == 200
    messages = [entry["message"] for entry in resp.json()["error_history"]]
    matching = [message for message in messages if "connection failed" in message]
    assert matching
    message = matching[-1]
    assert "192.168.1.20" not in message
    assert "admin:secret" not in message
    assert "abc" not in message
    assert "hidden" not in message
    assert "bearer-token" not in message
    assert "header-secret" not in message
    assert "colon-secret" not in message
    assert "basic-secret" not in message
    assert "json-token" not in message
    assert "[REDACTED" in message


async def test_support_package_includes_warning_history(client, auth_headers):
    logging.getLogger("tests.support").warning("degraded tunnel on 192.168.1.44")

    resp = await client.post("/api/v1/support/package", headers=auth_headers)
    assert resp.status_code == 200
    warnings = resp.json()["warning_history"]
    assert any(entry["level"] == "WARNING" and "[REDACTED_IP]" in entry["message"] for entry in warnings)


async def test_support_debug_log_window_can_be_enabled_and_disabled(client, auth_headers):
    resp = await client.post(
        "/api/v1/support/debug-log",
        json={"duration_seconds": 30, "level": "DEBUG"},
        headers=auth_headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["active"] is True
    assert body["level"] == "DEBUG"
    assert body["until"]

    status_resp = await client.get("/api/v1/support/debug-log", headers=auth_headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["active"] is True

    disable_resp = await client.delete("/api/v1/support/debug-log", headers=auth_headers)
    assert disable_resp.status_code == 200
    assert disable_resp.json()["active"] is False


async def test_support_debug_restore_does_not_overwrite_manual_level_change(client, auth_headers):
    import obs.api.v1.support as support_module

    await client.put("/api/v1/system/log-level", json={"level": "INFO"}, headers=auth_headers)
    resp = await client.post(
        "/api/v1/support/debug-log",
        json={"duration_seconds": 30, "level": "DEBUG"},
        headers=auth_headers,
    )
    assert resp.status_code == 200

    manual = await client.put("/api/v1/system/log-level", json={"level": "WARNING"}, headers=auth_headers)
    assert manual.status_code == 204
    await support_module._restore_debug_now()

    level_resp = await client.get("/api/v1/system/log-level", headers=auth_headers)
    assert level_resp.status_code == 200
    assert level_resp.json()["level"] == "WARNING"

    await client.put("/api/v1/system/log-level", json={"level": "INFO"}, headers=auth_headers)


async def test_support_debug_log_rejects_unknown_level(client, auth_headers):
    resp = await client.post(
        "/api/v1/support/debug-log",
        json={"duration_seconds": 30, "level": "TRACE"},
        headers=auth_headers,
    )
    assert resp.status_code == 422
