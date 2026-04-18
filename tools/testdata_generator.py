#!/usr/bin/env python3
"""
open bridge server Test Data Generator — Issue #110

Generates configurable test traffic for KNX, Modbus TCP, and MQTT adapters.
Each protocol runs as an independent async task; all three can run in parallel.

Usage:
    python tools/testdata_generator.py [config.yaml]

If no config file is given, tools/testdata_generator_example.yaml is used.

Value generation modes (per signal):
    fixed      — constant value (requires: value)
    sine       — sine wave between min/max (optional: period [s], default 60)
    random     — uniform random between min/max
    ramp       — linear ramp from min to max, then wraps (optional: period [s])
    sequence   — cycles through a list (requires: values: [...])
    toggle     — alternates True/False

KNX mode:
    Acts as a KNX/IP tunneling SERVER (gateway simulator).
    open bridge server KNX adapter connects to this server with connection_type: tunneling.
    No physical KNX bus or external gateway required.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import struct
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("testdata")


# ---------------------------------------------------------------------------
# Value generation
# ---------------------------------------------------------------------------

class ValueGenerator:
    """Stateful value generator; call .next() to get the next value."""

    def __init__(self, cfg: dict) -> None:
        self.mode: str = cfg.get("mode", "fixed")
        self.min_val: float = float(cfg.get("min", 0))
        self.max_val: float = float(cfg.get("max", 1))
        self.fixed_val: Any = cfg.get("value", self.min_val)
        self.period: float = float(cfg.get("period", 60.0))
        self.seq_values: list = cfg.get("values", [])
        self._seq_idx: int = 0
        self._toggle: bool = False
        self._t0: float = time.monotonic()

    def next(self) -> Any:
        elapsed = time.monotonic() - self._t0

        if self.mode == "fixed":
            return self.fixed_val
        if self.mode == "sine":
            ratio = (math.sin(2 * math.pi * elapsed / self.period) + 1) / 2
            return self.min_val + ratio * (self.max_val - self.min_val)
        if self.mode == "random":
            return random.uniform(self.min_val, self.max_val)
        if self.mode == "ramp":
            ratio = (elapsed % self.period) / self.period
            return self.min_val + ratio * (self.max_val - self.min_val)
        if self.mode == "sequence":
            if not self.seq_values:
                return self.min_val
            val = self.seq_values[self._seq_idx % len(self.seq_values)]
            self._seq_idx += 1
            return val
        if self.mode == "toggle":
            self._toggle = not self._toggle
            return self._toggle
        return self.fixed_val


# ---------------------------------------------------------------------------
# KNX/IP Tunneling Server (gateway simulator)
#
# Implements the minimal KNXnet/IP Core (0205–020A) and Tunneling (0420/0421)
# service types needed to accept incoming tunneling connections and push
# GroupValueWrite telegrams to all connected clients (open bridge server instances).
#
# References: KNX Standard vol. 3 part 8 (KNXnet/IP)
# ---------------------------------------------------------------------------

# Service type identifiers
_SVC_CONNECT_REQUEST     = 0x0205
_SVC_CONNECT_RESPONSE    = 0x0206
_SVC_CONNSTATE_REQUEST   = 0x0207
_SVC_CONNSTATE_RESPONSE  = 0x0208
_SVC_DISCONNECT_REQUEST  = 0x0209
_SVC_DISCONNECT_RESPONSE = 0x020A
_SVC_TUNNELING_REQUEST   = 0x0420
_SVC_TUNNELING_ACK       = 0x0421

# Error codes
_E_NO_ERROR            = 0x00
_E_CONNECTION_ID       = 0x21
_E_NO_MORE_CONNECTIONS = 0x24

# Individual address used as telegram source (15.15.255 — typical tool address)
_SERVER_IA = 0xFF00


def _knxip_frame(svc_type: int, body: bytes) -> bytes:
    """Wrap body in a KNXnet/IP header."""
    return struct.pack(">BBHH", 0x06, 0x10, svc_type, 6 + len(body)) + body


def _hpai_ipv4(ip: str, port: int) -> bytes:
    """Build a Host Protocol Address Information block (IPv4/UDP)."""
    parts = [int(x) for x in ip.split(".")]
    return struct.pack(">BB4BH", 0x08, 0x01, *parts, port)


def _ga_to_int(ga: str) -> int:
    """Convert group address string "M/S/D" to 16-bit integer."""
    main, sub, device = ga.split("/")
    return (int(main) << 11) | (int(sub) << 8) | int(device)


def _build_cemi(src_ia: int, ga: int, raw: bytes, is_boolean: bool) -> bytes:
    """
    Build a cEMI L_DATA.ind frame for a GroupValueWrite.

    Short format (is_boolean=True, 1-bit value):
        NPDU_LEN=1, APDU=[0x00, 0x80 | value]

    Standard format (multi-byte payload):
        NPDU_LEN=1+len(raw), APDU=[0x00, 0x80] + raw
    """
    mc    = 0x29   # L_DATA.ind
    ctrl1 = 0xBC   # standard frame, broadcast, normal priority, no repeat
    ctrl2 = 0xE0   # group address, hop count 6

    src_h, src_l = (src_ia >> 8) & 0xFF, src_ia & 0xFF
    dst_h, dst_l = (ga >> 8) & 0xFF, ga & 0xFF

    if is_boolean:
        npdu_len = 1
        apdu = bytes([0x00, 0x80 | (raw[0] & 0x3F)])
    else:
        npdu_len = 1 + len(raw)
        apdu = bytes([0x00, 0x80]) + raw

    return bytes([mc, 0x00, ctrl1, ctrl2, src_h, src_l, dst_h, dst_l, npdu_len]) + apdu


@dataclass
class _TunnelClient:
    channel_id: int
    data_addr: tuple[str, int]   # where to send TUNNELING_REQUEST
    seq_send: int = 0


class _KnxServerProtocol(asyncio.DatagramProtocol):
    """asyncio UDP protocol that delegates incoming datagrams to KnxTunnelingServer."""

    def __init__(self, server: KnxTunnelingServer) -> None:
        self._server = server

    def datagram_received(self, data: bytes, addr: tuple) -> None:
        self._server._on_datagram(data, (addr[0], addr[1]))

    def error_received(self, exc: Exception) -> None:
        logger.warning("KNX/IP UDP error: %s", exc)

    def connection_lost(self, exc: Exception | None) -> None:
        if exc:
            logger.warning("KNX/IP transport lost: %s", exc)


class KnxTunnelingServer:
    """
    Minimal KNX/IP tunneling server (gateway simulator).

    Accepts up to 4 simultaneous tunneling connections.
    Pushes GroupValueWrite telegrams to every connected client via
    send_telegram(). Responds to connection-state heartbeats and
    acknowledges any TUNNELING_REQUEST sent by clients (write from open bridge server).
    """

    MAX_CONNECTIONS = 4

    def __init__(self, host: str, port: int) -> None:
        self._host = host
        self._port = port
        self._transport: asyncio.DatagramTransport | None = None
        self._clients: dict[int, _TunnelClient] = {}
        self._next_channel: int = 1

    @property
    def client_count(self) -> int:
        return len(self._clients)

    async def start(self) -> None:
        loop = asyncio.get_running_loop()
        self._transport, _ = await loop.create_datagram_endpoint(
            lambda: _KnxServerProtocol(self),
            local_addr=(self._host, self._port),
        )
        logger.info("KNX/IP tunneling server listening on %s:%d", self._host, self._port)

    def stop(self) -> None:
        if self._transport:
            self._transport.close()
            self._transport = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_telegram(self, ga_str: str, raw: bytes, is_boolean: bool) -> None:
        """Send a GroupValueWrite telegram to every connected tunneling client."""
        if not self._clients or not self._transport:
            return
        ga_int = _ga_to_int(ga_str)
        cemi = _build_cemi(_SERVER_IA, ga_int, raw, is_boolean)
        for client in list(self._clients.values()):
            body = struct.pack(
                ">BBBB", 0x04, client.channel_id, client.seq_send & 0xFF, 0x00
            ) + cemi
            frame = _knxip_frame(_SVC_TUNNELING_REQUEST, body)
            try:
                self._transport.sendto(frame, client.data_addr)
                client.seq_send = (client.seq_send + 1) & 0xFF
            except Exception:
                logger.warning("KNX: sendto %s failed", client.data_addr)

    # ------------------------------------------------------------------
    # Incoming datagram dispatcher
    # ------------------------------------------------------------------

    def _on_datagram(self, data: bytes, addr: tuple[str, int]) -> None:
        if len(data) < 6:
            return
        hdr_len, version, svc_type, total_len = struct.unpack_from(">BBHH", data, 0)
        if hdr_len != 6 or version != 0x10:
            return
        body = data[6:]

        if svc_type == _SVC_CONNECT_REQUEST:
            self._handle_connect(body, addr)
        elif svc_type == _SVC_CONNSTATE_REQUEST:
            self._handle_connstate(body, addr)
        elif svc_type == _SVC_DISCONNECT_REQUEST:
            self._handle_disconnect(body, addr)
        elif svc_type == _SVC_TUNNELING_REQUEST:
            self._handle_tunneling_request(body, addr)
        elif svc_type == _SVC_TUNNELING_ACK:
            pass  # We don't retry, so ACKs can be ignored

    # ------------------------------------------------------------------
    # CONNECT_REQUEST → CONNECT_RESPONSE
    # ------------------------------------------------------------------

    def _handle_connect(self, body: bytes, addr: tuple[str, int]) -> None:
        if len(self._clients) >= self.MAX_CONNECTIONS:
            resp = _knxip_frame(
                _SVC_CONNECT_RESPONSE,
                bytes([0x00, _E_NO_MORE_CONNECTIONS]) + _hpai_ipv4("0.0.0.0", 0) + bytes(4),
            )
            if self._transport:
                self._transport.sendto(resp, addr)
            logger.warning("KNX: connection refused — max %d clients reached", self.MAX_CONNECTIONS)
            return

        # Parse data endpoint HPAI from CONNECT_REQUEST body (offset 8..15)
        data_addr = addr   # default: use UDP source
        if len(body) >= 16:
            hpai = body[8:16]
            if hpai[0] == 0x08 and hpai[1] == 0x01:
                hpai_port = struct.unpack(">H", hpai[6:8])[0]
                hpai_ip = f"{hpai[2]}.{hpai[3]}.{hpai[4]}.{hpai[5]}"
                # Use source IP (handles NAT / 0.0.0.0 clients) but HPAI port if valid
                ip = addr[0] if hpai_ip in ("0.0.0.0", "127.0.0.1") else hpai_ip
                port = hpai_port if hpai_port != 0 else addr[1]
                data_addr = (ip, port)

        channel_id = self._next_channel
        self._next_channel = max(1, (self._next_channel % 255) + 1)
        self._clients[channel_id] = _TunnelClient(channel_id=channel_id, data_addr=data_addr)

        # CRD: 04 04 [IA_H] [IA_L] — individual address assigned to client
        ia = _SERVER_IA + channel_id
        crd = bytes([0x04, 0x04, (ia >> 8) & 0xFF, ia & 0xFF])

        # Use bind IP or loopback in the HPAI
        bind_ip = self._host if self._host not in ("0.0.0.0", "") else "127.0.0.1"
        resp_body = bytes([channel_id, _E_NO_ERROR]) + _hpai_ipv4(bind_ip, self._port) + crd
        resp = _knxip_frame(_SVC_CONNECT_RESPONSE, resp_body)
        if self._transport:
            self._transport.sendto(resp, addr)
        logger.info(
            "KNX: client connected from %s → data=%s  channel=%d  (total=%d)",
            addr, data_addr, channel_id, len(self._clients),
        )

    # ------------------------------------------------------------------
    # CONNECTIONSTATE_REQUEST → CONNECTIONSTATE_RESPONSE (heartbeat)
    # ------------------------------------------------------------------

    def _handle_connstate(self, body: bytes, addr: tuple[str, int]) -> None:
        if len(body) < 1:
            return
        channel_id = body[0]
        status = _E_NO_ERROR if channel_id in self._clients else _E_CONNECTION_ID
        resp = _knxip_frame(_SVC_CONNSTATE_RESPONSE, bytes([channel_id, status]))
        if self._transport:
            self._transport.sendto(resp, addr)

    # ------------------------------------------------------------------
    # DISCONNECT_REQUEST → DISCONNECT_RESPONSE
    # ------------------------------------------------------------------

    def _handle_disconnect(self, body: bytes, addr: tuple[str, int]) -> None:
        if len(body) < 1:
            return
        channel_id = body[0]
        self._clients.pop(channel_id, None)
        resp = _knxip_frame(_SVC_DISCONNECT_RESPONSE, bytes([channel_id, _E_NO_ERROR]))
        if self._transport:
            self._transport.sendto(resp, addr)
        logger.info(
            "KNX: client disconnected channel=%d  (remaining=%d)",
            channel_id, len(self._clients),
        )

    # ------------------------------------------------------------------
    # TUNNELING_REQUEST from client (open bridge server writing to KNX GA)
    # ------------------------------------------------------------------

    def _handle_tunneling_request(self, body: bytes, addr: tuple[str, int]) -> None:
        """ACK every incoming tunneling request (write from open bridge server)."""
        if len(body) < 4:
            return
        channel_id = body[1]
        seq_counter = body[2]
        ack_body = bytes([0x04, channel_id, seq_counter, _E_NO_ERROR])
        ack = _knxip_frame(_SVC_TUNNELING_ACK, ack_body)
        if self._transport:
            self._transport.sendto(ack, addr)


# ---------------------------------------------------------------------------
# Rate limiter — token-bucket style, max N calls per second
# ---------------------------------------------------------------------------

class _RateLimiter:
    """Serialises async callers to at most `rate` calls per second."""

    def __init__(self, rate: float) -> None:
        self._min_interval = 1.0 / rate
        self._last: float = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            now = time.monotonic()
            wait = self._min_interval - (now - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


# ---------------------------------------------------------------------------
# KNX generator — starts the tunneling server and sends configured telegrams
# ---------------------------------------------------------------------------

async def knx_generator(cfg: dict) -> None:
    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    try:
        from obs.adapters.knx.dpt_registry import DPTRegistry
    except ImportError:
        logger.error("Cannot import DPTRegistry — run this script from the project root")
        return

    host = cfg.get("host", "0.0.0.0")
    port = int(cfg.get("port", 3671))

    server = KnxTunnelingServer(host, port)
    try:
        await server.start()
    except Exception:
        logger.exception("KNX/IP server failed to start")
        return

    max_rate = float(cfg.get("max_events_per_second", 3.0))
    limiter = _RateLimiter(max_rate)

    async def send_loop(tel_cfg: dict) -> None:
        ga_str = tel_cfg["group_address"]
        dpt_id = tel_cfg.get("dpt_id", "DPT9.001")
        interval = float(tel_cfg.get("interval", 5.0))
        dpt = DPTRegistry.get(dpt_id)
        is_boolean = dpt.data_type == "BOOLEAN"
        gen = ValueGenerator(tel_cfg)

        while True:
            await asyncio.sleep(interval)
            value = gen.next()
            try:
                raw = dpt.encoder(value)
                await limiter.acquire()
                server.send_telegram(ga_str, raw, is_boolean)
                logger.info(
                    "KNX  GA=%-10s DPT=%-12s value=%-10s  clients=%d",
                    ga_str, dpt_id, value, server.client_count,
                )
            except Exception:
                logger.exception("KNX send failed GA=%s", ga_str)

    tasks = [
        asyncio.create_task(send_loop(tc), name=f"knx-{tc['group_address']}")
        for tc in cfg.get("telegrams", [])
    ]
    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        server.stop()
        raise


# ---------------------------------------------------------------------------
# Modbus TCP generator (acts as a server/slave — open bridge server polls it)
# ---------------------------------------------------------------------------

async def modbus_generator(cfg: dict) -> None:
    try:
        from pymodbus.server import ModbusTcpServer
        from pymodbus.simulator.simdata import DataType, SimData
        from pymodbus.simulator.simdevice import SimDevice
    except ImportError:
        logger.error("pymodbus not installed — Modbus generator disabled")
        return

    _root = Path(__file__).resolve().parent.parent
    if str(_root) not in sys.path:
        sys.path.insert(0, str(_root))

    try:
        from obs.adapters.modbus_base import encode_value
    except ImportError:
        logger.warning("Cannot import encode_value — using basic uint16 encoding")

        def encode_value(  # type: ignore[misc]
            value: Any,
            data_format: str = "uint16",
            byte_order: str = "big",
            word_order: str = "big",
            scale_factor: float = 1.0,
        ) -> list[int]:
            return [int(float(value) / scale_factor) & 0xFFFF]

    host = cfg.get("host", "0.0.0.0")
    port = int(cfg.get("port", 502))
    unit_id = int(cfg.get("unit_id", 1))

    _SIZE = 1000
    co_values: list[int] = [0] * _SIZE
    di_values: list[int] = [0] * _SIZE
    hr_values: list[int] = [0] * _SIZE
    ir_values: list[int] = [0] * _SIZE

    # tuple: (coils, discrete_inputs, holding_registers, input_registers)
    device = SimDevice(
        id=unit_id,
        simdata=(
            [SimData(address=0, values=co_values, datatype=DataType.BITS)],
            [SimData(address=0, values=di_values, datatype=DataType.BITS)],
            [SimData(address=0, values=hr_values, datatype=DataType.REGISTERS)],
            [SimData(address=0, values=ir_values, datatype=DataType.REGISTERS)],
        ),
    )

    _FC = {"coil": 1, "discrete_input": 2, "holding": 3, "input": 4}
    _BUFFERS = {1: co_values, 2: di_values, 3: hr_values, 4: ir_values}

    async def update_loop() -> None:
        async def update_one(reg_cfg: dict) -> None:
            interval = float(reg_cfg.get("interval", 1.0))
            reg_type = reg_cfg.get("register_type", "holding")
            address = int(reg_cfg.get("address", 0))
            data_format = reg_cfg.get("data_format", "uint16")
            scale_factor = float(reg_cfg.get("scale_factor", 1.0))
            gen = ValueGenerator(reg_cfg)
            fc = _FC.get(reg_type, 3)
            buf = _BUFFERS[fc]

            while True:
                value = gen.next()
                try:
                    if reg_type in ("coil", "discrete_input"):
                        buf[address] = int(bool(value))
                        logger.info("Modbus %-16s[%d] = %s", reg_type, address, bool(value))
                    else:
                        regs = encode_value(value, data_format, scale_factor=scale_factor)
                        for i, v in enumerate(regs):
                            if address + i < len(buf):
                                buf[address + i] = v
                        logger.info("Modbus %-16s[%d] = %s  raw=%s", reg_type, address, value, regs)
                except Exception:
                    logger.exception("Modbus update failed register=%d", address)
                await asyncio.sleep(interval)

        tasks = [
            asyncio.create_task(update_one(rc), name=f"modbus-reg-{rc.get('address', 0)}")
            for rc in cfg.get("registers", [])
        ]
        try:
            await asyncio.gather(*tasks)
        except asyncio.CancelledError:
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise

    logger.info("Modbus TCP server starting on %s:%d (unit_id=%d)", host, port, unit_id)
    server = ModbusTcpServer(device, address=(host, port))
    server_task = asyncio.create_task(server.serve_forever(), name="modbus-server")
    update_task = asyncio.create_task(update_loop(), name="modbus-updater")

    try:
        await asyncio.gather(server_task, update_task)
    except asyncio.CancelledError:
        server_task.cancel()
        update_task.cancel()
        await asyncio.gather(server_task, update_task, return_exceptions=True)
        raise


# ---------------------------------------------------------------------------
# MQTT generator (publisher — open bridge server subscribes to these topics)
# ---------------------------------------------------------------------------

async def mqtt_generator(cfg: dict) -> None:
    try:
        import aiomqtt
    except ImportError:
        logger.error("aiomqtt not installed — MQTT generator disabled")
        return

    host = cfg.get("host", "localhost")
    port = int(cfg.get("port", 1883))
    username = cfg.get("username") or None
    password = cfg.get("password") or None

    topic_cfgs = cfg.get("topics", [])
    generators = [(tc, ValueGenerator(tc)) for tc in topic_cfgs]

    async def publish_loop(topic_cfg: dict, gen: ValueGenerator, client: Any) -> None:
        topic = topic_cfg["topic"]
        interval = float(topic_cfg.get("interval", 5.0))
        retain = bool(topic_cfg.get("retain", False))
        payload_template: str | None = topic_cfg.get("payload_template")

        while True:
            value = gen.next()
            try:
                if payload_template:
                    val_str = value if isinstance(value, str) else json.dumps(value)
                    payload = payload_template.replace("###DP###", val_str)
                else:
                    payload = value if isinstance(value, str) else json.dumps(value)
                await client.publish(topic, payload, retain=retain)
                logger.info("MQTT  %-30s = %s", topic, value)
            except Exception:
                logger.exception("MQTT publish failed topic=%s", topic)
            await asyncio.sleep(interval)

    while True:
        try:
            kwargs: dict[str, Any] = {"hostname": host, "port": port}
            if username:
                kwargs["username"] = username
            if password:
                kwargs["password"] = password

            async with aiomqtt.Client(**kwargs) as client:
                logger.info("MQTT generator connected → %s:%d", host, port)
                tasks = [
                    asyncio.create_task(
                        publish_loop(tc, gen, client),
                        name=f"mqtt-{tc['topic']}",
                    )
                    for tc, gen in generators
                ]
                try:
                    await asyncio.gather(*tasks)
                except asyncio.CancelledError:
                    for t in tasks:
                        t.cancel()
                    await asyncio.gather(*tasks, return_exceptions=True)
                    raise
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("MQTT generator error — reconnecting in 5 s")
            await asyncio.sleep(5)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

async def _main(config_path: str) -> None:
    config_file = Path(config_path)
    if not config_file.exists():
        logger.error("Config file not found: %s", config_path)
        sys.exit(1)

    with config_file.open(encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}

    tasks: list[asyncio.Task] = []

    if "knx" in config:
        tasks.append(asyncio.create_task(knx_generator(config["knx"]), name="knx"))
    if "modbus" in config:
        tasks.append(asyncio.create_task(modbus_generator(config["modbus"]), name="modbus"))
    if "mqtt" in config:
        tasks.append(asyncio.create_task(mqtt_generator(config["mqtt"]), name="mqtt"))

    if not tasks:
        logger.warning("No protocols configured — nothing to do. Check your config file.")
        return

    logger.info("Starting generators: %s", ", ".join(t.get_name() for t in tasks))
    logger.info("Press Ctrl+C to stop.")

    try:
        await asyncio.gather(*tasks)
    except asyncio.CancelledError:
        logger.info("Shutting down...")
        for t in tasks:
            t.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        logger.info("Stopped.")


if __name__ == "__main__":
    _default_cfg = Path(__file__).parent / "testdata_generator_example.yaml"
    _cfg_path = sys.argv[1] if len(sys.argv) > 1 else str(_default_cfg)
    try:
        asyncio.run(_main(_cfg_path))
    except KeyboardInterrupt:
        pass
