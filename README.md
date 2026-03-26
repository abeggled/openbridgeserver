# OpenTWS

**Open-Source Multiprotocol Server for Building Automation**

OpenTWS is a MIT-licensed replacement for the proprietary Timberwolf Server (TWS).
It connects KNX, Modbus RTU/TCP and 1-Wire devices through a unified object model and publishes all values via MQTT. The entire system is configured through a REST API — there is no proprietary configuration file format.

---

## Features

| | |
|---|---|
| **Protocols** | KNX/IP (Tunneling + Routing), Modbus TCP, Modbus RTU, 1-Wire |
| **MQTT** | Hybrid topic strategy: stable UUID topics + human-readable alias topics |
| **API** | FastAPI REST + WebSocket, JWT Bearer + API Key auth |
| **Storage** | SQLite (WAL mode), zero external dependencies |
| **History** | Plugin system — SQLite built-in, extensible (InfluxDB, TimescaleDB, …) |
| **Debug** | RingBuffer: in-memory or disk, runtime-switchable |
| **Runtime config** | All changes apply immediately — no restart needed |
| **Deployment** | Docker Compose (OpenTWS + Mosquitto) or bare-metal Python |
| **License** | MIT |

---

## Table of Contents

1. [Quick Start — Docker](#quick-start--docker)
2. [Quick Start — Bare Metal](#quick-start--bare-metal)
3. [Configuration Reference](#configuration-reference)
4. [Architecture Overview](#architecture-overview)
5. [API Reference](#api-reference)
   - [Authentication](#authentication)
   - [DataPoints](#datapoints)
   - [Bindings](#bindings)
   - [Search](#search)
   - [Adapters](#adapters)
   - [History](#history)
   - [RingBuffer](#ringbuffer)
   - [Import / Export](#import--export)
   - [System](#system)
   - [WebSocket](#websocket)
6. [Adapter Configuration](#adapter-configuration)
   - [KNX](#knx-adapter)
   - [Modbus TCP](#modbus-tcp-adapter)
   - [Modbus RTU](#modbus-rtu-adapter)
   - [1-Wire](#1-wire-adapter)
7. [MQTT Topics](#mqtt-topics)
8. [Data Types](#data-types)
9. [Development](#development)

---

## Quick Start — Docker

```bash
# 1. Clone
git clone https://github.com/opentws/opentws
cd opentws

# 2. Configure secrets
cp .env.example .env
# Edit .env: set OPENTWS_JWT_SECRET to a random string (min. 32 chars)

# 3. Start
docker compose up -d

# 4. Verify
curl http://localhost:8080/api/v1/system/health
# → {"status": "ok", "version": "0.1.0"}
```

**Default credentials:** `admin` / `admin`
⚠️ Change the password immediately after first login (see [User Management](#user-management)).

**Services:**

| Service | Port | Protocol |
|---|---|---|
| OpenTWS REST API | 8080 | HTTP |
| Mosquitto MQTT | 1883 | MQTT |
| Mosquitto WebSocket | 9001 | MQTT over WS |

---

## Quick Start — Bare Metal

**Requirements:** Python 3.11+, a running Mosquitto (or other MQTT broker)

```bash
# 1. Clone + venv
git clone https://github.com/opentws/opentws
cd opentws
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure
cp config.example.yaml config.yaml
# Edit config.yaml — set mqtt.host, security.jwt_secret

# 4. Run
python -m opentws
```

---

## Configuration Reference

Configuration is loaded in this priority order (highest wins):

1. Environment variables (`OPENTWS_<SECTION>__<KEY>`)
2. `config.yaml` (path via `OPENTWS_CONFIG` env var, default: `./config.yaml`)
3. Built-in defaults

**`config.yaml` / environment variable reference:**

```yaml
server:
  host: 0.0.0.0               # OPENTWS_SERVER__HOST
  port: 8080                  # OPENTWS_SERVER__PORT
  log_level: INFO             # OPENTWS_SERVER__LOG_LEVEL  (DEBUG|INFO|WARNING|ERROR)

mqtt:
  host: localhost             # OPENTWS_MQTT__HOST
  port: 1883                  # OPENTWS_MQTT__PORT
  username: null              # OPENTWS_MQTT__USERNAME
  password: null              # OPENTWS_MQTT__PASSWORD

database:
  path: /data/opentws.db      # OPENTWS_DATABASE__PATH
  history_plugin: sqlite      # OPENTWS_DATABASE__HISTORY_PLUGIN  (sqlite|influxdb|…)

ringbuffer:
  storage: memory             # OPENTWS_RINGBUFFER__STORAGE  (memory|disk)
  max_entries: 10000          # OPENTWS_RINGBUFFER__MAX_ENTRIES

security:
  jwt_secret: changeme        # OPENTWS_SECURITY__JWT_SECRET  ← change in production!
  jwt_expire_minutes: 1440    # OPENTWS_SECURITY__JWT_EXPIRE_MINUTES
```

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────┐
│                          OpenTWS Process                             │
│                                                                      │
│  ┌──────────┐   DataValueEvent   ┌──────────────────────────────┐   │
│  │ Adapters │ ────────────────▶  │         EventBus             │   │
│  │  KNX     │                    │  (asyncio.gather fan-out)    │   │
│  │  ModTCP  │ ◀──── write() ─── │                              │   │
│  │  ModRTU  │                    └──────┬──────────┬────────────┘   │
│  │  1-Wire  │                           │          │                │
│  └──────────┘                    ┌──────▼──┐  ┌────▼──────────┐    │
│                                  │Registry │  │  RingBuffer   │    │
│  ┌──────────┐   dp/+/set         │(in-mem) │  │  History      │    │
│  │   MQTT   │ ──────────────▶   │         │  │  WebSocket    │    │
│  │  Client  │ ◀── publish ───   │         │  │  Manager      │    │
│  └──────────┘                    └────┬────┘  └───────────────┘    │
│                                       │                             │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                      FastAPI  /api/v1                        │  │
│  │  auth  datapoints  bindings  adapters  history  ringbuffer   │  │
│  │  search  system  config  ws                                  │  │
│  └──────────────────────────────────────────────────────────────┘  │
│                                                                      │
│  ┌──────────────────────────────────────────────────────────────┐  │
│  │                   SQLite  (WAL mode)                         │  │
│  │  datapoints · adapter_bindings · adapter_configs             │  │
│  │  users · api_keys · history_values · schema_version          │  │
│  └──────────────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

**Key design principles:**
- **Registry pattern** throughout — DataTypeRegistry, AdapterRegistry, DPTRegistry are all self-registering; no hardcoding in the core.
- **EventBus** decouples adapters from the core. Adapters only publish `DataValueEvent`; they have no knowledge of MQTT, history, or WebSocket.
- **Write routing** is handled by a dedicated `WriteRouter`. MQTT `dp/{uuid}/set` messages are deserialized and dispatched to the correct adapter `write()` method via DB binding lookup.
- **Graceful degradation** — if a protocol library (xknx, pymodbus, w1thermsensor) is not installed, the adapter logs a warning and disables itself without crashing the server.

---

## API Reference

All endpoints are under `/api/v1`. The interactive API documentation (Swagger UI) is available at `http://localhost:8080/docs`.

### Authentication

OpenTWS supports two authentication methods that can be used interchangeably:

| Method | Header | Use case |
|---|---|---|
| JWT Bearer | `Authorization: Bearer {token}` | Web GUI, interactive use |
| API Key | `X-API-Key: opentws_{64 hex chars}` | Automation, scripts, MQTT clients |

**Endpoints:**

```
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh
```

```bash
# Login
curl -X POST http://localhost:8080/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "password": "admin"}'

# Response
{
  "access_token": "eyJ...",
  "refresh_token": "eyJ...",
  "token_type": "bearer"
}
```

**JWT details:** HS256, configurable expiry (default 24 h), 30-day refresh token.
**Password hashing:** PBKDF2-HMAC-SHA256, 260 000 iterations (stdlib, no external dependencies).

#### API Keys

```
POST   /api/v1/auth/apikeys          → create (returns key once, store it!)
DELETE /api/v1/auth/apikeys/{id}     → revoke
```

Keys are stored as SHA-256 hashes only — the plaintext key is returned exactly once at creation.

#### User Management

All `/users` endpoints except `/me` require `is_admin = true`.

```
GET    /api/v1/auth/users                      # list all users (admin)
POST   /api/v1/auth/users                      # create user (admin)
GET    /api/v1/auth/users/{username}           # get user (admin or self)
PATCH  /api/v1/auth/users/{username}           # update username / is_admin (admin)
DELETE /api/v1/auth/users/{username}           # delete user (admin, not self)

GET    /api/v1/auth/me                         # own profile
POST   /api/v1/auth/me/change-password         # change own password
```

```bash
# Create user
curl -X POST http://localhost:8080/api/v1/auth/users \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"username": "operator", "password": "s3cret", "is_admin": false}'

# Change own password
curl -X POST http://localhost:8080/api/v1/auth/me/change-password \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"current_password": "admin", "new_password": "newS3cret!"}'
```

---

### DataPoints

A DataPoint is the central object. Every physical or virtual value in the system is a DataPoint.

```
GET    /api/v1/datapoints?page=0&size=50       # list (paginated)
POST   /api/v1/datapoints                      # create
GET    /api/v1/datapoints/{id}                 # get one (includes current value)
PATCH  /api/v1/datapoints/{id}                 # update
DELETE /api/v1/datapoints/{id}                 # delete (cascades to bindings)
GET    /api/v1/datapoints/{id}/value           # current value only
```

**DataPoint fields:**

| Field | Type | Description |
|---|---|---|
| `id` | UUID | Auto-generated |
| `name` | string | Human-readable name |
| `data_type` | string | `BOOLEAN`, `INTEGER`, `FLOAT`, `STRING`, `DATE`, `TIME`, `DATETIME`, `UNKNOWN` |
| `unit` | string? | e.g. `°C`, `%`, `lux` |
| `tags` | string[] | For grouping/filtering |
| `mqtt_topic` | string | Auto-assigned: `dp/{uuid}/value` |
| `mqtt_alias` | string? | Optional: `alias/{tag}/{name}/value` |

```bash
# Create a temperature DataPoint
curl -X POST http://localhost:8080/api/v1/datapoints \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Living Room Temperature",
    "data_type": "FLOAT",
    "unit": "°C",
    "tags": ["climate", "living_room"]
  }'
```

---

### Bindings

A Binding connects a DataPoint to an adapter address.

```
GET    /api/v1/datapoints/{id}/bindings
POST   /api/v1/datapoints/{id}/bindings
PATCH  /api/v1/datapoints/{id}/bindings/{binding_id}
DELETE /api/v1/datapoints/{id}/bindings/{binding_id}
```

**Binding fields:**

| Field | Type | Description |
|---|---|---|
| `adapter_type` | string | `KNX`, `MODBUS_TCP`, `MODBUS_RTU`, `ONEWIRE` |
| `direction` | string | `SOURCE` (read), `DEST` (write), `BOTH` |
| `config` | object | Adapter-specific config (see [Adapter Configuration](#adapter-configuration)) |
| `enabled` | bool | Enable/disable without deleting |

**Important for KNX dimmers:** use two separate bindings — one `DEST` for the write GA, one `SOURCE` for the status GA.

---

### Search

Server-side filtered search across all DataPoints. Never returns the full dataset to the client.

```
GET /api/v1/search?q=&tag=&type=&adapter=&page=0&size=50
```

| Parameter | Description |
|---|---|
| `q` | Full-text search on name |
| `tag` | Filter by tag |
| `type` | Filter by data type (e.g. `FLOAT`) |
| `adapter` | Filter by adapter type (e.g. `KNX`) |

---

### Adapters

```
GET    /api/v1/adapters                        # all registered adapter types + status
GET    /api/v1/adapters/{type}/schema          # adapter config JSON schema
GET    /api/v1/adapters/{type}/binding-schema  # binding config JSON schema
POST   /api/v1/adapters/{type}/test            # test connection (no side effects)
GET    /api/v1/adapters/{type}/config          # current saved config
PATCH  /api/v1/adapters/{type}/config          # update config (applies immediately)
```

---

### History

```
GET /api/v1/history/{id}?from=&to=&limit=
GET /api/v1/history/{id}/aggregate?fn=avg&interval=1h&from=&to=
```

**Aggregate functions:** `avg`, `min`, `max`, `last`
**Intervals:** `1m`, `5m`, `15m`, `30m`, `1h`, `6h`, `12h`, `1d`

Intervals ≥ 1h use SQL-level grouping; sub-hourly intervals use Python-based grouping.

---

### RingBuffer

The RingBuffer is a circular debug log of the last N value changes. It can run in memory (default) or on disk and can be switched at runtime without data loss.

```
GET  /api/v1/ringbuffer?q=&adapter=&from=&limit=   # query entries
GET  /api/v1/ringbuffer/stats                       # entry count, oldest/newest ts
POST /api/v1/ringbuffer/config                      # reconfigure (storage, max_entries)
```

---

### Import / Export

Full configuration backup and restore. Uses upsert semantics — existing DataPoints and Bindings are updated, missing ones are created.

```
GET  /api/v1/config/export    # → JSON with all DataPoints, Bindings, AdapterConfigs
POST /api/v1/config/import    # ← JSON, returns {created, updated, errors}
```

---

### System

```
GET /api/v1/system/health      # no auth required — readiness probe
GET /api/v1/system/adapters    # adapter status + binding counts
GET /api/v1/system/datatypes   # all registered DataTypes
```

```bash
curl http://localhost:8080/api/v1/system/health
# → {"status": "ok", "version": "0.1.0"}
```

---

### WebSocket

Real-time value updates with selective subscribe per DataPoint.

```
WS /api/v1/ws?token={jwt}
```

**Authentication:** JWT via `?token=` query parameter or `Authorization` header.
**Keepalive:** 60 s timeout, ping/pong protocol.

**Subscribe to DataPoints:**
```json
{"action": "subscribe", "datapoint_ids": ["uuid-1", "uuid-2"]}
```

**Incoming value update:**
```json
{
  "datapoint_id": "550e8400-e29b-41d4-a716-446655440000",
  "value": 21.4,
  "quality": "good",
  "ts": "2026-03-26T10:23:41.123Z",
  "source_adapter": "KNX"
}
```

---

## Adapter Configuration

### KNX Adapter

**Adapter config** (via `PATCH /api/v1/adapters/KNX/config`):

```json
{
  "connection_type": "tunneling",
  "host": "192.168.1.100",
  "port": 3671,
  "individual_address": "1.1.255",
  "local_ip": null
}
```

| Field | Values | Description |
|---|---|---|
| `connection_type` | `tunneling` \| `routing` | Tunneling = unicast to gateway; Routing = IP multicast |
| `host` | IP address | KNX/IP gateway IP |
| `port` | default `3671` | KNX/IP port |
| `individual_address` | e.g. `1.1.255` | Own KNX individual address |
| `local_ip` | IP or null | Required for routing mode |

**Binding config:**

```json
{
  "group_address": "1/2/3",
  "dpt_id": "DPT9.001",
  "state_group_address": "1/2/4"
}
```

| Field | Description |
|---|---|
| `group_address` | KNX group address (3-level notation) |
| `dpt_id` | DPT identifier — see table below |
| `state_group_address` | Optional feedback GA for `DEST` bindings |

**Supported DPTs:**

| DPT | Bits | Typical use |
|---|---|---|
| `DPT1.001` | 1 bit | Switch on/off |
| `DPT1.008` | 1 bit | Up/Down |
| `DPT1.009` | 1 bit | Open/Close |
| `DPT5.001` | 8 bit unsigned | Dimming 0–100 % |
| `DPT5.003` | 8 bit unsigned | Angle 0–360° |
| `DPT6.001` | 8 bit signed | Relative value −128…127 |
| `DPT7.001` | 16 bit unsigned | Pulse count |
| `DPT8.001` | 16 bit signed | Relative value ±32767 |
| `DPT9.001` | 2-byte float | Temperature (°C) |
| `DPT9.002` | 2-byte float | Lux (lx) |
| `DPT9.004` | 2-byte float | Speed (m/s) |
| `DPT9.007` | 2-byte float | Humidity (%) |
| `DPT9.010` | 2-byte float | Power (W) |
| `DPT12.001` | 32 bit unsigned | Energy counter |
| `DPT13.001` | 32 bit signed | Counter value |
| `DPT14.019` | IEEE 754 float | Electrical current |
| `DPT14.027` | IEEE 754 float | Energy (J) |
| `DPT16.000` | 14-byte string | ASCII text |
| Unknown DPT | — | Falls back to `UNKNOWN` type (no crash) |

DPT9 uses the KNX EIS5 format: `SEEEEMMM MMMMMMMM`, `value = 0.01 × M × 2^E`.

---

### Modbus TCP Adapter

**Adapter config:**

```json
{
  "host": "192.168.1.50",
  "port": 502,
  "timeout": 3.0
}
```

**Binding config:**

```json
{
  "unit_id": 1,
  "register_type": "holding",
  "address": 100,
  "count": 2,
  "data_format": "float32",
  "scale_factor": 1.0,
  "byte_order": "big",
  "word_order": "big",
  "poll_interval": 1.0
}
```

| Field | Values | Description |
|---|---|---|
| `register_type` | `holding` \| `input` \| `coil` \| `discrete_input` | Modbus function code |
| `data_format` | `uint16` \| `int16` \| `uint32` \| `int32` \| `float32` \| `uint64` \| `int64` | Register interpretation |
| `scale_factor` | float | `raw × scale_factor = engineering value` |
| `poll_interval` | float (seconds) | For `SOURCE` / `BOTH` bindings |

---

### Modbus RTU Adapter

Same binding config as TCP. Additional adapter config fields:

```json
{
  "port": "/dev/ttyUSB0",
  "baudrate": 9600,
  "parity": "N",
  "stopbits": 1,
  "bytesize": 8,
  "timeout": 1.0
}
```

---

### 1-Wire Adapter

Reads temperature sensors via Linux sysfs (`/sys/bus/w1/devices/{sensor_id}/w1_slave`).
On non-Linux systems the adapter degrades gracefully (logs a warning, no crash).

**Binding config:**

```json
{
  "sensor_id": "28-0000012345ab",
  "poll_interval": 30.0
}
```

Use `GET /api/v1/adapters/ONEWIRE/test` to trigger `scan_sensors()` and list all detected sensor IDs.

---

## MQTT Topics

OpenTWS uses a **hybrid topic strategy**:

| Topic | Description |
|---|---|
| `dp/{uuid}/value` | Stable — never changes, safe for automations |
| `dp/{uuid}/raw` | Raw value without unit/quality wrapper |
| `dp/{uuid}/set` | Write to this topic to trigger `adapter.write()` |
| `dp/{uuid}/status` | Adapter connection status |
| `alias/{tag}/{name}/value` | Human-readable, browsable (optional, requires `mqtt_alias`) |

**Payload format:**

```json
{
  "v": 21.4,
  "u": "°C",
  "t": "2026-03-26T10:23:41.123Z",
  "q": "good"
}
```

| Key | Type | Description |
|---|---|---|
| `v` | any | Value (type-dependent serialization) |
| `u` | string \| null | Unit from DataPoint |
| `t` | string | ISO 8601 timestamp with milliseconds |
| `q` | string | `good` \| `bad` \| `uncertain` |

**Writing a value via MQTT:**
```bash
mosquitto_pub -t "dp/550e8400-e29b-41d4-a716-446655440000/set" \
  -m '{"v": true}'
```

---

## Data Types

| Type | Python | MQTT serialization |
|---|---|---|
| `BOOLEAN` | `bool` | `true` / `false` |
| `INTEGER` | `int` | number |
| `FLOAT` | `float` | number |
| `STRING` | `str` | string |
| `DATE` | `datetime.date` | ISO 8601 `YYYY-MM-DD` |
| `TIME` | `datetime.time` | ISO 8601 `HH:MM:SS` |
| `DATETIME` | `datetime.datetime` | ISO 8601 with timezone |
| `UNKNOWN` | `bytes` | hex string fallback |

Type conversions between incompatible types are **silent** (no runtime error). Loss of precision is logged and available to the GUI via `ConversionResult.loss_description`.

New types are registered via `DataTypeRegistry.register()` — no core code changes required.

---

## Development

### Prerequisites

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### Run in development mode

```bash
# Start Mosquitto (Docker)
docker run -d -p 1883:1883 eclipse-mosquitto:2

# Copy + edit config
cp config.example.yaml config.yaml

# Run with auto-reload
uvicorn opentws.main:create_app --factory --reload --host 0.0.0.0 --port 8080
```

### Project structure

```
opentws/
├── config.py                   # pydantic-settings, YAML + env var loading
├── main.py                     # FastAPI app, startup/shutdown sequence
├── __main__.py                 # python -m opentws entry point
│
├── db/
│   └── database.py             # aiosqlite wrapper, migration system (V1–V4)
│
├── models/
│   ├── types.py                # DataTypeRegistry, 8 built-in types
│   ├── datapoint.py            # DataPoint, DataPointCreate, DataPointUpdate
│   └── binding.py              # AdapterBinding
│
├── core/
│   ├── converter.py            # Type conversion with ConversionResult
│   ├── event_bus.py            # Async EventBus, DataValueEvent, AdapterStatusEvent
│   ├── mqtt_client.py          # aiomqtt wrapper, topic helpers, payload builder
│   ├── registry.py             # DataPointRegistry, in-memory ValueState
│   └── write_router.py         # dp/+/set → adapter.write() dispatcher
│
├── adapters/
│   ├── base.py                 # AdapterBase ABC
│   ├── registry.py             # @register decorator, start_all / stop_all
│   ├── modbus_base.py          # Shared Modbus binding config + codec
│   ├── knx/
│   │   ├── adapter.py          # KnxAdapter
│   │   └── dpt_registry.py     # DPTRegistry (37 DPTs)
│   ├── modbus_tcp/
│   │   └── adapter.py          # ModbusTcpAdapter
│   ├── modbus_rtu/
│   │   └── adapter.py          # ModbusRtuAdapter
│   └── onewire/
│       └── adapter.py          # OneWireAdapter
│
├── api/
│   ├── auth.py                 # JWT + API Key auth, user management endpoints
│   ├── router.py               # Aggregates all sub-routers
│   └── v1/
│       ├── datapoints.py       # CRUD + pagination
│       ├── bindings.py         # Binding CRUD, live adapter reload
│       ├── search.py           # Server-side filtered search
│       ├── adapters.py         # Adapter status, schema, test, config
│       ├── system.py           # Health, adapter status, datatypes
│       ├── websocket.py        # WebSocketManager, selective subscribe
│       ├── ringbuffer.py       # RingBuffer query + config
│       ├── history.py          # History query + aggregate
│       └── config.py           # Import / Export
│
├── ringbuffer/
│   └── ringbuffer.py           # SQLite-backed circular buffer (memory/disk)
│
└── history/
    ├── sqlite_plugin.py        # History writer + query + aggregate (SQLite)
    └── influxdb_plugin.py      # InfluxDB plugin stub
```

### Database schema

The database uses a version-based migration system. Current version: **V4**.

| Table | Description |
|---|---|
| `datapoints` | All DataPoints |
| `adapter_bindings` | Bindings between DataPoints and adapters |
| `adapter_configs` | Per-adapter JSON configuration |
| `users` | User accounts (username, PBKDF2 password hash, is_admin) |
| `api_keys` | API key names + SHA-256 hashes |
| `history_values` | Time-series value log |
| `schema_version` | Applied migration versions |

### Adding a new adapter

1. Create `opentws/adapters/{name}/adapter.py`
2. Subclass `AdapterBase`, decorate with `@register`
3. Define `adapter_type`, `config_schema`, `binding_config_schema`
4. Implement `connect()`, `disconnect()`, `read()`, `write()`
5. Import the module in `main.py` startup (one line)

No changes to the core, the API, or the database are needed.

### Adding a new DPT

```python
from opentws.adapters.knx.dpt_registry import DPTRegistry, DPTDefinition

DPTRegistry.register(DPTDefinition(
    dpt_id="DPT9.020",
    description="Sound intensity (dB)",
    encoder=lambda v: ...,
    decoder=lambda b: ...,
))
```

---

## License

MIT — see [LICENSE](LICENSE)
