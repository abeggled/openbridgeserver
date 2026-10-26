"""Update check service: schedule, GitHub fetch, state and system DataPoints (issue #1370)."""

from __future__ import annotations

import asyncio
import json
import logging
import random
import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from obs.core.event_bus import DataValueEvent, EventBus
from obs.core.registry import DataPointRegistry
from obs.core.system_datapoints import CURRENT_VERSION, LAST_CHECK, LATEST_VERSION, UPDATE_AVAILABLE, UPDATE_CHANNEL
from obs.db.database import Database
from obs.update import service as svc
from obs.update.service import (
    CHANNEL_DERIVED_KEY,
    CHANNEL_SETTING_KEY,
    CHECK_INTERVAL_SECONDS,
    STATE_SETTING_KEY,
    FetchResult,
    GitHubReleaseSource,
    UpdateCheckError,
    UpdateCheckService,
    UpdateState,
    resolve_installed_version,
)


def _gh(tag, *, prerelease=False, published="2026-10-08T19:40:34Z", body=None):
    return {
        "tag_name": tag,
        "prerelease": prerelease,
        "draft": False,
        "published_at": published,
        "body": body if body is not None else f"## {tag}\n* change",
        "name": f"Release {tag}",
        "html_url": f"https://github.com/o/r/releases/tag/{tag}",
    }


PAYLOAD = [
    _gh("nightly-20261017", prerelease=True, published="2026-10-17T07:04:23Z"),
    _gh("2026.11.0-RC1", prerelease=True, published="2026-10-12T10:00:00Z"),
    _gh("2026.10.0", published="2026-10-08T19:40:34Z"),
    _gh("2026.9.1", published="2026-09-08T20:22:13Z"),
    _gh("2026.9.0", published="2026-09-07T19:57:15Z"),
]


class FakeSource:
    """Scripted stand-in for GitHubReleaseSource."""

    def __init__(self, *results):
        self.results = list(results)
        self.etags: list[str | None] = []

    async def fetch(self, etag):
        self.etags.append(etag)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result


class Clock:
    def __init__(self):
        self.now = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture
async def db():
    database = Database(":memory:")
    await database.connect()
    try:
        yield database
    finally:
        await database.disconnect()


@pytest.fixture
async def env(db):
    bus = EventBus()
    registry = DataPointRegistry(db=db, mqtt_client=AsyncMock(), event_bus=bus)
    await registry.load_from_db()
    bus.subscribe(DataValueEvent, registry.handle_value_event)
    return SimpleNamespace(db=db, bus=bus, registry=registry, clock=Clock())


def _service(env, source, installed="2026.9.0", rng=None):
    return UpdateCheckService(env.db, env.bus, source=source, installed_version=installed, rng=rng, clock=env.clock)


def _dp(env, definition):
    state = env.registry.get_value(definition.id)
    return state.value, state.quality


async def _setting(db, key):
    row = await db.fetchone("SELECT value FROM app_settings WHERE key = ?", (key,))
    return row["value"] if row else None


# ---------------------------------------------------------------------------
# resolve_installed_version
# ---------------------------------------------------------------------------


def test_installed_version_prefers_the_install_root_tag(tmp_path):
    (tmp_path / "version").write_text("nightly-20261017\n")
    assert resolve_installed_version("2026.11.0", tmp_path) == "nightly-20261017"


@pytest.mark.parametrize("content", ["2026.99.0", "0.0.0-45-gabc123", "\udcff"])
def test_installed_version_ignores_unusable_install_root_tags(tmp_path, content):
    (tmp_path / "version").write_text(content, encoding="utf-8", errors="surrogateescape")
    assert resolve_installed_version("2026.11.0", tmp_path) == "2026.11.0"


def test_installed_version_without_install_root_file(tmp_path):
    assert resolve_installed_version(" 2026.10.0\n", tmp_path) == "2026.10.0"


def test_installed_version_defaults_to_the_package(monkeypatch):
    monkeypatch.setattr(svc.obs, "__version__", "2026.10.0")
    # The repository root has no `version` file, so the package stamp wins.
    assert resolve_installed_version() == "2026.10.0"


# ---------------------------------------------------------------------------
# GitHubReleaseSource
# ---------------------------------------------------------------------------


def _source(handler):
    return GitHubReleaseSource("https://api.example/", "o/r", transport=httpx.MockTransport(handler))


async def test_fetch_returns_payload_and_etag_and_sends_conditional_header():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        seen["inm"] = request.headers.get("if-none-match")
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, json=PAYLOAD, headers={"ETag": 'W/"abc"'})

    result = await _source(handler).fetch('W/"old"')
    assert result == FetchResult(not_modified=False, etag='W/"abc"', payload=PAYLOAD)
    assert seen["url"] == "https://api.example/repos/o/r/releases?per_page=100"
    assert seen["inm"] == 'W/"old"'
    assert seen["ua"].startswith("openbridgeserver/")


async def test_fetch_without_etag_sends_no_conditional_header():
    seen = {}

    def handler(request):
        seen["inm"] = request.headers.get("if-none-match")
        return httpx.Response(200, json=[])

    result = await _source(handler).fetch(None)
    assert seen["inm"] is None
    assert result.etag is None


async def test_fetch_not_modified_keeps_the_etag():
    result = await _source(lambda r: httpx.Response(304)).fetch('W/"abc"')
    assert result == FetchResult(not_modified=True, etag='W/"abc"')
    renewed = await _source(lambda r: httpx.Response(304, headers={"ETag": 'W/"new"'})).fetch('W/"abc"')
    assert renewed.etag == 'W/"new"'


@pytest.mark.parametrize(
    ("response", "etag", "code"),
    [
        (httpx.Response(304), None, "http_status"),
        (httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}), None, "rate_limited"),
        (httpx.Response(429, headers={"X-RateLimit-Remaining": "0"}), None, "rate_limited"),
        (httpx.Response(403, headers={"X-RateLimit-Remaining": "5"}), None, "http_status"),
        (httpx.Response(500), None, "http_status"),
        (httpx.Response(200, content=b"<html>"), None, "invalid_response"),
    ],
)
async def test_fetch_failures_map_to_stable_codes(response, etag, code):
    with pytest.raises(UpdateCheckError) as exc_info:
        await _source(lambda r: response).fetch(etag)
    assert exc_info.value.code == code


def _paged(pages, *, host="https://api.example"):
    """Handler serving *pages* with GitHub's Link header (its next links use /repositories/<id>)."""
    seen = []

    def handler(request):
        seen.append(request)
        index = int(request.url.params.get("page", "1"))
        headers = {"ETag": f'W/"page{index}"'}
        if index < len(pages):
            headers["Link"] = f'<{host}/repositories/1/releases?per_page=100&page={index + 1}>; rel="next"'
        return httpx.Response(200, json=pages[index - 1], headers=headers)

    return handler, seen


async def test_fetch_follows_every_page_and_keeps_the_first_etag():
    """The installed release may sit beyond the first hundred (#1370 review)."""
    handler, seen = _paged([PAYLOAD[:2], PAYLOAD[2:4], PAYLOAD[4:]])
    result = await _source(handler).fetch('W/"old"')
    assert result == FetchResult(not_modified=False, etag='W/"page1"', payload=PAYLOAD)
    # Only the first page is conditional: its ETag is the one stored.
    assert [r.headers.get("if-none-match") for r in seen] == ['W/"old"', None, None]


async def test_fetch_stops_at_the_page_limit(monkeypatch):
    monkeypatch.setattr(svc, "MAX_RELEASE_PAGES", 2)
    handler, seen = _paged([[_gh("2026.10.0")], [_gh("2026.9.0")], [_gh("2026.8.0")]])
    result = await _source(handler).fetch(None)
    assert [r["tag_name"] for r in result.payload] == ["2026.10.0", "2026.9.0"]
    assert len(seen) == 2


async def test_fetch_never_follows_a_next_link_to_another_host():
    handler, seen = _paged([[_gh("2026.10.0")], [_gh("2026.9.0")]], host="https://evil.example")
    result = await _source(handler).fetch(None)
    assert [r["tag_name"] for r in result.payload] == ["2026.10.0"]
    assert len(seen) == 1


async def test_fetch_ignores_a_next_link_without_url():
    response = httpx.Response(200, json=[], headers={"Link": '<>; rel="next"'})
    result = await _source(lambda r: response).fetch(None)
    assert result.payload == []


async def test_fetch_does_not_page_a_payload_that_is_no_list():
    """classify_releases reports it; there is nothing to append pages to."""
    response = httpx.Response(200, json={"message": "?"}, headers={"Link": '<https://api.example/x?page=2>; rel="next"'})
    result = await _source(lambda r: response).fetch(None)
    assert result.payload == {"message": "?"}


@pytest.mark.parametrize(
    ("second", "code"),
    [
        (httpx.Response(200, json={"message": "?"}), "invalid_response"),
        (httpx.Response(403, headers={"X-RateLimit-Remaining": "0"}), "rate_limited"),
        (httpx.Response(502), "http_status"),
    ],
)
async def test_a_failing_later_page_fails_the_whole_fetch(second, code):
    """A partial list could drop the installed release and flip the answer."""

    def handler(request):
        if request.url.params.get("page") == "2":
            return second
        return httpx.Response(200, json=[_gh("2026.10.0")], headers={"Link": '<https://api.example/repositories/1/releases?page=2>; rel="next"'})

    with pytest.raises(UpdateCheckError) as exc_info:
        await _source(handler).fetch(None)
    assert exc_info.value.code == code


async def test_fetch_network_error():
    def handler(request):
        raise httpx.ConnectError("boom", request=request)

    with pytest.raises(UpdateCheckError) as exc_info:
        await _source(handler).fetch(None)
    assert (exc_info.value.code, exc_info.value.detail) == ("network", "ConnectError")


def test_update_check_error_without_detail_uses_the_code():
    assert str(UpdateCheckError("network")) == "network"


# ---------------------------------------------------------------------------
# load / initial DataPoints
# ---------------------------------------------------------------------------


async def test_load_derives_and_persists_the_initial_channel(env):
    service = _service(env, None, installed="2026.11.0-RC1")
    await service.load()
    assert service.state.channel == "pre-release"
    assert await _setting(env.db, CHANNEL_SETTING_KEY) == "pre-release"
    assert _dp(env, UPDATE_CHANNEL) == ("pre-release", "good")
    assert _dp(env, CURRENT_VERSION) == ("2026.11.0-RC1", "good")
    # No successful online check yet: no false "no update" signal.
    assert _dp(env, LATEST_VERSION) == (None, "uncertain")
    assert _dp(env, UPDATE_AVAILABLE) == (None, "uncertain")
    assert _dp(env, LAST_CHECK) == (None, "uncertain")


async def test_load_keeps_a_stored_channel(env):
    await env.db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES (?, ?)", (CHANNEL_SETTING_KEY, "nightly"))
    service = _service(env, None, installed="2026.10.0")
    await service.load()
    assert service.state.channel == "nightly"


async def test_load_replaces_an_invalid_stored_channel(env):
    await env.db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES (?, ?)", (CHANNEL_SETTING_KEY, "beta"))
    service = _service(env, None, installed="nightly-20261017")
    await service.load()
    assert await _setting(env.db, CHANNEL_SETTING_KEY) == "nightly"


async def _store_state(db, **overrides):
    state = UpdateState(
        channel="stable",
        installed_version="2026.9.0",
        latest_version="2026.10.0",
        latest_url="https://github.com/o/r/releases/tag/2026.10.0",
        update_available=True,
        last_check_at=datetime(2026, 10, 16, tzinfo=UTC),
        last_success_at=datetime(2026, 10, 16, tzinfo=UTC),
    )
    data = json.loads(state.to_json())
    data["release_notes"] = [{"version": "2026.10.0", "name": "", "published_at": 5, "url": None, "body": None}]
    data.update(overrides)
    await db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES (?, ?)", (STATE_SETTING_KEY, json.dumps(data)))


async def test_load_restores_the_last_successful_result(env):
    await _store_state(env.db)
    service = _service(env, FakeSource())
    await service.load()
    state = service.state
    assert (state.latest_version, state.update_available) == ("2026.10.0", True)
    assert state.release_notes[0].name == "2026.10.0"
    assert state.release_notes[0].published_at is None
    assert state.release_notes[0].body == ""
    assert _dp(env, LATEST_VERSION) == ("2026.10.0", "good")
    assert _dp(env, UPDATE_AVAILABLE) == (True, "good")
    assert _dp(env, LAST_CHECK) == (datetime(2026, 10, 16, tzinfo=UTC), "good")


@pytest.mark.parametrize(
    "overrides",
    [
        {"installed_version": "2026.8.0"},  # an update was installed since
        {"channel": "nightly"},  # the channel changed since
    ],
)
async def test_load_discards_a_result_for_another_version_or_channel(env, overrides):
    await _store_state(env.db, **overrides)
    service = _service(env, FakeSource())
    await service.load()
    assert service.state.latest_version is None
    assert _dp(env, LATEST_VERSION) == (None, "uncertain")


async def test_load_reads_naive_timestamps_as_utc(env):
    await _store_state(env.db, last_check_at="2026-10-16T08:00:00")
    service = _service(env, FakeSource())
    await service.load()
    assert service.state.last_check_at == datetime(2026, 10, 16, 8, tzinfo=UTC)


async def test_load_tolerates_odd_field_types(env):
    await _store_state(
        env.db, latest_version=5, latest_url=None, update_available="yes", last_check_at="never", last_success_at=7, last_error=3, last_error_detail=4
    )
    service = _service(env, FakeSource())
    await service.load()
    state = service.state
    assert (state.latest_version, state.update_available, state.last_check_at, state.last_success_at, state.last_error) == (
        None,
        None,
        None,
        None,
        None,
    )


@pytest.mark.parametrize("raw", ["{not json", "[]", json.dumps({"channel": "stable", "installed_version": "2026.9.0", "release_notes": [{}]})])
async def test_load_ignores_unreadable_state(env, raw):
    await env.db.execute_and_commit("INSERT INTO app_settings (key, value) VALUES (?, ?)", (STATE_SETTING_KEY, raw))
    service = _service(env, FakeSource())
    await service.load()
    assert service.state.latest_version is None


async def test_load_with_the_check_disabled_restores_no_result(env):
    await _store_state(env.db)
    service = _service(env, None)
    await service.load()
    assert (service.state.latest_version, service.state.update_available) == (None, None)
    assert _dp(env, UPDATE_AVAILABLE) == (None, "uncertain")


async def test_derived_channel_follows_a_new_version_family(env):
    first = _service(env, None, installed="nightly-20261008")
    await first.load()
    assert await _setting(env.db, CHANNEL_DERIVED_KEY) == "nightly-20261008"
    # obs-update installed a stable release meanwhile.
    second = _service(env, None, installed="2026.11.0")
    await second.load()
    assert second.state.channel == "stable"
    assert await _setting(env.db, CHANNEL_DERIVED_KEY) == "2026.11.0"


async def test_admin_choice_survives_a_new_version_family(env):
    first = _service(env, None, installed="2026.10.0")
    await first.load()
    await first.set_channel("nightly")
    assert await _setting(env.db, CHANNEL_DERIVED_KEY) is None
    second = _service(env, None, installed="2026.11.0")
    await second.load()
    assert second.state.channel == "nightly"


async def test_keep_after_restore_writes_the_derived_marker_back(env):
    service = _service(env, None, installed="2026.10.0")
    await service.load()
    await env.db.execute_and_commit("DELETE FROM app_settings WHERE key = ?", (CHANNEL_DERIVED_KEY,))
    await service.keep_after_restore()
    assert await _setting(env.db, CHANNEL_DERIVED_KEY) == "2026.10.0"


# ---------------------------------------------------------------------------
# check
# ---------------------------------------------------------------------------


async def test_successful_check_publishes_and_persists(env):
    source = FakeSource(FetchResult(not_modified=False, etag='W/"1"', payload=PAYLOAD))
    service = _service(env, source)
    await service.load()
    state = await service.check()
    assert (state.latest_version, state.update_available) == ("2026.10.0", True)
    assert [n.version for n in state.release_notes] == ["2026.9.1", "2026.10.0"]
    assert state.last_success_at == state.last_check_at == env.clock.now
    assert source.etags == [None]
    assert _dp(env, LATEST_VERSION) == ("2026.10.0", "good")
    assert _dp(env, UPDATE_AVAILABLE) == (True, "good")
    assert _dp(env, LAST_CHECK) == (env.clock.now, "good")
    persisted = json.loads(await _setting(env.db, STATE_SETTING_KEY))
    assert persisted["latest_version"] == "2026.10.0"
    assert persisted["release_notes"][1]["version"] == "2026.10.0"


async def test_up_to_date_check_reports_false(env):
    service = _service(env, FakeSource(FetchResult(False, None, PAYLOAD)), installed="2026.10.0")
    await service.load()
    await service.check()
    assert _dp(env, UPDATE_AVAILABLE) == (False, "good")


async def test_not_modified_reuses_the_cached_release_list(env):
    source = FakeSource(FetchResult(False, 'W/"1"', PAYLOAD), FetchResult(True, 'W/"1"'))
    service = _service(env, source)
    await service.load()
    await service.check()
    env.clock.advance(3600)
    state = await service.check()
    assert source.etags == [None, 'W/"1"']
    assert state.latest_version == "2026.10.0"
    assert state.last_success_at == env.clock.now


async def test_failed_check_keeps_the_last_success_and_updates_last_check(env):
    source = FakeSource(FetchResult(False, 'W/"1"', PAYLOAD), UpdateCheckError("network", "ConnectError"))
    service = _service(env, source)
    await service.load()
    await service.check()
    first_success = service.state.last_success_at
    env.clock.advance(3600)
    state = await service.check()
    assert (state.last_error, state.last_error_detail) == ("network", "ConnectError")
    assert state.last_success_at == first_success
    assert state.last_check_at == env.clock.now
    assert _dp(env, LATEST_VERSION) == ("2026.10.0", "good")
    assert _dp(env, UPDATE_AVAILABLE) == (True, "good")
    assert _dp(env, LAST_CHECK) == (env.clock.now, "good")


async def test_failed_first_check_publishes_no_update_verdict(env):
    service = _service(env, FakeSource(UpdateCheckError("rate_limited")))
    await service.load()
    state = await service.check()
    assert state.last_error == "rate_limited"
    assert state.last_error_detail is None
    assert _dp(env, UPDATE_AVAILABLE) == (None, "uncertain")
    assert _dp(env, LATEST_VERSION) == (None, "uncertain")
    assert _dp(env, LAST_CHECK) == (env.clock.now, "good")


async def test_invalid_payload_is_a_failed_check(env):
    service = _service(env, FakeSource(FetchResult(False, None, {"message": "Not Found"})))
    await service.load()
    state = await service.check()
    assert state.last_error == "invalid_response"


async def test_unexpected_error_is_contained(env):
    service = _service(env, FakeSource(RuntimeError("bug")))
    await service.load()
    state = await service.check()
    assert state.last_error == "internal"


async def test_check_propagates_cancellation(env):
    service = _service(env, FakeSource(asyncio.CancelledError()))
    await service.load()
    with pytest.raises(asyncio.CancelledError):
        await service.check()


async def test_channel_without_release_publishes_uncertain_latest(env):
    service = _service(env, FakeSource(FetchResult(False, None, [_gh("2026.10.0")])))
    await service.load()
    await service.set_channel("nightly")
    assert service.state.last_success_at is not None
    assert _dp(env, LATEST_VERSION) == (None, "uncertain")
    assert _dp(env, UPDATE_AVAILABLE) == (None, "uncertain")


async def test_persist_failure_does_not_break_the_check(env, caplog):
    service = _service(env, FakeSource(FetchResult(False, None, PAYLOAD)))
    await service.load()
    env.db.execute_and_commit = AsyncMock(side_effect=RuntimeError("disk full"))
    state = await service.check()
    assert state.latest_version == "2026.10.0"
    assert "could not persist status" in caplog.text
    assert _dp(env, LATEST_VERSION) == ("2026.10.0", "good")


async def test_publish_failure_is_contained(env, caplog):
    service = _service(env, None)
    service._bus = SimpleNamespace(publish=AsyncMock(side_effect=RuntimeError("bus down")))
    await service.publish_datapoints()
    assert "could not publish OBS Update Channel" in caplog.text


# ---------------------------------------------------------------------------
# manual check
# ---------------------------------------------------------------------------


async def test_manual_check_respects_the_cooldown(env):
    source = FakeSource(FetchResult(False, None, PAYLOAD), FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    assert (await service.check_now())[1] is True
    env.clock.advance(svc.MANUAL_CHECK_COOLDOWN_SECONDS - 1)
    assert (await service.check_now())[1] is False
    assert len(source.etags) == 1
    env.clock.advance(1)
    assert (await service.check_now())[1] is True
    assert len(source.etags) == 2


async def test_manual_check_after_the_clock_was_set_back(env):
    service = _service(env, FakeSource(FetchResult(False, None, PAYLOAD), FetchResult(False, None, PAYLOAD)))
    await service.load()
    await service.check_now()
    env.clock.advance(-3600)
    assert (await service.check_now())[1] is True


async def test_a_restored_last_check_does_not_start_the_cooldown(env):
    """The previous process checked just before the restart (#1370 review)."""
    await _store_state(env.db, last_check_at=env.clock.now.isoformat())
    service = _service(env, FakeSource(FetchResult(False, None, PAYLOAD)))
    await service.load()
    assert service.state.last_check_at == env.clock.now
    # Nothing cached in this process yet: a channel switch must get its answer.
    state, checked = await service.set_channel("nightly")
    assert (state.latest_version, checked) == ("nightly-20261017", True)


async def test_manual_check_joins_a_running_check(env):
    gate = asyncio.Event()

    class SlowSource(FakeSource):
        async def fetch(self, etag):
            await gate.wait()
            return await super().fetch(etag)

    source = SlowSource(FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    running = asyncio.create_task(service.check())
    await asyncio.sleep(0)
    assert service.checking
    joined = asyncio.create_task(service.check_now())
    await asyncio.sleep(0)
    gate.set()
    await running
    state, ran = await joined
    assert state.latest_version == "2026.10.0"
    assert ran is False
    assert len(source.etags) == 1


class GatedSource(FakeSource):
    """A FakeSource whose requests hang until ``gate`` is set."""

    def __init__(self, *results):
        super().__init__(*results)
        self.gate = asyncio.Event()

    async def fetch(self, etag):
        await self.gate.wait()
        return await super().fetch(etag)


async def test_load_does_not_wait_for_a_running_github_request(env):
    source = GatedSource(FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    running = asyncio.create_task(service.check())
    await asyncio.sleep(0)
    await asyncio.wait_for(service.load(), timeout=1)
    source.gate.set()
    finished = await running
    # The check took its result over onto the reloaded state.
    assert finished is service.state
    assert service.state.latest_version == "2026.10.0"


@pytest.mark.parametrize("operation", ["set_channel", "keep_after_restore"])
async def test_admin_operations_do_not_wait_for_a_running_github_request(env, operation):
    """A slow or unresponsive GitHub must not block a channel change or a restore (#1371 review)."""
    source = GatedSource(FetchResult(False, None, PAYLOAD), FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    # A cached release list answers the channel change; without one it joins the check.
    source.gate.set()
    await service.check()
    source.gate.clear()
    running = asyncio.create_task(service.check())
    await asyncio.sleep(0)
    assert service.checking
    if operation == "set_channel":
        await asyncio.wait_for(service.set_channel("pre-release"), timeout=1)
    else:
        await asyncio.wait_for(service.keep_after_restore(), timeout=1)
    assert not running.done()
    source.gate.set()
    state = await running
    assert not service.checking
    # The result is evaluated for the channel current when it arrived.
    expected = "2026.11.0-RC1" if operation == "set_channel" else "2026.10.0"
    assert (state.latest_version, _dp(env, LATEST_VERSION)) == (expected, (expected, "good"))


async def test_a_cancelled_check_is_no_longer_running(env):
    service = _service(env, GatedSource(FetchResult(False, None, PAYLOAD)))
    await service.load()
    running = asyncio.create_task(service.check())
    await asyncio.sleep(0)
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    assert not service.checking
    assert service.state.last_check_at is None


async def test_manual_checks_queued_behind_the_lock_ask_github_once(env):
    source = FakeSource(FetchResult(False, None, PAYLOAD), FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    async with service._lock:
        # e.g. a channel change persisting while two admins click "Check now".
        pending = [asyncio.create_task(service.check_now()) for _ in range(2)]
        await asyncio.sleep(0)
    results = await asyncio.gather(*pending)
    assert [ran for _state, ran in results] == [True, False]
    assert len(source.etags) == 1


async def test_manual_check_is_not_swallowed_by_a_non_check_lock(env):
    source = FakeSource(FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    async with service._lock:
        # e.g. set_channel() persisting — not a check: nothing to join.
        assert not service.checking
        pending = asyncio.create_task(service.check_now())
        await asyncio.sleep(0)
    state, ran = await pending
    assert ran is True
    assert state.latest_version == "2026.10.0"


async def test_unchanged_values_are_not_published_again(env):
    source = FakeSource(FetchResult(False, None, PAYLOAD), FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    await service.check()
    seen = []

    async def record(event):
        seen.append(event.datapoint_id)

    env.bus.subscribe(DataValueEvent, record)
    env.clock.advance(3600)
    await service.check()
    # Only the time of the attempt changed.
    assert seen == [LAST_CHECK.id]
    await service.publish_datapoints(force=True)
    assert len(seen) == 6


async def test_only_restored_values_are_published_as_initialization(env):
    source = FakeSource(FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    seen = []

    async def record(event):
        seen.append(event.initialization)

    env.bus.subscribe(DataValueEvent, record)
    await service.load()
    assert seen == [True] * 5
    seen.clear()
    await service.check()
    assert seen and not any(seen)
    seen.clear()
    await service.keep_after_restore()
    assert seen == [True] * 5


async def test_an_unchanged_update_does_not_notify_again_after_restart_or_restore(env, monkeypatch):
    """MESSAGE on "OBS Update Available" fires once, not on every start-up or restore (#1371 review)."""
    from pydantic import BaseModel

    from obs.adapters.message.adapter import MessageAdapter
    from obs.adapters.message.providers import registry as providers
    from obs.adapters.message.providers.base import MessageSendResult

    class Config(BaseModel):
        enabled: bool = True
        targets: dict[str, dict] = {}

    provider = SimpleNamespace(
        provider_type="update-test",
        config_schema=Config,
        target_schema=BaseModel,
        send=AsyncMock(return_value=MessageSendResult("update-test", "default", True)),
    )
    monkeypatch.setitem(providers._providers, "update-test", provider)
    monkeypatch.setattr("obs.core.registry.get_registry", lambda: env.registry)
    binding = SimpleNamespace(
        id=uuid.uuid4(),
        datapoint_id=UPDATE_AVAILABLE.id,
        direction="SOURCE",
        config={
            "operator": "==",
            "compare_value": "true",
            "message": "Update available",
            "providers": [{"provider": "update-test", "target": "default"}],
            "send_on_change": True,
        },
    )
    adapters = []

    async def start_message_adapter():
        # A fresh adapter, as after a restart or the restore's start_all().
        adapter = MessageAdapter(event_bus=env.bus, config={"providers": {"update-test": {"enabled": True, "targets": {"default": {}}}}})
        await adapter.connect()
        await adapter.reload_bindings([binding])
        adapters.append(adapter)
        return adapter

    async def sends(adapter):
        for _ in range(5):
            while adapter._send_tasks:
                await asyncio.gather(*list(adapter._send_tasks))
            await asyncio.sleep(0)
        return provider.send.await_count

    try:
        adapter = await start_message_adapter()
        service = _service(env, FakeSource(FetchResult(False, None, PAYLOAD)))
        await service.load()
        await service.check()
        assert await sends(adapter) == 1
        # Restart: a new process restores the same result.
        adapter = await start_message_adapter()
        await _service(env, FakeSource()).load()
        assert await sends(adapter) == 1
        # Database restore in the running process.
        adapter = await start_message_adapter()
        await service.keep_after_restore()
        assert await sends(adapter) == 1
    finally:
        for adapter in adapters:
            await adapter.disconnect()


async def test_a_clock_set_back_does_not_block_manual_checks(env):
    source = FakeSource(FetchResult(False, None, PAYLOAD), FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    await service.check()
    env.clock.advance(-3600)
    assert (await service.check_now())[1] is True


# ---------------------------------------------------------------------------
# channel change
# ---------------------------------------------------------------------------


async def test_set_channel_rejects_unknown_channels(env):
    service = _service(env, None)
    await service.load()
    with pytest.raises(ValueError):
        await service.set_channel("beta")


async def test_set_channel_reevaluates_the_cached_release_list(env):
    source = FakeSource(FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    await service.check()
    fetched_at = service.state.last_success_at
    env.clock.advance(60)
    state, _checked = await service.set_channel("pre-release")
    assert state.latest_version == "2026.11.0-RC1"
    assert state.last_success_at == fetched_at
    assert await _setting(env.db, CHANNEL_SETTING_KEY) == "pre-release"
    assert _dp(env, UPDATE_CHANNEL) == ("pre-release", "good")
    assert _dp(env, LATEST_VERSION) == ("2026.11.0-RC1", "good")
    # Answered from the cached release list: no new request.
    assert len(source.etags) == 1


async def test_set_channel_without_cache_checks_before_answering(env):
    source = FakeSource(FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    state, _checked = await service.set_channel("nightly")
    assert state.latest_version == "nightly-20261017"
    assert _dp(env, LATEST_VERSION) == ("nightly-20261017", "good")


async def test_channel_changes_ask_github_only_until_a_check_succeeded(env):
    source = FakeSource(UpdateCheckError("network"), FetchResult(False, None, PAYLOAD))
    service = _service(env, source)
    await service.load()
    failed, checked = await service.set_channel("nightly")
    assert (failed.last_error, checked) == ("network", True)
    # Within the manual cool-down a further change does not ask again …
    skipped, checked = await service.set_channel("stable")
    assert (skipped.latest_version, len(source.etags), checked) == (None, 1, False)
    # The failure stays visible instead of a blank "never checked" status.
    assert (skipped.last_error, skipped.last_check_at) == ("network", env.clock.now)
    # … after it, the next change does.
    env.clock.advance(svc.MANUAL_CHECK_COOLDOWN_SECONDS)
    ok, _checked = await service.set_channel("pre-release")
    assert ok.latest_version == "2026.11.0-RC1"
    back, _checked = await service.set_channel("stable")
    assert back.latest_version == "2026.10.0"
    assert len(source.etags) == 2


async def test_resending_the_channel_does_not_check(env):
    source = FakeSource()
    service = _service(env, source)
    await service.load()
    state, _checked = await service.set_channel("stable")
    assert (state.channel, source.etags) == ("stable", [])


async def test_channel_switch_keeps_a_failed_attempt_visible(env):
    source = FakeSource(FetchResult(False, None, PAYLOAD), UpdateCheckError("network", "ConnectError"))
    service = _service(env, source)
    await service.load()
    await service.check()
    fetched_at = service.state.last_success_at
    env.clock.advance(3600)
    await service.check()
    state, _checked = await service.set_channel("pre-release")
    # Re-evaluated from the cached list, but that list predates the failed attempt.
    assert (state.latest_version, state.last_success_at) == ("2026.11.0-RC1", fetched_at)
    assert (state.last_error, state.last_error_detail) == ("network", "ConnectError")


async def test_keep_after_restore_writes_the_installation_state_back(env):
    service = _service(env, FakeSource(FetchResult(False, None, PAYLOAD)))
    await service.load()
    await service.set_channel("pre-release")
    # A restored backup brings another channel and an old status along.
    await env.db.execute_and_commit("UPDATE app_settings SET value='nightly' WHERE key=?", (CHANNEL_SETTING_KEY,))
    await env.db.execute_and_commit("UPDATE app_settings SET value='{}' WHERE key=?", (STATE_SETTING_KEY,))
    env.registry._values[LATEST_VERSION.id] = type(env.registry.get_value(LATEST_VERSION.id))()
    await service.keep_after_restore()
    assert await _setting(env.db, CHANNEL_SETTING_KEY) == "pre-release"
    assert json.loads(await _setting(env.db, STATE_SETTING_KEY))["latest_version"] == "2026.11.0-RC1"
    assert _dp(env, LATEST_VERSION) == ("2026.11.0-RC1", "good")


async def test_set_channel_without_source_does_not_check(env):
    service = _service(env, None)
    await service.load()
    state, _checked = await service.set_channel("nightly")
    assert (state.channel, state.last_check_at) == ("nightly", None)


async def test_set_same_channel_keeps_the_result(env):
    service = _service(env, FakeSource(FetchResult(False, None, PAYLOAD)))
    await service.load()
    await service.check()
    state, _checked = await service.set_channel("stable")
    assert state.latest_version == "2026.10.0"


# ---------------------------------------------------------------------------
# schedule
# ---------------------------------------------------------------------------


class _Rng:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def randint(self, a, b):
        self.calls.append((a, b))
        return self.value


@pytest.mark.parametrize("minutes", [0, 719])
def test_initial_delay_is_whole_minutes_between_0_and_719(env, minutes):
    rng = _Rng(minutes)
    service = _service(env, None, rng=rng)
    assert service.initial_delay_seconds() == minutes * 60
    assert rng.calls == [(0, 719)]


def test_initial_delay_default_rng_stays_in_range(env):
    service = _service(env, None, rng=random.Random(7))
    delays = {service.initial_delay_seconds() for _ in range(200)}
    assert all(0 <= d <= 719 * 60 and d % 60 == 0 for d in delays)
    assert len(delays) > 1


async def test_run_waits_the_offset_then_every_twelve_hours(env, monkeypatch):
    sleeps = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        if len(sleeps) == 3:
            raise asyncio.CancelledError

    monkeypatch.setattr(svc.asyncio, "sleep", fake_sleep)
    source = FakeSource(FetchResult(False, None, PAYLOAD), UpdateCheckError("network"))
    service = _service(env, source, rng=_Rng(42))
    await service.load()
    with pytest.raises(asyncio.CancelledError):
        await service._run()
    assert sleeps == [42 * 60, CHECK_INTERVAL_SECONDS, CHECK_INTERVAL_SECONDS]
    # A failed check does not retry early: two attempts for two intervals.
    assert len(source.etags) == 2
    assert service.next_check_at == env.clock.now + timedelta(seconds=CHECK_INTERVAL_SECONDS)


async def test_start_and_stop(env):
    service = _service(env, FakeSource(), rng=_Rng(719))
    await service.load()
    service.start()
    await asyncio.sleep(0)
    assert service.next_check_at == env.clock.now + timedelta(minutes=719)
    await service.stop()
    assert service._task is None


async def test_start_disabled_schedules_nothing(env, caplog):
    caplog.set_level(logging.INFO, logger="obs.update.service")
    service = _service(env, None)
    service.start()
    assert service._task is None
    assert "disabled" in caplog.text
    await service.stop()


# ---------------------------------------------------------------------------
# singleton
# ---------------------------------------------------------------------------


async def test_init_get_and_reset(env, monkeypatch):
    monkeypatch.setattr(svc, "resolve_installed_version", lambda: "2026.10.0")
    svc.reset_update_service()
    assert svc.get_optional_update_service() is None
    with pytest.raises(RuntimeError):
        svc.get_update_service()
    settings = SimpleNamespace(enabled=False, api_url="https://api.github.com", repository="o/r")
    disabled = await svc.init_update_service(env.db, env.bus, settings)
    assert svc.get_update_service() is disabled
    assert not disabled.enabled
    settings.enabled = True
    enabled = await svc.init_update_service(env.db, env.bus, settings)
    assert enabled.enabled
    assert isinstance(enabled._source, GitHubReleaseSource)
    await enabled.stop()
    svc.reset_update_service()
