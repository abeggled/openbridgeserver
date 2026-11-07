"""System API routes of the update check (issue #1370)."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from obs.api.v1 import system as system_api
from obs.update import service as svc
from obs.update.service import ReleaseNote, UpdateState

NOW = datetime(2026, 10, 17, 12, 0, tzinfo=UTC)


def _service(*, enabled=True, **state_fields):
    state = UpdateState(channel="stable", installed_version="2026.9.0", **state_fields)

    async def set_channel(channel):
        state.channel = channel
        return state, channel == "nightly"

    return SimpleNamespace(
        enabled=enabled,
        state=state,
        checking=False,
        next_check_at=NOW,
        check_now=AsyncMock(return_value=(state, True)),
        set_channel=AsyncMock(side_effect=set_channel),
    )


@pytest.fixture
def audit():
    writer = SimpleNamespace(write_contract=AsyncMock())
    with patch.object(system_api, "AuditLogWriter", return_value=writer):
        yield writer


def _install(monkeypatch, service):
    monkeypatch.setattr(svc, "_service", service)


async def test_status_without_service_is_503(monkeypatch):
    _install(monkeypatch, None)
    with pytest.raises(HTTPException) as exc_info:
        await system_api.get_update_status(_user="admin")
    assert exc_info.value.status_code == 503


async def test_status_reports_the_cached_state(monkeypatch):
    note = ReleaseNote(version="2026.10.0", name="Release 2026.10.0", published_at=None, url="https://x", body="* a")
    _install(
        monkeypatch,
        _service(
            latest_version="2026.10.0",
            update_available=True,
            release_notes=[note],
            last_check_at=NOW,
            last_success_at=NOW,
        ),
    )
    out = await system_api.get_update_status(_user="admin")
    assert out.channels == ["stable", "pre-release", "nightly"]
    assert (out.latest_version, out.update_available, out.release_notes_count, out.stale) == ("2026.10.0", True, 1, False)
    notes = await system_api.get_update_release_notes(_user="admin")
    assert notes[0].version == "2026.10.0"
    assert notes[0].body == "* a"


@pytest.mark.parametrize(
    ("last_error", "last_success_at", "stale"),
    [
        ("network", NOW, True),  # failed after an earlier success: maybe outdated
        ("network", None, False),  # never succeeded: nothing to be stale
        (None, NOW, False),
    ],
)
async def test_status_marks_a_stale_result(monkeypatch, last_error, last_success_at, stale):
    _install(monkeypatch, _service(last_error=last_error, last_success_at=last_success_at))
    assert (await system_api.get_update_status(_user="admin")).stale is stale


async def test_manual_check_when_disabled_is_409(monkeypatch, audit):
    _install(monkeypatch, _service(enabled=False))
    with pytest.raises(HTTPException) as exc_info:
        await system_api.check_for_updates(request=None, _admin="admin", db=object())
    assert exc_info.value.status_code == 409
    audit.write_contract.assert_not_awaited()


@pytest.mark.parametrize(("last_error", "outcome"), [(None, "success"), ("network", "failed")])
async def test_manual_check_runs_and_audits(monkeypatch, audit, last_error, outcome):
    service = _service(last_error=last_error)
    _install(monkeypatch, service)
    out = await system_api.check_for_updates(request=None, _admin="admin", db=object())
    service.check_now.assert_awaited_once()
    assert out.last_error == last_error
    call = audit.write_contract.await_args
    assert call.args == ("POST", "/api/v1/system/update-status/check")
    assert call.kwargs["outcome"].value == outcome


async def test_channel_change_is_audited(monkeypatch, audit):
    service = _service()
    _install(monkeypatch, service)
    out = await system_api.set_update_channel(system_api.UpdateChannelIn(channel="nightly"), request=None, _admin="admin", db=object())
    service.set_channel.assert_awaited_once_with("nightly")
    assert out.channel == "nightly"
    put_call, check_call = audit.write_contract.await_args_list
    assert put_call.args == ("PUT", "/api/v1/system/update-channel")
    assert put_call.kwargs["details"] == {"before": "stable", "after": "nightly"}
    # The switch asked GitHub (no cached list): audited like a manual check.
    assert check_call.args == ("POST", "/api/v1/system/update-status/check")


@pytest.mark.parametrize(("channel", "last_error", "calls"), [("pre-release", None, 1), ("nightly", "network", 2)])
async def test_channel_change_audits_a_check_only_when_it_ran(monkeypatch, audit, channel, last_error, calls):
    service = _service(last_error=last_error)
    _install(monkeypatch, service)
    await system_api.set_update_channel(system_api.UpdateChannelIn(channel=channel), request=None, _admin="admin", db=object())
    assert audit.write_contract.await_count == calls
    if calls == 2:
        assert audit.write_contract.await_args.kwargs["outcome"].value == "failed"


async def test_manual_check_without_a_request_is_not_audited(monkeypatch, audit):
    service = _service()
    service.check_now = AsyncMock(return_value=(service.state, False))
    _install(monkeypatch, service)
    out = await system_api.check_for_updates(request=None, _admin="admin", db=object())
    assert out.channel == "stable"
    audit.write_contract.assert_not_awaited()


def test_channel_body_rejects_unknown_channels():
    with pytest.raises(ValueError):
        system_api.UpdateChannelIn(channel="beta")
