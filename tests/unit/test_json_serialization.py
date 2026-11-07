from __future__ import annotations

import datetime
import json

import pytest

from obs.core.json import json_dumps, jsonable
from obs.core.mqtt_client import build_payload


def test_json_dumps_serializes_time_values():
    payload = json_dumps({"v": datetime.time(20, 50, 23)})

    assert json.loads(payload) == {"v": "20:50:23"}


def test_jsonable_converts_nested_time_values():
    value = jsonable({"new_value": datetime.time(7, 8, 9), "history": [datetime.time(1, 2, 3)]})

    assert value == {"new_value": "07:08:09", "history": ["01:02:03"]}


def test_jsonable_converts_tuple_of_time_values():
    value = jsonable((datetime.time(1, 2, 3), datetime.time(4, 5, 6)))

    assert value == ["01:02:03", "04:05:06"]


def test_jsonable_replaces_recursive_container_references():
    value: dict[str, object] = {}
    value["self"] = value

    assert jsonable(value) == {"self": "<recursive dict>"}


def test_jsonable_normalizes_non_json_mapping_keys():
    value = {(1, 2): "tuple", datetime.date(2026, 7, 29): "date"}

    assert jsonable(value) == {"(1, 2)": "tuple", "2026-07-29": "date"}
    assert json.loads(json.dumps(jsonable(value))) == {"(1, 2)": "tuple", "2026-07-29": "date"}


def test_json_dumps_raises_for_non_serializable():
    import pytest

    with pytest.raises(TypeError):
        json_dumps(object())


def test_mqtt_payload_accepts_time_values():
    payload = build_payload(datetime.time(20, 50, 23), None, "good")

    assert json.loads(payload)["v"] == "20:50:23"


def _strict_loads(payload):
    def reject(constant):
        raise ValueError(f"invalid JSON constant: {constant}")

    return json.loads(payload, parse_constant=reject)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_mqtt_payload_maps_non_finite_floats_to_null(value):
    # Bare NaN/Infinity tokens are not valid JSON (RFC 8259) (#1153).
    assert _strict_loads(build_payload(value, None, "good"))["v"] is None


def test_mqtt_payload_maps_nested_non_finite_floats_to_null():
    value = {"a": float("nan"), "b": [1.5, float("inf"), (float("-inf"), "x")], "c": 2}

    assert _strict_loads(build_payload(value, None, "good"))["v"] == {"a": None, "b": [1.5, None, [None, "x"]], "c": 2}


def test_mqtt_payload_keeps_finite_float():
    assert _strict_loads(build_payload(21.5, "°C", "good"))["v"] == 21.5
