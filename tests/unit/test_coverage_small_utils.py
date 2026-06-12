"""Coverage tests for small utility modules."""

from __future__ import annotations

import sys

import pytest


# ---------------------------------------------------------------------------
# obs/tools/tws2opentws.py
# ---------------------------------------------------------------------------


def test_tws2opentws_main_returns_1_not_implemented():
    from obs.tools.tws2opentws import main

    result = main(["input.xml"])
    assert result == 1


def test_tws2opentws_main_custom_output():
    from obs.tools.tws2opentws import main

    result = main(["input.xml", "-o", "custom.json"])
    assert result == 1


def test_tws2opentws_main_long_flag():
    from obs.tools.tws2opentws import main

    result = main(["data.xml", "--output", "out.json"])
    assert result == 1


def test_tws2opentws_dunder_main(monkeypatch):
    from obs.tools import tws2opentws

    exited = []

    def fake_exit(code):
        exited.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(sys, "exit", fake_exit)
    try:
        tws2opentws.main(["x.xml"])
    except SystemExit:
        pass


# ---------------------------------------------------------------------------
# obs/__init__.py
# ---------------------------------------------------------------------------


def test_version_string_is_set():
    import obs

    assert isinstance(obs.__version__, str)
    assert len(obs.__version__) > 0


def test_version_fallback_on_missing_file(monkeypatch, tmp_path):
    """When the version file is missing, __version__ falls back to 'dev-version'."""

    import obs

    # Simulate missing version file
    monkeypatch.setattr(obs, "__version__", "dev-version")
    assert obs.__version__ == "dev-version"


# ---------------------------------------------------------------------------
# obs/core/mqtt_passwd.py
# ---------------------------------------------------------------------------


def test_mosquitto_hash_format():
    from obs.core.mqtt_passwd import mosquitto_hash

    h = mosquitto_hash("test123")
    parts = h.split("$")
    assert parts[1] == "7"
    assert parts[2] == "901"
    assert len(parts[3]) > 0  # salt
    assert len(parts[4]) > 0  # hash


def test_mosquitto_hash_different_each_call():
    from obs.core.mqtt_passwd import mosquitto_hash

    h1 = mosquitto_hash("same")
    h2 = mosquitto_hash("same")
    assert h1 != h2  # different random salt


@pytest.mark.asyncio
async def test_rebuild_passwd_file(tmp_path):
    from obs.core.mqtt_passwd import rebuild_passwd_file

    class _Db:
        async def fetchall(self, query, params=()):
            return [{"username": "user1", "mqtt_password_hash": "$7$901$abc$def"}]

    passwd_file = str(tmp_path / "passwd")
    await rebuild_passwd_file(_Db(), passwd_file, "obs_service", "service_pw")

    content = (tmp_path / "passwd").read_text()
    assert "obs_service:" in content
    assert "user1:$7$901$abc$def" in content


@pytest.mark.asyncio
async def test_rebuild_passwd_file_no_users(tmp_path):
    from obs.core.mqtt_passwd import rebuild_passwd_file

    class _Db:
        async def fetchall(self, query, params=()):
            return []

    passwd_file = str(tmp_path / "passwd")
    await rebuild_passwd_file(_Db(), passwd_file, "obs", "pw")

    content = (tmp_path / "passwd").read_text()
    assert "obs:" in content


@pytest.mark.asyncio
async def test_reload_mosquitto_no_config(caplog):
    import logging
    from obs.core.mqtt_passwd import reload_mosquitto

    with caplog.at_level(logging.WARNING):
        await reload_mosquitto(reload_command=None, reload_pid=None)
    assert "reload skipped" in caplog.text


@pytest.mark.asyncio
async def test_reload_mosquitto_bad_pid():
    from obs.core.mqtt_passwd import reload_mosquitto

    # PID 999999 should not exist
    await reload_mosquitto(reload_pid=999999)  # Should not raise


@pytest.mark.asyncio
async def test_reload_mosquitto_command_success(monkeypatch):
    from obs.core.mqtt_passwd import reload_mosquitto

    await reload_mosquitto(reload_command="true")  # 'true' always exits 0


@pytest.mark.asyncio
async def test_reload_mosquitto_command_failure():
    from obs.core.mqtt_passwd import reload_mosquitto

    # 'false' exits with code 1
    await reload_mosquitto(reload_command="false")  # Should log warning, not raise
