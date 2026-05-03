"""Contract tests for xknx — verifies the import paths and API surface used by obs.adapters.knx."""

from __future__ import annotations

import pytest

xknx = pytest.importorskip("xknx", reason="xknx not installed")

from xknx.dpt import DPTArray, DPTBinary
from xknx.telegram import Telegram
from xknx.telegram.address import GroupAddress
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite


class TestGroupAddress:
    def test_construction_from_string(self):
        ga = GroupAddress("1/2/3")
        assert str(ga) == "1/2/3"

    def test_equality(self):
        assert GroupAddress("1/2/3") == GroupAddress("1/2/3")
        assert GroupAddress("1/2/3") != GroupAddress("1/2/4")


class TestDPTTypes:
    def test_dpt_array_from_list(self):
        arr = DPTArray([0x0C, 0x7A])
        assert arr.value == (0x0C, 0x7A)

    def test_dpt_array_single_byte(self):
        arr = DPTArray([0xFF])
        assert arr.value == (0xFF,)

    def test_dpt_binary_one(self):
        b = DPTBinary(1)
        assert b.value == 1

    def test_dpt_binary_zero(self):
        b = DPTBinary(0)
        assert b.value == 0


class TestTelegramConstruction:
    def test_with_group_value_write_dpt_binary(self):
        t = Telegram(
            destination_address=GroupAddress("0/0/1"),
            payload=GroupValueWrite(DPTBinary(1)),
        )
        assert t.destination_address == GroupAddress("0/0/1")
        assert isinstance(t.payload, GroupValueWrite)

    def test_with_group_value_write_dpt_array(self):
        t = Telegram(
            destination_address=GroupAddress("1/2/3"),
            payload=GroupValueWrite(DPTArray([0x0C, 0x7A])),
        )
        assert isinstance(t.payload, GroupValueWrite)
        assert isinstance(t.payload.value, DPTArray)

    def test_with_group_value_read(self):
        t = Telegram(
            destination_address=GroupAddress("0/0/1"),
            payload=GroupValueRead(),
        )
        assert isinstance(t.payload, GroupValueRead)

    def test_with_group_value_response(self):
        t = Telegram(
            destination_address=GroupAddress("2/3/4"),
            payload=GroupValueResponse(DPTArray([0xFF])),
        )
        assert isinstance(t.payload, GroupValueResponse)

    def test_payload_value_access_dpt_binary(self):
        w = GroupValueWrite(DPTBinary(1))
        assert isinstance(w.value, DPTBinary)

    def test_payload_value_access_dpt_array(self):
        w = GroupValueWrite(DPTArray([0x0C, 0x7A]))
        assert isinstance(w.value, DPTArray)
