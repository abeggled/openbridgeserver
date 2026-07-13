from __future__ import annotations

import json
import uuid

import pytest
from fastapi import HTTPException, Request
from pydantic import ValidationError

from obs.api.auth import (
    ApiKeyCapabilitiesReplace,
    Principal,
    _api_key_capabilities_response,
    get_api_key_capabilities,
    replace_api_key_capabilities,
)
from obs.api.capabilities import ConfigCapability, api_key_id, audit_config_capability_use, require_config_capability
from obs.db.database import Database


@pytest.fixture
async def db() -> Database:
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


def _request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "PUT",
            "path": "/api/v1/auth/apikeys/key/capabilities",
            "headers": [],
            "client": ("127.0.0.1", 1234),
        }
    )


async def _insert_key(db: Database, *, key_id: str | None = None) -> str:
    resolved_id = key_id or str(uuid.uuid4())
    await db.execute_and_commit(
        "INSERT INTO api_keys (id, name, key_hash, owner, created_at) VALUES (?, 'Automation', ?, 'admin', 'now')",
        (resolved_id, f"hash-{resolved_id}"),
    )
    return resolved_id


def _api_key_principal(key_id: str) -> Principal:
    return Principal(subject=f"api_key:{key_id}", type="api_key", is_admin=False, owner="admin")


def test_capability_replacement_rejects_unknown_wildcard_admin_and_duplicates():
    for capabilities in (["*"], ["admin"], ["arbitrary"], ["visu.page_config.write", "visu.page_config.write"]):
        with pytest.raises(ValidationError):
            ApiKeyCapabilitiesReplace(expected_revision=0, capabilities=capabilities)


def test_capability_helpers_reject_non_key_principals():
    assert api_key_id(Principal(subject="admin", type="user", is_admin=True)) is None


@pytest.mark.asyncio
async def test_existing_api_key_defaults_to_empty_capability_set(db: Database):
    key_id = await _insert_key(db)

    response = await _api_key_capabilities_response(db, key_id)

    assert response.revision == 0
    assert response.capabilities == []
    assert set(response.available_capabilities) == {capability.value for capability in ConfigCapability}
    assert (await get_api_key_capabilities(key_id, _admin="admin", db=db)) == response

    with pytest.raises(HTTPException) as exc_info:
        await _api_key_capabilities_response(db, "missing-key")
    assert exc_info.value.status_code == 404

    await audit_config_capability_use(
        db,
        Principal(subject="admin", type="user", is_admin=True),
        ConfigCapability.VISU_PAGE_CONFIG_WRITE,
        target_type="visu_page",
        target_id="page",
        allowed=True,
        request=None,
    )


@pytest.mark.asyncio
async def test_replacement_is_revisioned_atomic_and_rejects_stale_writes(db: Database):
    key_id = await _insert_key(db)
    body = ApiKeyCapabilitiesReplace(expected_revision=0, capabilities=[ConfigCapability.VISU_PAGE_CONFIG_WRITE.value])

    response = await replace_api_key_capabilities(key_id, body, _request(), admin_user="admin", db=db)
    assert response.revision == 1
    assert response.capabilities == [ConfigCapability.VISU_PAGE_CONFIG_WRITE.value]

    with pytest.raises(HTTPException) as exc_info:
        await replace_api_key_capabilities(key_id, body, _request(), admin_user="admin", db=db)
    assert exc_info.value.status_code == 409
    assert (await _api_key_capabilities_response(db, key_id)).capabilities == [ConfigCapability.VISU_PAGE_CONFIG_WRITE.value]


@pytest.mark.asyncio
async def test_capability_revocation_is_immediate_and_audit_is_secret_free(db: Database):
    key_id = await _insert_key(db)
    principal = _api_key_principal(key_id)
    capability = ConfigCapability.DATAPOINT_METADATA_WRITE

    with pytest.raises(HTTPException):
        await require_config_capability(
            db,
            principal,
            capability,
            target_type="datapoint",
            target_id="dp-1",
            request=_request(),
        )

    granted = await replace_api_key_capabilities(
        key_id,
        ApiKeyCapabilitiesReplace(expected_revision=0, capabilities=[capability.value]),
        _request(),
        admin_user="admin",
        db=db,
    )
    assert await require_config_capability(
        db,
        principal,
        capability,
        target_type="datapoint",
        target_id="dp-1",
        request=_request(),
    )

    await replace_api_key_capabilities(
        key_id,
        ApiKeyCapabilitiesReplace(expected_revision=granted.revision, capabilities=[]),
        _request(),
        admin_user="admin",
        db=db,
    )
    with pytest.raises(HTTPException):
        await require_config_capability(
            db,
            principal,
            capability,
            target_type="datapoint",
            target_id="dp-1",
            request=_request(),
        )

    audit_rows = await db.fetchall("SELECT actor, action, resource_id, details_json FROM audit_log_entries ORDER BY id")
    assert {row["action"] for row in audit_rows} >= {
        "api_key.capability.grant",
        "api_key.capability.revoke",
        "auth.api_key.capabilities_replaced",
        "api_key.capability.use",
    }
    serialized = json.dumps([dict(row) for row in audit_rows])
    assert key_id in serialized
    assert "hash-" not in serialized
    assert "X-API-Key" not in serialized
