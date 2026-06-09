from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest

from obs.knxproj import parser


class _FakeXKNXProj:
    def __init__(self, path: str, password: str | None = None) -> None:
        self.path = path
        self.password = password

    def parse(self):
        return {
            "devices": {
                "dev-1": {
                    "name": "Schaltaktor 16f",
                    "hardware_name": "AKS-1616",
                    "order_number": "AKS-1616.03",
                    "description": "Aktuator EG",
                    "manufacturer_name": "Acme KNX",
                    "individual_address": "1.1.10",
                    "application": "App-1",
                    "project_uid": 17,
                    "communication_object_ids": ["co-1", "co-2"],
                }
            },
            "communication_objects": {
                "co-1": {
                    "name": "Schalten",
                    "number": 0,
                    "text": "Switch",
                    "function_text": "Channel A",
                    "description": "Licht schalten",
                    "device_address": "1.1.10",
                    "device_application": "App-1",
                    "dpts": [{"main": 1, "sub": 1}],
                    "object_size": "1 Bit",
                    "group_address_links": ["1/0/1", "1/0/2"],
                    "flags": {
                        "read": True,
                        "write": True,
                        "communication": True,
                        "transmit": True,
                        "update": False,
                        "read_on_init": False,
                    },
                    "channel": "A",
                    "dpas": ["DPA-1"],
                },
                "co-2": {
                    "name": "Status",
                    "number": 1,
                    "text": "State",
                    "function_text": "Channel A",
                    "description": "Rückmeldung",
                    "device_address": "1.1.10",
                    "device_application": "App-1",
                    "dpts": [{"main": 1, "sub": 1}, {"main": 5, "sub": 1}],
                    "object_size": "1 Byte",
                    "group_address_links": ["1/0/3"],
                    "flags": {
                        "read": True,
                        "write": False,
                        "communication": True,
                        "transmit": False,
                        "update": True,
                        "read_on_init": True,
                    },
                    "channel": None,
                    "dpas": None,
                },
            },
            "group_addresses": {},
            "group_ranges": {},
            "locations": {},
            "functions": {},
        }


@pytest.fixture
def fake_xknxproject(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "xknxproject", SimpleNamespace(XKNXProj=_FakeXKNXProj))


def test_parse_knxproj_devices_extracts_devices_comm_objects_and_ga_links(fake_xknxproject):
    devices, comm_objects, ga_links = parser.parse_knxproj_devices(b"dummy")

    assert len(devices) == 1
    assert devices[0].identifier == "dev-1"
    assert devices[0].individual_address == "1.1.10"
    assert devices[0].communication_object_ids == ["co-1", "co-2"]

    assert len(comm_objects) == 2
    co1 = next(co for co in comm_objects if co.identifier == "co-1")
    assert co1.device_address == "1.1.10"
    assert co1.dpts == ["DPT1.001"]
    assert co1.flags["write"] is True

    co2 = next(co for co in comm_objects if co.identifier == "co-2")
    assert co2.dpts == ["DPT1.001", "DPT5.001"]
    assert co2.dpas == []

    assert sorted((link.comm_object_id, link.ga_address) for link in ga_links) == [
        ("co-1", "1/0/1"),
        ("co-1", "1/0/2"),
        ("co-2", "1/0/3"),
    ]


def test_parse_knxproj_devices_tolerates_missing_optional_fields(fake_xknxproject, monkeypatch: pytest.MonkeyPatch):
    class _Sparse(_FakeXKNXProj):
        def parse(self):
            return {
                "devices": {
                    "dev-a": {
                        "individual_address": "1.1.20",
                    }
                },
                "communication_objects": {
                    "co-a": {
                        "device_address": "1.1.20",
                        "dpts": [{"main": 9, "sub": 1}, {"main": 9, "sub": None}],
                        "group_address_links": [],
                    }
                },
            }

    monkeypatch.setitem(sys.modules, "xknxproject", SimpleNamespace(XKNXProj=_Sparse))

    devices, comm_objects, ga_links = parser.parse_knxproj_devices(b"dummy")

    assert len(devices) == 1
    assert devices[0].name == ""
    assert devices[0].communication_object_ids == []

    assert len(comm_objects) == 1
    assert comm_objects[0].dpts == ["DPT9.001", "DPT9.001"]
    assert ga_links == []


def test_parse_knxproj_group_address_parsing_still_works(monkeypatch: pytest.MonkeyPatch):
    class _GAProj(_FakeXKNXProj):
        def parse(self):
            return {
                "group_ranges": {
                    "1": {"name": "Licht", "group_ranges": {"1/0": {"name": "EG"}}},
                },
                "group_addresses": {
                    "1/0/1": {
                        "name": "Licht Küche",
                        "description": "",
                        "dpt": {"main": 1, "sub": 1},
                    }
                },
                "devices": {},
                "communication_objects": {},
            }

    monkeypatch.setitem(sys.modules, "xknxproject", SimpleNamespace(XKNXProj=_GAProj))

    records = parser.parse_knxproj(b"dummy")

    assert len(records) == 1
    assert records[0].address == "1/0/1"
    assert records[0].main_group_name == "Licht"
    assert records[0].mid_group_name == "EG"
