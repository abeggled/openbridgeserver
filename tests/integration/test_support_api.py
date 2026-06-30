"""Integration tests for the support diagnostics API."""

from __future__ import annotations

import logging
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import obs.api.v1.support as support_api
from obs.api.auth import create_access_token
from obs.api.v1.support import (
    _build_monitor_info,
    _disk_resource_snapshot,
    _parse_procfs_stat_cpu_seconds,
    _ringbuffer_tps,
    sanitize_support_data,
)

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
    assert body["generated_by"] == "[REDACTED]"
    assert body["privacy"]["generated_by_redacted"] is True
    assert body["privacy"]["path_policy"] == "basename_only"
    assert body["privacy"]["automatic_upload"] is False
    assert body["privacy"]["remote_access"] is False
    assert "installation" in body
    assert "runtime" in body
    assert "adapters" in body
    assert "history" in body
    assert "monitor" in body
    assert "health" in body
    assert isinstance(body["debug_log"], list)
    assert "resources" in body["runtime"]
    assert body["runtime"]["resources"]["system"]["cpu_count"] is not None
    assert "memory" in body["runtime"]["resources"]["system"]
    assert "disk" in body["runtime"]["resources"]
    assert "top_cpu_processes" in body["runtime"]["resources"]
    assert "top_memory_processes" in body["runtime"]["resources"]
    assert "/" not in body["installation"]["database"]["path"]
    assert body["installation"]["config_source"]
    assert "/" not in body["installation"]["config_source"]
    assert not body["installation"]["config_source"].startswith("[REDACTED")


async def test_support_package_config_source_keeps_basename_but_sanitizes_endpoints(client, auth_headers):
    with patch.dict("os.environ", {"OBS_CONFIG": "config.yaml"}):
        default_resp = await client.post("/api/v1/support/package", headers=auth_headers)

    assert default_resp.status_code == 200
    assert default_resp.json()["installation"]["config_source"] == "config.yaml"

    with patch.dict("os.environ", {"OBS_CONFIG": "/etc/obs/prod-192.168.10.5.yaml"}):
        resp = await client.post("/api/v1/support/package", headers=auth_headers)

    assert resp.status_code == 200
    config_source = resp.json()["installation"]["config_source"]
    assert "/" not in config_source
    assert "192.168.10.5" not in config_source
    assert config_source == "prod-[REDACTED_IP].yaml"

    with patch.dict("os.environ", {"OBS_CONFIG": "/etc/obs/site.internal.local.yaml"}):
        host_resp = await client.post("/api/v1/support/package", headers=auth_headers)

    assert host_resp.status_code == 200
    host_config_source = host_resp.json()["installation"]["config_source"]
    assert "site.internal.local" not in host_config_source
    assert host_config_source == "[REDACTED_DOMAIN].yaml"

    with patch.dict("os.environ", {"OBS_CONFIG": "/etc/obs/customer.com.yaml"}):
        two_label_resp = await client.post("/api/v1/support/package", headers=auth_headers)

    assert two_label_resp.status_code == 200
    two_label_config_source = two_label_resp.json()["installation"]["config_source"]
    assert "customer.com" not in two_label_config_source
    assert two_label_config_source == "[REDACTED_DOMAIN].yaml"

    with patch.dict("os.environ", {"OBS_CONFIG": "/etc/obs/site.internal.local"}):
        domain_only_resp = await client.post("/api/v1/support/package", headers=auth_headers)

    assert domain_only_resp.status_code == 200
    domain_only_config_source = domain_only_resp.json()["installation"]["config_source"]
    assert "site.internal.local" not in domain_only_config_source
    assert domain_only_config_source == "[REDACTED_DOMAIN]"


async def test_support_package_sanitizes_adapter_config_and_counts(client, auth_headers):
    instance_resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MQTT",
            "name": "mqtt.internal.local",
            "enabled": False,
            "config": {
                "host": "192.168.10.25",
                "port": 1883,
                "individual_address": "1.1.100",
                "auth_protocol": "SHA",
                "sniffer.process": "sniffer.process",
                "logger": {"password": "nested-secret", "name": "obs_logger"},
                "username": "support-user",
                "password": "top-secret",
                "community": "support-community",
                "knxkeys_file_path": "/home/support/secret.knxkeys",
                "psk": "support-psk",
                "pin": "123456",
                "pre_shared_key": "support-pre-shared",
                "pre-shared-key": "support-hyphen-pre-shared",
                "priv_key": "support-private-protocol-key",
                "backbone_key": "support-backbone-key",
                "api-key": "support-hyphen-api-key",
                "x-api-key": "support-hyphen-x-api-key",
                "private-key": "support-hyphen-private-key",
                "auth": "support-auth",
                "bearer": "support-bearer",
                "passphrase": "support-passphrase",
                "keyring": "support-keyring",
                "ca_cert": "support-ca-cert",
                "client_cert": "support-client-cert",
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
            "send_on_change": True,
            "value_formula": "x * 2",
        },
        headers=auth_headers,
    )
    assert binding_resp.status_code == 201, binding_resp.text

    resp = await client.post("/api/v1/support/package", headers=auth_headers)
    assert resp.status_code == 200
    package = resp.json()
    adapter = next(entry for entry in package["adapters"] if entry["id"] == instance_id)
    assert adapter["name"] == "[REDACTED_DOMAIN]"
    assert adapter["config"]["host"] == "[REDACTED_ENDPOINT]"
    assert adapter["config"]["individual_address"] == "1.1.100"
    assert adapter["config"]["auth_protocol"] == "SHA"
    assert adapter["config"]["sniffer.process"] == "sniffer.process"
    assert adapter["config"]["logger"]["password"] == "[REDACTED]"
    assert adapter["config"]["logger"]["name"] == "obs_logger"
    assert adapter["config"]["username"] == "[REDACTED]"
    assert adapter["config"]["password"] == "[REDACTED]"
    assert adapter["config"]["community"] == "[REDACTED]"
    assert adapter["config"]["knxkeys_file_path"] == "[REDACTED]"
    assert adapter["config"]["psk"] == "[REDACTED]"
    assert adapter["config"]["pin"] == "[REDACTED]"
    assert adapter["config"]["pre_shared_key"] == "[REDACTED]"
    assert adapter["config"]["pre-shared-key"] == "[REDACTED]"
    assert adapter["config"]["priv_key"] == "[REDACTED]"
    assert adapter["config"]["backbone_key"] == "[REDACTED]"
    assert adapter["config"]["api-key"] == "[REDACTED]"
    assert adapter["config"]["x-api-key"] == "[REDACTED]"
    assert adapter["config"]["private-key"] == "[REDACTED]"
    assert adapter["config"]["auth"] == "[REDACTED]"
    assert adapter["config"]["bearer"] == "[REDACTED]"
    assert adapter["config"]["passphrase"] == "[REDACTED]"
    assert adapter["config"]["keyring"] == "[REDACTED]"
    assert adapter["config"]["ca_cert"] == "[REDACTED]"
    assert adapter["config"]["client_cert"] == "[REDACTED]"
    assert adapter["config"]["client_id"] == "client-without-secret"
    assert adapter["bindings"] == 1
    assert adapter["objects"] == 1
    assert adapter["active_transformations"] == 1
    assert adapter["active_filters"] == 1
    assert adapter["transactions_per_second"] == 0.0
    assert adapter["metrics_available"] is True
    assert adapter["metrics_source"] == "ringbuffer_metadata_adapter_instance_60s"


async def test_support_package_redacts_message_telegram_chat_id(client, auth_headers):
    instance_resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MESSAGE",
            "name": "Support Message Telegram",
            "enabled": False,
            "config": {
                "providers": {
                    "telegram": {
                        "enabled": True,
                        "bot_token": "telegram-token",
                        "targets": {"default": {"chat_id": "123456789"}},
                    }
                }
            },
        },
        headers=auth_headers,
    )
    assert instance_resp.status_code == 201, instance_resp.text
    instance_id = instance_resp.json()["id"]

    resp = await client.post("/api/v1/support/package", headers=auth_headers)

    assert resp.status_code == 200
    adapter = next(entry for entry in resp.json()["adapters"] if entry["id"] == instance_id)
    telegram = adapter["config"]["providers"]["telegram"]
    assert telegram["bot_token"] == "[REDACTED]"
    assert telegram["targets"]["default"]["chat_id"] == "[REDACTED]"


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


async def test_support_package_marks_ringbuffer_metrics_unavailable_on_query_failure(client, auth_headers):
    instance_resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MQTT",
            "name": "Support TPS broken MQTT",
            "enabled": False,
            "config": {"host": "localhost", "port": 1883},
        },
        headers=auth_headers,
    )
    assert instance_resp.status_code == 201, instance_resp.text
    instance_id = instance_resp.json()["id"]

    class BrokenRingBuffer:
        async def stats(self) -> dict[str, object]:
            return {"total": 0}

        async def query(self, **_kwargs):
            raise OSError("locked")

    with patch("obs.ringbuffer.ringbuffer.get_ringbuffer", return_value=BrokenRingBuffer()):
        resp = await client.post("/api/v1/support/package", headers=auth_headers)

    assert resp.status_code == 200
    body = resp.json()
    adapter = next(entry for entry in body["adapters"] if entry["id"] == instance_id)
    assert adapter["transactions_per_second"] is None
    assert adapter["metrics_available"] is False
    assert adapter["metrics_source"] is None
    assert body["monitor"] == {"available": False, "reason": "OSError"}


async def test_ringbuffer_helpers_tolerate_query_failures():
    class BrokenRingBuffer:
        async def stats(self) -> dict[str, object]:
            return {"total": 0}

        async def query(self, **_kwargs):
            raise OSError("locked")

    with patch("obs.ringbuffer.ringbuffer.get_ringbuffer", return_value=BrokenRingBuffer()):
        assert await _ringbuffer_tps() == (False, {}, {})
        assert await _build_monitor_info() == {"available": False, "reason": "OSError"}


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


async def test_support_package_tolerates_history_stats_failure(client, auth_headers):
    with patch("obs.api.v1.support._history_table_stats", side_effect=OSError("locked")):
        resp = await client.post("/api/v1/support/package", headers=auth_headers)

    assert resp.status_code == 200
    assert resp.json()["history"]["sqlite_storage"] == {
        "available": False,
        "reason": "OSError",
    }


async def test_support_package_sanitizes_error_history(client, auth_headers):
    logging.getLogger("tests.support").error(
        "connection failed url=http://admin:secret@192.168.1.20/api?token=abc password=hidden "
        "dsn=mqtt://mqtt-user:mqtt-pass@broker.internal.local/topic "
        "postgres://pg-user:pg-pass@db.internal.local/app "
        "telegram=https://api.telegram.org/bot123456:telegram-secret-token/sendMessage "
        "webhook=https://hooks.example.com/services/T000/B000/webhook-secret "
        "Authorization: Bearer bearer-token X-API-Key: header-secret password: colon-secret "
        "Authorization: Basic basic-secret "
        "Authorization=Bearer equals-bearer-token Authorization=Basic equals-basic-token "
        "Authorization: Token token-scheme-secret Authorization=Digest digest-scheme-secret "
        "Cookie: sessionid=cookie-secret; csrftoken=csrf-secret "
        "Set-Cookie: refresh_token=set-cookie-secret; Path=/; HttpOnly "
        "api_key = spaced-api-key password = spaced-password "
        "api.key=dotted-api-key x.api.key: dotted-colon-api-key "
        'password="quoted password" api_key=\'quoted api key\' password="multi word secret" '
        "password: \"quoted colon password\" api_key: 'quoted colon api key' api_key: 'multi word api secret' "
        "access_token=access-secret refresh_token=refresh-secret client_secret: prefixed-colon "
        "community=public-community knxkeys_file_path=/home/support/secret.knxkeys "
        "failed to open /home/alice/obs/config.yaml "
        "ipv6 ::1 ::dead:beef 2001:db8:: "
        r"windows path C:\Users\Alice\obs\customer.com.yaml unc \\fileserver\share\obs\config.yaml "
        "logger sniffer.process still visible and sniffer.process: label visible "
        "but sniffer.process.com and sniffer.process:123 are private "
        "contact admin@example.com connecting to mqtt.customer-site.com failed "
        '{"token":"json-token","client_secret":"json-client-secret","pin":123456,"api_key":true}'
    )
    logging.getLogger("mqtt.customer.local.192.168.1.5").error("endpoint logger leak sentinel")

    resp = await client.post("/api/v1/support/package", headers=auth_headers)
    assert resp.status_code == 200
    messages = [entry["message"] for entry in resp.json()["error_history"]]
    matching = [message for message in messages if "connection failed" in message]
    assert matching
    message = matching[-1]
    assert "192.168.1.20" not in message
    assert "admin:secret" not in message
    assert "mqtt-user:mqtt-pass" not in message
    assert "pg-user:pg-pass" not in message
    assert "broker.internal.local" not in message
    assert "db.internal.local" not in message
    assert "bot123456:telegram-secret-token" not in message
    assert "webhook-secret" not in message
    assert "/sendMessage" not in message
    assert "/services/T000/B000" not in message
    assert "abc" not in message
    assert "hidden" not in message
    assert "bearer-token" not in message
    assert "header-secret" not in message
    assert "colon-secret" not in message
    assert "basic-secret" not in message
    assert "equals-bearer-token" not in message
    assert "equals-basic-token" not in message
    assert "token-scheme-secret" not in message
    assert "digest-scheme-secret" not in message
    assert "cookie-secret" not in message
    assert "csrf-secret" not in message
    assert "set-cookie-secret" not in message
    assert "spaced-api-key" not in message
    assert "spaced-password" not in message
    assert "dotted-api-key" not in message
    assert "dotted-colon-api-key" not in message
    assert "quoted password" not in message
    assert "quoted api key" not in message
    assert "multi word secret" not in message
    assert "multi word api secret" not in message
    assert "quoted colon password" not in message
    assert "quoted colon api key" not in message
    assert "access-secret" not in message
    assert "refresh-secret" not in message
    assert "prefixed-colon" not in message
    assert "public-community" not in message
    assert "secret.knxkeys" not in message
    assert "::1" not in message
    assert "::dead:beef" not in message
    assert "2001:db8::" not in message
    assert "/home/alice/obs" not in message
    assert "[REDACTED_PATH]/config.yaml" in message
    assert "C:\\Users\\Alice\\obs" not in message
    assert "\\\\fileserver\\share\\obs" not in message
    assert "customer.com" not in message
    assert "[REDACTED_PATH]/[REDACTED_DOMAIN].yaml" in message
    assert "sniffer.process" in message
    assert "sniffer.process: label" in message
    assert "sniffer.process.com" not in message
    assert "sniffer.process:123" not in message
    assert "admin@example.com" not in message
    assert "mqtt.customer-site.com" not in message
    assert "json-token" not in message
    assert "json-client-secret" not in message
    assert "123456" not in message
    assert "true" not in message
    assert "[REDACTED" in message
    assert matching[-1]
    assert [entry for entry in resp.json()["error_history"] if entry["message"] == message][-1]["logger"] == "[REDACTED_DOMAIN]"
    logger_entry = next(entry for entry in resp.json()["error_history"] if entry["message"] == "endpoint logger leak sentinel")
    assert "mqtt.customer.local" not in logger_entry["logger"]
    assert "192.168.1.5" not in logger_entry["logger"]
    assert "[REDACTED_DOMAIN]" in logger_entry["logger"]
    assert "[REDACTED_IP]" in logger_entry["logger"]


async def test_support_package_tolerates_corrupt_adapter_config_rows(client, auth_headers):
    from obs.db.database import get_db

    instance_resp = await client.post(
        "/api/v1/adapters/instances",
        json={
            "adapter_type": "MQTT",
            "name": "Support Corrupt Config MQTT",
            "enabled": False,
            "config": {"host": "localhost", "port": 1883},
        },
        headers=auth_headers,
    )
    assert instance_resp.status_code == 201, instance_resp.text
    instance_id = instance_resp.json()["id"]

    db = get_db()
    await db.execute_and_commit("UPDATE adapter_instances SET config=? WHERE id=?", ("{not valid json", instance_id))

    resp = await client.post("/api/v1/support/package", headers=auth_headers)

    assert resp.status_code == 200
    adapter = next(entry for entry in resp.json()["adapters"] if entry["id"] == instance_id)
    assert adapter["config"] == {"available": False, "reason": "invalid_json"}


def test_sanitize_support_data_redacts_dictionary_keys_and_preserves_path_basenames():
    sanitized = sanitize_support_data(
        {
            "devices": {
                "192.168.1.5": {"status": "ok"},
                "10.0.0.8": {"status": "ok"},
                "broker.internal.local": {"status": "ok"},
                "access_token": "secret-token",
                "https://api.telegram.org/bot123456:dict-key-token/sendMessage": {"status": "ok"},
                "sniffer.process.com": {"status": "ok"},
            },
            "logger": "mqtt.customer.local api_key=logger-secret",
            "to": "+41000000000",
            "user_key": "pushover-user-key",
            "chat_id": "123456789",
            "path": "obs.db",
            "config_source": r"C:\Users\Alice\obs\customer.com.key",
        }
    )

    assert "192.168.1.5" not in sanitized["devices"]
    assert "broker.internal.local" not in sanitized["devices"]
    sanitized_device_keys = " ".join(sanitized["devices"])
    assert "dict-key-token" not in sanitized_device_keys
    assert "sniffer.process.com" not in sanitized_device_keys
    assert not any("/sendMessage" in key for key in sanitized["devices"])
    assert "[REDACTED_IP]" in sanitized["devices"]
    assert "[REDACTED_IP] (2)" in sanitized["devices"]
    assert "[REDACTED_DOMAIN]" in sanitized["devices"]
    assert sanitized["devices"]["access_token"] == "[REDACTED]"
    assert sanitized["to"] == "[REDACTED]"
    assert sanitized["user_key"] == "[REDACTED]"
    assert sanitized["chat_id"] == "[REDACTED]"
    assert "mqtt.customer.local" not in sanitized["logger"]
    assert "logger-secret" not in sanitized["logger"]
    assert "[REDACTED_DOMAIN]" in sanitized["logger"]
    assert "api_key=[REDACTED]" in sanitized["logger"]
    assert sanitized["path"] == "obs.db"
    assert sanitized["config_source"] == "[REDACTED_DOMAIN].key"
    assert all(isinstance(key, str) for key in sanitized["devices"])


def test_disk_resource_snapshot_measures_configured_database_volume(tmp_path, monkeypatch):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    db_path = data_dir / "obs.db"
    captured: dict[str, object] = {}

    def fake_disk_usage(path):
        captured["path"] = path
        return SimpleNamespace(total=100, used=40, free=60)

    monkeypatch.setattr(
        support_api,
        "get_settings",
        lambda: SimpleNamespace(database=SimpleNamespace(path=str(db_path))),
    )
    monkeypatch.setattr(support_api.shutil, "disk_usage", fake_disk_usage)

    snapshot = _disk_resource_snapshot()

    assert captured["path"] == data_dir
    assert snapshot == {
        "path": "obs.db",
        "available": True,
        "total_bytes": 100,
        "used_bytes": 40,
        "free_bytes": 60,
    }


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
    assert logging.getLevelName(logging.getLogger().level) == "DEBUG"

    status_resp = await client.get("/api/v1/support/debug-log", headers=auth_headers)
    assert status_resp.status_code == 200
    assert status_resp.json()["active"] is True
    assert status_resp.json()["level"] == "DEBUG"

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


def test_parse_procfs_stat_cpu_seconds_handles_comm_with_spaces_and_parentheses():
    stat_row = "123 (worker (tenant a)) S 1 2 3 4 5 6 7 8 9 10 25 75 16 17"

    assert _parse_procfs_stat_cpu_seconds(stat_row, clock_ticks=100) == 1.0
