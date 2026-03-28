# Changelog

All notable changes to OpenTWS are documented here.
Format: [Keep a Changelog](https://keepachangelog.com/en/1.0.0/)

---

## [Unreleased — visu branch] — 2026-03-28

### Added

**Logic Engine (Phase 7 — visu branch)**

- `opentws/logic/` — vollständiges Backend-Modul:
  - `models.py`: Pydantic-Modelle `FlowData`, `LogicNode`, `LogicEdge`, `LogicGraphCreate/Update/Out`, `NodeTypeDef`, `NodeTypePort`
  - `node_types.py`: 15 eingebaute Blocktyp-Definitionen in 6 Kategorien:
    - **Konstante:** `const_value` (Festwert: Zahl / Bool / Text)
    - **Logik:** `and`, `or`, `not`, `xor`, `compare` (Vergleich mit Operator-Config), `hysteresis` (Schwellwert-Schalter mit persistentem Zustand)
    - **DataPoint:** `datapoint_read`, `datapoint_write` (mit Filter & Transformation)
    - **Mathematik:** `math_formula` (Formel mit Variablen a, b), `math_map` (lineares Skalieren)
    - **Timer:** `timer_delay` (Verzögerung), `timer_pulse` (Impuls), `timer_cron` (Zeitplan)
    - **Skript:** `python_script` (eingeschränkte Python-Sandbox)
    - **MCP:** `mcp_tool`
  - `executor.py`: `GraphExecutor` — topologische Sortierung (Kahn), alle Node-Evaluatoren, `_safe_eval` für Formeln, `_run_script` für Python-Sandbox
    - `_to_num()`: universelle Koercion zu `float` (bool→1/0, str→float, None→0.0)
    - `_to_bool()`: universelle Koercion zu `bool` ("false"/"off"/"0"→False)
    - `_safe_eval()`: alle `math.*`-Funktionen + `abs`, `round`, `min`, `max` als Builtins
  - `manager.py`: `LogicManager` — EventBus-Integration, automatische Graph-Ausführung bei `DataValueEvent`
    - Read-seitige Filter: `trigger_on_change`, `min_delta`, `min_delta_pct`, `throttle_value`+`throttle_unit`
    - Write-seitige Filter: `only_on_change`, `min_delta`, `throttle_value`+`throttle_unit`
    - State-Tracking pro Graph/Node für Throttle und Delta-Filter
    - Manuelle Ausführung liest aktuelle Registry-Werte für alle DP-LESEN-Nodes
    - DP-SCHREIBEN publiziert `DataValueEvent` → Registry, Ring-Buffer, MQTT, WebSocket
  - `__init__.py`: `LogicManager`, `get_logic_manager`, `init_logic_manager`

- `opentws/api/v1/logic.py`: vollständige REST-API:
  - `GET /api/v1/logic/node-types` — alle Blocktyp-Definitionen
  - `GET/POST /api/v1/logic/graphs` — Liste + Erstellen
  - `GET/PUT/PATCH/DELETE /api/v1/logic/graphs/{id}` — CRUD
  - `POST /api/v1/logic/graphs/{id}/run` — manuelle Ausführung, Response enthält alle Node-Ausgangswerte

- `opentws/db/database.py`: Migration V12 — `logic_graphs`-Tabelle:
  ```sql
  CREATE TABLE logic_graphs (
    id TEXT PRIMARY KEY, name TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    enabled INTEGER NOT NULL DEFAULT 1,
    flow_data TEXT NOT NULL DEFAULT '{"nodes":[],"edges":[]}',
    created_at TEXT NOT NULL, updated_at TEXT NOT NULL
  );
  ```

- GUI (`gui/src/`) — Vue Flow Canvas:
  - `@vue-flow/core`, `@vue-flow/background`, `@vue-flow/controls`, `@vue-flow/minimap` als Dependencies
  - `stores/logic.js`: Pinia-Store für Graphen und Blocktypen
  - `api/client.js`: `logicApi` mit allen Endpunkten
  - `views/LogicView.vue`: Haupt-Canvas mit Toolbar (Speichern, Ausführen, Debug, Graph-Auswahl), Drag-and-drop, `@connect`-Handler mit `addEdge()`
  - `components/logic/NodePalette.vue`: Drag-and-drop Block-Palette, gruppiert nach Kategorie
  - `components/logic/NodeConfigPanel.vue`: Konfigurations-Panel mit 3 Tabs für DataPoint-Blöcke:
    - **Verbindung**: DataPoint-Auswahl mit Live-Suche
    - **Transformation •**: Preset-Dropdown (×/÷ Presets) + Formel-Eingabe, Variable `x`
    - **Filter •**: Drosselung mit Einheit-Dropdown (ms/s/min/h), Checkbox, Absolut + Relativ (%)
  - `components/logic/nodes/GenericNode.vue`: universeller Block für 13 Typen, berechnete Kartenhöhe für exakte Handle-Ausrichtung, Debug-Werteband
  - `components/logic/nodes/DatapointNode.vue`: spezialisierter Block mit amber ⊘-Badge bei aktivem Filter
  - `components/logic/nodes/PythonScriptNode.vue`: Block mit Code-Vorschau

**Import / Export — Erweiterungen**

- `opentws/api/v1/config.py`:
  - Bindings: `value_formula`, `send_throttle_ms`, `send_on_change`, `send_min_delta`, `send_min_delta_pct` werden jetzt korrekt exportiert und importiert (waren zuvor fehlend)
  - KNX Group Addresses werden exportiert und bei Wiederherstellung per Upsert eingefügt
  - Export-Version `"3"`, `ImportResult` enthält `knx_group_addresses_upserted`
- GUI: Tab umbenannt in „Sicherung/Wiederherstellung", Dateiname `opentws-backup.json`

**Bug Fixes**

- `AdaptersView.vue`: Löschen-Dialog war unsichtbar (`v-if` ohne `v-model` → Modal nie gerendert). Fix: `v-model="showDeleteConfirm"`-Pattern
- `logic/manager.py`: `registry.update_value()` existiert nicht → ersetzt durch `EventBus.publish(DataValueEvent)` (damit werden jetzt alle Subscriber korrekt benachrichtigt)
- `logic/executor._safe_eval()`: `abs`, `round`, `min`, `max` waren nicht verfügbar (nur `math.*`). Jetzt explizit als Builtins hinzugefügt
- `logic/executor`: Variable `value`/`v` → **`x`** (konsistent mit Binding-Formeln)
- `logic/manager`: `throttle_ms` (rohes ms-Feld) → `throttle_value` + `throttle_unit` (Zahl + Einheit getrennt gespeichert, bei Laufzeit konvertiert)

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
