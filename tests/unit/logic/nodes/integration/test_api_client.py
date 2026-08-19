from __future__ import annotations

from obs.logic.nodes.integration.api_client import NODE_TYPE


def test_request_timeout_has_a_positive_lower_bound():
    assert NODE_TYPE.config_schema["timeout_s"]["min"] == 1
    assert NODE_TYPE.config_schema["timeout_s"]["default"] == 10


def test_credentials_are_marked_as_password_fields():
    assert NODE_TYPE.config_schema["auth_password"]["subtype"] == "password"
    assert NODE_TYPE.config_schema["auth_token"]["subtype"] == "password"


def test_ssl_verification_is_enabled_by_default():
    assert NODE_TYPE.config_schema["verify_ssl"]["default"] is True
