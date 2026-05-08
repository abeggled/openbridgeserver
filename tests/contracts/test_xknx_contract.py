"""Contract tests for xknx — verifies the import paths and API surface used by obs.adapters.knx."""

from __future__ import annotations

import pytest

xknx = pytest.importorskip("xknx", reason="xknx not installed")

from xknx.dpt import DPTArray, DPTBinary
from xknx.io import ConnectionConfig, ConnectionType, SecureConfig
from xknx.telegram import Telegram
from xknx.telegram.address import GroupAddress, IndividualAddress
from xknx.telegram.apci import GroupValueRead, GroupValueResponse, GroupValueWrite


class TestGroupAddress:
    def test_construction_from_string(self):
        ga = GroupAddress("1/2/3")
        assert str(ga) == "1/2/3"

    def test_equality(self):
        assert GroupAddress("1/2/3") == GroupAddress("1/2/3")
        assert GroupAddress("1/2/3") != GroupAddress("1/2/4")


class TestIndividualAddress:
    def test_construction_from_string(self):
        ia = IndividualAddress("1.1.255")
        assert str(ia) == "1.1.255"

    def test_custom_address(self):
        ia = IndividualAddress("2.3.10")
        assert str(ia) == "2.3.10"


class TestConnectionConfig:
    def test_tunneling_with_individual_address(self):
        cfg = ConnectionConfig(
            connection_type=ConnectionType.TUNNELING,
            gateway_ip="192.168.1.100",
            gateway_port=3671,
            individual_address=IndividualAddress("1.1.255"),
        )
        assert cfg.connection_type == ConnectionType.TUNNELING

    def test_routing_with_local_ip(self):
        cfg = ConnectionConfig(
            connection_type=ConnectionType.ROUTING,
            gateway_ip="224.0.23.12",
            local_ip="192.168.1.5",
        )
        assert cfg.connection_type == ConnectionType.ROUTING

    def test_tunneling_with_local_ip(self):
        """local_ip ist auch bei Tunneling gültig (Schnittstellen-Binding)."""
        cfg = ConnectionConfig(
            connection_type=ConnectionType.TUNNELING,
            gateway_ip="192.168.1.100",
            gateway_port=3671,
            local_ip="192.168.1.5",
        )
        assert cfg.connection_type == ConnectionType.TUNNELING

    def test_tunneling_tcp_secure_connection_type_exists(self):
        assert hasattr(ConnectionType, "TUNNELING_TCP_SECURE")

    def test_routing_secure_connection_type_exists(self):
        assert hasattr(ConnectionType, "ROUTING_SECURE")


class TestSecureConfig:
    def test_tunneling_secure_config(self):
        sc = SecureConfig(
            device_authentication_password="devauth",
            user_id=2,
            user_password="userpass",
        )
        assert sc is not None

    def test_routing_secure_config_with_backbone_key(self):
        # xknx erwartet backbone_key als Hex-String, nicht als bytes
        sc = SecureConfig(backbone_key="0102030405060708090a0b0c0d0e0f10")
        assert sc is not None

    def test_backbone_key_hex_parse_and_validate(self):
        """Normalisierung von Hex-String (Trennzeichen entfernen) + Längenprüfung."""
        hex_str = "0102030405060708090a0b0c0d0e0f10"
        normalized = hex_str.replace(":", "").replace(" ", "")
        assert len(bytes.fromhex(normalized)) == 16

    def test_backbone_key_with_colons(self):
        hex_str = "01:02:03:04:05:06:07:08:09:0a:0b:0c:0d:0e:0f:10"
        normalized = hex_str.replace(":", "").replace(" ", "")
        assert len(bytes.fromhex(normalized)) == 16
        sc = SecureConfig(backbone_key=normalized)
        assert sc is not None

    def test_connection_config_with_secure_config(self):
        sc = SecureConfig(
            device_authentication_password="devauth",
            user_id=2,
            user_password="userpass",
        )
        cfg = ConnectionConfig(
            connection_type=ConnectionType.TUNNELING_TCP_SECURE,
            gateway_ip="192.168.1.100",
            gateway_port=3671,
            individual_address=IndividualAddress("1.1.255"),
            secure_config=sc,
        )
        assert cfg.connection_type == ConnectionType.TUNNELING_TCP_SECURE


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
