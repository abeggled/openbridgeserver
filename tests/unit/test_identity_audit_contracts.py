"""Wave 14 audit coverage for identity, settings, support and backup routes."""

from __future__ import annotations

import ast
import sqlite3
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException, UploadFile
from fastapi.security import HTTPAuthorizationCredentials
from starlette.requests import Request

from obs.api.auth import (
    ApiKeyCreate,
    Principal,
    SetMqttPasswordRequest,
    UserCreate,
    UserDeletionRequest,
    UserUpdate,
    _deletion_inventory,
    create_api_key,
    create_user,
    delete_api_key,
    delete_mqtt_password,
    delete_user,
    get_admin_user,
    get_current_principal,
    set_mqtt_password,
    update_user,
)
from obs.api.audit import AuditLogWriter
from obs.api.v1.autobackup import delete_autobackup, restore_autobackup
from obs.api.v1.config import import_db
from obs.api.v1.security_contract_registry import AuditEffect, AuditMode, ROUTE_SECURITY_CONTRACTS
from obs.api.v1.system import (
    AppSettingsIn,
    HistorySettingsIn,
    test_history_connection as run_history_test,
    update_app_settings,
    update_history_settings,
)
from obs.db.database import Database


IDENTITY_AUDIT_ROUTES = {
    ("POST", "/api/v1/auth/login"),
    ("POST", "/api/v1/auth/refresh"),
    ("POST", "/api/v1/auth/apikeys"),
    ("DELETE", "/api/v1/auth/apikeys/{key_id}"),
    ("PUT", "/api/v1/auth/apikeys/{key_id}/capabilities"),
    ("POST", "/api/v1/auth/users"),
    ("PATCH", "/api/v1/auth/users/{username}"),
    ("DELETE", "/api/v1/auth/users/{username}"),
    ("POST", "/api/v1/auth/users/{username}/mqtt-password"),
    ("DELETE", "/api/v1/auth/users/{username}/mqtt-password"),
    ("POST", "/api/v1/auth/me/change-password"),
    ("PUT", "/api/v1/authz/principals/{principal_type}/{principal_id:path}/grants"),
    ("PUT", "/api/v1/system/settings"),
    ("PUT", "/api/v1/system/history/settings"),
    ("POST", "/api/v1/system/history/test"),
    ("POST", "/api/v1/system/nav-links"),
    ("PATCH", "/api/v1/system/nav-links/{link_id}"),
    ("DELETE", "/api/v1/system/nav-links/{link_id}"),
    ("PUT", "/api/v1/system/log-level"),
    ("POST", "/api/v1/support/debug-log"),
    ("DELETE", "/api/v1/support/debug-log"),
    ("POST", "/api/v1/security/url-target-allowlist"),
    ("DELETE", "/api/v1/security/url-target-allowlist"),
    ("POST", "/api/v1/config/import/db"),
    ("POST", "/api/v1/config/import"),
    ("DELETE", "/api/v1/config/reset"),
    ("DELETE", "/api/v1/config/reset/bindings"),
    ("DELETE", "/api/v1/config/reset/datapoints"),
    ("DELETE", "/api/v1/config/reset/logic"),
    ("DELETE", "/api/v1/config/reset/adapters"),
    ("PUT", "/api/v1/config/autobackup/config"),
    ("POST", "/api/v1/config/autobackup/run"),
    ("POST", "/api/v1/config/autobackup/restore/{name}"),
    ("DELETE", "/api/v1/config/autobackup/{name}"),
}

_AUDITED_SOURCE_FILES = (
    "obs/api/auth.py",
    "obs/api/v1/authz.py",
    "obs/api/v1/autobackup.py",
    "obs/api/v1/config.py",
    "obs/api/v1/security.py",
    "obs/api/v1/support.py",
    "obs/api/v1/system.py",
)


def test_all_34_identity_routes_have_literal_contract_writes() -> None:
    repo = Path(__file__).parents[2]
    literal_writes: set[tuple[str, str]] = set()
    for relative_path in _AUDITED_SOURCE_FILES:
        tree = ast.parse((repo / relative_path).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "write_contract"
                and len(node.args) >= 2
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[1], ast.Constant)
            ):
                continue
            literal_writes.add((str(node.args[0].value), str(node.args[1].value)))

    assert len(IDENTITY_AUDIT_ROUTES) == 34
    assert IDENTITY_AUDIT_ROUTES <= ROUTE_SECURITY_CONTRACTS.keys()
    assert IDENTITY_AUDIT_ROUTES <= literal_writes


@pytest.mark.asyncio
async def test_admin_denial_is_persisted_with_principal_and_canonical_route() -> None:
    db = Database(":memory:")
    await db.connect()
    try:
        request = Request(
            {
                "type": "http",
                "method": "DELETE",
                "path": "/api/v1/config/reset",
                "query_string": b"",
                "headers": [],
                "client": ("127.0.0.1", 1),
                "route": SimpleNamespace(path="/api/v1/config/reset"),
            }
        )
        principal = Principal(subject="operator", type="user", is_admin=False)
        with pytest.raises(Exception) as exc_info:
            await get_admin_user(principal=principal, db=db, request=request)
        assert getattr(exc_info.value, "status_code", None) == 403

        row = await db.fetchone("SELECT * FROM audit_log_entries WHERE outcome='denied'")
        assert row is not None
        assert (row["principal_type"], row["principal_id"]) == ("user", "operator")
        assert (row["action"], row["route_template"]) == ("config.factory_reset", "/api/v1/config/reset")
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_atomic_settings_mutation_rolls_back_when_audit_write_fails(monkeypatch) -> None:
    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("obs.api.audit.AuditLogWriter.write_contract", fail_audit)
    db = Database(":memory:")
    await db.connect()
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await update_app_settings(body=AppSettingsIn(timezone="Europe/Berlin"), db=db, _user="operator")
        row = await db.fetchone("SELECT value FROM app_settings WHERE key='timezone'")
        assert row is not None
        assert row["value"] == "Europe/Zurich"
    finally:
        await db.disconnect()


def test_identity_contract_allowlists_reject_secret_sentinel_fields() -> None:
    forbidden = {"password", "secret", "token", "credential", "private_key", "pin"}
    for signature in IDENTITY_AUDIT_ROUTES:
        for field in ROUTE_SECURITY_CONTRACTS[signature].allowed_detail_fields:
            assert not (set(field.lower().split("_")) & forbidden), (signature, field)


def test_identity_mqtt_mutations_are_result_external_mutations() -> None:
    signatures = {
        ("POST", "/api/v1/auth/users"),
        ("PATCH", "/api/v1/auth/users/{username}"),
        ("DELETE", "/api/v1/auth/users/{username}"),
        ("POST", "/api/v1/auth/users/{username}/mqtt-password"),
        ("DELETE", "/api/v1/auth/users/{username}/mqtt-password"),
    }
    for signature in signatures:
        contract = ROUTE_SECURITY_CONTRACTS[signature]
        assert (contract.audit_mode, contract.audit_effect) == (AuditMode.RESULT, AuditEffect.EXTERNAL_MUTATION)


def _http_request(method: str, path: str) -> Request:
    return Request(
        {
            "type": "http",
            "method": method,
            "path": path,
            "query_string": b"",
            "headers": [],
            "client": ("127.0.0.1", 1),
            "route": SimpleNamespace(path=path),
        }
    )


@pytest.mark.asyncio
async def test_self_and_policy_denials_are_audited() -> None:
    db = Database(":memory:")
    await db.connect()
    try:
        ownerless = Principal(subject="api_key:key-0", type="api_key", is_admin=False)
        with pytest.raises(HTTPException, match="owner"):
            await create_api_key.__wrapped__(
                request=_http_request("POST", "/api/v1/auth/apikeys"),
                body=ApiKeyCreate(name="replacement"),
                principal=ownerless,
                db=db,
            )
        await db.execute_and_commit(
            "INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, created_at) VALUES ('alice-id','alice','x',0,0,'2026-01-01')"
        )
        await db.execute_and_commit(
            "INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, created_at) VALUES ('bob-id','bob','x',0,0,'2026-01-01')"
        )
        await db.execute_and_commit("INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES ('key-1','owned','hash','bob','2026-01-01')")
        with pytest.raises(HTTPException, match="another user's"):
            await delete_api_key(
                key_id="key-1",
                request=_http_request("DELETE", "/api/v1/auth/apikeys/key-1"),
                current_user="alice",
                db=db,
            )
        with pytest.raises(HTTPException, match="Admin access"):
            await set_mqtt_password(
                username="bob",
                body=SetMqttPasswordRequest(password="never-audited"),
                request=_http_request("POST", "/api/v1/auth/users/bob/mqtt-password"),
                current_user="alice",
                db=db,
            )
        rows = await db.fetchall("SELECT action, outcome FROM audit_log_entries ORDER BY id")
        assert [(row["action"], row["outcome"]) for row in rows] == [
            ("auth.api_key.created", "denied"),
            ("auth.api_key.deleted", "denied"),
            ("auth.user.mqtt_password_set", "denied"),
        ]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_mqtt_password_set_sync_failure_persists_state_and_failed_audit(monkeypatch) -> None:
    async def fail_sync(_db):
        raise RuntimeError("mqtt reload failed")

    monkeypatch.setattr("obs.api.auth._sync_mqtt", fail_sync)
    monkeypatch.setattr("obs.core.mqtt_passwd.mosquitto_hash", lambda _password: "stored-mqtt-hash")
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute_and_commit(
            "INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, created_at) "
            "VALUES ('admin-id','admin','x',1,0,'2026-01-01'), ('alice-id','alice','x',0,0,'2026-01-01')"
        )
        with pytest.raises(RuntimeError, match="mqtt reload failed"):
            await set_mqtt_password(
                username="alice",
                body=SetMqttPasswordRequest(password="audit-secret-sentinel"),
                request=_http_request("POST", "/api/v1/auth/users/alice/mqtt-password"),
                current_user="admin",
                db=db,
            )

        user = await db.fetchone("SELECT mqtt_enabled, mqtt_password_hash FROM users WHERE username='alice'")
        event = await db.fetchone("SELECT outcome, details_json FROM audit_log_entries WHERE action='auth.user.mqtt_password_set'")
        assert (user["mqtt_enabled"], user["mqtt_password_hash"]) == (1, "stored-mqtt-hash")
        assert event is not None and event["outcome"] == "failed"
        assert "audit-secret-sentinel" not in event["details_json"]
        assert "stored-mqtt-hash" not in event["details_json"]
        assert await db.fetchone("SELECT 1 FROM audit_log_entries WHERE action='auth.user.mqtt_password_set' AND outcome='success'") is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_mqtt_password_delete_sync_failure_persists_revocation_and_failed_audit(monkeypatch) -> None:
    async def fail_sync(_db):
        raise RuntimeError("mqtt reload failed")

    monkeypatch.setattr("obs.api.auth._sync_mqtt", fail_sync)
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute_and_commit(
            "INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, mqtt_password_hash, created_at) "
            "VALUES ('alice-id','alice','x',0,1,'old-secret-hash','2026-01-01')"
        )
        with pytest.raises(RuntimeError, match="mqtt reload failed"):
            await delete_mqtt_password(
                username="alice",
                request=_http_request("DELETE", "/api/v1/auth/users/alice/mqtt-password"),
                _admin="admin",
                db=db,
            )

        user = await db.fetchone("SELECT mqtt_enabled, mqtt_password_hash FROM users WHERE username='alice'")
        event = await db.fetchone("SELECT outcome, details_json FROM audit_log_entries WHERE action='auth.user.mqtt_password_deleted'")
        assert (user["mqtt_enabled"], user["mqtt_password_hash"]) == (0, None)
        assert event is not None and event["outcome"] == "failed"
        assert "old-secret-hash" not in event["details_json"]
        assert await db.fetchone("SELECT 1 FROM audit_log_entries WHERE action='auth.user.mqtt_password_deleted' AND outcome='success'") is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_user_create_sync_failure_persists_user_and_failed_non_secret_audit(monkeypatch) -> None:
    async def fail_sync(_db):
        raise RuntimeError("mqtt reload failed")

    monkeypatch.setattr("obs.api.auth._sync_mqtt", fail_sync)
    monkeypatch.setattr("obs.api.auth.hash_password", lambda _password: "stored-login-hash")
    monkeypatch.setattr("obs.core.mqtt_passwd.mosquitto_hash", lambda _password: "stored-mqtt-hash")
    db = Database(":memory:")
    await db.connect()
    try:
        with pytest.raises(RuntimeError, match="mqtt reload failed"):
            await create_user(
                body=UserCreate(
                    username="alice",
                    password="login-secret-sentinel",
                    mqtt_enabled=True,
                    mqtt_password="mqtt-secret-sentinel",
                ),
                request=_http_request("POST", "/api/v1/auth/users"),
                _admin="admin",
                db=db,
            )

        user = await db.fetchone("SELECT mqtt_enabled, password_hash, mqtt_password_hash FROM users WHERE username='alice'")
        event = await db.fetchone("SELECT outcome, details_json FROM audit_log_entries WHERE action='auth.user.created'")
        assert (user["mqtt_enabled"], user["password_hash"], user["mqtt_password_hash"]) == (
            1,
            "stored-login-hash",
            "stored-mqtt-hash",
        )
        assert event is not None and event["outcome"] == "failed"
        for secret in ("login-secret-sentinel", "mqtt-secret-sentinel", "stored-login-hash", "stored-mqtt-hash"):
            assert secret not in event["details_json"]
        assert await db.fetchone("SELECT 1 FROM audit_log_entries WHERE action='auth.user.created' AND outcome='success'") is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_user_update_sync_failure_persists_update_and_failed_audit(monkeypatch) -> None:
    async def fail_sync(_db):
        raise RuntimeError("mqtt reload failed")

    monkeypatch.setattr("obs.api.auth._sync_mqtt", fail_sync)
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute_and_commit(
            "INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, mqtt_password_hash, created_at) "
            "VALUES ('alice-id','alice','x',0,1,'old-secret-hash','2026-01-01')"
        )
        with pytest.raises(RuntimeError, match="mqtt reload failed"):
            await update_user(
                username="alice",
                body=UserUpdate(mqtt_enabled=False),
                request=_http_request("PATCH", "/api/v1/auth/users/alice"),
                _admin="admin",
                db=db,
            )

        user = await db.fetchone("SELECT mqtt_enabled, mqtt_password_hash FROM users WHERE username='alice'")
        event = await db.fetchone("SELECT outcome, details_json FROM audit_log_entries WHERE action='auth.user.updated'")
        assert (user["mqtt_enabled"], user["mqtt_password_hash"]) == (0, None)
        assert event is not None and event["outcome"] == "failed"
        assert "old-secret-hash" not in event["details_json"]
        assert await db.fetchone("SELECT 1 FROM audit_log_entries WHERE action='auth.user.updated' AND outcome='success'") is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_user_delete_sync_failure_persists_deletion_and_failed_audit(monkeypatch) -> None:
    async def fail_sync(_db):
        raise RuntimeError("mqtt reload failed")

    monkeypatch.setattr("obs.api.auth._sync_mqtt", fail_sync)
    db = Database(":memory:")
    await db.connect()
    try:
        await db.execute_and_commit(
            "INSERT INTO users (id, username, password_hash, is_admin, mqtt_enabled, mqtt_password_hash, created_at) "
            "VALUES ('admin-id','admin','x',1,0,NULL,'2026-01-01'), "
            "('alice-id','alice','x',0,1,'old-secret-hash','2026-01-01')"
        )
        inventory = await _deletion_inventory(db, "alice")
        with pytest.raises(RuntimeError, match="mqtt reload failed"):
            await delete_user(
                username="alice",
                body=UserDeletionRequest(revision=inventory.revision),
                request=_http_request("DELETE", "/api/v1/auth/users/alice"),
                admin_user="admin",
                db=db,
            )

        event = await db.fetchone("SELECT outcome, details_json FROM audit_log_entries WHERE action='auth.user.deleted'")
        assert await db.fetchone("SELECT 1 FROM users WHERE username='alice'") is None
        assert event is not None and event["outcome"] == "failed"
        assert "old-secret-hash" not in event["details_json"]
        assert await db.fetchone("SELECT 1 FROM audit_log_entries WHERE action='auth.user.deleted' AND outcome='success'") is None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_history_settings_reload_is_result_audited(monkeypatch) -> None:
    async def fail_reload(_db):
        raise RuntimeError("reload failed")

    monkeypatch.setattr("obs.history.factory.reload_history_plugin", fail_reload)
    db = Database(":memory:")
    await db.connect()
    try:
        contract = ROUTE_SECURITY_CONTRACTS[("PUT", "/api/v1/system/history/settings")]
        assert (contract.audit_mode, contract.audit_effect) == (AuditMode.RESULT, AuditEffect.EXTERNAL_MUTATION)
        with pytest.raises(HTTPException, match="Settings saved"):
            await update_history_settings(
                body=HistorySettingsIn(plugin="sqlite"),
                request=_http_request("PUT", "/api/v1/system/history/settings"),
                db=db,
                _admin="admin",
            )
        saved = await db.fetchone("SELECT value FROM app_settings WHERE key='history.plugin'")
        audit = await db.fetchone("SELECT outcome FROM audit_log_entries WHERE action='system.history.settings_updated'")
        assert saved is not None and saved["value"] == "sqlite"
        assert audit is not None and audit["outcome"] == "failed"
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_history_test_does_not_retry_failed_audit(monkeypatch) -> None:
    calls = 0

    async def fail_audit(*_args, **_kwargs):
        nonlocal calls
        calls += 1
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr("obs.api.audit.AuditLogWriter.write_contract", fail_audit)
    db = Database(":memory:")
    await db.connect()
    try:
        with pytest.raises(RuntimeError, match="audit unavailable"):
            await run_history_test(body=HistorySettingsIn(plugin="sqlite"), _admin="admin", db=db)
        assert calls == 1
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_early_autobackup_failures_are_audited(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr("obs.api.v1.autobackup._autobackup_dir", lambda: tmp_path)
    db = Database(":memory:")
    await db.connect()
    try:
        with pytest.raises(HTTPException, match="Ungültiger"):
            await restore_autobackup("../invalid", request=_http_request("POST", "/api/v1/config/autobackup/restore/invalid"), _admin="admin", db=db)
        with pytest.raises(HTTPException, match="nicht gefunden"):
            await delete_autobackup(
                "20260101-0300",
                request=_http_request("DELETE", "/api/v1/config/autobackup/20260101-0300"),
                _admin="admin",
                db=db,
            )
        rows = await db.fetchall("SELECT action, outcome FROM audit_log_entries ORDER BY id")
        assert [(row["action"], row["outcome"]) for row in rows] == [
            ("autobackup.restored", "failed"),
            ("autobackup.deleted", "failed"),
        ]
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_database_import_reconnects_and_audits_backup_failure(monkeypatch) -> None:
    original_connect = sqlite3.connect
    calls = 0

    def fail_first_connect(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise sqlite3.OperationalError("restore failed")
        return original_connect(*args, **kwargs)

    db = Database(":memory:")
    await db.connect()
    monkeypatch.setattr("obs.api.v1.config.sqlite3.connect", fail_first_connect)
    upload = UploadFile(filename="restore.sqlite", file=BytesIO(b"SQLite format 3\x00" + b"invalid"))
    try:
        with pytest.raises(HTTPException, match="Datenbankwiederherstellung"):
            await import_db(request=_http_request("POST", "/api/v1/config/import/db"), file=upload, _admin="admin", db=db)
        audit = await db.fetchone("SELECT outcome FROM audit_log_entries WHERE action='config.database.imported'")
        assert audit is not None and audit["outcome"] == "failed"
        assert await db.fetchone("SELECT 1 AS ok") is not None
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_invalid_token_on_mutation_is_canonically_audited() -> None:
    db = Database(":memory:")
    await db.connect()
    try:
        request = _http_request("DELETE", "/api/v1/config/reset")
        with pytest.raises(HTTPException) as exc_info:
            await get_current_principal(
                credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid-token"),
                api_key=None,
                db=db,
                request=request,
            )
        assert exc_info.value.status_code == 401
        audit = await db.fetchone("SELECT principal_type, principal_id, action, outcome, route_template FROM audit_log_entries")
        assert audit is not None
        assert (audit["principal_type"], audit["principal_id"]) == ("anonymous", None)
        assert (audit["action"], audit["outcome"], audit["route_template"]) == (
            "config.factory_reset",
            "denied",
            "/api/v1/config/reset",
        )
    finally:
        await db.disconnect()


@pytest.mark.asyncio
async def test_invalid_token_remains_401_when_denial_audit_is_unavailable(monkeypatch) -> None:
    async def fail_audit(*_args, **_kwargs):
        raise RuntimeError("audit unavailable")

    monkeypatch.setattr(AuditLogWriter, "write_contract", fail_audit)
    db = Database(":memory:")
    await db.connect()
    try:
        with pytest.raises(HTTPException) as exc_info:
            await get_current_principal(
                credentials=HTTPAuthorizationCredentials(scheme="Bearer", credentials="invalid-token"),
                api_key=None,
                db=db,
                request=_http_request("DELETE", "/api/v1/config/reset"),
            )
        assert exc_info.value.status_code == 401
    finally:
        await db.disconnect()
