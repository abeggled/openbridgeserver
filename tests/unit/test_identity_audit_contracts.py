"""Wave 14 audit coverage for identity, settings, support and backup routes."""

from __future__ import annotations

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from obs.api.auth import Principal, get_admin_user
from obs.api.v1.security_contract_registry import ROUTE_SECURITY_CONTRACTS
from obs.api.v1.system import AppSettingsIn, update_app_settings
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
