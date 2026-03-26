# Changelog

All notable changes to OpenTWS are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [0.1.0] — 2026-03-26

### Added

**Phase 1 — Foundation**
- `config.py`: pydantic-settings v2 with YAML + environment variable loading
  - Env prefix `OPENTWS_`, nested delimiter `__`
  - Priority: env vars > config.yaml > built-in defaults
- `db/database.py`: async SQLite wrapper (aiosqlite), WAL mode, FK constraints
  - Version-based migration system (V1–V4)
  - V1: `datapoints`, `adapter_bindings`, `api_keys`, `users` tables + indexes
  - V2: `adapter_configs` table
  - V3: `history_values` table
  - V4: `is_admin` column on `users`
- `models/types.py`: `DataTypeRegistry` with 8 built-in types: `UNKNOWN`, `BOOLEAN`, `INTEGER`, `FLOAT`, `STRING`, `DATE`, `TIME`, `DATETIME`
- `models/datapoint.py`: `DataPoint` model with auto-generated `mqtt_topic = dp/{uuid}/value`
- `models/binding.py`: `AdapterBinding` with `direction: SOURCE | DEST | BOTH`
- `core/converter.py`: `ConversionResult(value, loss, loss_description)`, full conversion matrix

**Phase 2 — Core**
- `core/event_bus.py`: async `EventBus` with `DataValueEvent` and `AdapterStatusEvent`
- `core/mqtt_client.py`: aiomqtt wrapper, `{v, u, t, q}` payload, topic helpers
- `core/registry.py`: `DataPointRegistry` with `ValueState`, in-memory + DB-backed
- `adapters/base.py`: `AdapterBase` ABC with `reload_bindings()` hook
- `adapters/registry.py`: `@register` decorator, `start_all()` / `stop_all()`

**Phase 3 — Adapters**
- `adapters/knx/dpt_registry.py`: `DPTRegistry` with 37 DPTs
  - DPT9 EIS5 2-byte float codec (`SEEEEMMM MMMMMMMM`)
  - Unknown DPT → `UNKNOWN` (no crash)
- `adapters/knx/adapter.py`: `KnxAdapter` — Tunneling + Routing, telegram callback
- `adapters/modbus_base.py`: shared `ModbusBindingConfig`, `decode_registers()`, `encode_value()` for all 7 data formats
- `adapters/modbus_tcp/adapter.py`: `ModbusTcpAdapter`, asyncio poll loop per SOURCE binding
- `adapters/modbus_rtu/adapter.py`: `ModbusRtuAdapter`, serial line
- `adapters/onewire/adapter.py`: Linux sysfs reader, graceful degradation on Windows

**Phase 4 — API**
- `api/auth.py`: JWT HS256 (python-jose), PBKDF2-HMAC-SHA256 password hashing (stdlib), API Keys
  - Default user `admin`/`admin` created on first startup with log warning
  - Full user management: `GET/POST /auth/users`, `GET/PATCH/DELETE /auth/users/{username}`, `GET /auth/me`, `POST /auth/me/change-password`
- `api/v1/datapoints.py`: full CRUD + pagination (`DataPointPage`)
- `api/v1/bindings.py`: binding CRUD, validates config against adapter schema, live adapter reload
- `api/v1/search.py`: server-side filtering by name, tag, type, adapter
- `api/v1/adapters.py`: status, JSON schema, connection test, config CRUD
- `api/v1/system.py`: `/health` (no auth), `/adapters`, `/datatypes`
- `api/v1/websocket.py`: `WebSocketManager`, selective subscribe, 60 s keepalive
- `core/write_router.py`: MQTT `dp/+/set` → `adapter.write()` via DB binding lookup

**Phase 5 — Advanced Features**
- `ringbuffer/ringbuffer.py`: SQLite circular buffer (`:memory:` or disk), runtime-switchable via `reconfigure()`
- `history/sqlite_plugin.py`: `history_values` writer, raw query, SQL + Python aggregation (avg/min/max/last × 8 intervals)
- `api/v1/ringbuffer.py`: query, stats, runtime config
- `api/v1/history.py`: raw query + aggregate endpoint
- `api/v1/config.py`: full JSON export + import with upsert semantics

**Phase 6 — Deployment**
- `Dockerfile`: multi-stage build (builder + runtime), `python:3.11-slim`, non-root user `opentws`
- `docker-compose.yml`: OpenTWS + Mosquitto, healthchecks, `OPENTWS_JWT_SECRET` env var
- `mosquitto/mosquitto.conf`: plain MQTT (1883) + WebSocket (9001), persistence enabled
- `.dockerignore`, `.gitignore`, `.env.example`

### Fixed

- pydantic-settings 2.13 renamed `secrets_settings` → `file_secret_settings` in `settings_customise_sources()`. Fixed by using `**kwargs` to absorb the renamed parameter.
- passlib + bcrypt 5.0 incompatible on Python 3.14 (`__about__` removed, password length constraints changed). Replaced with stdlib `hashlib.pbkdf2_hmac` (PBKDF2-HMAC-SHA256, 260 000 iterations, `hmac.compare_digest`).
- `aiosqlite.Connection` has no `execute_fetchone` method. Fixed by using `async with conn.execute() as cur: await cur.fetchone()`.

### Known Limitations

- Web GUI not yet implemented (technology TBD: React / Vue / HTMX)
- `tws2opentws.py` migration CLI deferred
- Single-user role model (admin / non-admin); granular RBAC planned for a later phase
- 1-Wire adapter requires Linux sysfs; Windows is development-only
