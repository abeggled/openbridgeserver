import os

from obs.config import _import_legacy_env_vars


def test_legacy_config_overrides_docker_default_obs_config(monkeypatch):
    monkeypatch.setenv("OBS_CONFIG", "/data/config.yaml")
    monkeypatch.setenv("OPENTWS_CONFIG", "/legacy/config.yaml")

    _import_legacy_env_vars()

    assert os.environ["OBS_CONFIG"] == "/legacy/config.yaml"


def test_legacy_db_path_overrides_docker_default_obs_db_path(monkeypatch):
    monkeypatch.setenv("OBS_DATABASE__PATH", "/data/obs.db")
    monkeypatch.setenv("OPENTWS_DATABASE__PATH", "/legacy/opentws.db")

    _import_legacy_env_vars()

    assert os.environ["OBS_DATABASE__PATH"] == "/legacy/opentws.db"


def test_explicit_obs_config_is_not_overwritten(monkeypatch):
    monkeypatch.setenv("OBS_CONFIG", "/custom/obs.yaml")
    monkeypatch.setenv("OPENTWS_CONFIG", "/legacy/config.yaml")

    _import_legacy_env_vars()

    assert os.environ["OBS_CONFIG"] == "/custom/obs.yaml"


def test_legacy_import_when_obs_key_missing(monkeypatch):
    monkeypatch.delenv("OBS_CONFIG", raising=False)
    monkeypatch.setenv("OPENTWS_CONFIG", "/legacy/config.yaml")

    _import_legacy_env_vars()

    assert os.environ["OBS_CONFIG"] == "/legacy/config.yaml"
