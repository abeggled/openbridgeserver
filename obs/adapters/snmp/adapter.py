"""SNMP Adapter — SNMPv1, v2c, v3

Pollt SOURCE-Bindings per UDP/SNMP zyklisch, schreibt DEST-Bindings via SNMP SET.
Jedes Binding konfiguriert seinen eigenen Host/Port – SNMP ist zustandslos/UDP,
daher gibt es keine persistente Verbindung, sondern einen Credential-Container.

Adapter-Konfiguration (adapter_instances.config):
  version:          "1" | "2c" | "3"                            (default: "2c")
  -- v1/v2c --
  community:        str                                          (default: "public")
  -- v3 --
  security_name:    str                                          (default: "")
  security_level:   "noAuthNoPriv" | "authNoPriv" | "authPriv"  (default: "noAuthNoPriv")
  auth_protocol:    "MD5" | "SHA" | "SHA256" | "SHA512"         (default: "MD5")
  auth_key:         str                                          (default: "")
  priv_protocol:    "DES" | "3DES" | "AES128" | "AES192" | "AES256"  (default: "DES")
  priv_key:         str                                          (default: "")

Binding-Konfiguration (adapter_bindings.config):
  host:             str    IP oder DNS-Name des Geräts           (default: "192.168.1.1")
  port:             int    UDP-Port                              (default: 161)
  oid:              str    z.B. "1.3.6.1.2.1.1.1.0"
  data_type:        "auto" | "int" | "float" | "string" | "hex" | "counter" | "gauge" | "timeticks"
                                                                 (default: "auto")
  poll_interval:    float  Sekunden (SOURCE/BOTH)                (default: 30.0)
  timeout:          float  Sekunden pro Anfrage                  (default: 5.0)
  retries:          int    Wiederholungen bei Fehler             (default: 1)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Literal

from pydantic import BaseModel, Field

from obs.adapters.base import AdapterBase
from obs.adapters.registry import register
from obs.core.event_bus import DataValueEvent

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Config Schemas
# ---------------------------------------------------------------------------


class SnmpAdapterConfig(BaseModel):
    version: Literal["1", "2c", "3"] = Field(default="2c", title="SNMP-Version")
    community: str = Field(default="public", title="Community-String (v1/v2c)")
    security_name: str = Field(default="", title="Security Name / Username (v3)")
    security_level: Literal["noAuthNoPriv", "authNoPriv", "authPriv"] = Field(default="noAuthNoPriv", title="Security Level (v3)")
    auth_protocol: Literal["MD5", "SHA", "SHA256", "SHA512"] = Field(default="MD5", title="Auth-Protokoll (v3)")
    auth_key: str = Field(default="", title="Auth-Key (v3)", json_schema_extra={"format": "password"})
    priv_protocol: Literal["DES", "3DES", "AES128", "AES192", "AES256"] = Field(default="DES", title="Privacy-Protokoll (v3)")
    priv_key: str = Field(default="", title="Privacy-Key (v3)", json_schema_extra={"format": "password"})


class SnmpBindingConfig(BaseModel):
    host: str = Field(default="192.168.1.1", title="Host (IP/DNS)")
    port: int = Field(default=161, ge=1, le=65535, title="UDP-Port")
    oid: str = Field(default="1.3.6.1.2.1.1.1.0", title="OID")
    data_type: Literal["auto", "int", "float", "string", "hex", "counter", "gauge", "timeticks"] = Field(default="auto", title="Datentyp")
    poll_interval: float = Field(default=30.0, ge=1.0, title="Poll-Intervall (s)")
    timeout: float = Field(default=5.0, ge=0.5, le=30.0, title="Timeout (s)")
    retries: int = Field(default=1, ge=0, le=5, title="Wiederholungen")


# ---------------------------------------------------------------------------
# Internal helpers — lazy pysnmp import
# ---------------------------------------------------------------------------


def _import_pysnmp() -> dict:
    """Import pysnmp symbols; supports both v5 (hlapi.asyncio) and v6 (hlapi.v3arch.asyncio)."""
    symbols: dict = {}
    try:
        try:
            from pysnmp.hlapi.v3arch.asyncio import (  # type: ignore[import]
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
        except ImportError:
            from pysnmp.hlapi.asyncio import (  # type: ignore[import]
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
        symbols.update(
            {
                "getCmd": getCmd,
                "setCmd": setCmd,
                "nextCmd": nextCmd,
                "SnmpEngine": SnmpEngine,
                "CommunityData": CommunityData,
                "UsmUserData": UsmUserData,
                "UdpTransportTarget": UdpTransportTarget,
                "ContextData": ContextData,
                "ObjectType": ObjectType,
                "ObjectIdentity": ObjectIdentity,
            }
        )

        # Auth/priv constants: in v6.x they live in hlapi.asyncio (or hlapi.v3arch.asyncio),
        # not in hlapi directly. Try the path that already succeeded above.
        _hlapi_mod: Any
        try:
            import pysnmp.hlapi.v3arch.asyncio as _hlapi_mod  # type: ignore[import]
        except ImportError:
            import pysnmp.hlapi.asyncio as _hlapi_mod  # type: ignore[import]

        usmHMACMD5AuthProtocol = _hlapi_mod.usmHMACMD5AuthProtocol
        usmHMACSHAAuthProtocol = _hlapi_mod.usmHMACSHAAuthProtocol
        usmNoAuthProtocol = _hlapi_mod.usmNoAuthProtocol
        usmNoPrivProtocol = _hlapi_mod.usmNoPrivProtocol
        usmDESPrivProtocol = _hlapi_mod.usmDESPrivProtocol

        auth_map: dict[str, Any] = {
            "MD5": usmHMACMD5AuthProtocol,
            "SHA": usmHMACSHAAuthProtocol,
            "SHA256": usmHMACSHAAuthProtocol,
            "SHA512": usmHMACSHAAuthProtocol,
        }
        priv_map: dict[str, Any] = {
            "DES": usmDESPrivProtocol,
            "noPriv": usmNoPrivProtocol,
        }

        # Optional modern auth protocols
        for attr, key in [("usmHMAC192SHA256AuthProtocol", "SHA256"), ("usmHMAC384SHA512AuthProtocol", "SHA512")]:
            val = getattr(_hlapi_mod, attr, None)
            if val is not None:
                auth_map[key] = val

        # Optional privacy protocols
        for attr, key, fallback in [
            ("usm3DESEDEPrivProtocol", "3DES", usmDESPrivProtocol),
            ("usmAesCfb128Protocol", "AES128", None),
            ("usmAesCfb192Protocol", "AES192", None),
            ("usmAesCfb256Protocol", "AES256", None),
        ]:
            val = getattr(_hlapi_mod, attr, fallback)
            if val is not None:
                priv_map[key] = val

        symbols["_auth_map"] = auth_map
        symbols["_priv_map"] = priv_map
        symbols["_no_auth"] = usmNoAuthProtocol
        symbols["_no_priv"] = usmNoPrivProtocol
        return symbols

    except ImportError:
        return {}


def _pretty(snmp_value: Any) -> str:
    """Return a string representation regardless of whether the value is a pysnmp type or a Python native."""
    if hasattr(snmp_value, "prettyPrint"):
        return snmp_value.prettyPrint()
    return str(snmp_value)


def _coerce_value(snmp_value: Any, data_type: str) -> Any:
    """Convert a pysnmp value object (or already-native Python value) to a Python native type.

    pysnmp 6.x with lookupMib=True may return Python int/str directly instead of
    pysnmp wrapper objects, so we must not assume prettyPrint() is available.
    """
    # Already a native Python scalar — return directly or apply requested cast
    if isinstance(snmp_value, (bool, int, float)):
        if data_type == "float":
            return float(snmp_value)
        if data_type in ("int", "counter", "gauge", "timeticks"):
            return int(snmp_value)
        if data_type == "hex":
            return hex(int(snmp_value))
        if data_type == "string":
            return str(snmp_value)
        return snmp_value  # auto: keep native type

    if isinstance(snmp_value, (bytes, bytearray)):
        if data_type == "hex" or data_type == "auto":
            return snmp_value.hex()
        return snmp_value.decode(errors="replace")

    if isinstance(snmp_value, str):
        if data_type == "auto":
            try:
                return int(snmp_value)
            except ValueError:
                pass
            try:
                return float(snmp_value)
            except ValueError:
                pass
        return snmp_value

    # pysnmp wrapper object — use prettyPrint() / int() as before
    type_name = type(snmp_value).__name__

    if data_type == "int" or (data_type == "auto" and type_name in ("Integer32", "Integer", "Unsigned32")):
        try:
            return int(snmp_value)
        except Exception:
            return _pretty(snmp_value)

    if data_type in ("counter", "gauge") or (data_type == "auto" and type_name in ("Counter32", "Counter64", "Gauge32", "Gauge")):
        try:
            return int(snmp_value)
        except Exception:
            return _pretty(snmp_value)

    if data_type == "timeticks" or (data_type == "auto" and type_name == "TimeTicks"):
        try:
            return int(snmp_value)
        except Exception:
            return _pretty(snmp_value)

    if data_type == "float":
        try:
            return float(_pretty(snmp_value))
        except ValueError:
            return _pretty(snmp_value)

    if data_type == "hex":
        try:
            raw = bytes(snmp_value)
            return raw.hex()
        except Exception:
            return _pretty(snmp_value)

    # "string" or "auto" fallback
    pp = _pretty(snmp_value)
    if data_type == "auto":
        try:
            return int(pp)
        except ValueError:
            pass
        try:
            return float(pp)
        except ValueError:
            pass
    return pp


def _encode_write_value(value: Any, data_type: str) -> Any:
    """Encode a Python value to a pysnmp type for SNMP SET operations."""
    try:
        from pysnmp.proto.rfc1902 import Integer32, OctetString  # type: ignore[import]
    except ImportError:
        return value

    if data_type in ("int", "counter", "gauge"):
        return Integer32(int(value))
    if data_type == "string":
        return OctetString(str(value).encode())
    if data_type == "float":
        return OctetString(str(value).encode())
    if data_type == "hex":
        if isinstance(value, str):
            try:
                return OctetString(bytes.fromhex(value))
            except ValueError:
                pass
        return OctetString(str(value).encode())

    # auto: detect from Python type
    if isinstance(value, bool):
        return Integer32(int(value))
    if isinstance(value, int):
        return Integer32(value)
    return OctetString(str(value).encode())


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@register
class SnmpAdapter(AdapterBase):
    """SNMPv1/v2c/v3 adapter — polls SOURCE bindings, writes DEST bindings via SET."""

    adapter_type = "SNMP"
    config_schema = SnmpAdapterConfig
    binding_config_schema = SnmpBindingConfig

    def __init__(self, event_bus: Any, config: dict | None = None, **kwargs) -> None:
        super().__init__(event_bus, config, **kwargs)
        self._snmp: dict = {}  # lazy-loaded pysnmp symbols
        self._engine: Any = None  # SnmpEngine instance
        self._poll_tasks: list[asyncio.Task] = []

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def connect(self) -> None:
        self._snmp = _import_pysnmp()
        if not self._snmp:
            msg = "pysnmp nicht installiert — SNMP deaktiviert. Ausführen: pip install pysnmp"
            logger.error(msg)
            await self._publish_status(False, msg)
            return

        self._engine = self._snmp["SnmpEngine"]()
        cfg = SnmpAdapterConfig(**self._config)
        detail = f"SNMPv{cfg.version}"
        if cfg.version in ("1", "2c"):
            detail += f" community={cfg.community!r}"
        else:
            detail += f" user={cfg.security_name!r} level={cfg.security_level}"

        await self._publish_status(True, detail)
        logger.info("SNMP Adapter bereit: %s", detail)

    async def disconnect(self) -> None:
        for t in self._poll_tasks:
            t.cancel()
        self._poll_tasks.clear()
        self._engine = None
        await self._publish_status(False, "Getrennt")

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    async def _on_bindings_reloaded(self) -> None:
        for t in self._poll_tasks:
            t.cancel()
        self._poll_tasks.clear()

        source_bindings = [b for b in self._bindings if b.direction in ("SOURCE", "BOTH")]
        for binding in source_bindings:
            t = asyncio.create_task(
                self._poll_loop(binding),
                name=f"snmp-poll-{binding.id}",
            )
            self._poll_tasks.append(t)

        logger.debug("SNMP: %d Poll-Tasks gestartet", len(self._poll_tasks))

    # ------------------------------------------------------------------
    # Poll loop
    # ------------------------------------------------------------------

    async def _poll_loop(self, binding: Any) -> None:
        try:
            bc = SnmpBindingConfig(**binding.config)
        except Exception:
            logger.warning("Ungültige SNMP-Binding-Konfiguration %s — übersprungen", binding.id)
            return

        while True:
            try:
                raw = await self._snmp_get(bc)
                if raw is None:
                    raise ValueError("Kein Wert empfangen")

                value = _coerce_value(raw, bc.data_type)
                quality = "good"

                if binding.value_formula:
                    from obs.core.formula import apply_formula

                    value = apply_formula(binding.value_formula, value)
                if binding.value_map:
                    from obs.core.transformation import apply_value_map

                    value = apply_value_map(value, binding.value_map)

                await self._bus.publish(
                    DataValueEvent(
                        datapoint_id=binding.datapoint_id,
                        value=value,
                        quality=quality,
                        source_adapter=self.adapter_type,
                        binding_id=binding.id,
                    )
                )
            except asyncio.CancelledError:
                return
            except Exception as exc:
                logger.warning("SNMP Poll-Fehler (Binding %s): %s", binding.id, exc)
                await self._bus.publish(
                    DataValueEvent(
                        datapoint_id=binding.datapoint_id,
                        value=None,
                        quality="bad",
                        source_adapter=self.adapter_type,
                        binding_id=binding.id,
                    )
                )

            await asyncio.sleep(bc.poll_interval)

    # ------------------------------------------------------------------
    # Read / Write
    # ------------------------------------------------------------------

    async def read(self, binding: Any) -> Any:
        if not self._engine:
            return None
        try:
            bc = SnmpBindingConfig(**binding.config)
            raw = await self._snmp_get(bc)
            if raw is None:
                return None
            return _coerce_value(raw, bc.data_type)
        except Exception:
            logger.exception("SNMP read fehlgeschlagen für Binding %s", binding.id)
            return None

    async def write(self, binding: Any, value: Any) -> None:
        if not self._engine:
            logger.warning("SNMP write übersprungen — nicht verbunden")
            return
        try:
            bc = SnmpBindingConfig(**binding.config)
            await self._snmp_set(bc, value)
        except Exception:
            logger.exception("SNMP write fehlgeschlagen für Binding %s", binding.id)

    # ------------------------------------------------------------------
    # SNMP GET / SET low-level
    # ------------------------------------------------------------------

    def _build_auth(self, cfg: SnmpAdapterConfig) -> Any:
        s = self._snmp
        if cfg.version == "3":
            if not cfg.security_name:
                raise ValueError("SNMPv3: Security Name / Username darf nicht leer sein")
            auth_proto = s["_auth_map"].get(cfg.auth_protocol, s["_no_auth"])
            priv_proto = s["_priv_map"].get(cfg.priv_protocol, s["_no_priv"])

            if cfg.security_level == "noAuthNoPriv":
                return s["UsmUserData"](cfg.security_name)
            if cfg.security_level == "authNoPriv":
                return s["UsmUserData"](
                    cfg.security_name,
                    authKey=cfg.auth_key,
                    authProtocol=auth_proto,
                )
            # authPriv
            return s["UsmUserData"](
                cfg.security_name,
                authKey=cfg.auth_key,
                privKey=cfg.priv_key,
                authProtocol=auth_proto,
                privProtocol=priv_proto,
            )

        mp_model = 0 if cfg.version == "1" else 1
        return s["CommunityData"](cfg.community, mpModel=mp_model)

    async def _snmp_get(self, bc: SnmpBindingConfig) -> Any:
        s = self._snmp
        cfg = SnmpAdapterConfig(**self._config)
        auth = self._build_auth(cfg)
        transport = s["UdpTransportTarget"]((bc.host, bc.port), timeout=bc.timeout, retries=bc.retries)

        error_indication, error_status, error_index, var_binds = await s["getCmd"](
            self._engine,
            auth,
            transport,
            s["ContextData"](),
            s["ObjectType"](s["ObjectIdentity"](bc.oid)),
        )

        if error_indication:
            raise RuntimeError(str(error_indication))
        if error_status:
            raise RuntimeError(f"{error_status.prettyPrint()} bei Index {int(error_index)}")

        for var_bind in var_binds:
            return var_bind[1]  # ObjectType[0]=OID, ObjectType[1]=value
        return None

    async def _snmp_set(self, bc: SnmpBindingConfig, value: Any) -> None:
        s = self._snmp
        cfg = SnmpAdapterConfig(**self._config)
        auth = self._build_auth(cfg)
        transport = s["UdpTransportTarget"]((bc.host, bc.port), timeout=bc.timeout, retries=bc.retries)
        snmp_value = _encode_write_value(value, bc.data_type)

        error_indication, error_status, error_index, _var_binds = await s["setCmd"](
            self._engine,
            auth,
            transport,
            s["ContextData"](),
            s["ObjectType"](s["ObjectIdentity"](bc.oid), snmp_value),
        )

        if error_indication:
            raise RuntimeError(str(error_indication))
        if error_status:
            raise RuntimeError(f"{error_status.prettyPrint()} bei Index {int(error_index)}")

    # ------------------------------------------------------------------
    # SNMP Walk (für Discovery-Endpunkt)
    # ------------------------------------------------------------------

    async def snmp_walk(
        self,
        host: str,
        oid: str,
        port: int = 161,
        timeout: float = 5.0,
        retries: int = 1,
        max_results: int = 50,
        start_oid: str | None = None,
    ) -> list[dict[str, str]]:
        """SNMP-Walk über einen OID-Teilbaum; gibt Liste von {oid, value, type} zurück.

        getCmd()  → var_binds is 1D: [ObjectType, ...]
        nextCmd() → var_binds is 2D: [[ObjectType, ...], ...]  (table rows)
        Each call to nextCmd() returns one GETNEXT step; we loop until the
        returned OID leaves the requested subtree.

        start_oid: optional cursor for pagination — walk starts here instead of
        at `oid`, but the subtree boundary is still determined by `oid`.
        """
        if not self._engine:
            raise RuntimeError("SNMP Adapter nicht verbunden")

        s = self._snmp
        cfg = SnmpAdapterConfig(**self._config)
        auth = self._build_auth(cfg)
        transport = s["UdpTransportTarget"]((host, port), timeout=timeout, retries=retries)

        root_prefix = oid if oid.endswith(".") else oid + "."
        results: list[dict[str, str]] = []
        current_oid = start_oid if start_oid else oid

        while True:
            error_indication, error_status, _error_index, var_binds = await s["nextCmd"](
                self._engine,
                auth,
                transport,
                s["ContextData"](),
                s["ObjectType"](s["ObjectIdentity"](current_oid)),
            )

            if error_indication:
                logger.warning("SNMP Walk Fehler: %s", error_indication)
                break
            if error_status:
                logger.warning("SNMP Walk Status-Fehler: %s", error_status.prettyPrint())
                break

            if not var_binds:
                break

            # nextCmd var_binds is 2D: list of rows; each row holds one ObjectType per
            # requested variable. We pass one OID → each row has exactly one entry.
            for row in var_binds:
                var_bind = row[0]  # ObjectType for our single OID
                obj_oid = var_bind[0]  # ObjectType[0] = OID
                value = var_bind[1]  # ObjectType[1] = value
                oid_str = str(obj_oid)

                # Stop when we leave the requested subtree
                if not (oid_str == oid or oid_str.startswith(root_prefix)):
                    return results

                results.append(
                    {
                        "oid": oid_str,
                        "value": _pretty(value),
                        "type": type(value).__name__,
                    }
                )
                current_oid = oid_str

            if len(results) >= max_results:
                break

        return results
