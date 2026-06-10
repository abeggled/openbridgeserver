from __future__ import annotations

from obs.api.v1.redaction import REDACTED, redact_sensitive_fields


def test_redact_sensitive_fields_redacts_non_empty_values():
    payload = {
        "token": "abc",
        "password": "pw",
        "dsn": "postgres://db",
        "count": 0,
        "enabled": False,
    }

    result = redact_sensitive_fields(payload, ("token", "password", "dsn", "count", "enabled"))

    assert result["token"] == REDACTED
    assert result["password"] == REDACTED
    assert result["dsn"] == REDACTED
    assert result["count"] == REDACTED
    assert result["enabled"] == REDACTED


def test_redact_sensitive_fields_keeps_none_and_empty_string():
    payload = {
        "token": "",
        "password": None,
        "username": "alice",
    }

    result = redact_sensitive_fields(payload, ("token", "password"))

    assert result["token"] == ""
    assert result["password"] is None
    assert result["username"] == "alice"


def test_redact_sensitive_fields_uses_custom_replacement_and_does_not_mutate_input():
    payload = {"secret": "value", "name": "demo"}
    original = dict(payload)

    result = redact_sensitive_fields(payload, ("secret",), replacement="***")

    assert result == {"secret": "***", "name": "demo"}
    assert payload == original
