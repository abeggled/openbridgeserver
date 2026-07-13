from __future__ import annotations

import json
import uuid

import pytest

from obs.db.database import get_db

pytestmark = pytest.mark.integration


async def _replace_capabilities(client, auth_headers, key_id: str, revision: int, capabilities: list[str]):
    return await client.put(
        f"/api/v1/auth/apikeys/{key_id}/capabilities",
        json={"expected_revision": revision, "capabilities": capabilities},
        headers=auth_headers,
    )


async def test_api_key_configuration_capabilities_are_exact_scoped_revocable_and_secret_free(client, auth_headers):
    created_key = await client.post(
        "/api/v1/auth/apikeys",
        json={"name": f"capability-key-{uuid.uuid4().hex[:8]}"},
        headers=auth_headers,
    )
    assert created_key.status_code == 201, created_key.text
    key_id = created_key.json()["id"]
    actual_key_secret = created_key.json()["key"]
    key_headers = {"X-API-Key": actual_key_secret}

    created_dp = await client.post(
        "/api/v1/datapoints/",
        json={"name": f"Capability DP {uuid.uuid4().hex[:8]}", "data_type": "FLOAT"},
        headers=auth_headers,
    )
    assert created_dp.status_code == 201, created_dp.text
    dp_id = created_dp.json()["id"]

    created_page = await client.post(
        "/api/v1/visu/nodes",
        json={"name": f"Capability Page {uuid.uuid4().hex[:8]}", "type": "PAGE", "access": "public"},
        headers=auth_headers,
    )
    assert created_page.status_code == 201, created_page.text
    page_id = created_page.json()["id"]
    page_config = {"grid_cols": 12, "grid_row_height": 80, "background": None, "widgets": []}

    try:
        default_state = await client.get(f"/api/v1/auth/apikeys/{key_id}/capabilities", headers=auth_headers)
        assert default_state.status_code == 200
        assert default_state.json()["revision"] == 0
        assert default_state.json()["capabilities"] == []

        self_escalation = await client.put(
            f"/api/v1/auth/apikeys/{key_id}/capabilities",
            json={"expected_revision": 0, "capabilities": ["datapoint.metadata.write"]},
            headers=key_headers,
        )
        assert self_escalation.status_code == 403
        assert (await client.patch(f"/api/v1/datapoints/{dp_id}", json={"name": "Denied"}, headers=key_headers)).status_code == 403

        grant_state = await client.get(f"/api/v1/authz/principals/api_key/{key_id}/grants", headers=auth_headers)
        assert grant_state.status_code == 200
        grant_replace = await client.put(
            f"/api/v1/authz/principals/api_key/{key_id}/grants",
            json={"grants": [{"node_type": "datapoint", "node_id": dp_id, "role": "resident", "effect": "allow"}]},
            headers={**auth_headers, "If-Match": grant_state.headers["etag"]},
        )
        assert grant_replace.status_code == 200, grant_replace.text

        granted = await _replace_capabilities(client, auth_headers, key_id, 0, ["datapoint.metadata.write"])
        assert granted.status_code == 200, granted.text
        assert granted.json()["revision"] == 1

        metadata_write = await client.patch(f"/api/v1/datapoints/{dp_id}", json={"name": "Delegated metadata"}, headers=key_headers)
        assert metadata_write.status_code == 200, metadata_write.text
        assert metadata_write.json()["name"] == "Delegated metadata"
        assert (await client.patch(f"/api/v1/datapoints/{dp_id}", json={"value": 42}, headers=key_headers)).status_code == 403
        assert (await client.put(f"/api/v1/visu/pages/{page_id}", json=page_config, headers=key_headers)).status_code == 403

        visu_only = await _replace_capabilities(client, auth_headers, key_id, 1, ["visu.page_config.write"])
        assert visu_only.status_code == 200, visu_only.text
        assert (await client.put(f"/api/v1/visu/pages/{page_id}", json=page_config, headers=key_headers)).status_code == 204
        assert (await client.patch(f"/api/v1/datapoints/{dp_id}", json={"name": "Wrong capability"}, headers=key_headers)).status_code == 403

        revoked = await _replace_capabilities(client, auth_headers, key_id, 2, [])
        assert revoked.status_code == 200, revoked.text
        assert (await client.put(f"/api/v1/visu/pages/{page_id}", json=page_config, headers=key_headers)).status_code == 403

        stale = await _replace_capabilities(client, auth_headers, key_id, 2, ["visu.page_config.write"])
        assert stale.status_code == 409

        audit_rows = await get_db().fetchall(
            "SELECT actor, action, resource_id, details_json FROM audit_log_entries WHERE details_json LIKE ? ORDER BY id",
            (f'%"api_key_id":"{key_id}"%',),
        )
        actions = {row["action"] for row in audit_rows}
        assert {"api_key.capability.grant", "api_key.capability.revoke", "api_key.capability.use"} <= actions
        audit_json = json.dumps([dict(row) for row in audit_rows])
        assert actual_key_secret not in audit_json
    finally:
        await client.delete(f"/api/v1/visu/nodes/{page_id}", headers=auth_headers)
        await client.delete(f"/api/v1/datapoints/{dp_id}", headers=auth_headers)
        await client.delete(f"/api/v1/auth/apikeys/{key_id}", headers=auth_headers)
