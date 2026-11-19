"""Modbus TCP over TLS (issue #1448).

Unit tests for the SSLContext builder, client selection and TLS error reporting,
plus end-to-end tests of the real pymodbus TLS client against a local pymodbus
TLS server (MBAP framing inside TLS) with certificates generated per test session.
No hardware and no Docker required.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime as dt
import ipaddress
import socket
import ssl
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

from obs.adapters.modbus_tcp.adapter import ModbusTcpAdapter, ModbusTcpAdapterConfig, build_tls_context
from tests.adapters.conftest import make_binding

_HOLDING = {"unit_id": 1, "register_type": "holding", "address": 0, "data_format": "uint16", "poll_interval": 0.05}
_KEY_PASSWORD = "s3cret-key-pass"

# ---------------------------------------------------------------------------
# Certificates
# ---------------------------------------------------------------------------


def _name(cn: str) -> x509.Name:
    return x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, cn)])


def _cert(subject_cn, key, issuer_cn, issuer_key, *, ca=False, sans=(), expired=False, client=False):
    now = dt.datetime.now(dt.UTC)
    not_before, not_after = (
        (now - dt.timedelta(days=30), now - dt.timedelta(days=1)) if expired else (now - dt.timedelta(days=1), now + dt.timedelta(days=30))
    )
    builder = (
        x509.CertificateBuilder()
        .subject_name(_name(subject_cn))
        .issuer_name(_name(issuer_cn))
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(not_before)
        .not_valid_after(not_after)
        .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
    )
    if ca:
        builder = builder.add_extension(
            x509.KeyUsage(False, False, False, False, False, True, True, False, False),
            critical=True,
        ).add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
    else:
        builder = builder.add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer_key.public_key()),
            critical=False,
        ).add_extension(
            x509.ExtendedKeyUsage([x509.ExtendedKeyUsageOID.CLIENT_AUTH if client else x509.ExtendedKeyUsageOID.SERVER_AUTH]),
            critical=False,
        )
    if sans:
        builder = builder.add_extension(x509.SubjectAlternativeName(list(sans)), critical=False)
    return builder.sign(issuer_key, hashes.SHA256())


def _write_pem(path: Path, cert=None, key=None, password: str | None = None) -> str:
    data = b""
    if cert is not None:
        data += cert.public_bytes(serialization.Encoding.PEM)
    if key is not None:
        enc = serialization.BestAvailableEncryption(password.encode()) if password else serialization.NoEncryption()
        data += key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, enc)
    path.write_bytes(data)
    return str(path)


@pytest.fixture(scope="module")
def pki(tmp_path_factory):
    d = tmp_path_factory.mktemp("modbus-pki")
    localhost = [x509.DNSName("localhost"), x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]

    ca_key = ec.generate_private_key(ec.SECP256R1())
    ca = _cert("OBS Test CA", ca_key, "OBS Test CA", ca_key, ca=True)
    other_key = ec.generate_private_key(ec.SECP256R1())
    other_ca = _cert("Unrelated CA", other_key, "Unrelated CA", other_key, ca=True)

    def leaf(name, *, sans=localhost, expired=False, client=False, issuer=(ca, ca_key)):
        key = ec.generate_private_key(ec.SECP256R1())
        cert = _cert(name, key, issuer[0].subject.rfc4514_string().split("=", 1)[1], issuer[1], sans=sans, expired=expired, client=client)
        return cert, key

    server, server_key = leaf("localhost")
    expired, expired_key = leaf("localhost", expired=True)
    wrong_host, wrong_host_key = leaf("device.example", sans=[x509.DNSName("device.example")])
    client, client_key = leaf("obs-client", sans=(), client=True)
    self_key = ec.generate_private_key(ec.SECP256R1())
    self_signed = _cert("localhost", self_key, "localhost", self_key, sans=localhost)

    return types.SimpleNamespace(
        dir=d,
        ca=_write_pem(d / "ca.pem", ca),
        other_ca=_write_pem(d / "other-ca.pem", other_ca),
        server=(_write_pem(d / "server.pem", server), _write_pem(d / "server.key", key=server_key)),
        expired=(_write_pem(d / "expired.pem", expired), _write_pem(d / "expired.key", key=expired_key)),
        wrong_host=(_write_pem(d / "wrong.pem", wrong_host), _write_pem(d / "wrong.key", key=wrong_host_key)),
        self_signed=(_write_pem(d / "self.pem", self_signed), _write_pem(d / "self.key", key=self_key)),
        client_cert=_write_pem(d / "client.pem", client),
        client_key=_write_pem(d / "client.key", key=client_key),
        client_key_encrypted=_write_pem(d / "client-enc.key", key=client_key, password=_KEY_PASSWORD),
        client_combined=_write_pem(d / "client-combined.pem", client, client_key),
    )


# ---------------------------------------------------------------------------
# build_tls_context
# ---------------------------------------------------------------------------


class TestBuildTlsContext:
    def test_verification_is_on_by_default(self):
        cfg = ModbusTcpAdapterConfig(tls=True)
        assert cfg.tls_verify is True

        ctx = build_tls_context(cfg)

        assert ctx.verify_mode == ssl.CERT_REQUIRED
        assert ctx.check_hostname is True

    def test_custom_ca_is_the_only_trust_anchor(self, pki):
        ctx = build_tls_context(ModbusTcpAdapterConfig(tls=True, tls_ca_file=pki.ca))

        subjects = [dict(x[0] for x in c["subject"])["commonName"] for c in ctx.get_ca_certs()]
        assert subjects == ["OBS Test CA"]

    def test_disabled_verification_checks_neither_chain_nor_hostname(self, pki):
        # A configured CA is ignored (not even read) once verification is off.
        ctx = build_tls_context(ModbusTcpAdapterConfig(tls=True, tls_verify=False, tls_ca_file=str(pki.dir / "missing.pem")))

        assert ctx.verify_mode == ssl.CERT_NONE
        assert ctx.check_hostname is False
        assert ctx.get_ca_certs() == []  # the system store is not even loaded

    def test_strict_x509_mode_is_relaxed_but_verification_stays(self):
        ctx = build_tls_context(ModbusTcpAdapterConfig(tls=True))

        assert not ctx.verify_flags & ssl.VERIFY_X509_STRICT
        assert ctx.verify_mode == ssl.CERT_REQUIRED

    def test_client_certificate_is_loaded_even_without_verification(self, pki):
        cfg = ModbusTcpAdapterConfig(tls=True, tls_verify=False, tls_client_cert_file=pki.client_cert, tls_client_key_file=pki.client_key)

        assert build_tls_context(cfg).verify_mode == ssl.CERT_NONE

    def test_combined_cert_and_key_file(self, pki):
        build_tls_context(ModbusTcpAdapterConfig(tls=True, tls_client_cert_file=pki.client_combined))

    def test_encrypted_key_with_password(self, pki):
        cfg = ModbusTcpAdapterConfig(
            tls=True,
            tls_client_cert_file=pki.client_cert,
            tls_client_key_file=pki.client_key_encrypted,
            tls_client_key_password=_KEY_PASSWORD,
        )
        build_tls_context(cfg)

    def test_wrong_key_password_fails_without_leaking_it(self, pki):
        cfg = ModbusTcpAdapterConfig(
            tls=True,
            tls_client_cert_file=pki.client_cert,
            tls_client_key_file=pki.client_key_encrypted,
            tls_client_key_password="wrong-password",
        )
        with pytest.raises(ssl.SSLError) as err:
            build_tls_context(cfg)
        assert "wrong-password" not in str(err.value)

    def test_encrypted_key_without_password_fails_instead_of_prompting(self, pki, monkeypatch):
        """password=None would make OpenSSL prompt on the TTY and block the event loop."""
        seen = {}
        original = ssl.SSLContext.load_cert_chain

        def spy(self, certfile, keyfile=None, password=None):
            seen["password"] = password
            return original(self, certfile, keyfile, password)

        monkeypatch.setattr(ssl.SSLContext, "load_cert_chain", spy)
        cfg = ModbusTcpAdapterConfig(tls=True, tls_client_cert_file=pki.client_cert, tls_client_key_file=pki.client_key_encrypted)

        with pytest.raises(ValueError, match="encrypted: set tls_client_key_password"):
            build_tls_context(cfg)
        assert callable(seen["password"])

    def test_key_without_certificate_is_rejected(self, pki):
        with pytest.raises(ValueError, match="requires tls_client_cert_file"):
            build_tls_context(ModbusTcpAdapterConfig(tls=True, tls_client_key_file=pki.client_key))

    def test_missing_ca_file_names_field_and_path(self, pki):
        missing = str(pki.dir / "missing.pem")
        with pytest.raises(OSError, match=f"tls_ca_file: file not found or not readable: {missing}"):
            build_tls_context(ModbusTcpAdapterConfig(tls=True, tls_ca_file=missing))

    def test_unreadable_client_key_names_field_and_path(self, pki, monkeypatch):
        monkeypatch.setattr("obs.adapters.modbus_tcp.adapter.os.access", lambda path, mode: path != pki.client_key)
        cfg = ModbusTcpAdapterConfig(tls=True, tls_client_cert_file=pki.client_cert, tls_client_key_file=pki.client_key)

        with pytest.raises(OSError, match="tls_client_key_file: file not found or not readable"):
            build_tls_context(cfg)


class TestConfigDefaults:
    def test_existing_configs_stay_plain_modbus_tcp(self):
        cfg = ModbusTcpAdapterConfig(host="10.0.0.1", port=502)

        assert cfg.tls is False
        assert cfg.tls_verify is True
        assert cfg.tls_ca_file is None
        assert cfg.tls_client_cert_file is None

    def test_key_password_is_a_password_field(self):
        schema = ModbusTcpAdapterConfig.model_json_schema()

        assert schema["properties"]["tls_client_key_password"]["format"] == "password"


# ---------------------------------------------------------------------------
# connect() with mocked pymodbus
# ---------------------------------------------------------------------------


def _fake_pymodbus(monkeypatch):
    client = MagicMock()
    client.connected = True
    client.connect = AsyncMock()
    client.ctx = None  # no transport to wrap
    mod = types.ModuleType("pymodbus.client")
    mod.AsyncModbusTcpClient = MagicMock(return_value=client)
    # The MBAP-over-TLS client class; exposed on the fake module for the assertions.
    mod.AsyncModbusTlsClient = MagicMock(return_value=client)
    monkeypatch.setitem(sys.modules, "pymodbus.client", mod)
    monkeypatch.setattr("obs.adapters.modbus_tcp.adapter._mbap_tls_client_class", lambda: mod.AsyncModbusTlsClient)
    return mod, client


def _adapter(config):
    adapter = ModbusTcpAdapter(MagicMock(publish=AsyncMock()), config)
    adapter._publish_status = AsyncMock()
    return adapter


class TestConnectClientSelection:
    async def test_plain_config_uses_the_tcp_client_without_sslctx(self, monkeypatch):
        mod, _ = _fake_pymodbus(monkeypatch)
        adapter = _adapter({"host": "plc", "port": 502})

        await adapter.connect()

        mod.AsyncModbusTlsClient.assert_not_called()
        assert "sslctx" not in mod.AsyncModbusTcpClient.call_args.kwargs

    async def test_tls_config_uses_the_tls_client_with_the_instance_context(self, monkeypatch):
        mod, _ = _fake_pymodbus(monkeypatch)
        adapter = _adapter({"host": "plc", "port": 802, "tls": True, "tls_verify": False})

        await adapter.connect()

        mod.AsyncModbusTcpClient.assert_not_called()
        kwargs = mod.AsyncModbusTlsClient.call_args.kwargs
        assert kwargs["host"] == "plc" and kwargs["port"] == 802
        assert kwargs["sslctx"] is adapter._sslctx
        assert kwargs["sslctx"].verify_mode == ssl.CERT_NONE
        adapter._publish_status.assert_awaited_with(True, "plc:802", code="connectedTo", params={"host": "plc", "port": 802})

    async def test_reload_reconnect_reuses_the_same_tls_settings(self, monkeypatch):
        mod, _ = _fake_pymodbus(monkeypatch)
        adapter = _adapter({"host": "plc", "port": 802, "tls": True, "tls_verify": False})
        await adapter.connect()
        adapter._initial_load_done = True

        await adapter._on_bindings_reloaded()

        assert mod.AsyncModbusTlsClient.call_count == 2
        first, second = mod.AsyncModbusTlsClient.call_args_list
        assert second.kwargs["sslctx"] is first.kwargs["sslctx"]

    async def test_invalid_tls_files_report_a_config_error_and_never_fall_back(self, monkeypatch, pki, caplog):
        mod, _ = _fake_pymodbus(monkeypatch)
        adapter = _adapter(
            {
                "host": "plc",
                "port": 802,
                "tls": True,
                "tls_client_cert_file": pki.client_cert,
                "tls_client_key_file": pki.client_key_encrypted,
                "tls_client_key_password": _KEY_PASSWORD + "-typo",
            },
        )

        await adapter.connect()

        mod.AsyncModbusTcpClient.assert_not_called()
        mod.AsyncModbusTlsClient.assert_not_called()
        assert adapter._client is None
        args, kwargs = adapter._publish_status.await_args
        assert args[0] is False
        assert kwargs["code"] == "modbusTlsConfigError"
        assert _KEY_PASSWORD not in caplog.text


# ---------------------------------------------------------------------------
# TLS error recording / status mapping
# ---------------------------------------------------------------------------


class TestTlsErrorReporting:
    def test_client_without_transport_is_left_alone(self):
        adapter = _adapter({"tls": True})
        client = MagicMock(spec=[])

        adapter._record_tls_errors(client)  # must not raise

    async def test_wrapper_records_ssl_errors_and_clears_them_on_the_next_attempt(self):
        adapter = _adapter({"tls": True})
        error = ssl.SSLError(1, "boom")
        create = AsyncMock(side_effect=[error, "transport"])
        client = types.SimpleNamespace(ctx=types.SimpleNamespace(call_create=create))
        adapter._record_tls_errors(client)

        with pytest.raises(ssl.SSLError):
            await client.ctx.call_create()
        assert adapter._last_tls_error is error

        assert await client.ctx.call_create() == "transport"
        assert adapter._last_tls_error is None

    async def test_wrapper_ignores_plain_socket_errors(self):
        adapter = _adapter({"tls": True})
        client = types.SimpleNamespace(ctx=types.SimpleNamespace(call_create=AsyncMock(side_effect=ConnectionRefusedError())))
        adapter._record_tls_errors(client)

        with pytest.raises(ConnectionRefusedError):
            await client.ctx.call_create()
        assert adapter._last_tls_error is None

    def test_failure_without_tls_error_keeps_the_generic_message(self):
        adapter = _adapter({"host": "plc", "port": 802})
        adapter._adp_cfg = ModbusTcpAdapterConfig(host="plc", port=802)

        assert adapter._connect_failure("connect", "Could not connect to", "couldNotConnectTo") == (
            "Could not connect to plc:802",
            "couldNotConnectTo",
            {"host": "plc", "port": 802},
        )

    def test_certificate_rejection_names_the_reason(self, caplog):
        adapter = _adapter({})
        adapter._adp_cfg = ModbusTcpAdapterConfig(host="plc", port=802)
        exc = ssl.SSLCertVerificationError(1, "certificate verify failed")
        exc.verify_message = "certificate has expired"
        adapter._last_tls_error = exc

        detail, code, params = adapter._connect_failure("reconnect", "Could not reconnect to", "couldNotReconnectTo")

        assert code == "modbusTlsCertificateRejected"
        assert params == {"host": "plc", "port": 802, "reason": "certificate has expired"}
        assert "certificate has expired" in detail
        assert "TLS reconnect to plc:802 failed: certificate has expired" in caplog.text

    def test_a_persistent_tls_failure_warns_once_per_reason(self, caplog):
        adapter = _adapter({})
        adapter._adp_cfg = ModbusTcpAdapterConfig(host="plc", port=802)
        expired = ssl.SSLCertVerificationError(1, "verify failed")
        expired.verify_message = "certificate has expired"
        unknown = ssl.SSLCertVerificationError(1, "verify failed")
        unknown.verify_message = "unable to get local issuer certificate"

        with caplog.at_level("DEBUG", logger="obs.adapters.modbus_tcp.adapter"):
            for exc in (expired, expired, unknown):
                adapter._last_tls_error = exc
                adapter._connect_failure("reconnect", "Could not reconnect to", "couldNotReconnectTo")

        levels = [(r.levelname, r.getMessage().rsplit(": ", 1)[1]) for r in caplog.records]
        assert levels == [
            ("WARNING", "certificate has expired"),
            ("DEBUG", "certificate has expired"),
            ("WARNING", "unable to get local issuer certificate"),
        ]

    async def test_a_successful_handshake_rearms_the_warning(self):
        adapter = _adapter({"tls": True})
        adapter._logged_tls_reason = "certificate has expired"
        client = types.SimpleNamespace(ctx=types.SimpleNamespace(call_create=AsyncMock(return_value="transport")))
        adapter._record_tls_errors(client)

        await client.ctx.call_create()

        assert adapter._logged_tls_reason is None

    def test_certificate_rejection_without_verify_message_uses_the_error_text(self):
        adapter = _adapter({})
        adapter._adp_cfg = ModbusTcpAdapterConfig(host="plc", port=802)
        exc = ssl.SSLCertVerificationError(1, "verify failed")
        exc.verify_message = None
        adapter._last_tls_error = exc

        _, code, params = adapter._connect_failure("connect", "Could not connect to", "couldNotConnectTo")

        assert code == "modbusTlsCertificateRejected"
        assert params["reason"] == str(exc)

    def test_handshake_failure_names_the_reason(self):
        adapter = _adapter({})
        adapter._adp_cfg = ModbusTcpAdapterConfig(host="plc", port=802)
        exc = ssl.SSLError(1, "alert")
        exc.reason = "TLSV1_ALERT_UNKNOWN_CA"
        adapter._last_tls_error = exc

        _, code, params = adapter._connect_failure("connect", "Could not connect to", "couldNotConnectTo")

        assert code == "modbusTlsHandshakeFailed"
        assert params["reason"] == "TLSV1_ALERT_UNKNOWN_CA"

    def test_handshake_failure_without_reason_uses_the_error_text(self):
        adapter = _adapter({})
        adapter._adp_cfg = ModbusTcpAdapterConfig(host="plc", port=802)
        exc = ssl.SSLError(1, "alert")
        adapter._last_tls_error = exc

        _, _, params = adapter._connect_failure("connect", "Could not connect to", "couldNotConnectTo")

        assert params["reason"] == str(exc)


# ---------------------------------------------------------------------------
# End to end: real pymodbus TLS client ↔ pymodbus TLS server
# ---------------------------------------------------------------------------


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _server_context(cert_and_key, *, client_ca: str | None = None) -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(*cert_and_key)
    if client_ca:
        ctx.load_verify_locations(client_ca)
        ctx.verify_mode = ssl.CERT_REQUIRED
    return ctx


@contextlib.asynccontextmanager
async def _tls_server(cert_and_key, *, client_ca: str | None = None):
    from pymodbus.datastore import ModbusDeviceContext, ModbusSequentialDataBlock, ModbusServerContext
    from pymodbus.server import ModbusTlsServer

    store = ModbusDeviceContext(hr=ModbusSequentialDataBlock(1, [1234] * 10))
    port = _free_port()
    server = ModbusTlsServer(
        ModbusServerContext(devices=store, single=True),
        address=("127.0.0.1", port),
        sslctx=_server_context(cert_and_key, client_ca=client_ca),
    )
    await server.serve_forever(background=True)
    try:
        yield port, store
    finally:
        await server.shutdown()


async def _connect(port, *, host="127.0.0.1", **tls):
    adapter = _adapter({"host": host, "port": port, "timeout": 2.0, "tls": True, **tls})
    await adapter.connect()
    return adapter


def _last_status(adapter):
    args, kwargs = adapter._publish_status.await_args
    return args[0], kwargs.get("code"), kwargs.get("params")


class TestRealTlsServer:
    async def test_read_and_write_with_a_trusted_certificate(self, pki):
        async with _tls_server(pki.server) as (port, _):
            adapter = await _connect(port, tls_ca_file=pki.ca)
            try:
                assert _last_status(adapter)[:2] == (True, "connectedTo")
                binding = make_binding(_HOLDING)

                assert await adapter.read(binding) == 1234
                await adapter.write(binding, 4321)
                assert await adapter.read(binding) == 4321
            finally:
                await adapter.disconnect()

    async def test_hostname_is_verified_against_the_certificate(self, pki):
        async with _tls_server(pki.server) as (port, _):
            adapter = await _connect(port, host="localhost", tls_ca_file=pki.ca)
            try:
                assert _last_status(adapter)[:2] == (True, "connectedTo")
            finally:
                await adapter.disconnect()

    @pytest.mark.parametrize(
        ("cert", "ca", "reason"),
        [
            ("server", "other_ca", "unable to get local issuer certificate"),
            ("expired", "ca", "certificate has expired"),
            ("wrong_host", "ca", "IP address mismatch"),
            ("self_signed", None, "self-signed certificate"),
        ],
    )
    async def test_untrusted_certificates_are_rejected_with_a_reason(self, pki, cert, ca, reason):
        async with _tls_server(getattr(pki, cert)) as (port, _):
            adapter = await _connect(port, tls_ca_file=getattr(pki, ca) if ca else None)
            try:
                connected, code, params = _last_status(adapter)
                assert connected is False
                assert code == "modbusTlsCertificateRejected"
                assert reason in params["reason"]
                assert await adapter.read(make_binding(_HOLDING)) is None
            finally:
                await adapter.disconnect()

    @pytest.mark.parametrize("cert", ["self_signed", "expired", "wrong_host"])
    async def test_disabled_verification_accepts_any_certificate(self, pki, cert):
        async with _tls_server(getattr(pki, cert)) as (port, _):
            adapter = await _connect(port, tls_verify=False)
            try:
                assert _last_status(adapter)[:2] == (True, "connectedTo")
                assert await adapter.read(make_binding(_HOLDING)) == 1234
            finally:
                await adapter.disconnect()

    async def test_mutual_tls_with_a_client_certificate(self, pki):
        async with _tls_server(pki.server, client_ca=pki.ca) as (port, _):
            adapter = await _connect(
                port,
                tls_ca_file=pki.ca,
                tls_client_cert_file=pki.client_cert,
                tls_client_key_file=pki.client_key_encrypted,
                tls_client_key_password=_KEY_PASSWORD,
            )
            try:
                assert await adapter.read(make_binding(_HOLDING)) == 1234
            finally:
                await adapter.disconnect()

    async def test_mutual_tls_with_disabled_server_verification(self, pki):
        async with _tls_server(pki.self_signed, client_ca=pki.ca) as (port, _):
            adapter = await _connect(port, tls_verify=False, tls_client_cert_file=pki.client_combined)
            try:
                assert await adapter.read(make_binding(_HOLDING)) == 1234
            finally:
                await adapter.disconnect()

    async def test_plain_modbus_tcp_against_a_tls_port_does_not_connect_silently(self, pki):
        async with _tls_server(pki.server) as (port, _):
            adapter = _adapter({"host": "127.0.0.1", "port": port, "timeout": 1.0})
            await adapter.connect()
            try:
                assert await adapter.read(make_binding(_HOLDING)) is None
            finally:
                await adapter.disconnect()

    async def test_poll_loop_reconnects_over_tls_after_the_connection_dropped(self, pki):
        async with _tls_server(pki.server) as (port, _):
            adapter = await _connect(port, tls_ca_file=pki.ca)
            first_client = adapter._client
            first_client.close()  # simulate a dropped session
            assert not first_client.connected
            binding = make_binding(_HOLDING)
            adapter._bindings = [binding]
            poll = asyncio.create_task(adapter._poll_loop(binding, apply_jitter=False))
            try:
                for _ in range(60):
                    await asyncio.sleep(0.05)
                    if any(e.args[0].value == 1234 for e in adapter._bus.publish.await_args_list):
                        break
                assert adapter._client is first_client
                assert adapter._client.ctx.comm_params.sslctx is adapter._sslctx
                assert any(e.args[0].value == 1234 for e in adapter._bus.publish.await_args_list)
            finally:
                poll.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poll
                await adapter.disconnect()


# ---------------------------------------------------------------------------
# Connection-test API: the TLS cause replaces the generic connectFailed
# ---------------------------------------------------------------------------


class TestConnectFailedResult:
    def test_tls_status_codes_are_passed_through(self):
        from obs.api.v1.adapters import _connect_failed_result

        instance = _adapter({})
        instance._last_detail = "TLS certificate of plc:802 rejected: certificate has expired"
        instance._last_detail_code = "modbusTlsCertificateRejected"
        instance._last_detail_params = {"host": "plc", "port": 802, "reason": "certificate has expired"}

        result = _connect_failed_result(instance)

        assert result.success is False
        assert result.detail_code == "modbusTlsCertificateRejected"
        assert result.detail_params["reason"] == "certificate has expired"
        assert result.detail == instance.last_detail

    def test_other_status_codes_keep_the_generic_result(self):
        from obs.api.v1.adapters import _connect_failed_result

        instance = _adapter({})
        instance._last_detail_code = "couldNotConnectTo"

        result = _connect_failed_result(instance)

        assert result.detail_code == "connectFailed"

    def test_adapters_without_declared_codes_keep_the_generic_result(self):
        from obs.api.v1.adapters import _connect_failed_result

        instance = types.SimpleNamespace(last_detail="x", last_detail_code="modbusTlsCertificateRejected", last_detail_params={})

        assert _connect_failed_result(instance).detail_code == "connectFailed"


# ---------------------------------------------------------------------------
# Reconnect failures: a new TLS cause replaces an older status
# ---------------------------------------------------------------------------


class TestPublishReconnectFailure:
    def _disconnected(self, code, params):
        adapter = ModbusTcpAdapter(MagicMock(publish=AsyncMock()), {})
        adapter._connected = False
        adapter._last_detail_code = code
        adapter._last_detail_params = params
        adapter._publish_status = AsyncMock()
        return adapter

    async def test_new_tls_cause_is_published_while_disconnected(self):
        adapter = self._disconnected("couldNotConnectTo", {"host": "plc", "port": 802})
        params = {"host": "plc", "port": 802, "reason": "certificate has expired"}

        await adapter._publish_reconnect_failure("TLS ... rejected", "modbusTlsCertificateRejected", params)

        adapter._publish_status.assert_awaited_once_with(False, "TLS ... rejected", code="modbusTlsCertificateRejected", params=params)

    async def test_unchanged_tls_cause_is_not_republished(self):
        params = {"host": "plc", "port": 802, "reason": "certificate has expired"}
        adapter = self._disconnected("modbusTlsCertificateRejected", params)

        await adapter._publish_reconnect_failure("TLS ... rejected", "modbusTlsCertificateRejected", dict(params))

        adapter._publish_status.assert_not_awaited()

    async def test_generic_failure_keeps_the_disconnected_only_rule(self):
        adapter = self._disconnected("modbusReconnectBackoff", {})

        await adapter._publish_reconnect_failure("Could not reconnect to plc:502", "couldNotReconnectTo", {"host": "plc", "port": 502})

        adapter._publish_status.assert_not_awaited()


class TestMbapFramingOverTls:
    async def test_response_split_across_tls_records_is_reassembled(self, pki):
        """stunnel or slow links may split one MBAP frame into several TLS records."""

        async def handler(reader, writer):
            while request := await reader.read(12):
                response = request[0:2] + bytes.fromhex("0000 0005 01 0302 04d2")
                writer.write(response[:5])
                await writer.drain()
                await asyncio.sleep(0.05)
                writer.write(response[5:])
                await writer.drain()

        server = await asyncio.start_server(handler, "127.0.0.1", 0, ssl=_server_context(pki.server))
        adapter = await _connect(server.sockets[0].getsockname()[1], tls_ca_file=pki.ca)
        try:
            assert await adapter.read(make_binding(_HOLDING)) == 1234
        finally:
            await adapter.disconnect()
            server.close()

    async def test_certificate_without_authority_key_identifier_is_accepted(self, pki, tmp_path):
        """Many device CAs lack the AKI that Python's strict X.509 mode demands."""
        ca_key = ec.generate_private_key(ec.SECP256R1())
        ca = (
            x509.CertificateBuilder()
            .subject_name(_name("Device CA"))
            .issuer_name(_name("Device CA"))
            .public_key(ca_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
            .not_valid_after(dt.datetime.now(dt.UTC) + dt.timedelta(days=30))
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(ca_key, hashes.SHA256())
        )
        leaf_key = ec.generate_private_key(ec.SECP256R1())
        leaf = (
            x509.CertificateBuilder()
            .subject_name(_name("localhost"))
            .issuer_name(_name("Device CA"))
            .public_key(leaf_key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(dt.datetime.now(dt.UTC) - dt.timedelta(days=1))
            .not_valid_after(dt.datetime.now(dt.UTC) + dt.timedelta(days=30))
            .add_extension(x509.SubjectAlternativeName([x509.IPAddress(ipaddress.ip_address("127.0.0.1"))]), critical=False)
            .sign(ca_key, hashes.SHA256())
        )
        ca_file = _write_pem(tmp_path / "device-ca.pem", ca)
        server_files = (_write_pem(tmp_path / "leaf.pem", leaf), _write_pem(tmp_path / "leaf.key", key=leaf_key))

        async with _tls_server(server_files) as (port, _):
            adapter = await _connect(port, tls_ca_file=ca_file)
            try:
                assert await adapter.read(make_binding(_HOLDING)) == 1234
            finally:
                await adapter.disconnect()
