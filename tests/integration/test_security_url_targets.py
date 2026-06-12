from __future__ import annotations

from unittest.mock import patch

from obs.api.auth import create_access_token


def _headers_for(username: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(username)}"}


async def test_url_target_allowlist_admin_flow(client, auth_headers):
    blocked = await client.post(
        "/api/v1/security/url-target-check",
        json={"url": "http://10.38.113.23/api/v1/status"},
        headers=auth_headers,
    )
    assert blocked.status_code == 200
    blocked_body = blocked.json()
    assert blocked_body["allowed"] is False
    assert blocked_body["suggested_target"] == "10.38.113.23/32"

    created = await client.post(
        "/api/v1/security/url-target-allowlist",
        json={"target": blocked_body["suggested_target"], "reason": "integration test"},
        headers=auth_headers,
    )
    assert created.status_code == 200
    assert created.json()["target"] == "10.38.113.23/32"

    allowed = await client.post(
        "/api/v1/security/url-target-check",
        json={"url": "http://10.38.113.23/api/v1/status"},
        headers=auth_headers,
    )
    assert allowed.status_code == 200
    assert allowed.json()["allowed"] is True

    listed = await client.get("/api/v1/security/url-target-allowlist", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["entries"][0]["target"] == "10.38.113.23/32"

    deleted = await client.delete(
        "/api/v1/security/url-target-allowlist",
        params={"target": "10.38.113.23/32"},
        headers=auth_headers,
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True


async def test_url_target_allowlist_fqdn_suggested_ip_flow(client, auth_headers):
    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("10.38.113.23", 0))]):
        blocked = await client.post(
            "/api/v1/security/url-target-check",
            json={"url": "http://internal.example/api/v1/status"},
            headers=auth_headers,
        )
        assert blocked.status_code == 200
        blocked_body = blocked.json()
        assert blocked_body["allowed"] is False
        assert blocked_body["host"] == "internal.example"
        assert blocked_body["blocked_ips"] == ["10.38.113.23"]
        assert blocked_body["suggested_target"] == "10.38.113.23/32"

        created = await client.post(
            "/api/v1/security/url-target-allowlist",
            json={"target": blocked_body["suggested_target"], "reason": "fqdn integration test"},
            headers=auth_headers,
        )
        assert created.status_code == 200
        assert created.json()["target"] == "10.38.113.23/32"

        allowed = await client.post(
            "/api/v1/security/url-target-check",
            json={"url": "http://internal.example/api/v1/status"},
            headers=auth_headers,
        )
        assert allowed.status_code == 200
        allowed_body = allowed.json()
        assert allowed_body["allowed"] is True
        assert allowed_body["host"] == "internal.example"
        assert allowed_body["allowlisted_by"] == "10.38.113.23/32"


async def test_url_target_allowlist_rejects_invalid_target_values(client, auth_headers):
    before = await client.get("/api/v1/security/url-target-allowlist", headers=auth_headers)
    assert before.status_code == 200
    before_entries = before.json()["entries"]

    for target in ["not a host", "Gugeseli", "10.38.113.23/33", "http://internal.example:99999/status"]:
        created = await client.post(
            "/api/v1/security/url-target-allowlist",
            json={"target": target, "reason": "invalid integration test"},
            headers=auth_headers,
        )
        assert created.status_code == 400
        assert created.json()["detail"]

    with patch("obs.security.url_targets.socket.getaddrinfo", side_effect=OSError("dns down")):
        unresolved = await client.post(
            "/api/v1/security/url-target-allowlist",
            json={"target": "internal.example", "reason": "unresolved integration test"},
            headers=auth_headers,
        )
    assert unresolved.status_code == 400
    assert "FQDN target must resolve" in unresolved.json()["detail"]

    listed = await client.get("/api/v1/security/url-target-allowlist", headers=auth_headers)
    assert listed.status_code == 200
    assert listed.json()["entries"] == before_entries


async def test_url_target_check_allows_authenticated_non_admin_but_not_allowlist_write(client):
    headers = _headers_for("graph-editor")

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        checked = await client.post(
            "/api/v1/security/url-target-check",
            json={"url": "http://example.com/status"},
            headers=headers,
        )

    assert checked.status_code == 200
    assert checked.json()["allowed"] is True

    created = await client.post(
        "/api/v1/security/url-target-allowlist",
        json={"target": "10.38.113.23/32", "reason": "non-admin must not write"},
        headers=headers,
    )
    assert created.status_code == 403
