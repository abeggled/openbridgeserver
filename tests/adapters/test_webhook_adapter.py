"""Unit tests for the WEBHOOK adapter (issue #1256).

No HTTP server is involved — the adapter's trigger entry point is called
directly, the EventBus is a mock and the DataPoint registry is stubbed.
"""

from __future__ import annotations

import asyncio
import datetime
import time
import uuid

import pytest
from pydantic import ValidationError

from obs.adapters.webhook import adapter as webhook_module
from obs.adapters.webhook.adapter import (
    DEFAULT_PATH_PREFIX,
    FixedWindowRateLimiter,
    RejectionReason,
    WebhookAdapter,
    WebhookAdapterConfig,
    WebhookBindingConfig,
    active_prefixes,
    coerce_webhook_value,
    generate_token,
    normalise_path_prefix,
    resolve_webhook_target,
    token_matches,
)
from obs.core.event_bus import DataValueEvent
from tests.adapters.conftest import make_binding

TOKEN = "s3cr3t-token-value"


class _Dp:
    def __init__(self, dp_id: uuid.UUID, data_type: str = "BOOLEAN", control_class: str = "room_local") -> None:
        self.id = dp_id
        self.name = "Klingel"
        self.data_type = data_type
        self.control_class = control_class


class _Registry:
    def __init__(self, dp: _Dp | None) -> None:
        self._dp = dp

    def get(self, dp_id: uuid.UUID):
        if self._dp is not None and self._dp.id == dp_id:
            return self._dp
        return None


@pytest.fixture(autouse=True)
def _clean_dispatch_registry():
    """Keep the module-level prefix registry isolated between tests."""
    webhook_module._instances_by_prefix.clear()
    yield
    webhook_module._instances_by_prefix.clear()


def _data_events(mock_bus) -> list:
    """Only the value events — ``_publish_status`` uses the same bus."""
    return [call.args[0] for call in mock_bus.publish.await_args_list if isinstance(call.args[0], DataValueEvent)]


def _binding(**config_overrides):
    config = {"slug": "haustuer-klingel", "token": TOKEN, "methods": ["GET"], **config_overrides}
    return make_binding(config)


def _stub_registry(monkeypatch, dp: _Dp | None) -> None:
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(dp))


async def _adapter(mock_bus, bindings, config=None) -> WebhookAdapter:
    instance = WebhookAdapter(mock_bus, config or {}, instance_id=uuid.uuid4(), name="Webhook")
    await instance.connect()
    await instance.reload_bindings(bindings)
    return instance


# ---------------------------------------------------------------------------
# Config schemas
# ---------------------------------------------------------------------------


def test_path_prefix_defaults_and_normalises():
    assert normalise_path_prefix("") == DEFAULT_PATH_PREFIX
    assert normalise_path_prefix("hook") == "/hook"
    assert normalise_path_prefix("/iot/hook/") == "/iot/hook"
    assert WebhookAdapterConfig().path_prefix == DEFAULT_PATH_PREFIX
    assert WebhookAdapterConfig(path_prefix="webhooks").path_prefix == "/webhooks"


@pytest.mark.parametrize(
    "raw",
    [
        "/",
        "/api",
        "/API/x",
        "/a/b/c/d",
        "/bad segment",
        "/-leading",
        "/docs",
        "/redoc",
        "/openapi.json",
        "/favicon.svg",
        "/manifest.webmanifest",
        "/apple-touch-icon.png",
        "/obs_logo_light.svg",
        "/obs_logo_dark.svg",
        "/datapoints",
        "/adapters",
        "/settings",
        "/knx-devices",
        "/login",
    ],
)
def test_path_prefix_rejects_unusable_values(raw):
    with pytest.raises(ValueError):
        normalise_path_prefix(raw)


def test_every_admin_gui_route_is_reserved_as_a_prefix():
    """A new top-level Admin-GUI route must not be claimable as a webhook prefix.

    The middleware answers before the SPA fallback, so a prefix on a history-mode
    route would break direct navigation and reloads of that page.
    """
    import pathlib
    import re

    router = pathlib.Path(__file__).resolve().parents[2] / "gui" / "src" / "router" / "index.js"
    segments = {
        match.group(1).split("/")[0]
        for match in re.finditer(r"path:\s*'/([^']*)'", router.read_text(encoding="utf-8"))
        if match.group(1) and not match.group(1).startswith(":")
    }

    assert segments, "no routes found — the router file layout changed"
    assert segments <= webhook_module._RESERVED_PREFIX_SEGMENTS


def test_adapter_config_has_no_allowlist_of_its_own():
    """The allowlist belongs to the binding, not to the endpoint as a whole."""
    assert not hasattr(WebhookAdapterConfig(), "allowed_networks")


def test_binding_config_carries_the_allowlist():
    assert WebhookBindingConfig(slug="bell").allowed_networks == []
    cfg = WebhookBindingConfig(slug="bell", allowed_networks=["192.168.1.5"])
    assert cfg.allowed_networks == ["192.168.1.5/32"]


def test_binding_config_canonicalises_and_deduplicates_entries():
    cfg = WebhookBindingConfig(slug="bell", allowed_networks=["10.38.111.21/16", "10.38.0.0/16", "10.0.0.0/8"])
    assert cfg.allowed_networks == ["10.38.0.0/16", "10.0.0.0/8"]


def test_binding_config_still_accepts_a_legacy_comma_string():
    cfg = WebhookBindingConfig(slug="bell", allowed_networks=" 10.0.0.0/8, 192.168.1.5 ")
    assert cfg.allowed_networks == ["10.0.0.0/8", "192.168.1.5/32"]


def test_binding_config_rejects_a_malformed_network():
    with pytest.raises(ValidationError):
        WebhookBindingConfig(slug="bell", allowed_networks=["nope"])


def test_binding_config_defaults():
    cfg = WebhookBindingConfig(slug="bell")
    assert cfg.methods == ["GET"]
    assert cfg.value_source == "fixed"
    assert cfg.fixed_value == "true"
    assert cfg.value_param == "value"
    assert cfg.debounce_ms == 0


def test_binding_config_normalises_slug_and_methods():
    cfg = WebhookBindingConfig(slug="  Haustuer-Klingel  ", methods=["GET", "GET", "POST"])
    assert cfg.slug == "haustuer-klingel"
    assert cfg.methods == ["GET", "POST"]


@pytest.mark.parametrize("slug", ["", "-bell", "Bell!", "a" * 65, "bell/other"])
def test_binding_config_rejects_bad_slug(slug):
    with pytest.raises(ValidationError):
        WebhookBindingConfig(slug=slug)


@pytest.mark.parametrize("name", ["token", "Token", " TOKEN "])
def test_binding_config_rejects_the_credential_parameter_as_value_param(name):
    with pytest.raises(ValueError, match="credential"):
        WebhookBindingConfig(slug="bell", value_source="request", value_param=name)


def test_binding_config_rejects_empty_methods_and_value_param():
    with pytest.raises(ValidationError):
        WebhookBindingConfig(slug="bell", methods=[])
    with pytest.raises(ValidationError):
        WebhookBindingConfig(slug="bell", value_param="   ")


# ---------------------------------------------------------------------------
# Token helpers
# ---------------------------------------------------------------------------


def test_generate_token_is_unique_and_long():
    first, second = generate_token(), generate_token()
    assert first != second
    assert len(first) >= 40


@pytest.mark.parametrize(
    ("expected", "provided", "result"),
    [
        (TOKEN, TOKEN, True),
        (TOKEN, "wrong", False),
        (TOKEN, None, False),
        ("", TOKEN, False),
        ("", "", False),
        (TOKEN, "é", False),  # non-ASCII must be a plain mismatch, not a TypeError
        (TOKEN, "tök€n", False),
    ],
)
def test_token_matches(expected, provided, result):
    assert token_matches(expected, provided) is result


# ---------------------------------------------------------------------------
# Value coercion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "data_type", "expected"),
    [
        ("true", "BOOLEAN", True),
        ("1", "BOOLEAN", True),
        ("on", "BOOLEAN", True),
        ("YES", "BOOLEAN", True),
        ("0", "BOOLEAN", False),
        ("off", "BOOLEAN", False),
        (True, "BOOLEAN", True),
        (1, "BOOLEAN", True),
        (0.0, "BOOLEAN", False),
        ("42", "INTEGER", 42),
        ("42.0", "INTEGER", 42),
        (42.0, "INTEGER", 42),
        ("-1.5", "FLOAT", -1.5),
        ("9007199254740993", "INTEGER", 9007199254740993),
        (7, "INTEGER", 7),
        (True, "INTEGER", 1),
        ("1.5", "FLOAT", 1.5),
        (3, "FLOAT", 3.0),
        (False, "FLOAT", 0.0),
        (5, "STRING", "5"),
        ("2026-10-06", "DATE", datetime.date(2026, 10, 6)),
        ("07:30", "TIME", datetime.time(7, 30)),
        ("2026-10-06T07:30:00+00:00", "DATETIME", datetime.datetime(2026, 10, 6, 7, 30, tzinfo=datetime.UTC)),
        ("raw", "UNKNOWN", "raw"),
    ],
)
def test_coerce_webhook_value(raw, data_type, expected):
    assert coerce_webhook_value(raw, data_type) == expected


@pytest.mark.parametrize(
    ("raw", "data_type"),
    [
        ("maybe", "BOOLEAN"),
        (2, "BOOLEAN"),
        (-1, "BOOLEAN"),
        (0.5, "BOOLEAN"),
        (float("nan"), "BOOLEAN"),
        (None, "BOOLEAN"),
        ("abc", "INTEGER"),
        ("42.7", "INTEGER"),
        (42.7, "INTEGER"),
        ("NaN", "FLOAT"),
        (float("nan"), "FLOAT"),
        ("inf", "FLOAT"),
        (float("inf"), "FLOAT"),
        (None, "INTEGER"),
        ("abc", "FLOAT"),
        (None, "FLOAT"),
        ("not-a-date", "DATE"),
        ("25:99", "TIME"),
        ("nope", "DATETIME"),
    ],
)
def test_coerce_webhook_value_rejects_incompatible(raw, data_type):
    with pytest.raises(ValueError):
        coerce_webhook_value(raw, data_type)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------


def test_rate_limiter_allows_up_to_the_limit_then_blocks():
    limiter = FixedWindowRateLimiter(2)
    assert limiter.allow("ip", now=0.0) is True
    assert limiter.allow("ip", now=1.0) is True
    assert limiter.allow("ip", now=2.0) is False


def test_rate_limiter_reports_the_first_denial_of_each_window_once():
    limiter = FixedWindowRateLimiter(1, window_seconds=60.0)
    assert limiter.check("a", now=0.0) == (True, False)
    assert limiter.check("a", now=1.0) == (False, True)
    assert limiter.check("a", now=2.0) == (False, False)
    assert limiter.check("b", now=2.0) == (True, False)
    # a new window announces again
    assert limiter.check("a", now=61.0) == (True, False)
    assert limiter.check("a", now=62.0) == (False, True)


def test_rate_limiter_forgets_announcements_of_pruned_keys():
    limiter = FixedWindowRateLimiter(1, window_seconds=60.0)
    limiter.check("gone", now=0.0)
    limiter.check("gone", now=1.0)
    assert "gone" in limiter._announced
    for index in range(1100):
        limiter._windows[f"old-{index}"] = (0.0, 1)
    limiter.check("fresh", now=120.0)
    assert "gone" not in limiter._announced


def test_rate_limiter_resets_in_the_next_window():
    limiter = FixedWindowRateLimiter(1)
    assert limiter.allow("ip", now=10.0) is True
    assert limiter.allow("ip", now=20.0) is False
    assert limiter.allow("ip", now=61.0) is True


def test_rate_limiter_counts_per_key():
    limiter = FixedWindowRateLimiter(1)
    assert limiter.allow("a", now=0.0) is True
    assert limiter.allow("b", now=0.0) is True
    assert limiter.allow("a", now=0.0) is False


def test_rate_limiter_uses_the_wall_clock_when_no_time_is_given():
    limiter = FixedWindowRateLimiter(1)
    assert limiter.allow("ip") is True
    assert limiter.allow("ip") is False


def test_rate_limiter_prunes_stale_windows():
    limiter = FixedWindowRateLimiter(10)
    for index in range(1100):
        limiter.allow(f"ip-{index}", now=0.0)
    limiter.allow("fresh", now=120.0)
    assert limiter._windows == {"fresh": (120.0, 1)}


# ---------------------------------------------------------------------------
# Lifecycle and dispatch registry
# ---------------------------------------------------------------------------


async def test_connect_claims_its_prefix_and_disconnect_releases_it(mock_bus):
    instance = await _adapter(mock_bus, [])
    assert active_prefixes() == ["/hook"]
    assert instance.connected is True
    assert instance.path_prefix == "/hook"

    await instance.disconnect()
    assert active_prefixes() == []
    assert instance.connected is False


async def test_connect_reports_an_invalid_instance_configuration(mock_bus):
    instance = WebhookAdapter(mock_bus, {"path_prefix": "/api"})
    await instance.connect()
    assert instance.connected is False
    assert instance.last_detail_code == "webhookInvalidConfig"
    assert active_prefixes() == []


async def test_second_instance_on_the_same_prefix_is_refused(mock_bus):
    first = await _adapter(mock_bus, [])
    second = WebhookAdapter(mock_bus, {})
    await second.connect()

    assert second.connected is False
    assert second.last_detail_code == "webhookPrefixConflict"
    assert webhook_module._instances_by_prefix["/hook"] is first


@pytest.mark.parametrize(
    ("claimed", "requested"),
    [("/hook", "/hook/inner"), ("/hook/inner", "/hook"), ("/a/b", "/a/b/c")],
)
async def test_an_instance_on_an_overlapping_prefix_is_refused(mock_bus, claimed, requested):
    first = await _adapter(mock_bus, [], {"path_prefix": claimed})
    nested = WebhookAdapter(mock_bus, {"path_prefix": requested})
    await nested.connect()

    assert nested.connected is False
    assert nested.last_detail_code == "webhookPrefixConflict"
    assert webhook_module._instances_by_prefix == {claimed: first}


async def test_sibling_prefixes_do_not_overlap(mock_bus):
    await _adapter(mock_bus, [], {"path_prefix": "/hook"})
    sibling = WebhookAdapter(mock_bus, {"path_prefix": "/hooks"})
    await sibling.connect()

    assert sibling.connected is True
    assert sorted(active_prefixes()) == ["/hook", "/hooks"]


async def test_reconnecting_the_same_instance_keeps_its_prefix(mock_bus):
    instance = await _adapter(mock_bus, [])
    await instance.connect()
    assert instance.connected is True
    assert active_prefixes() == ["/hook"]


async def test_resolve_webhook_target_matches_only_claimed_prefixes(mock_bus):
    instance = await _adapter(mock_bus, [], {"path_prefix": "/iot/hook"})

    assert resolve_webhook_target("/iot/hook/bell") == (instance, "bell")
    assert resolve_webhook_target("/iot/hook/bell/tok") == (instance, "bell/tok")
    assert resolve_webhook_target("/iot/hook") == (instance, "")
    assert resolve_webhook_target("/iot/hook/") == (instance, "")
    assert resolve_webhook_target("/iot/hooks/bell") is None
    assert resolve_webhook_target("/datapoints/42") is None


def test_resolve_webhook_target_without_running_instances():
    assert resolve_webhook_target("/hook/bell") is None


async def test_sibling_prefixes_resolve_to_their_own_instance(mock_bus):
    # Nested prefixes are refused at registration (see the overlap test), so a
    # path can never be claimed by two instances and dispatch needs no
    # "longest wins" tie-break that would silently shadow the outer one.
    first = await _adapter(mock_bus, [], {"path_prefix": "/hook"})
    second = await _adapter(mock_bus, [], {"path_prefix": "/hooks"})

    assert resolve_webhook_target("/hook/bell") == (first, "bell")
    assert resolve_webhook_target("/hooks/bell") == (second, "bell")


# ---------------------------------------------------------------------------
# Binding index
# ---------------------------------------------------------------------------


async def test_only_source_bindings_are_indexed(mock_bus):
    source = _binding()
    dest = make_binding({"slug": "other", "token": TOKEN}, direction="DEST")
    instance = await _adapter(mock_bus, [source, dest])

    assert set(instance._by_slug) == {"haustuer-klingel"}


async def test_both_direction_bindings_are_indexed(mock_bus):
    both = make_binding({"slug": "bell", "token": TOKEN}, direction="BOTH")
    instance = await _adapter(mock_bus, [both])

    assert set(instance._by_slug) == {"bell"}


async def test_invalid_binding_configuration_is_skipped(mock_bus):
    instance = await _adapter(mock_bus, [make_binding({"slug": "not a slug"})])
    assert instance._by_slug == {}


async def test_duplicate_slugs_raise_a_warning_status(mock_bus):
    first = _binding()
    second = _binding()
    instance = await _adapter(mock_bus, [first, second])

    assert instance._by_slug["haustuer-klingel"] is first
    assert instance.last_severity == "warning"
    assert instance.last_detail_code == "webhookDuplicateSlug"


async def test_reloading_bindings_drops_statistics_of_removed_bindings(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])
    await instance.handle_trigger(method="GET", remainder=f"haustuer-klingel/{TOKEN}", query_params={}, body=b"", peer_ip="10.0.0.1")
    assert instance.stats_for(binding.id).call_count == 1

    await instance.reload_bindings([])
    assert instance.stats_for(binding.id).call_count == 0


async def test_read_and_write_are_inert(mock_bus):
    binding = _binding()
    instance = await _adapter(mock_bus, [binding])

    assert await instance.read(binding) is None
    assert await instance.write(binding, True) is None


# ---------------------------------------------------------------------------
# Trigger — happy paths
# ---------------------------------------------------------------------------


async def test_get_with_query_token_publishes_the_fixed_value(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(
        method="GET",
        remainder="haustuer-klingel",
        query_params={"token": TOKEN},
        body=b"",
        peer_ip="192.168.1.9",
    )

    assert outcome.status == 204
    event = _data_events(mock_bus)[-1]
    assert event.datapoint_id == binding.datapoint_id
    assert event.value is True
    assert event.quality == "good"
    assert event.source_adapter == "WEBHOOK"
    assert event.binding_id == binding.id

    stats = instance.stats_for(binding.id)
    assert (stats.call_count, stats.publish_count, stats.last_status) == (1, 1, 204)
    assert isinstance(stats.last_called, datetime.datetime)


async def test_token_in_the_path_is_accepted(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(
        method="GET",
        remainder=f"haustuer-klingel/{TOKEN}",
        query_params={},
        body=b"",
        peer_ip="192.168.1.9",
    )

    assert outcome.status == 204
    assert len(_data_events(mock_bus)) == 1


async def test_slug_lookup_is_case_insensitive(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(
        method="GET",
        remainder="Haustuer-Klingel",
        query_params={"token": TOKEN},
        body=b"",
        peer_ip="192.168.1.9",
    )
    assert outcome.status == 204


async def test_value_from_query_parameter(mock_bus, monkeypatch):
    binding = _binding(value_source="request")
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="INTEGER"))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(
        method="GET",
        remainder="haustuer-klingel",
        query_params={"token": TOKEN, "value": "23"},
        body=b"",
        peer_ip="192.168.1.9",
    )

    assert outcome.status == 204
    assert _data_events(mock_bus)[-1].value == 23


async def test_value_from_json_body_on_post(mock_bus, monkeypatch):
    binding = _binding(methods=["POST"], value_source="request")
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="FLOAT"))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(
        method="POST",
        remainder="haustuer-klingel",
        query_params={"token": TOKEN},
        body=b'{"value": 21.5}',
        peer_ip="192.168.1.9",
    )

    assert outcome.status == 204
    assert _data_events(mock_bus)[-1].value == 21.5


async def test_post_falls_back_to_the_query_parameter(mock_bus, monkeypatch):
    binding = _binding(methods=["POST"], value_source="request")
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="INTEGER"))
    instance = await _adapter(mock_bus, [binding])

    for body in (b"not json at all", b'{"other": 1}', b"\xff\xfe"):
        mock_bus.publish.reset_mock()
        outcome = await instance.handle_trigger(
            method="POST",
            remainder="haustuer-klingel",
            query_params={"token": TOKEN, "value": "5"},
            body=body,
            peer_ip="192.168.1.9",
        )
        assert outcome.status == 204
        assert _data_events(mock_bus)[-1].value == 5


async def test_custom_value_param_name(mock_bus, monkeypatch):
    binding = _binding(value_source="request", value_param="kovalue")
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="INTEGER"))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(
        method="GET",
        remainder="haustuer-klingel",
        query_params={"token": TOKEN, "kovalue": "9"},
        body=b"",
        peer_ip="192.168.1.9",
    )
    assert outcome.status == 204
    assert _data_events(mock_bus)[-1].value == 9


async def test_formula_and_value_map_are_applied(mock_bus, monkeypatch):
    binding = make_binding(
        {"slug": "bell", "token": TOKEN, "value_source": "request"},
        value_formula="x * 10",
        value_map={"50": "99"},
    )
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="INTEGER"))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(
        method="GET",
        remainder="bell",
        query_params={"token": TOKEN, "value": "5"},
        body=b"",
        peer_ip="192.168.1.9",
    )

    assert outcome.status == 204
    assert _data_events(mock_bus)[-1].value == "99"


# ---------------------------------------------------------------------------
# Trigger — rejections
# ---------------------------------------------------------------------------


async def test_unknown_slug_and_wrong_token_are_indistinguishable(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    unknown = await instance.handle_trigger(method="GET", remainder="nope", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    wrong = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": "bad"}, body=b"", peer_ip="10.0.0.1")

    assert (unknown.status, unknown.detail) == (wrong.status, wrong.detail) == (404, "Not found")
    assert _data_events(mock_bus) == []


async def test_missing_token_is_rejected(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={}, body=b"", peer_ip="10.0.0.1")
    assert outcome.status == 404


async def test_empty_and_overlong_remainders_are_rejected(mock_bus):
    instance = await _adapter(mock_bus, [_binding()])

    for remainder in ("", "a/b/c"):
        outcome = await instance.handle_trigger(method="GET", remainder=remainder, query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
        assert outcome.status == 404


async def test_disallowed_method_returns_404_and_is_recorded(mock_bus, monkeypatch):
    binding = _binding(methods=["POST"])
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")

    assert outcome.status == 404
    assert instance.stats_for(binding.id).last_status == 404
    assert instance.stats_for(binding.id).call_count == 0


async def test_binding_whose_config_became_invalid_is_rejected(mock_bus):
    binding = _binding()
    instance = await _adapter(mock_bus, [binding])
    binding.config = {"slug": "haustuer-klingel", "token": TOKEN, "methods": ["TRACE"]}

    outcome = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    assert outcome.status == 404


async def test_ip_allowlist_blocks_a_foreign_caller(mock_bus, monkeypatch):
    binding = _binding(allowed_networks=["192.168.1.0/24"])
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    blocked = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.1.2.3")
    allowed = await instance.handle_trigger(
        method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="192.168.1.7"
    )

    assert blocked.status == 404
    assert allowed.status == 204


async def test_forwarded_for_is_only_trusted_when_configured(mock_bus, monkeypatch):
    binding = _binding(allowed_networks=["192.168.1.0/24"])
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    untrusting = await _adapter(mock_bus, [binding])

    spoofed = await untrusting.handle_trigger(
        method="GET",
        remainder="haustuer-klingel",
        query_params={"token": TOKEN},
        body=b"",
        peer_ip="10.1.2.3",
        forwarded_for="192.168.1.7",
    )
    assert spoofed.status == 404
    await untrusting.disconnect()

    trusting = await _adapter(mock_bus, [binding], {"trust_forwarded_for": True})
    honoured = await trusting.handle_trigger(
        method="GET",
        remainder="haustuer-klingel",
        query_params={"token": TOKEN},
        body=b"",
        peer_ip="10.1.2.3",
        forwarded_for="192.168.1.7, 10.1.2.3",
    )
    assert honoured.status == 204


async def test_rate_limit_returns_429(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding], {"rate_limit_per_minute": 1})

    first = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    second = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")

    assert first.status == 204
    assert (second.status, second.detail) == (429, "Too many requests")


async def test_rate_limit_without_a_client_address(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding], {"rate_limit_per_minute": 1})

    first = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip=None)
    second = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip=None)

    assert first.status == 204
    assert second.status == 429


async def test_a_rate_limit_flood_is_logged_once_per_window(mock_bus, caplog):
    instance = await _adapter(mock_bus, [], {"rate_limit_per_minute": 1})

    with caplog.at_level("WARNING", logger="obs.adapters.webhook.adapter"):
        outcomes = [await instance.handle_trigger(method="GET", remainder="x", query_params={}, body=b"", peer_ip="198.51.100.9") for _ in range(6)]

    assert [outcome.status for outcome in outcomes[1:]] == [429] * 5
    assert [record.getMessage() for record in caplog.records if "rate limit" in record.getMessage()] == [
        "WEBHOOK: rate limit exceeded for 198.51.100.9 (further calls in this window are not logged)"
    ]
    assert instance._rejections.counts[RejectionReason.RATE_LIMITED.value] == 5


async def test_debounce_suppresses_a_repeat_call(mock_bus, monkeypatch):
    binding = _binding(debounce_ms=60_000)
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    first = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    second = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")

    assert first.status == 204
    assert second.status == 204
    assert len(_data_events(mock_bus)) == 1
    stats = instance.stats_for(binding.id)
    assert (stats.call_count, stats.publish_count) == (2, 1)


async def test_a_rejected_call_does_not_consume_the_debounce_window(mock_bus, monkeypatch):
    binding = _binding(debounce_ms=60_000, value_source="request")
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="INTEGER"))
    instance = await _adapter(mock_bus, [binding])

    rejected = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    retry = await instance.handle_trigger(
        method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN, "value": "5"}, body=b"", peer_ip="10.0.0.1"
    )

    assert rejected.status == 400
    assert retry.status == 204
    assert [event.value for event in _data_events(mock_bus)] == [5]


async def test_a_rejected_call_restores_the_previous_debounce_stamp(mock_bus, monkeypatch):
    binding = _binding(debounce_ms=60_000, value_source="request")
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="INTEGER"))
    instance = await _adapter(mock_bus, [binding])
    long_ago = time.monotonic() - 3600
    instance._last_trigger[str(binding.id)] = long_ago

    rejected = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")

    assert rejected.status == 400
    assert instance._last_trigger[str(binding.id)] == long_ago


async def test_missing_datapoint_returns_404(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, None)
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    assert outcome.status == 404
    assert _data_events(mock_bus) == []


async def test_central_plant_datapoint_is_refused(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, control_class="central_plant"))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")

    assert outcome.status == 403
    assert _data_events(mock_bus) == []


async def test_missing_request_value_returns_400(mock_bus, monkeypatch):
    binding = _binding(value_source="request")
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="INTEGER"))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")

    assert outcome.status == 400
    assert "value" in outcome.detail
    assert _data_events(mock_bus) == []


async def test_oversized_post_body_returns_400(mock_bus, monkeypatch):
    binding = _binding(methods=["POST"], value_source="request")
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="INTEGER"))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(
        method="POST",
        remainder="haustuer-klingel",
        query_params={"token": TOKEN},
        body=b"x" * (64 * 1024 + 1),
        peer_ip="10.0.0.1",
    )

    assert outcome.status == 400
    assert outcome.detail == "Request body is too large"


async def test_incompatible_value_returns_400(mock_bus, monkeypatch):
    binding = _binding(value_source="request")
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="INTEGER"))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(
        method="GET",
        remainder="haustuer-klingel",
        query_params={"token": TOKEN, "value": "nope"},
        body=b"",
        peer_ip="10.0.0.1",
    )

    assert outcome.status == 400
    assert _data_events(mock_bus) == []


# ---------------------------------------------------------------------------
# Two-level ingress allowlist
# ---------------------------------------------------------------------------


async def test_binding_allowlist_blocks_a_foreign_caller_for_that_slug(mock_bus, monkeypatch):
    binding = _binding(allowed_networks=["192.168.1.0/24"])
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    blocked = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    allowed = await instance.handle_trigger(
        method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="192.168.1.7"
    )

    assert blocked.status == 404
    assert allowed.status == 204
    assert _data_events(mock_bus) != []


async def test_each_binding_has_its_own_allowlist(mock_bus, monkeypatch):
    """Two slugs on one instance restrict their callers independently."""
    open_binding = _binding()
    restricted = make_binding({"slug": "seiteneingang", "token": TOKEN, "allowed_networks": ["192.168.1.0/24"]})
    _stub_registry(monkeypatch, _Dp(open_binding.datapoint_id))
    instance = await _adapter(mock_bus, [open_binding, restricted])

    allowed = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    blocked = await instance.handle_trigger(method="GET", remainder="seiteneingang", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")

    assert allowed.status == 204
    assert blocked.status == 404


async def test_an_empty_binding_allowlist_restricts_nothing(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(
        method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="203.0.113.9"
    )

    assert outcome.status == 204


# ---------------------------------------------------------------------------
# Auto-reset — the webhook as a trigger
# ---------------------------------------------------------------------------


async def _settle_autoreset(instance, binding):
    """Await the armed reset timer instead of sleeping past it."""
    task = instance._autoreset_tasks.get(str(binding.id))
    if task is not None:
        await task


async def test_autoreset_publishes_the_reset_value_after_the_delay(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=10)
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    assert outcome.status == 204
    assert [event.value for event in _data_events(mock_bus)] == [True]

    await _settle_autoreset(instance, binding)

    events = _data_events(mock_bus)
    assert [event.value for event in events] == [True, False]
    reset = events[-1]
    assert reset.datapoint_id == binding.datapoint_id
    assert reset.binding_id == binding.id
    assert reset.quality == "good"
    assert reset.source_adapter == "WEBHOOK"
    # A plain value event, so the WriteRouter fans it out to the DEST bindings
    # too — that is what makes 1-then-0 reach KNX.
    assert reset.suppress_write_propagation is False


async def test_autoreset_is_skipped_when_the_datapoint_was_reclassified_meanwhile(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=10)
    dp = _Dp(binding.datapoint_id)
    _stub_registry(monkeypatch, dp)
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    dp.control_class = "central_plant"
    await _settle_autoreset(instance, binding)

    assert [event.value for event in _data_events(mock_bus)] == [True]


async def test_autoreset_is_skipped_when_the_datapoint_disappeared_meanwhile(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=10)
    dp = _Dp(binding.datapoint_id)
    _stub_registry(monkeypatch, dp)
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    _stub_registry(monkeypatch, None)
    await _settle_autoreset(instance, binding)

    assert [event.value for event in _data_events(mock_bus)] == [True]


async def _armed_instance(mock_bus, monkeypatch, *bindings):
    _stub_registry(monkeypatch, _Dp(bindings[0].datapoint_id))
    instance = await _adapter(mock_bus, list(bindings))
    for binding in bindings:
        await instance.handle_trigger(method="GET", remainder=binding.config["slug"], query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    return instance


async def test_a_reload_that_only_rotates_a_token_keeps_the_pending_reset(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=30)
    instance = await _armed_instance(mock_bus, monkeypatch, binding)

    binding.config["token"] = "rotated"
    await instance.reload_bindings([binding])
    await _settle_autoreset(instance, binding)

    assert [event.value for event in _data_events(mock_bus)] == [True, False]


async def test_a_reload_that_changes_the_reset_behaviour_drops_the_pending_reset(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=30)
    instance = await _armed_instance(mock_bus, monkeypatch, binding)
    task = instance._autoreset_tasks[str(binding.id)]

    binding.config["autoreset_value"] = "true"
    await instance.reload_bindings([binding])
    await asyncio.wait([task])

    assert task.cancelled()
    assert [event.value for event in _data_events(mock_bus)] == [True]
    assert str(binding.id) not in instance._autoreset_armed


async def test_a_reload_that_removes_the_binding_drops_its_pending_reset(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=30)
    instance = await _armed_instance(mock_bus, monkeypatch, binding)
    task = instance._autoreset_tasks[str(binding.id)]

    await instance.reload_bindings([])
    await asyncio.wait([task])

    assert task.cancelled()


async def test_rotating_one_binding_does_not_cancel_the_reset_of_another(mock_bus, monkeypatch):
    first = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=30)
    second = make_binding(
        {"slug": "zweite", "token": TOKEN, "methods": ["GET"], "autoreset": True, "autoreset_value": "false", "autoreset_delay_ms": 30}
    )
    second.datapoint_id = first.datapoint_id
    instance = await _armed_instance(mock_bus, monkeypatch, first, second)

    first.config["token"] = "rotated"
    await instance.reload_bindings([first, second])
    await _settle_autoreset(instance, first)
    await _settle_autoreset(instance, second)

    assert [event.value for event in _data_events(mock_bus)] == [True, True, False, False]


async def test_autoreset_coerces_with_the_type_the_datapoint_has_by_then(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=10)
    dp = _Dp(binding.datapoint_id)
    _stub_registry(monkeypatch, dp)
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    dp.data_type = "STRING"
    await _settle_autoreset(instance, binding)

    assert [event.value for event in _data_events(mock_bus)] == [True, "false"]


async def test_autoreset_with_an_incompatible_value_after_a_type_change_is_dropped(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=10)
    dp = _Dp(binding.datapoint_id)
    _stub_registry(monkeypatch, dp)
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    dp.data_type = "DATE"
    await _settle_autoreset(instance, binding)

    assert [event.value for event in _data_events(mock_bus)] == [True]


async def test_admit_charges_the_rate_limit_and_an_admitted_call_is_not_charged_twice(mock_bus, monkeypatch):
    binding = _binding(methods=["POST"])
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding], {"rate_limit_per_minute": 1})

    assert instance.admit(peer_ip="198.51.100.4") is None
    outcome = await instance.handle_trigger(
        method="POST", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="198.51.100.4", admitted=True
    )
    assert outcome.status == 204

    denied = instance.admit(peer_ip="198.51.100.4")
    assert denied is not None and denied.status == 429


async def test_a_completed_pulse_counts_both_values_as_published(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=0)
    instance = await _armed_instance(mock_bus, monkeypatch, binding)
    await _settle_autoreset(instance, binding)

    stats = instance.stats_for(binding.id)
    assert [event.value for event in _data_events(mock_bus)] == [True, False]
    assert (stats.call_count, stats.publish_count) == (1, 2)


async def test_no_autoreset_when_it_is_switched_off(mock_bus, monkeypatch):
    binding = _binding(autoreset_value="false", autoreset_delay_ms=1)
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    await asyncio.sleep(0.02)

    assert [event.value for event in _data_events(mock_bus)] == [True]
    assert instance._autoreset_tasks == {}


async def test_autoreset_with_no_delay_resets_immediately(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=0)
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    await _settle_autoreset(instance, binding)

    assert [event.value for event in _data_events(mock_bus)] == [True, False]


async def test_a_second_call_restarts_the_reset_timer(mock_bus, monkeypatch):
    """Retriggerable: the value stands for the delay after the *last* call."""
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=10)
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    first = instance._autoreset_tasks[str(binding.id)]
    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    second = instance._autoreset_tasks[str(binding.id)]

    assert second is not first
    with pytest.raises(asyncio.CancelledError):
        await first
    await _settle_autoreset(instance, binding)

    # Two triggers, one reset — not two resets racing each other.
    assert [event.value for event in _data_events(mock_bus)] == [True, True, False]


async def test_the_reset_value_goes_through_formula_and_value_map(mock_bus, monkeypatch):
    binding = make_binding(
        {"slug": "bell", "token": TOKEN, "autoreset": True, "autoreset_value": "0", "autoreset_delay_ms": 0},
        value_map={"1": "on", "0": "off"},
    )
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id, data_type="INTEGER"))
    binding.config["value_source"] = "request"
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="bell", query_params={"token": TOKEN, "value": "1"}, body=b"", peer_ip="10.0.0.1")
    await _settle_autoreset(instance, binding)

    assert [event.value for event in _data_events(mock_bus)] == ["on", "off"]


async def test_an_unusable_reset_value_is_logged_and_skipped(mock_bus, monkeypatch, caplog):
    binding = _binding(autoreset=True, autoreset_value="not-a-boolean", autoreset_delay_ms=0)
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    await _settle_autoreset(instance, binding)

    assert [event.value for event in _data_events(mock_bus)] == [True]
    assert "auto-reset value" in caplog.text


async def test_disconnect_cancels_a_pending_reset(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=60_000)
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    task = instance._autoreset_tasks[str(binding.id)]
    await asyncio.sleep(0)  # let the timer actually start, so it is cancelled mid-sleep
    await instance.disconnect()

    with pytest.raises(asyncio.CancelledError):
        await task
    assert instance._autoreset_tasks == {}
    assert [event.value for event in _data_events(mock_bus)] == [True]


async def test_reloading_bindings_cancels_a_pending_reset(mock_bus, monkeypatch):
    """A pending reset belongs to the configuration it was armed under."""
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=60_000)
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    binding.config["autoreset_delay_ms"] = 1_000
    await instance.reload_bindings([binding])

    assert instance._autoreset_tasks == {}
    assert [event.value for event in _data_events(mock_bus)] == [True]


async def test_a_debounced_call_does_not_rearm_the_reset(mock_bus, monkeypatch):
    binding = _binding(autoreset=True, autoreset_value="false", autoreset_delay_ms=10, debounce_ms=60_000)
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    armed = instance._autoreset_tasks[str(binding.id)]
    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")

    assert instance._autoreset_tasks[str(binding.id)] is armed
    await _settle_autoreset(instance, binding)
    assert [event.value for event in _data_events(mock_bus)] == [True, False]


def test_autoreset_defaults_are_off():
    cfg = WebhookBindingConfig(slug="bell")
    assert cfg.autoreset is False
    assert cfg.autoreset_value == "false"
    assert cfg.autoreset_delay_ms == 1000


# ---------------------------------------------------------------------------
# Rejection diagnostics
# ---------------------------------------------------------------------------


async def test_instance_counts_rejections_by_reason_with_the_last_address(mock_bus, monkeypatch):
    binding = _binding(allowed_networks=["10.38.0.0/16"])
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="127.0.0.1")
    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="127.0.0.1")
    await instance.handle_trigger(method="GET", remainder="unknown-slug", query_params={"token": TOKEN}, body=b"", peer_ip="10.38.1.2")

    rejections = instance.rejections
    assert rejections.counts == {"address_blocked": 2, "unknown_slug": 1}
    assert rejections.total == 3
    assert rejections.last.reason == "unknown_slug"
    assert rejections.last.client_ip == "10.38.1.2"
    assert rejections.last.slug == "unknown-slug"
    assert isinstance(rejections.last.at, datetime.datetime)


@pytest.mark.parametrize(
    ("make_call", "reason"),
    [
        (lambda t: {"method": "GET", "remainder": "haustuer-klingel", "query_params": {"token": "wrong"}}, "invalid_token"),
        (lambda t: {"method": "POST", "remainder": "haustuer-klingel", "query_params": {"token": t}}, "method_not_allowed"),
    ],
)
async def test_binding_level_rejections_are_attributed_to_the_binding(mock_bus, monkeypatch, make_call, reason):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(body=b"", peer_ip="10.0.0.1", **make_call(TOKEN))

    assert instance.rejections.counts == {reason: 1}
    # An invalid token must not be blamed on the binding — the caller only
    # guessed its slug, which is not a reason to mark that binding as failing.
    expected_binding_counts = {} if reason == "invalid_token" else {reason: 1}
    assert instance.stats_for(binding.id).rejections.counts == expected_binding_counts


async def test_an_address_rejection_is_counted_on_instance_and_binding(mock_bus, monkeypatch):
    binding = _binding(allowed_networks=["192.168.1.0/24"])
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")

    assert instance.rejections.counts == {"address_blocked": 1}
    stats = instance.stats_for(binding.id)
    assert stats.rejections.counts == {"address_blocked": 1}
    assert stats.rejections.last.client_ip == "10.0.0.1"
    assert stats.last_status == 404
    assert stats.call_count == 0


async def test_rate_limit_and_invalid_binding_are_counted(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding], {"rate_limit_per_minute": 1})

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    assert instance.rejections.counts == {"rate_limited": 1}

    # Indexed while valid, then corrupted — the slug still resolves, so this is
    # the one path that reports an invalid binding rather than an unknown slug.
    second_binding = _binding()
    second = await _adapter(mock_bus, [second_binding], {"path_prefix": "/hook2"})
    second_binding.config = {"slug": "haustuer-klingel", "token": TOKEN, "methods": ["TRACE"]}
    await second.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.2")
    assert second.rejections.counts == {"invalid_binding": 1}


async def test_a_successful_call_records_no_rejection(mock_bus, monkeypatch):
    binding = _binding()
    _stub_registry(monkeypatch, _Dp(binding.datapoint_id))
    instance = await _adapter(mock_bus, [binding])

    await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")

    assert instance.rejections.counts == {}
    assert instance.rejections.total == 0
    assert instance.rejections.last is None


async def test_datapoint_without_a_control_class_attribute_is_treated_as_room_local(mock_bus, monkeypatch):
    binding = _binding()

    class _Bare:
        id = binding.datapoint_id
        name = "Klingel"
        data_type = "BOOLEAN"

    monkeypatch.setattr("obs.core.registry.get_registry", lambda: _Registry(_Bare()))
    instance = await _adapter(mock_bus, [binding])

    outcome = await instance.handle_trigger(method="GET", remainder="haustuer-klingel", query_params={"token": TOKEN}, body=b"", peer_ip="10.0.0.1")
    assert outcome.status == 204
