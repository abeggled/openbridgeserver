"""Structured 422 for a missing or invalid KNX group address in a binding (#1296, P4b round 2)."""

from __future__ import annotations

import pytest

from obs.api.v1.bindings import GroupAddressInputError, _normalize_knx_group_addresses


@pytest.mark.parametrize(
    ("config", "code", "field", "value"),
    [
        ({"dpt_id": "DPT1.001"}, "knxGroupAddressMissing", "group_address", None),
        ({"group_address": " "}, "knxGroupAddressMissing", "group_address", " "),
        ({"group_address": "1/5000"}, "knxGroupAddressInvalid", "group_address", "1/5000"),
        ({"group_address": 2282.5}, "knxGroupAddressInvalid", "group_address", 2282.5),
        ({"group_address": "1/234", "state_group_address": "40/1"}, "knxGroupAddressInvalid", "state_group_address", "40/1"),
    ],
)
def test_bad_group_address_raises_a_structured_error(config, code, field, value):
    with pytest.raises(GroupAddressInputError) as caught:
        _normalize_knx_group_addresses("KNX", config)
    error = caught.value
    assert error.status_code == 422
    assert (error.detail["code"], error.detail["field"], error.detail["value"]) == (code, field, value)
    assert str(error) == f"422: {error.detail['message']}"
    assert "pydantic" not in str(error)


def test_valid_addresses_are_normalized_and_an_empty_feedback_address_kept():
    config = {"group_address": "1/234", "state_group_address": " ", "dpt_id": "DPT1.001"}
    assert _normalize_knx_group_addresses("KNX", config) == {"group_address": "1/0/234", "state_group_address": " ", "dpt_id": "DPT1.001"}
    assert _normalize_knx_group_addresses("KNX", {"group_address": "2282", "state_group_address": "1/235"})["state_group_address"] == "1/0/235"


def test_other_adapters_are_left_alone():
    config = {"group_address": "not checked"}
    assert _normalize_knx_group_addresses("MQTT", config) is config
