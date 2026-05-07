"""Contract test für pysnmp — verifiziert die genauen Import-Pfade und API-Signaturen,
die obs/adapters/snmp/adapter.py verwendet.

Schlägt sofort fehl, wenn ein Dependency-Upgrade die API verändert,
statt erst beim Laufzeit-Fehler im Adapter zu scheitern.
"""

import pytest

pysnmp = pytest.importorskip("pysnmp", reason="pysnmp nicht installiert")

# ---------------------------------------------------------------------------
# hlapi Import-Pfad (v5: hlapi.asyncio, v6.2+: hlapi.v3arch.asyncio)
# ---------------------------------------------------------------------------


def _import_hlapi():
    """Importiert hlapi-Symbole — probiert v6-Pfad zuerst, fällt auf v5 zurück."""
    try:
        from pysnmp.hlapi.v3arch.asyncio import (  # noqa: F401
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            UsmUserData,
            getCmd,
            nextCmd,
            setCmd,
        )

        return "v6"
    except ImportError:
        from pysnmp.hlapi.asyncio import (  # noqa: F401
            CommunityData,
            ContextData,
            ObjectIdentity,
            ObjectType,
            SnmpEngine,
            UdpTransportTarget,
            UsmUserData,
            getCmd,
            nextCmd,
            setCmd,
        )

        return "v5"


def test_hlapi_importable():
    path = _import_hlapi()
    assert path in ("v5", "v6")


def test_get_cmd_is_callable():
    _import_hlapi()
    try:
        from pysnmp.hlapi.v3arch.asyncio import getCmd
    except ImportError:
        from pysnmp.hlapi.asyncio import getCmd
    import inspect

    assert callable(getCmd)
    # getCmd muss mindestens snmpEngine, authData, transportTarget, contextData, *varBinds annehmen
    sig = inspect.signature(getCmd)
    params = list(sig.parameters.keys())
    assert len(params) >= 4


def test_set_cmd_is_callable():
    _import_hlapi()
    try:
        from pysnmp.hlapi.v3arch.asyncio import setCmd
    except ImportError:
        from pysnmp.hlapi.asyncio import setCmd
    assert callable(setCmd)


def test_next_cmd_is_callable():
    _import_hlapi()
    try:
        from pysnmp.hlapi.v3arch.asyncio import nextCmd
    except ImportError:
        from pysnmp.hlapi.asyncio import nextCmd
    assert callable(nextCmd)


# ---------------------------------------------------------------------------
# SnmpEngine
# ---------------------------------------------------------------------------


def test_snmp_engine_instantiable():
    try:
        from pysnmp.hlapi.v3arch.asyncio import SnmpEngine
    except ImportError:
        from pysnmp.hlapi.asyncio import SnmpEngine
    engine = SnmpEngine()
    assert engine is not None


# ---------------------------------------------------------------------------
# CommunityData — v1/v2c
# ---------------------------------------------------------------------------


def test_community_data_v2c():
    try:
        from pysnmp.hlapi.v3arch.asyncio import CommunityData
    except ImportError:
        from pysnmp.hlapi.asyncio import CommunityData
    # mpModel=1 → SNMPv2c
    cd = CommunityData("public", mpModel=1)
    assert cd is not None


def test_community_data_v1():
    try:
        from pysnmp.hlapi.v3arch.asyncio import CommunityData
    except ImportError:
        from pysnmp.hlapi.asyncio import CommunityData
    # mpModel=0 → SNMPv1
    cd = CommunityData("private", mpModel=0)
    assert cd is not None


# ---------------------------------------------------------------------------
# UsmUserData — SNMPv3
# ---------------------------------------------------------------------------


def test_usm_user_data_no_auth():
    try:
        from pysnmp.hlapi.v3arch.asyncio import UsmUserData
    except ImportError:
        from pysnmp.hlapi.asyncio import UsmUserData
    usm = UsmUserData("testuser")
    assert usm is not None


def _import_auth_constants():
    """Auth/priv constants moved from hlapi to hlapi.asyncio in pysnmp 6.x."""
    try:
        from pysnmp.hlapi.v3arch.asyncio import usmHMACMD5AuthProtocol, usmDESPrivProtocol  # noqa: F401

        return usmHMACMD5AuthProtocol, usmDESPrivProtocol
    except ImportError:
        from pysnmp.hlapi.asyncio import usmHMACMD5AuthProtocol, usmDESPrivProtocol  # noqa: F401

        return usmHMACMD5AuthProtocol, usmDESPrivProtocol


def test_usm_user_data_auth_no_priv():
    try:
        from pysnmp.hlapi.v3arch.asyncio import UsmUserData
    except ImportError:
        from pysnmp.hlapi.asyncio import UsmUserData
    usmHMACMD5AuthProtocol, _ = _import_auth_constants()
    usm = UsmUserData("testuser", authKey="authpass", authProtocol=usmHMACMD5AuthProtocol)
    assert usm is not None


def test_usm_user_data_auth_priv():
    try:
        from pysnmp.hlapi.v3arch.asyncio import UsmUserData
    except ImportError:
        from pysnmp.hlapi.asyncio import UsmUserData
    usmHMACMD5AuthProtocol, usmDESPrivProtocol = _import_auth_constants()
    usm = UsmUserData(
        "testuser",
        authKey="authpass",
        privKey="privpass",
        authProtocol=usmHMACMD5AuthProtocol,
        privProtocol=usmDESPrivProtocol,
    )
    assert usm is not None


# ---------------------------------------------------------------------------
# UdpTransportTarget — host/port/timeout/retries
# ---------------------------------------------------------------------------


def test_udp_transport_target():
    try:
        from pysnmp.hlapi.v3arch.asyncio import UdpTransportTarget
    except ImportError:
        from pysnmp.hlapi.asyncio import UdpTransportTarget
    t = UdpTransportTarget(("192.168.1.1", 161), timeout=5, retries=1)
    assert t is not None


# ---------------------------------------------------------------------------
# ObjectType / ObjectIdentity
# ---------------------------------------------------------------------------


def test_object_type_with_oid():
    try:
        from pysnmp.hlapi.v3arch.asyncio import ObjectIdentity, ObjectType
    except ImportError:
        from pysnmp.hlapi.asyncio import ObjectIdentity, ObjectType
    ot = ObjectType(ObjectIdentity("1.3.6.1.2.1.1.1.0"))
    assert ot is not None


# ---------------------------------------------------------------------------
# Auth/Priv protocol constants — in pysnmp 6.x moved to hlapi.asyncio
# ---------------------------------------------------------------------------


def _hlapi_asyncio():
    try:
        import pysnmp.hlapi.v3arch.asyncio as m

        return m
    except ImportError:
        import pysnmp.hlapi.asyncio as m

        return m


def test_auth_protocol_md5():
    assert _hlapi_asyncio().usmHMACMD5AuthProtocol is not None


def test_auth_protocol_sha():
    assert _hlapi_asyncio().usmHMACSHAAuthProtocol is not None


def test_priv_protocol_des():
    assert _hlapi_asyncio().usmDESPrivProtocol is not None


def test_priv_protocol_aes128():
    assert _hlapi_asyncio().usmAesCfb128Protocol is not None


def test_no_auth_no_priv_protocols():
    m = _hlapi_asyncio()
    assert m.usmNoAuthProtocol is not None
    assert m.usmNoPrivProtocol is not None


# ---------------------------------------------------------------------------
# rfc1902 value types — verwendet in _encode_write_value
# ---------------------------------------------------------------------------


def test_integer32_importable():
    from pysnmp.proto.rfc1902 import Integer32

    v = Integer32(42)
    assert int(v) == 42


def test_octet_string_importable():
    from pysnmp.proto.rfc1902 import OctetString

    v = OctetString(b"hello")
    assert v is not None


def test_value_pretty_print():
    from pysnmp.proto.rfc1902 import Integer32, OctetString

    assert Integer32(99).prettyPrint() == "99"
    assert OctetString(b"test").prettyPrint() == "test"
