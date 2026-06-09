from __future__ import annotations

from unittest.mock import AsyncMock, patch

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
    UrlTargetAllowEntry,
    UrlTargetAllowlistReadError,
    UrlTargetDecision,
    _match_allowlist,
    add_allowed_url_target,
    build_pinned_url_targets,
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


def test_private_ip_can_be_allowed_for_authenticated_weather_mode(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    decision = evaluate_url_target("http://10.38.113.23/api/v1/status", allow_private_networks=True)

    assert decision.allowed is True
    assert decision.resolved_ips == ["10.38.113.23"]


def test_allow_private_networks_does_not_allow_link_local_targets(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    decision = evaluate_url_target("http://169.254.169.254/latest/meta-data/", allow_private_networks=True)

    assert decision.allowed is False
    assert decision.blocked_ips == ["169.254.169.254"]


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


def test_hostname_resolving_to_allowlisted_ip_is_allowed(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    add_allowed_url_target("10.38.113.23", reason="internal host", created_by="admin")

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("10.38.113.23", 0))]):
        decision = evaluate_url_target("http://internal.example/status")

    assert decision.allowed is True
    assert decision.resolved_ips == ["10.38.113.23"]
    assert decision.allowlisted_by == "10.38.113.23/32"


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


@pytest.mark.parametrize(
    "target",
    [
        "not a host",
        "Gugeseli",
        "*.internal.example",
        "-internal.example",
        "internal-.example",
        "internal..example",
        "10.38.113.23/33",
        "10.38.113.23/24",
        "999.999.999.999",
        "http://exa mple.local/status",
        "http://internal.example:99999/status",
        "http://[broken/status",
    ],
)
def test_rejects_invalid_allowlist_target_values(tmp_path, target):
    allowlist = tmp_path / "allow.yaml"
    override_settings(_settings_for(allowlist))

    with pytest.raises(ValueError):
        add_allowed_url_target(target, reason="invalid")

    assert not allowlist.exists()


def test_rejects_unresolvable_fqdn_allowlist_target(tmp_path):
    allowlist = tmp_path / "allow.yaml"
    override_settings(_settings_for(allowlist))

    with patch("obs.security.url_targets.socket.getaddrinfo", side_effect=OSError("dns down")):
        with pytest.raises(ValueError, match="FQDN target must resolve"):
            add_allowed_url_target("internal.example", reason="invalid")

    assert not allowlist.exists()


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


def test_malformed_allowlist_yaml_is_ignored(tmp_path):
    allowlist = tmp_path / "allow.yaml"
    allowlist.write_text("version: 1\nallowed_targets:\n  - [broken\n", encoding="utf-8")
    override_settings(_settings_for(allowlist))

    assert list_allowed_url_targets() == []

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("10.38.113.23", 0))]):
        decision = evaluate_url_target("http://internal.example/status")

    assert decision.allowed is False
    assert decision.blocked_ips == ["10.38.113.23"]


def test_malformed_allowlist_yaml_blocks_writes_without_overwrite(tmp_path):
    allowlist = tmp_path / "allow.yaml"
    broken_yaml = "version: 1\nallowed_targets:\n  - [broken\n"
    allowlist.write_text(broken_yaml, encoding="utf-8")
    override_settings(_settings_for(allowlist))

    with pytest.raises(UrlTargetAllowlistReadError):
        add_allowed_url_target("10.38.113.23/32", reason="unit")

    assert allowlist.read_text(encoding="utf-8") == broken_yaml


def test_invalid_allowlist_structure_blocks_writes_without_overwrite(tmp_path):
    allowlist = tmp_path / "allow.yaml"
    invalid_yaml = "version: 1\nallowed_targets: nope\n"
    allowlist.write_text(invalid_yaml, encoding="utf-8")
    override_settings(_settings_for(allowlist))

    with pytest.raises(UrlTargetAllowlistReadError):
        add_allowed_url_target("10.38.113.23/32", reason="unit")

    with pytest.raises(UrlTargetAllowlistReadError):
        remove_allowed_url_target("10.38.113.23/32")

    assert allowlist.read_text(encoding="utf-8") == invalid_yaml


def test_allowlist_read_errors_are_ignored(tmp_path):
    allowlist = tmp_path / "allow.yaml"
    allowlist.write_bytes(b"\xff\xfe\x00")
    override_settings(_settings_for(allowlist))

    assert list_allowed_url_targets() == []

    allowlist.write_text("version: 1\nallowed_targets: []\n", encoding="utf-8")
    with patch("builtins.open", side_effect=OSError("permission denied")):
        assert list_allowed_url_targets() == []


def test_allowlist_read_errors_block_writes(tmp_path):
    allowlist = tmp_path / "allow.yaml"
    allowlist.write_text("version: 1\nallowed_targets: []\n", encoding="utf-8")
    override_settings(_settings_for(allowlist))

    with patch("builtins.open", side_effect=OSError("permission denied")):
        with pytest.raises(UrlTargetAllowlistReadError):
            add_allowed_url_target("10.38.113.23/32", reason="unit")


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


def test_hostname_allowlist_does_not_allow_dns_failure(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        add_allowed_url_target("http://internal.example/path")

    with patch("obs.security.url_targets.socket.getaddrinfo", side_effect=OSError("dns down")):
        decision = evaluate_url_target("http://internal.example/status")

    assert decision.allowed is False
    assert decision.allowlisted_by is None
    assert decision.resolved_ips == []
    assert "Hostname could not be resolved" in decision.reason


def test_hostname_allowlist_does_not_allow_empty_dns_answer(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        add_allowed_url_target("internal.example")

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[]):
        decision = evaluate_url_target("http://internal.example/status")

    assert decision.allowed is False
    assert decision.reason == "Hostname did not resolve to any usable address"


def test_exact_hostname_allowlist_does_not_override_resolved_private_ip(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        add_allowed_url_target("internal.example")

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("10.38.113.23", 0))]):
        decision = evaluate_url_target("http://internal.example/status")

    assert decision.allowed is False
    assert decision.allowlisted_by is None
    assert decision.blocked_ips == ["10.38.113.23"]
    assert decision.suggested_target == "10.38.113.23/32"


def test_hostname_allowlist_entry_is_skipped_for_resolved_public_ip(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        add_allowed_url_target("other.example")

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        decision = evaluate_url_target("http://internal.example/status")

    assert decision.allowed is True
    assert decision.allowlisted_by is None


def test_hostname_allowlist_match_tolerates_unencodable_hostname(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))
    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        add_allowed_url_target("internal.example")

    assert _match_allowlist("\udcff") is None


def test_url_ip_literal_allowlist_entry_is_canonicalized(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    first = add_allowed_url_target("http://10.38.113.23/api/v1/status", reason="url")
    second = add_allowed_url_target("10.38.113.23", reason="ip")

    entries = list_allowed_url_targets()
    assert first.target == "10.38.113.23/32"
    assert second.target == "10.38.113.23/32"
    assert [entry.target for entry in entries] == ["10.38.113.23/32"]
    assert entries[0].reason == "ip"


def test_bare_hostname_url_target_entry_is_canonicalized(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        first = add_allowed_url_target("internal.example:8443/status", reason="bare")
        second = add_allowed_url_target("https://internal.example/api", reason="url")

    entries = list_allowed_url_targets()
    assert first.target == "internal.example"
    assert second.target == "internal.example"
    assert [entry.target for entry in entries] == ["internal.example"]
    assert entries[0].reason == "url"


def test_evaluate_rejects_wrong_scheme_and_missing_host(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    assert evaluate_url_target("ftp://example.com/file").reason == "Only HTTP/HTTPS URLs are allowed"
    assert evaluate_url_target("http:///path").reason == "URL has no hostname"
    assert evaluate_url_target("http://example.com", require_https=True).reason == "Only HTTPS URLs are allowed"


def test_evaluate_reports_urlparse_exception(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    decision = evaluate_url_target("http://[::1")

    assert decision.allowed is False
    assert decision.reason.startswith("Invalid URL:")


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


def test_evaluate_allows_direct_loopback_ip_when_requested(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    decision = evaluate_url_target("http://127.0.0.1/status", allow_loopback=True)

    assert decision.allowed is True
    assert decision.resolved_ips == ["127.0.0.1"]


def test_evaluate_blocks_nat64_embedded_private_address(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("64:ff9b::a00:1", 0))]):
        decision = evaluate_url_target("http://nat64.example/status")

    assert decision.allowed is False
    assert decision.blocked_ips == ["64:ff9b::a00:1"]
    assert decision.suggested_target == "64:ff9b::a00:1/128"


def test_evaluate_blocks_nat64_embedded_multicast_address(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("64:ff9b::e000:1", 0))]):
        decision = evaluate_url_target("http://nat64.example/status")

    assert decision.allowed is False
    assert decision.blocked_ips == ["64:ff9b::e000:1"]
    assert decision.suggested_target == "64:ff9b::e000:1/128"


@pytest.mark.parametrize("address", ["224.0.0.1", "ff02::1"])
def test_evaluate_blocks_multicast_addresses(tmp_path, address):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, (address, 0))]):
        decision = evaluate_url_target("http://multicast.example/status")

    assert decision.allowed is False
    assert decision.blocked_ips == [address]


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


@pytest.mark.parametrize(
    "url",
    [
        "http://not a host/status",
        "http://*.internal.example/status",
        "http://999.999.999.999/status",
        "http://internal.example:99999/status",
        "http://[broken/status",
    ],
)
def test_evaluate_rejects_invalid_url_host_values(tmp_path, url):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    decision = evaluate_url_target(url)

    assert decision.allowed is False
    assert decision.reason.startswith("Invalid URL")


def test_resolve_url_target_returns_dns_pinned_target(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        target = resolve_url_target("https://example.com:8443/status", require_https=True)

    assert target.scheme == "https"
    assert target.hostname_ascii == "example.com"
    assert target.port == 8443
    assert target.addresses == ["93.184.216.34"]


def test_resolve_url_target_accepts_public_ip_literal(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    target = resolve_url_target("https://93.184.216.34/status", require_https=True)

    assert target.hostname_ascii == "93.184.216.34"
    assert target.addresses == ["93.184.216.34"]


def test_build_pinned_url_targets_sets_host_and_sni(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.security.url_targets.socket.getaddrinfo", return_value=[(None, None, None, None, ("93.184.216.34", 0))]):
        pinned_urls, headers, extensions = build_pinned_url_targets("https://example.com:8443/status?x=1")

    assert pinned_urls == ["https://93.184.216.34:8443/status?x=1"]
    assert headers == {"Host": "example.com:8443"}
    assert extensions == {"sni_hostname": "example.com"}


def test_resolve_url_target_raises_for_blocked_target(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with pytest.raises(ValueError, match="internal, reserved"):
        resolve_url_target("http://10.38.113.23/status")


def test_url_target_decision_api_detail():
    decision = UrlTargetDecision(
        allowed=False,
        url="http://internal.example/status",
        host="internal.example",
        resolved_ips=["10.38.113.23"],
        blocked_ips=["10.38.113.23"],
        reason="blocked",
        allowlisted_by=None,
        suggested_target="10.38.113.23/32",
    )

    assert decision.api_detail() == {
        "code": "url_target_blocked",
        "message": "blocked",
        "url": "http://internal.example/status",
        "host": "internal.example",
        "resolved_ips": ["10.38.113.23"],
        "blocked_ips": ["10.38.113.23"],
        "allowlisted_by": None,
        "suggested_target": "10.38.113.23/32",
    }


@pytest.mark.asyncio
async def test_security_api_rejects_invalid_create_target(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with pytest.raises(HTTPException) as exc:
        await create_url_target_allowlist_entry(UrlTargetAllowlistCreate(target=" "), admin="admin")

    assert exc.value.status_code == 400


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["not a host", "Gugeseli", "10.38.113.23/33", "http://internal.example:99999/status"])
async def test_security_api_rejects_nonsense_create_targets(tmp_path, target):
    allowlist = tmp_path / "allow.yaml"
    override_settings(_settings_for(allowlist))

    with pytest.raises(HTTPException) as exc:
        await create_url_target_allowlist_entry(UrlTargetAllowlistCreate(target=target), admin="admin")

    assert exc.value.status_code == 400
    assert not allowlist.exists()


@pytest.mark.asyncio
async def test_security_api_rejects_unresolvable_fqdn_create_target(tmp_path):
    allowlist = tmp_path / "allow.yaml"
    override_settings(_settings_for(allowlist))

    with patch("obs.security.url_targets.socket.getaddrinfo", side_effect=OSError("dns down")):
        with pytest.raises(HTTPException) as exc:
            await create_url_target_allowlist_entry(UrlTargetAllowlistCreate(target="internal.example"), admin="admin")

    assert exc.value.status_code == 400
    assert "FQDN target must resolve" in exc.value.detail
    assert not allowlist.exists()


@pytest.mark.asyncio
async def test_security_api_reports_allowlist_write_error(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.api.v1.security.add_allowed_url_target", side_effect=OSError("permission denied")):
        with pytest.raises(HTTPException) as exc:
            await create_url_target_allowlist_entry(UrlTargetAllowlistCreate(target="10.38.113.23/32", reason="unit"), admin="admin")

    assert exc.value.status_code == 500
    assert "Could not write URL target allowlist" in exc.value.detail
    assert "permission denied" in exc.value.detail


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
        _user="admin",
    )
    assert checked.allowed is True
    assert checked.allowlisted_by == "10.38.113.23/32"

    deleted = await delete_url_target_allowlist_entry("10.38.113.23/32", _admin="admin")
    assert deleted == {"deleted": True}


@pytest.mark.asyncio
async def test_security_api_check_runs_target_evaluation_off_event_loop(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.api.v1.security.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = UrlTargetDecision(
            allowed=True,
            url="http://example.com/status",
            host="example.com",
            resolved_ips=["93.184.216.34"],
            blocked_ips=[],
            reason="URL target is allowed",
        )

        checked = await check_url_target(
            UrlTargetCheckIn(url="http://example.com/status", require_https=True, allow_loopback=True),
            _user="editor",
        )

    mock_to_thread.assert_awaited_once_with(
        evaluate_url_target,
        "http://example.com/status",
        require_https=True,
        allow_loopback=True,
    )
    assert checked.allowed is True
    assert checked.resolved_ips == ["93.184.216.34"]


@pytest.mark.asyncio
async def test_security_api_create_runs_allowlist_write_off_event_loop(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with patch("obs.api.v1.security.asyncio.to_thread", new_callable=AsyncMock) as mock_to_thread:
        mock_to_thread.return_value = UrlTargetAllowEntry(
            id="unit",
            target="10.38.113.23/32",
            reason="unit",
            created_by="admin",
            created_at="2026-06-04T00:00:00+00:00",
        )

        created = await create_url_target_allowlist_entry(
            UrlTargetAllowlistCreate(target="10.38.113.23/32", reason="unit"),
            admin="admin",
        )

    mock_to_thread.assert_awaited_once_with(
        add_allowed_url_target,
        "10.38.113.23/32",
        reason="unit",
        created_by="admin",
    )
    assert created.target == "10.38.113.23/32"
    assert created.created_by == "admin"


@pytest.mark.asyncio
async def test_security_api_delete_error_paths(tmp_path):
    override_settings(_settings_for(tmp_path / "allow.yaml"))

    with pytest.raises(HTTPException) as missing:
        await delete_url_target_allowlist_entry("10.38.113.23/32", _admin="admin")
    assert missing.value.status_code == 404

    with pytest.raises(HTTPException) as invalid:
        await delete_url_target_allowlist_entry(" ", _admin="admin")
    assert invalid.value.status_code == 400
