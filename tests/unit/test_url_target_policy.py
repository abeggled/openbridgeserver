from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi import HTTPException

from obs.api.v1.security import (
    UrlTargetCheckIn,
    UrlTargetAllowlistCreate,
    check_url_target,
    create_url_target_allowlist_entry,
    delete_url_target_allowlist_entry,
    get_url_target_allowlist,
)
from obs.config import SecuritySettings, Settings, override_settings
from obs.security.url_targets import (
    add_allowed_url_target,
    evaluate_url_target,
    list_allowed_url_targets,
    remove_allowed_url_target,
    resolve_url_target,
)


def _settings_for(path) -> Settings:
    return Settings(security=SecuritySettings(jwt_secret="unit-test-secret-32-chars-xxx", url_target_allowlist_path=str(path)))


def test_private_ip_is_blocked_without_allowlist(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    decision = evaluate_url_target("http://10.38.113.23/api/v1/status")

    assert decision.allowed is False
    assert decision.blocked_ips == ["10.38.113.23"]
    assert decision.suggested_target == "10.38.113.23/32"


def test_private_ip_is_allowed_by_yaml_allowlist(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    add_allowed_url_target("10.38.113.23", reason="internal test target", created_by="admin")

    decision = evaluate_url_target("http://10.38.113.23/api/v1/status")

    assert decision.allowed is True
    assert decision.allowlisted_by == "10.38.113.23/32"
    entries = list_allowed_url_targets()
    assert entries[0].target == "10.38.113.23/32"
    assert entries[0].reason == "internal test target"


def test_hostname_resolving_to_private_ip_uses_allowlisted_network(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    add_allowed_url_target("10.38.113.0/24", reason="internal subnet", created_by="admin")

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("10.38.113.23", 0))]):
        decision = evaluate_url_target("http://internal.example/status")

    assert decision.allowed is True
    assert decision.resolved_ips == ["10.38.113.23"]
    assert decision.allowlisted_by == "10.38.113.0/24"


def test_remove_allowlist_entry(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    add_allowed_url_target("10.38.113.23", reason="", created_by="admin")

    assert remove_allowed_url_target("10.38.113.23/32") is True
    assert list_allowed_url_targets() == []


def test_rejects_empty_allowlist_target(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    try:
        add_allowed_url_target(" ")
    except ValueError as exc:
        assert "must not be empty" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("empty target should be rejected")


def test_rejects_url_target_without_hostname(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    try:
        add_allowed_url_target("http:///missing-host")
    except ValueError as exc:
        assert "hostname" in str(exc)
    else:  # pragma: no cover - defensive assertion
        raise AssertionError("URL without hostname should be rejected")


def test_legacy_string_allowlist_entries_are_loaded(tmp_path):
    allowlist = tmp_path / "allow.yaml"
    allowlist.write_text("- 10.38.113.23\n", encoding="utf-8")
    override_settings(_settings_for(allowlist))

    entries = list_allowed_url_targets()

    assert len(entries) == 1
    assert entries[0].target == "10.38.113.23/32"
    assert entries[0].reason == ""


def test_invalid_allowlist_documents_are_ignored(tmp_path):
    allowlist = tmp_path / "allow.yaml"
    allowlist.write_text("42\n", encoding="utf-8")
    override_settings(_settings_for(allowlist))

    assert list_allowed_url_targets() == []

    allowlist.write_text("version: 1\nallowed_targets: nope\n", encoding="utf-8")
    assert list_allowed_url_targets() == []


def test_invalid_allowlist_items_are_skipped(tmp_path):
    allowlist = tmp_path / "allow.yaml"
    allowlist.write_text(
        "version: 1\nallowed_targets:\n  - 123\n  - target: ''\n  - target: internal.example\n",
        encoding="utf-8",
    )
    override_settings(_settings_for(allowlist))

    entries = list_allowed_url_targets()

    assert [entry.target for entry in entries] == ["internal.example"]


def test_remove_missing_allowlist_entry_returns_false(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    assert remove_allowed_url_target("10.38.113.23") is False


def test_hostname_allowlist_allows_dns_failure(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    add_allowed_url_target("http://internal.example/path")

    with patch("obs.security.url_targets.socket.getaddrinfo", side_effect=OSError("dns down")):
        decision = evaluate_url_target("http://internal.example/status")

    assert decision.allowed is True
    assert decision.allowlisted_by == "internal.example"
    assert decision.resolved_ips == []


def test_exact_hostname_allowlist_matches_resolved_private_ip(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    add_allowed_url_target("internal.example")

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("10.38.113.23", 0))]):
        decision = evaluate_url_target("http://internal.example/status")

    assert decision.allowed is True
    assert decision.allowlisted_by == "internal.example"


def test_evaluate_rejects_wrong_scheme_and_missing_host(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    assert evaluate_url_target("ftp://example.com/file").reason == "Only HTTP/HTTPS URLs are allowed"
    assert evaluate_url_target("http:///path").reason == "URL has no hostname"
    assert evaluate_url_target("http://example.com", require_https=True).reason == "Only HTTPS URLs are allowed"


def test_evaluate_rejects_invalid_port(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    decision = evaluate_url_target("http://example.com:bad/")

    assert decision.allowed is False
    assert "Invalid URL host or port" in decision.reason


def test_evaluate_allows_loopback_when_requested(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    decision = evaluate_url_target("http://localhost/status", allow_loopback=True)

    assert decision.allowed is True
    assert decision.resolved_ips == ["127.0.0.1"]


def test_evaluate_handles_invalid_dns_answer(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("not-an-ip", 0))]):
        decision = evaluate_url_target("http://bad-dns.example/status")

    assert decision.allowed is False
    assert decision.blocked_ips == ["not-an-ip"]
    assert decision.suggested_target == "not-an-ip"


def test_evaluate_rejects_empty_dns_answer(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[]):
        decision = evaluate_url_target("http://empty.example/status")

    assert decision.allowed is False
    assert decision.reason == "Hostname did not resolve to any usable address"


def test_resolve_url_target_returns_dns_pinned_target(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        target = resolve_url_target("https://example.com:8443/status", require_https=True)

    assert target.scheme == "https"
    assert target.hostname_ascii == "example.com"
    assert target.port == 8443
    assert target.addresses == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_security_api_rejects_invalid_create_target(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with pytest.raises(HTTPException) as exc:
        await create_url_target_allowlist_entry(UrlTargetAllowlistCreate(target=" "), admin="admin")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
async def test_security_api_happy_paths(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    created = await create_url_target_allowlist_entry(
        UrlTargetAllowlistCreate(target="10.38.113.23", reason="unit"),
        admin="admin",
    )
    assert created.target == "10.38.113.23/32"
    assert created.created_by == "admin"

    listed = await get_url_target_allowlist(_admin="admin")
    assert listed.entries[0].target == "10.38.113.23/32"

    checked = await check_url_target(
        UrlTargetCheckIn(url="http://10.38.113.23/api/v1/status"),
        _admin="admin",
    )
    assert checked.allowed is True
    assert checked.allowlisted_by == "10.38.113.23/32"

    deleted = await delete_url_target_allowlist_entry("10.38.113.23/32", _admin="admin")
    assert deleted == {"deleted": True}


@pytest.mark.asyncio
async def test_security_api_delete_error_paths(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with pytest.raises(HTTPException) as missing:
        await delete_url_target_allowlist_entry("10.38.113.23/32", _admin="admin")
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as invalid:
        await delete_url_target_allowlist_entry(" ", _admin="admin")
    assert invalid.value.status_code == 400
