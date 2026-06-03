from __future__ import annotations

import datetime
import json

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


def test_json_dumps_raises_for_non_serializable():
    import pytest

    with pytest.raises(TypeError):
        json_dumps(object())


def test_mqtt_payload_accepts_time_values():
    payload = build_payload(datetime.time(20, 50, 23), None, "good")

    assert json.loads(payload)["v"] == "20:50:23"
