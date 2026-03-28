# openTWS

![openTWS Logo](logo/opentws_logo.svg)

**Open-Source Multiprotocol Server for Building Automation**

It connects KNX, Modbus RTU/TCP, 1-Wire and external MQTT brokers through a unified object model and publishes all values via MQTT. The entire system is configured through a REST API — there is no proprietary configuration file format.

---

## Features

| | |
|---|---|
| **Protocols** | KNX/IP (Tunneling + Routing), Modbus TCP, Modbus RTU, 1-Wire, external MQTT |
| **Multi-Instance** | Any number of adapter instances per type (e.g. 2× KNX, 3× MQTT, 2× Modbus TCP) |
| **Cross-Protocol** | SOURCE → DEST propagation: a KNX value automatically writes to a Modbus register and vice versa |
| **Logic Engine** | Visual canvas (Vue Flow) — 15 built-in block types: logic gates, comparators, formulas, timers, Python scripts, MCP; automatic type coercion; per-block filter & transformation |
| **MQTT** | Hybrid topic strategy: stable UUID topics + human-readable alias topics; retain support |
| **API** | FastAPI REST + WebSocket, JWT Bearer + API Key auth |
| **Storage** | SQLite (WAL mode), zero external dependencies |
| **History** | Plugin system — SQLite built-in, extensible (InfluxDB, TimescaleDB, …) |
| **Debug** | RingBuffer: memory or disk (default: disk), searchable by name |
| **Runtime config** | All changes apply immediately — no restart needed |
| **Deployment** | Docker Compose (openTWS + Mosquitto) or bare-metal Python |
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
6. [Logic Engine](#logic-engine)
   - [Übersicht](#übersicht)
   - [Blocktypen](#blocktypen)
   - [DataPoint-Blöcke: Filter & Transformation](#datapoint-blöcke-filter--transformation)
   - [Formel-Referenz](#formel-referenz)
   - [Typ-Koercion](#typ-koercion)
   - [Debug-Modus](#debug-modus)
   - [API](#logic-engine-api)
7. [Adapter Configuration](#adapter-configuration)
   - [KNX](#knx-adapter)
   - [Modbus TCP](#modbus-tcp-adapter)
   - [Modbus RTU](#modbus-rtu-adapter)
   - [1-Wire](#1-wire-adapter)
   - [MQTT (external broker)](#mqtt-adapter-external-broker)
8. [MQTT Topics](#mqtt-topics)
9. [Data Types](#data-types)
10. [Development](#development)

---

## Quick Start — Docker

```bash
# 1. Clone
git clone https://github.com/abeggled/opentws
cd opentws

# 2. Configure secrets
cp .env.example .env
# Edit .env — at minimum set:
#   OPENTWS_JWT_SECRET        → random string, min. 32 chars
#   OPENTWS_MQTT_USERNAME     → service account username (default: opentws)
#   OPENTWS_MQTT_PASSWORD     → service account password — CHANGE THIS!

# 3. Start
docker compose up -d

# 4. Verify
curl http://localhost:8080/api/v1/system/health
# → {"status": "ok", "version": "0.1.0"}
```

**Default credentials:** `admin` / `admin`
⚠️ Change the password immediately after first login (see [User Management](#user-management)).

> **MQTT security:** Mosquitto starts with `allow_anonymous false`. Only the openTWS service account (from `.env`) and users with an MQTT password set can connect. See [MQTT Access per User](#mqtt-access-per-user).

**Services:**

| Service | Port | Protocol |
|---|---|---|
| openTWS REST API + GUI | 8080 | HTTP |
| Mosquitto MQTT (internal) | 1883 | MQTT |
| Mosquitto WebSocket | 9001 | MQTT over WS |

---

## Quick Start — Bare Metal

**Requirements:** Python 3.11+, a running Mosquitto (or other MQTT broker)

```bash
# 1. Clone + venv
git clone https://github.com/abeggled/opentws
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
  host: localhost             # OPENTWS_MQTT__HOST  (internal Mosquitto)
  port: 1883                  # OPENTWS_MQTT__PORT
  username: null              # OPENTWS_MQTT__USERNAME
  password: null              # OPENTWS_MQTT__PASSWORD

database:
  path: /data/opentws.db      # OPENTWS_DATABASE__PATH
  history_plugin: sqlite      # OPENTWS_DATABASE__HISTORY_PLUGIN  (sqlite|influxdb|…)

ringbuffer:
  storage: disk               # OPENTWS_RINGBUFFER__STORAGE  (memory|disk)
  max_entries: 10000          # OPENTWS_RINGBUFFER__MAX_ENTRIES

security:
  jwt_secret: changeme        # OPENTWS_SECURITY__JWT_SECRET  ← change in production!
  jwt_expire_minutes: 1440    # OPENTWS_SECURITY__JWT_EXPIRE_MINUTES
```

> **Note:** The `mqtt` section configures the **internal** Mosquitto broker used for the openTWS topic bus. External MQTT brokers are configured as separate adapter instances (see [MQTT Adapter](#mqtt-adapter-external-broker)).

---

## Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────┐
│                             openTWS Process                              │
│                                                                          │
│  ┌──────────────────────────┐  DataValueEvent  ┌─────────────────────┐  │
│  │    Adapter Instances     │ ───────────────▶ │      EventBus       │  │
│  │                          │                  │     (fan-out)       │  │
│  │  KNX "IP Router"         │ ◀── write() ──── │                     │  │
│  │  KNX "IP Interface"      │                  └──┬──────┬──────┬────┘  │
│  │  Modbus TCP "SPS"        │                     │      │      │       │
│  │  MQTT "HomeAssistant"    │           ┌─────────▼─┐ ┌──▼───┐ │       │
│  │  1-Wire "Keller"  …      │           │ Registry  │ │ Ring │ │       │
│  └──────────────────────────┘           │ (in-mem)  │ │ Buf  │ │       │
│                                         │ ValueState│ │ Hist │ │       │
│  ┌──────────────────┐  dp/+/set         └─────┬─────┘ │ WS   │ │       │
│  │  Internal MQTT   │ ──────────▶             │       └──────┘ │       │
│  │  Client          │ ◀─ publish ─  ┌─────────▼──────────┐    │       │
│  └──────────────────┘               │    WriteRouter      │    │       │
│                                     │ formula · filters   │    │       │
│                                     │ MQTT set→write()    │    │       │
│                                     │ SOURCE→DEST bridge  │    │       │
│                                     └────────────────────┘    │       │
│                                                                 │       │
│                                                    ┌────────────▼────┐  │
│                                                    │  LogicManager   │  │
│                                                    │ ─────────────── │  │
│                                                    │ DataValueEvent  │  │
│                                                    │  → graph exec.  │  │
│                                                    │ filter/throttle │  │
│                                                    │ → DataValueEvent│  │
│                                                    └─────────────────┘  │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                        FastAPI  /api/v1                            │ │
│  │  auth · datapoints · bindings · adapters · history                │ │
│  │  ringbuffer · search · system · config · websocket · logic        │ │
│  └────────────────────────────────────────────────────────────────────┘ │
│                                                                          │
│  ┌────────────────────────────────────────────────────────────────────┐ │
│  │                      SQLite  (WAL mode)                            │ │
│  │  datapoints · adapter_bindings · adapter_instances                │ │
│  │  adapter_configs · users · api_keys · history_values              │ │
│  │  logic_graphs · schema_version                                    │ │
│  └────────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────────┘
```

**Key design principles:**
- **Multi-Instance Adapters** — each adapter type can run multiple independent instances (e.g. two KNX gateways). Instances are identified by UUID and managed at runtime without restarts.
- **Registry pattern** throughout — DataTypeRegistry, AdapterRegistry, DPTRegistry are all self-registering; no hardcoding in the core.
- **EventBus** decouples adapters from the core. Adapters only publish `DataValueEvent`; they have no knowledge of MQTT, history, or WebSocket.
- **WriteRouter** handles two write paths:
  1. External: `dp/{uuid}/set` → MQTT → `adapter.write()`
  2. Internal: `DataValueEvent` from a SOURCE binding → propagated to all DEST/BOTH bindings of the same DataPoint (cross-protocol bridging)
- **Graceful degradation** — if a protocol library (xknx, pymodbus, w1thermsensor) is not installed, the adapter logs a warning and disables itself without crashing the server.

---

## API Reference

All endpoints are under `/api/v1`. The interactive API documentation (Swagger UI) is available at `http://localhost:8080/docs`.

### Authentication

openTWS supports two authentication methods that can be used interchangeably:

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
GET    /api/v1/auth/users                          # list all users (admin)
POST   /api/v1/auth/users                          # create user (admin)
GET    /api/v1/auth/users/{username}               # get user (admin or self)
PATCH  /api/v1/auth/users/{username}               # update username / is_admin / mqtt_enabled (admin)
DELETE /api/v1/auth/users/{username}               # delete user (admin, not self)

POST   /api/v1/auth/users/{username}/mqtt-password # set MQTT password (admin or self)
DELETE /api/v1/auth/users/{username}/mqtt-password # revoke MQTT access (admin)

GET    /api/v1/auth/me                             # own profile
POST   /api/v1/auth/me/change-password             # change own password
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

#### MQTT Access per User

The internal Mosquitto broker is password-protected. Access is controlled per user via the `mqtt_enabled` flag and a **separate** MQTT password (independent of the login password).

| Field | Description |
|---|---|
| `mqtt_enabled` | Whether the user can connect to Mosquitto |
| `mqtt_password_set` | `true` if an MQTT password is configured (hash never exposed) |

**Workflow:**

```bash
# 1. Grant MQTT access to a user (set password → automatically enables mqtt_enabled)
curl -X POST http://localhost:8080/api/v1/auth/users/operator/mqtt-password \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"password": "mqttS3cret!"}'

# 2. User can now connect directly to Mosquitto:
mosquitto_sub -h localhost -p 1883 -u operator -P mqttS3cret! -t dp/#

# 3. Revoke access (removes password + sets mqtt_enabled=false)
curl -X DELETE http://localhost:8080/api/v1/auth/users/operator/mqtt-password \
  -H "Authorization: Bearer {token}"

# 4. Users can rotate their own MQTT password:
curl -X POST http://localhost:8080/api/v1/auth/users/operator/mqtt-password \
  -H "Authorization: Bearer {operator-token}" \
  -H "Content-Type: application/json" \
  -d '{"password": "newMqttPass!"}'
```

**How it works internally:**

1. openTWS stores the MQTT password as a Mosquitto-compatible PBKDF2-SHA512 hash (not the same as the login hash).
2. On every change, openTWS rewrites `/mosquitto/passwd/passwd` and sends `SIGHUP` to Mosquitto.
3. Mosquitto reloads the file **without dropping existing connections**.
4. The `opentws` service account is always included and cannot be removed via the API.

**MQTT password vs. login password:**
- Login password: used for the openTWS REST API / GUI.
- MQTT password: used to connect directly to the Mosquitto broker (port 1883 / 9001).
- They are independent — changing one does not affect the other.
- MQTT password can be rotated by the user themselves or by an admin.

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
| `unit` | string? | ISO unit, e.g. `°C`, `%rH`, `kWh`, `lx` (dropdown in GUI) |
| `tags` | string[] | For grouping/filtering |
| `mqtt_topic` | string | Auto-assigned: `dp/{uuid}/value` |
| `mqtt_alias` | string? | Optional: `alias/{tag}/{name}/value` |

```bash
# Create a temperature DataPoint
curl -X POST http://localhost:8080/api/v1/datapoints \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Wohnzimmer Temperatur",
    "data_type": "FLOAT",
    "unit": "°C",
    "tags": ["klima", "wohnzimmer"]
  }'
```

---

### Bindings

A Binding connects a DataPoint to a specific adapter instance and address.

```
GET    /api/v1/datapoints/{id}/bindings
POST   /api/v1/datapoints/{id}/bindings
PATCH  /api/v1/datapoints/{id}/bindings/{binding_id}
DELETE /api/v1/datapoints/{id}/bindings/{binding_id}
```

**Binding fields:**

| Field | Type | Description |
|---|---|---|
| `adapter_instance_id` | UUID | Which adapter instance handles this binding |
| `adapter_type` | string | Auto-derived from the instance (`KNX`, `MODBUS_TCP`, …) |
| `direction` | string | `SOURCE` (read), `DEST` (write), `BOTH` |
| `config` | object | Adapter-specific config (see [Adapter Configuration](#adapter-configuration)) |
| `enabled` | bool | Enable/disable without deleting |
| `value_formula` | string \| null | Math transformation applied to the value (see below) |
| `send_throttle_ms` | int \| null | Minimum interval between two sends in milliseconds (DEST/BOTH only) |
| `send_on_change` | bool | Only send if value differs from last sent value (default: `false`) |
| `send_min_delta` | float \| null | Only send if absolute deviation from last value ≥ threshold |
| `send_min_delta_pct` | float \| null | Only send if relative deviation from last value ≥ threshold (%) |

**Value transformation (`value_formula`):**

An optional formula string applied to the value before it enters (SOURCE) or leaves (DEST/BOTH) the system. The variable is always `x`.

```json
{ "value_formula": "x / 10" }
```

| Example formula | Effect |
|---|---|
| `x * 3600` | Hours → seconds |
| `x / 3600` | Seconds → hours |
| `x / 10` | Fixed-point ÷ 10 |
| `x * 0.1 + 20` | Scale + offset |
| `round(x, 2)` | Round to 2 decimal places |
| `max(0, min(100, x))` | Clamp to 0–100 |

Available functions: `abs`, `round`, `min`, `max` and all `math.*` functions (`sqrt`, `floor`, `ceil`, `log`, …). Division by zero and `nan`/`inf` results are caught — the original value is kept.

**Send filters** (applied in order, DEST/BOTH only):

1. **Throttle** — if `send_throttle_ms` is set and the last send was less than that many ms ago, the value is discarded.
2. **On-change** — if `send_on_change` is `true` and the new value equals the last sent value, it is discarded.
3. **Delta** — if `send_min_delta` or `send_min_delta_pct` is set and the deviation is below the threshold, the value is discarded. Both conditions must be met if both are configured. Non-numeric values bypass delta checks.

> All filters compare against the **untransformed** value. The formula is applied afterwards.

**Cross-protocol bridging:**
When a SOURCE binding receives a value, the WriteRouter automatically propagates it to **all** DEST/BOTH bindings of the same DataPoint — regardless of adapter type. A KNX temperature value is written to a Modbus register without any additional configuration.

**Example:** KNX temperature → Modbus register

```bash
# 1. Create DataPoint
DP_ID=$(curl -s -X POST http://localhost:8080/api/v1/datapoints \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"name":"Wohnzimmer Temperatur","data_type":"FLOAT","unit":"°C","tags":["klima"]}' \
  | jq -r .id)

# 2. Get adapter instance IDs
curl http://localhost:8080/api/v1/adapters/instances \
  -H "Authorization: Bearer {token}"

# 3a. KNX SOURCE binding (reads from GA 1/2/3)
curl -X POST http://localhost:8080/api/v1/datapoints/$DP_ID/bindings \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "adapter_instance_id": "KNX-INSTANCE-UUID",
    "direction": "SOURCE",
    "config": {"group_address": "1/2/3", "dpt_id": "DPT9.001"}
  }'

# 3b. Modbus DEST binding (writes to holding register 100)
curl -X POST http://localhost:8080/api/v1/datapoints/$DP_ID/bindings \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "adapter_instance_id": "MODBUS-INSTANCE-UUID",
    "direction": "DEST",
    "config": {"unit_id": 1, "register_type": "holding", "address": 100, "data_format": "float32"}
  }'
```

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

**Typ-Routen** (schema lookup, legacy config):

```
GET    /api/v1/adapters                        # all registered adapter types + status
GET    /api/v1/adapters/{type}/schema          # adapter config JSON schema
GET    /api/v1/adapters/{type}/binding-schema  # binding config JSON schema
POST   /api/v1/adapters/{type}/test            # test connection with given config (no persist)
```

**Instanz-Routen** (multi-instance management):

```
GET    /api/v1/adapters/instances              # list all instances with status
POST   /api/v1/adapters/instances              # create new instance (hot-start)
GET    /api/v1/adapters/instances/{id}         # get one instance
PATCH  /api/v1/adapters/instances/{id}         # update config + hot-reload
DELETE /api/v1/adapters/instances/{id}         # stop + delete (cascades bindings)
POST   /api/v1/adapters/instances/{id}/test    # test connection (ephemeral, no persist)
POST   /api/v1/adapters/instances/{id}/restart # stop + reconnect
```

**Create an adapter instance:**

```bash
# KNX gateway "Erdgeschoss"
curl -X POST http://localhost:8080/api/v1/adapters/instances \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "adapter_type": "KNX",
    "name": "KNX Erdgeschoss",
    "config": {
      "connection_type": "tunneling",
      "host": "192.168.114.44",
      "port": 3674,
      "individual_address": "1.1.210"
    }
  }'

# Second KNX gateway "Obergeschoss"
curl -X POST http://localhost:8080/api/v1/adapters/instances \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "adapter_type": "KNX",
    "name": "KNX Obergeschoss",
    "config": {
      "connection_type": "tunneling",
      "host": "192.168.114.45",
      "port": 3674,
      "individual_address": "1.1.211"
    }
  }'
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

The RingBuffer is a circular debug log of the last N value changes. It runs on disk by default (survives restarts) and can be switched to memory at runtime.

```
GET  /api/v1/ringbuffer?q=&adapter=&from=&limit=   # query entries (search by name or UUID)
GET  /api/v1/ringbuffer/stats                       # entry count, oldest/newest ts
POST /api/v1/ringbuffer/config                      # reconfigure (storage, max_entries)
```

The `q` parameter accepts both DataPoint names (partial match) and UUIDs.

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
  "ts": "2026-03-27T10:23:41.123Z",
  "source_adapter": "KNX"
}
```

---

## Logic Engine

### Übersicht

Die Logic Engine ermöglicht das Erstellen von Automatisierungslogiken auf einer visuellen Tapete (Canvas) ohne Programmierung. Logik-Graphen werden als **Blöcke** (Nodes) und **Verbindungen** (Edges) dargestellt.

**Wie es funktioniert:**

1. Jeder Graph wird in der Datenbank als JSON gespeichert (`logic_graphs`-Tabelle).
2. Der **LogicManager** abonniert alle `DataValueEvent`-Ereignisse am EventBus.
3. Ändert sich ein DataPoint, der von einem **DP LESEN**-Block beobachtet wird, führt der Manager den zugehörigen Graph automatisch aus.
4. Der **GraphExecutor** verarbeitet alle Blöcke in topologischer Reihenfolge (Kahn-Algorithmus).
5. **DP SCHREIBEN**-Blöcke publizieren das Ergebnis als `DataValueEvent` — damit werden Registry, Ring-Buffer, MQTT und WebSocket automatisch benachrichtigt.

Der Graph wird **auch manuell** über den „▶ Ausführen"-Button oder die API ausgelöst. In diesem Fall liest der Manager die aktuellen Werte aller DP-LESEN-Blöcke aus der Registry.

---

### Blocktypen

#### Kategorie: Konstante

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **Festwert** | — | Wert | Gibt einen festen Wert aus (Zahl, Bool oder Text). Nützlich als Schwellwert oder Referenz. Config: `Wert` + `Datentyp` (number / bool / string). |

#### Kategorie: Logik

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **AND** | A, B | Out | Wahr wenn **alle** Eingänge wahr sind. |
| **OR** | A, B | Out | Wahr wenn **mindestens ein** Eingang wahr ist. |
| **NOT** | In | Out | Invertiert den Eingang. |
| **XOR** | A, B | Out | Wahr wenn **genau ein** Eingang wahr ist. |
| **Vergleich** | A, B | Ergebnis (Bool) | Vergleicht zwei Werte. Config: Operator `>` `<` `=` `>=` `<=` `!=`. |
| **Hysterese** | Wert | Out (Bool) | Schaltet bei Überschreitung von `Schwelle ON` ein, erst bei Unterschreitung von `Schwelle OFF` wieder aus. Zustand wird graph-seitig gespeichert. |

#### Kategorie: DataPoint

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **DP Lesen** | — | Wert, Geändert | Liest einen DataPoint. Löst den Graph bei jeder Wertänderung automatisch aus. Optionale Filter & Transformation (siehe unten). |
| **DP Schreiben** | Wert, Trigger | — | Schreibt einen Wert in einen DataPoint. Publiziert `DataValueEvent` → Ring-Buffer, MQTT, WebSocket werden benachrichtigt. Optionale Filter & Transformation. |

#### Kategorie: Mathematik

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **Formel** | a, b | Ergebnis | Berechnet einen Ausdruck. Variablen `a` und `b` entsprechen den Eingängen. Beispiel: `a * 2 + b`. Alle Formelfunktionen sind verfügbar (siehe [Formel-Referenz](#formel-referenz)). |
| **Skalieren** | Wert | Ergebnis | Lineares Mapping: `[in_min … in_max]` → `[out_min … out_max]`. |

#### Kategorie: Timer

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **Verzögerung** | Trigger | Trigger | Verzögert ein Signal um N Sekunden (async, wird vom LogicManager verwaltet). |
| **Impuls** | Trigger | Out | Gibt `true` für N Sekunden aus, dann `false` (async). |
| **Zeitplan** | — | Trigger | Löst nach einem Cron-Ausdruck aus, z. B. `0 7 * * *` = täglich 07:00. |

#### Kategorie: Skript

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **Python Script** | a, b, c | Ergebnis | Führt Python-Code aus. `inputs['a']`, `inputs['b']`, `inputs['c']` enthalten die Eingangswerte. `result = ...` definiert den Ausgangswert. Eingeschränkte Sandbox (`abs`, `min`, `max`, `round`, `math`, kein IO/Import). |

#### Kategorie: MCP

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **MCP Tool** | Trigger, Input | Ergebnis, Fertig | Ruft ein MCP-Tool auf einem externen MCP-Server auf. Config: `server_url`, `tool_name`. |

---

### DataPoint-Blöcke: Filter & Transformation

Beide DataPoint-Blöcke besitzen drei Konfigurations-Tabs: **Verbindung**, **Transformation** und **Filter**.

#### Transformation (Tab: Transformation •)

Das Ergebnis-Tab zeigt einen Punkt (•) sobald eine Formel aktiv ist.

| Feld | Beschreibung |
|---|---|
| **Formel** | Mathematischer Ausdruck, der auf den Wert angewendet wird. Variable: **`x`** (konsistent mit Binding-Formeln). |

Vordefinierte Presets:

| Preset | Formel |
|---|---|
| × 86.400 (Tage → Sekunden) | `x * 86400` |
| × 3.600 (Stunden → Sekunden) | `x * 3600` |
| × 1.440 (Tage → Minuten) | `x * 1440` |
| × 1.000 | `x * 1000` |
| × 100 | `x * 100` |
| × 60 (Minuten → Sekunden) | `x * 60` |
| × 10 | `x * 10` |
| ÷ 10 (Festkomma) | `round(x / 10, 1)` |
| ÷ 60 (Sekunden → Minuten) | `x / 60` |
| ÷ 100 (Festkomma) | `round(x / 100, 2)` |
| ÷ 1.000 (Festkomma) | `round(x / 1000, 3)` |
| ÷ 1.440 (Minuten → Tage) | `x / 1440` |
| ÷ 3.600 (Sekunden → Stunden) | `x / 3600` |
| ÷ 86.400 (Sekunden → Tage) | `x / 86400` |

Die Formel wird **bei DP LESEN** auf den Rohwert angewendet, bevor er in den Graph einfließt. **Bei DP SCHREIBEN** wird sie auf den Eingangswert angewendet, bevor er in den DataPoint geschrieben wird.

#### Filter (Tab: Filter •)

Das Filter-Tab zeigt einen Punkt (•) sobald ein Filter aktiv ist.

**DP Lesen — verfügbare Filter:**

| Feld | Beschreibung |
|---|---|
| **Min. Zeitabstand** | Mindestabstand zwischen zwei Graph-Auslösungen. Einheit: `ms` / `s` / `min` / `h`. Auslösungen innerhalb des Intervalls werden verworfen. |
| **Nur bei Wertänderung** | Graph nur auslösen wenn der neue Wert vom zuletzt gesendeten abweicht (Duplikat-Filter). |
| **Mindeständerung (absolut)** | Graph nur auslösen wenn `|neu − alt| ≥ Schwelle`. Nur für numerische Werte. |
| **Mindeständerung (%)** | Graph nur auslösen wenn die relative Abweichung `≥ Schwelle %`. Nur für numerische Werte. |

> Beide Delta-Filter müssen erfüllt sein wenn beide konfiguriert sind.

**DP Schreiben — verfügbare Filter:**

| Feld | Beschreibung |
|---|---|
| **Min. Zeitabstand** | Mindestabstand zwischen zwei Schreibvorgängen. Einheit: `ms` / `s` / `min` / `h`. |
| **Nur bei Wertänderung** | Nicht schreiben wenn der Wert identisch mit dem zuletzt geschriebenen ist. |
| **Mindeständerung (absolut)** | Nur schreiben wenn `|neu − letzter Schreibwert| ≥ Schwelle`. |

**Blockbadge:** Wenn Filter oder Formel konfiguriert sind, erscheint ein amber ⊘-Symbol im Block-Header.

---

### Formel-Referenz

In **allen** Formelfeldern (DP Lesen, DP Schreiben, Formel-Block, Binding `value_formula`) gilt:

- **Variable:** `x` (Eingangswert, immer als `float` übergeben)
- **Keine Imports nötig** — alle Funktionen sind direkt verfügbar

#### Verfügbare Funktionen

| Funktion | Beispiel | Beschreibung |
|---|---|---|
| `abs(x)` | `abs(x - 50)` | Absolutbetrag |
| `round(x, n)` | `round(x, 2)` | Runden auf n Dezimalstellen |
| `min(x, y)` | `min(x, 100)` | Minimum zweier Werte |
| `max(x, y)` | `max(x, 0)` | Maximum zweier Werte |
| `sqrt(x)` | `sqrt(x)` | Quadratwurzel |
| `floor(x)` | `floor(x)` | Abrunden auf ganze Zahl |
| `ceil(x)` | `ceil(x)` | Aufrunden auf ganze Zahl |
| `math.log(x)` | `math.log(x)` | Natürlicher Logarithmus |
| `math.log10(x)` | `math.log10(x)` | Zehner-Logarithmus |
| `math.log(x, b)` | `math.log(x, 2)` | Logarithmus zur Basis b |
| `math.sin(x)` | `math.sin(x)` | Sinus (Bogenmass) |
| `math.cos(x)` | `math.cos(x)` | Kosinus |
| `math.pow(x, n)` | `math.pow(x, 2)` | Potenz (x^n) |
| `math.exp(x)` | `math.exp(x)` | e^x |
| `math.pi` | `x * math.pi / 180` | Kreiszahl π ≈ 3.14159 |
| `math.e` | `math.e ** x` | Euler'sche Zahl e ≈ 2.71828 |

> **Alle weiteren `math.*`-Funktionen** aus dem Python-Standardmodul sind ebenfalls verfügbar (`math.atan`, `math.degrees`, `math.radians`, `math.hypot`, etc.).

#### Beispiele

| Zweck | Formel |
|---|---|
| Bereich begrenzen (Clamp) | `max(0, min(100, x))` |
| Fahrenheit → Celsius | `(x - 32) * 5 / 9` |
| Celsius → Fahrenheit | `x * 9 / 5 + 32` |
| Wh → kWh | `x / 1000` |
| Auf halbe Stufen runden | `round(x * 2) / 2` |
| Prozent aus Rohwert 0–4095 | `x / 4095 * 100` |
| Negativen Wert verwerfen | `max(0, x)` |
| Absolutwert einer Abweichung | `abs(x - 230)` |

#### Formel-Block (Eingänge a, b)

Der **Formel**-Block hat zwei Eingänge `a` und `b` (keine `x`-Variable):

```
a * 2 + b        # Eingang a verdoppeln, b addieren
max(a, b)        # Größeren der beiden Werte nehmen
round((a + b) / 2, 1)   # Mittelwert, 1 Dezimalstelle
abs(a - b)       # Absolute Differenz
```

---

### Typ-Koercion

Die Logic Engine konvertiert Werte automatisch wenn Eingang und Ausgang verschiedene Typen haben:

| Von | Nach | Regel |
|---|---|---|
| `bool` | `float` | `True → 1.0`, `False → 0.0` |
| `float` / `int` | `bool` | `0 → False`, alle anderen → `True` |
| `str "123"` | `float` | `float("123") = 123.0` |
| `str "true"` / `"on"` / `"1"` | `bool` | `True` |
| `str "false"` / `"off"` / `"0"` / `""` | `bool` | `False` |
| `None` | `float` | `0.0` (Default) |
| `None` | `bool` | `False` |

Verbindungen zwischen unterschiedlichen Blocktypen funktionieren damit **immer**. Nur bei völlig inkompatiblen Typen (Freitext-String in numerischen Vergleich) fällt der Block auf String-Vergleich zurück.

---

### Debug-Modus

Der Debug-Modus zeigt nach einer Graph-Ausführung die berechneten Zwischenwerte direkt auf den Blöcken an.

**Bedienung:**
1. Graph öffnen
2. **🔍 Debug**-Button in der Toolbar klicken → Button leuchtet amber
3. **▶ Ausführen** klicken
4. Jeder Block zeigt unten ein gelbes Werteband mit seinen Ausgangswerten

**Anzeigeformat:**

| Typ | Darstellung |
|---|---|
| Boolean `true` | `out=✓` |
| Boolean `false` | `out=✗` |
| Zahl | `value=230.45` (max. 5 signifikante Stellen) |
| Text | `value=Hallo` (max. 18 Zeichen) |
| Kein Wert | `value=—` |

Debug deaktivieren → alle Wertebänder verschwinden.

> **Hinweis:** Manuelle Ausführung liest die aktuellen Registry-Werte für alle DP-LESEN-Blöcke. Automatische Ausführung (durch DataValueEvent) verwendet den tatsächlichen Ereigniswert.

---

### Logic Engine API

```
GET    /api/v1/logic/node-types              # alle Blocktyp-Definitionen
GET    /api/v1/logic/graphs                  # alle Logic-Graphen auflisten
POST   /api/v1/logic/graphs                  # neuen Graphen erstellen
GET    /api/v1/logic/graphs/{id}             # Graphen laden (inkl. flow_data)
PUT    /api/v1/logic/graphs/{id}             # Graphen speichern (Canvas-Stand)
PATCH  /api/v1/logic/graphs/{id}             # Teilupdate (name / enabled)
DELETE /api/v1/logic/graphs/{id}             # Graphen löschen
POST   /api/v1/logic/graphs/{id}/run         # manuell ausführen
```

**Graph-Objekt:**

```json
{
  "id": "uuid",
  "name": "Lüftungssteuerung",
  "description": "Schaltet Lüftung bei CO2 > 1000 ppm ein",
  "enabled": true,
  "flow_data": {
    "nodes": [ ... ],
    "edges": [ ... ]
  },
  "created_at": "2026-03-28T10:00:00Z",
  "updated_at": "2026-03-28T10:00:00Z"
}
```

**Manuell ausführen:**

```bash
curl -X POST http://localhost:8080/api/v1/logic/graphs/{id}/run \
  -H "Authorization: Bearer {token}"

# Response:
{
  "status": "ok",
  "outputs": {
    "node-uuid-1": {"value": 230.5, "changed": false},
    "node-uuid-2": {"result": 461.0},
    "node-uuid-3": {"out": true}
  }
}
```

Der `outputs`-Response enthält die Ausgangswerte aller Nodes — dieselben Werte, die der Debug-Modus in der GUI anzeigt.

---

## Adapter Configuration

Each adapter type can be instantiated multiple times. All instances are managed via `POST /api/v1/adapters/instances` (or through the GUI under **Adapters → + Neue Instanz**).

### KNX Adapter

**Adapter config** (`config` field when creating/updating an instance):

```json
{
  "connection_type": "tunneling",
  "host": "192.168.114.44",
  "port": 3674,
  "individual_address": "1.1.210",
  "local_ip": null
}
```

| Field | Values | Description |
|---|---|---|
| `connection_type` | `tunneling` \| `routing` | Tunneling = unicast to gateway; Routing = IP multicast |
| `host` | IP address | KNX/IP gateway IP |
| `port` | default `3671` | KNX/IP port (ETS default); some gateways use `3674` |
| `individual_address` | e.g. `1.1.210` | Own KNX individual address |
| `local_ip` | IP or null | Required for routing mode |

**Binding config:**

```json
{
  "group_address": "27/6/6",
  "dpt_id": "DPT9.001",
  "state_group_address": null
}
```

| Field | Description |
|---|---|
| `group_address` | KNX group address (3-level notation) |
| `dpt_id` | DPT identifier — see table below |
| `state_group_address` | Optional feedback GA for `DEST` bindings (reads back the set value) |

**Supported DPTs:**

| DPT | Size | Value type | Typical use |
|---|---|---|---|
| `DPT1.001` | 1 bit | bool | Switch on/off |
| `DPT1.008` | 1 bit | bool | Up/Down |
| `DPT1.009` | 1 bit | bool | Open/Close |
| `DPT5.001` | 8 bit | int (0–255) | Dimming 0–100 % |
| `DPT5.003` | 8 bit | int (0–255) | Angle 0–360° |
| `DPT6.001` | 8 bit | int (−128…127) | Relative value |
| `DPT7.001` | 16 bit | int (0–65535) | Pulse count |
| `DPT8.001` | 16 bit | int (±32767) | Relative value |
| `DPT9.001` | 2 byte float | float | Temperature (°C) |
| `DPT9.002` | 2 byte float | float | Illuminance (lx) |
| `DPT9.004` | 2 byte float | float | Speed (m/s) |
| `DPT9.007` | 2 byte float | float | Humidity (%) |
| `DPT9.010` | 2 byte float | float | Power (W) |
| `DPT10.001` | 3 byte | string `"HH:MM:SS"` | Time of day |
| `DPT11.001` | 3 byte | string `"YYYY-MM-DD"` | Date |
| `DPT12.001` | 32 bit | int (0–4 294 967 295) | Energy counter |
| `DPT13.001` | 32 bit | int (±2 147 483 647) | Counter value |
| `DPT14.019` | 32 bit float | float | Electrical current |
| `DPT14.027` | 32 bit float | float | Energy (J) |
| `DPT16.000` | 14 byte | string | ASCII text |
| `DPT16.001` | 14 byte | string | ISO 8859-1 text |
| `DPT18.001` | 1 byte | int | Scene Control (negative = learn mode) |
| `DPT19.001` | 8 byte | string ISO 8601 | Date and Time |
| `DPT219.001` | 2 byte | int (0–65535) | AlarmInfo (mode + status bits) |
| Unknown DPT | — | — | Falls back to `UNKNOWN` type (no crash) |

DPT9 uses the KNX EIS5 format: `SEEEEMMM MMMMMMMM`, `value = 0.01 × M × 2^E`.
DPT10/11 encode/decode as ISO strings; `x` in formulas is the numeric timestamp if combined with `value_formula`.

---

### Modbus TCP Adapter

**Adapter config:**

```json
{
  "host": "192.168.115.31",
  "port": 1502,
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
| `byte_order` | `big` \| `little` | Byte order within each 16-bit register |
| `word_order` | `big` \| `little` | Word order for multi-register values (32/64-bit) |
| `poll_interval` | float (seconds) | For `SOURCE` / `BOTH` bindings |

**`byte_order` — Byte-Reihenfolge innerhalb eines Registers (16 bit):**

Ein Modbus-Register enthält 2 Bytes. Bei `big` (Standard, Motorola) steht das höherwertige Byte zuerst:

```
big endian:    Register = [ High-Byte | Low-Byte ]   → 0x0A0B = 2571
little endian: Register = [ Low-Byte  | High-Byte ]  → 0x0A0B = 2826
```

**`word_order` — Word-Reihenfolge bei Multi-Register-Typen (32/64 bit):**

`float32`, `uint32`, `int32` belegen **2 aufeinanderfolgende Register**. `word_order` bestimmt, ob das höherwertige Register zuerst oder zuletzt kommt:

```
big (Standard):  Register[0] = High-Word,  Register[1] = Low-Word
                 → 0x44F6_0000 = 1971.0 (IEEE 754)

little:          Register[0] = Low-Word,   Register[1] = High-Word
                 → gleiche Bytes, andere Reihenfolge
```

> **Faustregel:** Die meisten SPS (Siemens, Beckhoff, …) verwenden `big`/`big`. Einige ältere Geräte (bestimmte Wechselrichter, Energiezähler) verwenden `big`/`little` (CDAB-Format). Wenn der gelesene Wert völlig falsch erscheint, zuerst `word_order` auf `little` wechseln.

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

Use `POST /api/v1/adapters/instances/{id}/test` to trigger `scan_sensors()` and list all detected sensor IDs.

---

### MQTT Adapter (external broker)

Connects to an **external** MQTT broker (distinct from the internal openTWS Mosquitto). Supports authentication and bidirectional bindings.

**Adapter config:**

```json
{
  "host": "192.168.114.44",
  "port": 1883,
  "username": "username",
  "password": "password"
}
```

| Field | Description |
|---|---|
| `host` | External broker IP or hostname |
| `port` | Default `1883` |
| `username` | Optional authentication username |
| `password` | Optional authentication password |

**Binding config:**

```json
{
  "topic": "sensors/living_room/temperature",
  "publish_topic": "actuators/living_room/setpoint",
  "retain": false
}
```

| Field | Description |
|---|---|
| `topic` | Topic to **subscribe** to (for `SOURCE` / `BOTH` bindings) |
| `publish_topic` | Topic to **publish** to (for `DEST` / `BOTH` bindings). Defaults to `topic` if omitted |
| `retain` | Set MQTT retain flag on published messages |

**Example — bridge Home Assistant temperature to KNX:**

```bash
# 1. Create MQTT adapter instance
curl -X POST http://localhost:8080/api/v1/adapters/instances \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "adapter_type": "MQTT",
    "name": "Home Assistant",
    "config": {"host": "192.168.114.44", "port": 1883, "username": "twsmqtt", "password": "twsmqtt"}
  }'

# 2. Add SOURCE binding on the DataPoint (subscribe to HA topic)
curl -X POST http://localhost:8080/api/v1/datapoints/{DP_ID}/bindings \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{
    "adapter_instance_id": "MQTT-INSTANCE-UUID",
    "direction": "SOURCE",
    "config": {"topic": "homeassistant/sensor/living_room_temp/state"}
  }'
```

When a value arrives on the MQTT topic, the WriteRouter automatically propagates it to all DEST bindings of the same DataPoint (e.g. a KNX group address).

---

## MQTT Topics

openTWS uses a **hybrid topic strategy** on the internal Mosquitto:

| Topic | Description |
|---|---|
| `dp/{uuid}/value` | Stable — never changes, safe for automations. Published with `retain=true` |
| `dp/{uuid}/raw` | Raw value without unit/quality wrapper |
| `dp/{uuid}/set` | Write to this topic to trigger `adapter.write()` |
| `dp/{uuid}/status` | Adapter connection status (retain=true) |
| `alias/{tag}/{name}/value` | Human-readable, browsable (optional, requires `mqtt_alias`) |

**Payload format (`dp/{uuid}/value`):**

```json
{
  "v": 21.4,
  "u": "°C",
  "t": "2026-03-27T10:23:41.123Z",
  "q": "good"
}
```

| Key | Type | Description |
|---|---|---|
| `v` | any | Value (type-dependent serialization) |
| `u` | string \| null | Unit from DataPoint |
| `t` | string | ISO 8601 timestamp with milliseconds |
| `q` | string | `good` \| `bad` \| `uncertain` — see below |

**Quality labels (`q`):**

| Value | Meaning |
|---|---|
| `good` | Value was received/read successfully. The adapter is connected and the data is fresh. |
| `bad` | Adapter is disconnected or the read/receive failed (timeout, CRC error, exception code, …). The value field may contain the last known value or null. |
| `uncertain` | Value exists but freshness or accuracy is questionable — e.g. adapter is reconnecting, value is stale beyond a threshold, or the source flagged it as approximate. |

> Automations should check `q === "good"` before acting on a value.

**Writing a value via MQTT:**
```bash
mosquitto_pub -t "dp/550e8400-e29b-41d4-a716-446655440000/set" \
  -m '{"v": 22.5}'
```

**Writing with MQTT Explorer:**
- Topic: `dp/{uuid}/set`
- Payload: `{"v": true}` (boolean) or `{"v": 21.5}` (float) or `{"v": "text"}` (string)
- QoS: 0 or 1

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
│   └── database.py             # aiosqlite wrapper, migration system (V1–V5)
│
├── models/
│   ├── types.py                # DataTypeRegistry, 8 built-in types
│   ├── datapoint.py            # DataPoint, DataPointCreate, DataPointUpdate
│   └── binding.py              # AdapterBinding (with adapter_instance_id)
│
├── core/
│   ├── converter.py            # Type conversion with ConversionResult
│   ├── event_bus.py            # Async EventBus, DataValueEvent, AdapterStatusEvent
│   ├── mqtt_client.py          # aiomqtt wrapper (split pub/sub), topic helpers
│   ├── registry.py             # DataPointRegistry, in-memory ValueState
│   └── write_router.py         # dp/+/set → write() + SOURCE→DEST bridge
│
├── adapters/
│   ├── base.py                 # AdapterBase ABC (instance_id, name support)
│   ├── registry.py             # @register, start_all/stop_all, multi-instance mgmt
│   ├── modbus_base.py          # Shared Modbus binding config + codec
│   ├── knx/
│   │   ├── adapter.py          # KnxAdapter (xknx 3.x)
│   │   └── dpt_registry.py     # DPTRegistry (37 DPTs)
│   ├── modbus_tcp/
│   │   └── adapter.py          # ModbusTcpAdapter
│   ├── modbus_rtu/
│   │   └── adapter.py          # ModbusRtuAdapter
│   ├── onewire/
│   │   └── adapter.py          # OneWireAdapter
│   └── mqtt/
│       └── adapter.py          # MqttAdapter (external broker, split pub/sub loops)
│
├── api/
│   ├── auth.py                 # JWT + API Key auth, user management endpoints
│   ├── router.py               # Aggregates all sub-routers
│   └── v1/
│       ├── datapoints.py       # CRUD + pagination
│       ├── bindings.py         # Binding CRUD (adapter_instance_id), live reload
│       ├── search.py           # Server-side filtered search
│       ├── adapters.py         # Instance CRUD + type schema + connection test
│       ├── system.py           # Health, adapter status, datatypes
│       ├── websocket.py        # WebSocketManager, selective subscribe
│       ├── ringbuffer.py       # RingBuffer query (name search) + config
│       ├── history.py          # History query + aggregate
│       └── config.py           # Import / Export
│
├── ringbuffer/
│   └── ringbuffer.py           # SQLite-backed circular buffer (disk default)
│
└── history/
    ├── sqlite_plugin.py        # History writer + query + aggregate (SQLite)
    └── influxdb_plugin.py      # InfluxDB plugin stub
```

### Database schema

The database uses a version-based migration system. Current version: **V5**.

| Table | Description |
|---|---|
| `datapoints` | All DataPoints |
| `adapter_bindings` | Bindings between DataPoints and adapter instances (includes `adapter_instance_id`) |
| `adapter_instances` | **New V5** — one row per adapter instance (UUID PK, N instances per type) |
| `adapter_configs` | Legacy flat config (migrated to `adapter_instances` on V5 upgrade) |
| `users` | User accounts (username, PBKDF2 password hash, is_admin) |
| `api_keys` | API key names + SHA-256 hashes |
| `history_values` | Time-series value log |
| `schema_version` | Applied migration versions |

> **Upgrade note:** When upgrading from a pre-V5 installation, Migration V5 automatically creates one `adapter_instances` entry per existing `adapter_configs` row and links all bindings to the new instance IDs.

### Adding a new adapter

1. Create `opentws/adapters/{name}/adapter.py`
2. Subclass `AdapterBase`, decorate with `@register`
3. Define `adapter_type`, `config_schema`, `binding_config_schema`
4. Implement `connect()`, `disconnect()`, `read()`, `write()`
5. Import the module in `main.py` startup (one line)

No changes to the core, the API, or the database are needed. The new adapter type immediately appears in the GUI under **Adapters → + Neue Instanz**.

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
