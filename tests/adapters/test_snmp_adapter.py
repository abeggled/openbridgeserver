"""Unit tests für den SNMP Adapter.

Alle pysnmp-Aufrufe werden gemockt — kein echtes SNMP-Gerät erforderlich.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from obs.adapters.snmp.adapter import (
    SnmpAdapter,
    SnmpAdapterConfig,
    SnmpBindingConfig,
    _coerce_value,
    _encode_write_value,
)
from tests.adapters.conftest import make_binding


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_bus():
    bus = MagicMock()
    bus.publish = AsyncMock()
    return bus


@pytest.fixture
def snmp_symbols():
    """Fake pysnmp-Symbol-Dict, das _import_pysnmp() zurückgibt."""
    fake_engine = MagicMock(name="SnmpEngine")
    fake_community = MagicMock(name="CommunityData")
    fake_usm = MagicMock(name="UsmUserData")
    fake_transport = MagicMock(name="UdpTransportTarget")
    fake_ctx = MagicMock(name="ContextData")
    fake_ot = MagicMock(name="ObjectType")
    fake_oi = MagicMock(name="ObjectIdentity")
    fake_no_auth = MagicMock(name="noAuth")
    fake_no_priv = MagicMock(name="noPriv")

    return {
        "getCmd": None,  # wird pro Test gesetzt
        "setCmd": None,
        "nextCmd": None,
        "SnmpEngine": MagicMock(return_value=fake_engine),
        "CommunityData": MagicMock(return_value=fake_community),
        "UsmUserData": MagicMock(return_value=fake_usm),
        "UdpTransportTarget": MagicMock(return_value=fake_transport),
        "ContextData": MagicMock(return_value=fake_ctx),
        "ObjectType": MagicMock(return_value=fake_ot),
        "ObjectIdentity": MagicMock(return_value=fake_oi),
        "_auth_map": {"MD5": MagicMock(), "SHA": MagicMock(), "SHA256": MagicMock(), "SHA512": MagicMock()},
        "_priv_map": {"DES": MagicMock(), "3DES": MagicMock(), "AES128": MagicMock(), "AES192": MagicMock(), "AES256": MagicMock()},
        "_no_auth": fake_no_auth,
        "_no_priv": fake_no_priv,
    }


def _make_snmp_value(pp: str, type_name: str = "OctetString", int_val: int | None = None) -> MagicMock:
    """Erzeugt ein Mock-pysnmp-Wertobjekt."""
    v = MagicMock()
    v.prettyPrint.return_value = pp
    type(v).__name__ = type_name
    if int_val is not None:
        v.__int__ = MagicMock(return_value=int_val)
        # Damit int(v) funktioniert
        v.__index__ = MagicMock(return_value=int_val)
    return v


async def _async_get_result(error_indication, error_status, error_index, var_binds):
    """Hilfsfunktion: erstellt eine Coroutine, die ein Tupel zurückgibt."""
    return (error_indication, error_status, error_index, var_binds)


# ---------------------------------------------------------------------------
# Config Validation
# ---------------------------------------------------------------------------


class TestSnmpAdapterConfig:
    def test_defaults_v2c(self):
        cfg = SnmpAdapterConfig()
        assert cfg.version == "2c"
        assert cfg.community == "public"

    def test_v1_community(self):
        cfg = SnmpAdapterConfig(version="1", community="private")
        assert cfg.version == "1"
        assert cfg.community == "private"

    def test_v3_fields(self):
        cfg = SnmpAdapterConfig(
            version="3",
            security_name="admin",
            security_level="authPriv",
            auth_protocol="SHA",
            auth_key="authpass",
            priv_protocol="AES128",
            priv_key="privpass",
        )
        assert cfg.version == "3"
        assert cfg.security_level == "authPriv"
        assert cfg.priv_protocol == "AES128"


class TestSnmpBindingConfig:
    def test_defaults(self):
        bc = SnmpBindingConfig(oid="1.3.6.1.2.1.1.1.0")
        assert bc.host == "192.168.1.1"
        assert bc.port == 161
        assert bc.data_type == "auto"
        assert bc.poll_interval == 30.0

    def test_custom_host_and_oid(self):
        bc = SnmpBindingConfig(host="switch.local", port=161, oid="1.3.6.1.2.1.2.2.1.10.1")
        assert bc.host == "switch.local"
        assert bc.oid == "1.3.6.1.2.1.2.2.1.10.1"


# ---------------------------------------------------------------------------
# Value Coercion
# ---------------------------------------------------------------------------


class TestCoerceValue:
    def test_auto_integer32(self):
        v = _make_snmp_value("42", "Integer32", int_val=42)
        result = _coerce_value(v, "auto")
        assert result == 42

    def test_auto_counter32(self):
        v = _make_snmp_value("12345", "Counter32", int_val=12345)
        result = _coerce_value(v, "auto")
        assert result == 12345

    def test_auto_timeticks(self):
        v = _make_snmp_value("360000", "TimeTicks", int_val=360000)
        result = _coerce_value(v, "auto")
        assert result == 360000

    def test_auto_numeric_string(self):
        v = _make_snmp_value("99", "OctetString")
        result = _coerce_value(v, "auto")
        assert result == 99

    def test_auto_float_string(self):
        v = _make_snmp_value("23.5", "OctetString")
        result = _coerce_value(v, "auto")
        assert result == pytest.approx(23.5)

    def test_auto_non_numeric_string(self):
        v = _make_snmp_value("Linux 5.15.0", "OctetString")
        result = _coerce_value(v, "auto")
        assert result == "Linux 5.15.0"

    def test_explicit_int(self):
        v = _make_snmp_value("100", "OctetString", int_val=100)
        result = _coerce_value(v, "int")
        assert result == 100

    def test_explicit_float(self):
        v = _make_snmp_value("22.75", "OctetString")
        result = _coerce_value(v, "float")
        assert result == pytest.approx(22.75)

    def test_explicit_string(self):
        v = _make_snmp_value("42", "Integer32", int_val=42)
        result = _coerce_value(v, "string")
        assert result == "42"

    def test_gauge(self):
        v = _make_snmp_value("500", "Gauge32", int_val=500)
        result = _coerce_value(v, "gauge")
        assert result == 500

    def test_counter(self):
        v = _make_snmp_value("9999", "Counter64", int_val=9999)
        result = _coerce_value(v, "counter")
        assert result == 9999

    def test_timeticks_explicit(self):
        v = _make_snmp_value("7200", "TimeTicks", int_val=7200)
        result = _coerce_value(v, "timeticks")
        assert result == 7200


# ---------------------------------------------------------------------------
# Write Value Encoding
# ---------------------------------------------------------------------------


class TestEncodeWriteValue:
    def test_int_type(self):
        try:
            from pysnmp.proto.rfc1902 import Integer32

            val = _encode_write_value(42, "int")
            assert isinstance(val, Integer32)
        except ImportError:
            pytest.skip("pysnmp nicht installiert")

    def test_string_type(self):
        try:
            from pysnmp.proto.rfc1902 import OctetString

            val = _encode_write_value("hello", "string")
            assert isinstance(val, OctetString)
        except ImportError:
            pytest.skip("pysnmp nicht installiert")

    def test_bool_auto(self):
        try:
            from pysnmp.proto.rfc1902 import Integer32

            val = _encode_write_value(True, "auto")
            assert isinstance(val, Integer32)
        except ImportError:
            pytest.skip("pysnmp nicht installiert")

    def test_float_auto(self):
        try:
            from pysnmp.proto.rfc1902 import OctetString

            val = _encode_write_value(3.14, "auto")
            assert isinstance(val, OctetString)
        except ImportError:
            pytest.skip("pysnmp nicht installiert")


# ---------------------------------------------------------------------------
# Adapter Lifecycle
# ---------------------------------------------------------------------------


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_without_pysnmp(self, mock_bus):
        adapter = SnmpAdapter(event_bus=mock_bus, config={"version": "2c", "community": "public"})
        with patch("obs.adapters.snmp.adapter._import_pysnmp", return_value={}):
            await adapter.connect()
        assert adapter.connected is False
        mock_bus.publish.assert_called_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.connected is False

    @pytest.mark.asyncio
    async def test_connect_with_pysnmp_v2c(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(event_bus=mock_bus, config={"version": "2c", "community": "public"})
        with patch("obs.adapters.snmp.adapter._import_pysnmp", return_value=snmp_symbols):
            await adapter.connect()
        assert adapter.connected is True

    @pytest.mark.asyncio
    async def test_connect_v3_auth_priv(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(
            event_bus=mock_bus,
            config={
                "version": "3",
                "security_name": "obs-user",
                "security_level": "authPriv",
                "auth_protocol": "SHA",
                "auth_key": "authpassphrase",
                "priv_protocol": "AES128",
                "priv_key": "privpassphrase",
            },
        )
        with patch("obs.adapters.snmp.adapter._import_pysnmp", return_value=snmp_symbols):
            await adapter.connect()
        assert adapter.connected is True

    @pytest.mark.asyncio
    async def test_disconnect_cancels_poll_tasks(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(event_bus=mock_bus, config={"version": "2c", "community": "public"})
        with patch("obs.adapters.snmp.adapter._import_pysnmp", return_value=snmp_symbols):
            await adapter.connect()

        # Fake poll task
        async def _dummy():
            await asyncio.sleep(9999)

        t = asyncio.create_task(_dummy())
        adapter._poll_tasks.append(t)

        await adapter.disconnect()
        await asyncio.sleep(0)  # let event loop process the cancellation
        assert adapter.connected is False
        assert len(adapter._poll_tasks) == 0
        assert t.cancelled()


# ---------------------------------------------------------------------------
# Poll Loop
# ---------------------------------------------------------------------------


class TestPollLoop:
    def _make_adapter(self, mock_bus, snmp_symbols, config=None):
        cfg = config or {"version": "2c", "community": "public"}
        adapter = SnmpAdapter(event_bus=mock_bus, config=cfg)
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True
        return adapter

    @pytest.mark.asyncio
    async def test_publishes_good_value(self, mock_bus, snmp_symbols):
        adapter = self._make_adapter(mock_bus, snmp_symbols)
        snmp_value = _make_snmp_value("42", "Integer32", int_val=42)

        async def fake_get(*args, **kwargs):
            return (None, None, None, [(MagicMock(), snmp_value)])

        snmp_symbols["getCmd"] = fake_get
        binding = make_binding({"host": "192.168.1.1", "oid": "1.3.6.1.2.1.1.1.0", "poll_interval": 999})

        # Einen Durchlauf des Poll-Loops ausführen
        async def run_once():
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_once()
        assert mock_bus.publish.called
        event = mock_bus.publish.call_args[0][0]
        assert event.quality == "good"
        assert event.datapoint_id == binding.datapoint_id

    @pytest.mark.asyncio
    async def test_publishes_bad_quality_on_snmp_error(self, mock_bus, snmp_symbols):
        adapter = self._make_adapter(mock_bus, snmp_symbols)

        async def fake_get_error(*args, **kwargs):
            error_indication = MagicMock()
            error_indication.__str__ = MagicMock(return_value="Timeout")
            return (error_indication, None, None, [])

        snmp_symbols["getCmd"] = fake_get_error
        binding = make_binding({"host": "192.168.1.1", "oid": "1.3.6.1.2.1.1.1.0", "poll_interval": 999})

        async def run_once():
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.quality == "bad"
        assert event.value is None

    @pytest.mark.asyncio
    async def test_applies_value_formula(self, mock_bus, snmp_symbols):
        adapter = self._make_adapter(mock_bus, snmp_symbols)
        snmp_value = _make_snmp_value("1000", "Integer32", int_val=1000)

        async def fake_get(*args, **kwargs):
            return (None, None, None, [(MagicMock(), snmp_value)])

        snmp_symbols["getCmd"] = fake_get
        binding = make_binding(
            {"host": "192.168.1.1", "oid": "1.3.6.1.2.1.1.1.0", "poll_interval": 999},
            value_formula="x / 10",
        )

        async def run_once():
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.quality == "good"
        assert event.value == pytest.approx(100.0)

    @pytest.mark.asyncio
    async def test_applies_value_map(self, mock_bus, snmp_symbols):
        adapter = self._make_adapter(mock_bus, snmp_symbols)
        snmp_value = _make_snmp_value("1", "Integer32", int_val=1)

        async def fake_get(*args, **kwargs):
            return (None, None, None, [(MagicMock(), snmp_value)])

        snmp_symbols["getCmd"] = fake_get
        binding = make_binding(
            {"host": "192.168.1.1", "oid": "1.3.6.1.2.1.1.1.0", "poll_interval": 999},
            value_map={"1": "up", "2": "down"},
        )

        async def run_once():
            task = asyncio.create_task(adapter._poll_loop(binding))
            await asyncio.sleep(0.05)
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

        await run_once()
        event = mock_bus.publish.call_args[0][0]
        assert event.value == "up"

    @pytest.mark.asyncio
    async def test_invalid_binding_config_skipped(self, mock_bus, snmp_symbols):
        adapter = self._make_adapter(mock_bus, snmp_symbols)
        binding = make_binding({"oid": "1.3.6.1.2.1.1.1.0", "poll_interval": -999})  # ungültig

        # Soll sofort beendet werden ohne publish
        task = asyncio.create_task(adapter._poll_loop(binding))
        await asyncio.sleep(0.05)
        assert task.done()
        mock_bus.publish.assert_not_called()


# ---------------------------------------------------------------------------
# On Bindings Reloaded
# ---------------------------------------------------------------------------


class TestOnBindingsReloaded:
    @pytest.mark.asyncio
    async def test_starts_poll_task_for_source_binding(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(event_bus=mock_bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        snmp_value = _make_snmp_value("1", "Integer32", int_val=1)

        async def fake_get(*args, **kwargs):
            return (None, None, None, [(MagicMock(), snmp_value)])

        snmp_symbols["getCmd"] = fake_get

        source_binding = make_binding(
            {"host": "192.168.1.1", "oid": "1.3.6.1.2.1.1.1.0", "poll_interval": 999},
            direction="SOURCE",
        )
        dest_binding = make_binding(
            {"host": "192.168.1.2", "oid": "1.3.6.1.4.1.1.0", "poll_interval": 999},
            direction="DEST",
        )

        await adapter.reload_bindings([source_binding, dest_binding])
        await asyncio.sleep(0.05)

        assert len(adapter._poll_tasks) == 1  # nur SOURCE-Binding bekommt einen Task
        # Aufräumen
        for t in adapter._poll_tasks:
            t.cancel()

    @pytest.mark.asyncio
    async def test_cancels_old_tasks_on_reload(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(event_bus=mock_bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        snmp_value = _make_snmp_value("0", "Integer32", int_val=0)

        async def fake_get(*args, **kwargs):
            return (None, None, None, [(MagicMock(), snmp_value)])

        snmp_symbols["getCmd"] = fake_get

        binding = make_binding({"host": "192.168.1.1", "oid": "1.3.6.1.2.1.1.1.0", "poll_interval": 999})

        await adapter.reload_bindings([binding])
        old_task = adapter._poll_tasks[0]

        await adapter.reload_bindings([binding])  # zweites Reload
        await asyncio.sleep(0)  # let event loop process the cancellation
        assert old_task.cancelled()
        for t in adapter._poll_tasks:
            t.cancel()


# ---------------------------------------------------------------------------
# Read / Write
# ---------------------------------------------------------------------------


class TestRead:
    @pytest.mark.asyncio
    async def test_read_returns_coerced_value(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(event_bus=mock_bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        snmp_value = _make_snmp_value("55", "Integer32", int_val=55)

        async def fake_get(*args, **kwargs):
            return (None, None, None, [(MagicMock(), snmp_value)])

        snmp_symbols["getCmd"] = fake_get
        binding = make_binding({"host": "192.168.1.1", "oid": "1.3.6.1.2.1.1.1.0", "poll_interval": 30})
        result = await adapter.read(binding)
        assert result == 55

    @pytest.mark.asyncio
    async def test_read_returns_none_when_not_connected(self, mock_bus):
        adapter = SnmpAdapter(event_bus=mock_bus, config={})
        result = await adapter.read(make_binding({"host": "192.168.1.1", "oid": "1.2.3"}))
        assert result is None


class TestWrite:
    @pytest.mark.asyncio
    async def test_write_calls_set_cmd(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(event_bus=mock_bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        set_called_with: list = []

        async def fake_set(*args, **kwargs):
            set_called_with.append((args, kwargs))
            return (None, None, None, [])

        snmp_symbols["setCmd"] = fake_set
        binding = make_binding(
            {"host": "192.168.1.1", "oid": "1.3.6.1.4.1.1.0", "data_type": "int", "poll_interval": 30},
            direction="DEST",
        )
        await adapter.write(binding, 42)
        assert len(set_called_with) == 1

    @pytest.mark.asyncio
    async def test_write_skipped_when_not_connected(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(event_bus=mock_bus, config={})
        snmp_symbols["setCmd"] = AsyncMock()
        binding = make_binding({"host": "192.168.1.1", "oid": "1.3.6.1.4.1.1.0", "poll_interval": 30})
        await adapter.write(binding, 1)
        snmp_symbols["setCmd"].assert_not_called()


# ---------------------------------------------------------------------------
# SNMP Walk
# ---------------------------------------------------------------------------


class TestSnmpWalk:
    @pytest.mark.asyncio
    async def test_walk_returns_oid_value_type(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(event_bus=mock_bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        oid1 = MagicMock()
        oid1.__str__ = MagicMock(return_value="1.3.6.1.2.1.1.1.0")
        val1 = _make_snmp_value("Linux 5.15", "OctetString")
        type(val1).__name__ = "OctetString"

        oid2 = MagicMock()
        oid2.__str__ = MagicMock(return_value="1.3.6.1.2.1.1.2.0")
        val2 = _make_snmp_value("42", "Integer32", int_val=42)
        type(val2).__name__ = "Integer32"

        oid_out = MagicMock()
        oid_out.__str__ = MagicMock(return_value="1.3.6.1.3.1.1.0")  # outside subtree
        val_out = _make_snmp_value("x", "OctetString")
        type(val_out).__name__ = "OctetString"

        # nextCmd is a coroutine (one GETNEXT per call); walk loops until subtree ends
        responses = iter([
            (None, None, None, [(oid1, val1)]),
            (None, None, None, [(oid2, val2)]),
            (None, None, None, [(oid_out, val_out)]),
        ])

        async def fake_next(*args, **kwargs):
            return next(responses)

        snmp_symbols["nextCmd"] = fake_next

        results = await adapter.snmp_walk(host="192.168.1.1", oid="1.3.6.1.2.1")
        assert len(results) == 2
        assert results[0]["oid"] == "1.3.6.1.2.1.1.1.0"
        assert results[0]["value"] == "Linux 5.15"
        assert results[0]["type"] == "OctetString"
        assert results[1]["oid"] == "1.3.6.1.2.1.1.2.0"

    @pytest.mark.asyncio
    async def test_walk_stops_at_max_results(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(event_bus=mock_bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        counter = {"n": 0}

        async def fake_next_many(*args, **kwargs):
            i = counter["n"]
            counter["n"] += 1
            oid = MagicMock()
            oid.__str__ = MagicMock(return_value=f"1.3.6.1.2.1.1.{i}.0")
            val = _make_snmp_value(str(i), "Integer32", int_val=i)
            type(val).__name__ = "Integer32"
            return (None, None, None, [(oid, val)])

        snmp_symbols["nextCmd"] = fake_next_many

        results = await adapter.snmp_walk(host="192.168.1.1", oid="1.3.6.1.2.1", max_results=5)
        assert len(results) == 5

    @pytest.mark.asyncio
    async def test_walk_raises_when_not_connected(self, mock_bus):
        adapter = SnmpAdapter(event_bus=mock_bus, config={})
        with pytest.raises(RuntimeError, match="nicht verbunden"):
            await adapter.snmp_walk(host="192.168.1.1", oid="1.3.6.1.2.1")

    @pytest.mark.asyncio
    async def test_walk_stops_on_error_indication(self, mock_bus, snmp_symbols):
        adapter = SnmpAdapter(event_bus=mock_bus, config={"version": "2c", "community": "public"})
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        adapter._connected = True

        error_ind = MagicMock()
        error_ind.__str__ = MagicMock(return_value="noSuchName")

        async def fake_next_err(*args, **kwargs):
            return (error_ind, None, None, [])

        snmp_symbols["nextCmd"] = fake_next_err

        results = await adapter.snmp_walk(host="192.168.1.1", oid="1.3.6.1.2.1")
        assert results == []


# ---------------------------------------------------------------------------
# Auth helper: _build_auth
# ---------------------------------------------------------------------------


class TestBuildAuth:
    def _make_adapter(self, mock_bus, snmp_symbols, config):
        adapter = SnmpAdapter(event_bus=mock_bus, config=config)
        adapter._snmp = snmp_symbols
        adapter._engine = snmp_symbols["SnmpEngine"]()
        return adapter

    def test_v2c_uses_community_data(self, mock_bus, snmp_symbols):
        adapter = self._make_adapter(mock_bus, snmp_symbols, {"version": "2c", "community": "private"})
        cfg = SnmpAdapterConfig(**adapter._config)
        adapter._build_auth(cfg)
        snmp_symbols["CommunityData"].assert_called_with("private", mpModel=1)

    def test_v1_uses_mp_model_0(self, mock_bus, snmp_symbols):
        adapter = self._make_adapter(mock_bus, snmp_symbols, {"version": "1", "community": "public"})
        cfg = SnmpAdapterConfig(**adapter._config)
        adapter._build_auth(cfg)
        snmp_symbols["CommunityData"].assert_called_with("public", mpModel=0)

    def test_v3_no_auth_no_priv(self, mock_bus, snmp_symbols):
        adapter = self._make_adapter(
            mock_bus,
            snmp_symbols,
            {"version": "3", "security_name": "user", "security_level": "noAuthNoPriv"},
        )
        cfg = SnmpAdapterConfig(**adapter._config)
        adapter._build_auth(cfg)
        snmp_symbols["UsmUserData"].assert_called_with("user")

    def test_v3_auth_no_priv(self, mock_bus, snmp_symbols):
        adapter = self._make_adapter(
            mock_bus,
            snmp_symbols,
            {
                "version": "3",
                "security_name": "user",
                "security_level": "authNoPriv",
                "auth_protocol": "MD5",
                "auth_key": "secret123",
            },
        )
        cfg = SnmpAdapterConfig(**adapter._config)
        adapter._build_auth(cfg)
        call_kwargs = snmp_symbols["UsmUserData"].call_args[1]
        assert call_kwargs["authKey"] == "secret123"
        assert "privKey" not in call_kwargs

    def test_v3_auth_priv(self, mock_bus, snmp_symbols):
        adapter = self._make_adapter(
            mock_bus,
            snmp_symbols,
            {
                "version": "3",
                "security_name": "user",
                "security_level": "authPriv",
                "auth_protocol": "SHA",
                "auth_key": "authpass",
                "priv_protocol": "AES128",
                "priv_key": "privpass",
            },
        )
        cfg = SnmpAdapterConfig(**adapter._config)
        adapter._build_auth(cfg)
        call_kwargs = snmp_symbols["UsmUserData"].call_args[1]
        assert call_kwargs["authKey"] == "authpass"
        assert call_kwargs["privKey"] == "privpass"
