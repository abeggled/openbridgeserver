# OpenTWS — Architecture Documentation

**Version:** 0.1 (pre-implementation)  
**Lizenz:** MIT  
**Zielplattform:** Python 3.11+, Linux, ARM Cortex-A72 / x86_64  
**Stand:** 2026-03-26

---

## 1. Projektziel

OpenTWS ist ein Open-Source Multiprotokoll-Server als MIT-lizenzierter Ersatz für den proprietären Timberwolf Server (TWS). Ziel ist eine modulare, erweiterbare Datendrehscheibe für Gebäude- und Industrieautomation, die KNX, Modbus RTU, Modbus TCP und 1-Wire sowie zukünftige Protokolle verbindet.

---

## 2. Systemarchitektur

```
┌─────────────────────────────────────────────────────────────────┐
│                        Protokoll-Adapter                        │
│  ┌──────────────┐ ┌──────────────┐ ┌──────────┐ ┌──────────┐  │
│  │ KNX Adapter  │ │ Modbus RTU   │ │ Modbus   │ │ 1-Wire   │  │
│  │ (xknx)       │ │ (pymodbus)   │ │ TCP      │ │ Adapter  │  │
│  └──────┬───────┘ └──────┬───────┘ └────┬─────┘ └────┬─────┘  │
└─────────┼────────────────┼──────────────┼─────────────┼────────┘
          │                │              │             │
          └────────────────┴──────────────┴─────────────┘
                                    │
┌───────────────────────────────────▼─────────────────────────────┐
│                          Core Engine                            │
│  ┌─────────────────────┐  ┌──────────────┐  ┌───────────────┐  │
│  │  DataPoint Registry │  │ Type         │  │ asyncio       │  │
│  │                     │  │ Converter    │  │ Event Loop    │  │
│  └─────────────────────┘  └──────────────┘  └───────────────┘  │
└──────────────────────────────┬──────────────────────────────────┘
                               │ bidirektional
┌──────────────────────────────▼──────────────────────────────────┐
│                    Mosquitto MQTT Broker                        │
│                    (extern / Docker)                            │
└──────────────────────────────┬──────────────────────────────────┘
                               │
┌──────────────────────────────▼──────────────────────────────────┐
│                        FastAPI Server                           │
│         REST API + WebSocket    /api/v1/...                     │
└──────────┬──────────────────────────────────────────────────────┘
           │
    ┌──────┴──────┐
    │   Web GUI   │  (konsumiert ausschliesslich die API)
    └─────────────┘
```

### Schichtenprinzipien

- **Protokoll-Adapter** sind vollständig entkoppelt. Sie kennen nur ihr eigenes Protokoll und kommunizieren ausschliesslich über definierte interne Interfaces mit dem Core.
- **Core Engine** ist protokollneutral. Er kennt keine Adapter direkt, nur die abstrakte `AdapterBase`-Klasse.
- **Mosquitto** läuft als externer Prozess (bevorzugt Docker). Bei einem Neustart des Python-Servers bleiben alle MQTT-Verbindungen bestehen.
- **FastAPI** ist die einzige Schnittstelle nach aussen. Das Web GUI ist ein normaler API-Konsument ohne Sonderbehandlung.

---

## 3. Technologie-Stack

| Komponente | Bibliothek / Tool | Begründung |
|---|---|---|
| Async-Basis | `asyncio` | Non-blocking I/O für alle Adapter |
| REST + WebSocket API | `FastAPI` + `uvicorn` | Performance, OpenAPI auto-docs |
| Datenvalidierung | `pydantic v2` | Schema-Validierung, JSON-Serialisierung |
| MQTT Client | `aiomqtt` | Async-natives MQTT |
| MQTT Broker | `Mosquitto` (extern) | Battle-tested, Docker-kompatibel |
| KNX | `xknx` | Vollständigste Python KNX-Bibliothek |
| Modbus | `pymodbus` | Async-Support, RTU + TCP |
| 1-Wire | `w1thermsensor` | Standard Linux 1-Wire Interface |
| Datenbank | `SQLite` + `aiosqlite` | Zero-dependency, ARM-optimiert |
| Authentifizierung | `python-jose` (JWT) + API Keys | Dual-Auth-System |
| Konfiguration | `pydantic-settings` + YAML | Env-Vars überschreiben Datei |

---

## 4. Modulstruktur

```
opentws/
├── main.py                    # Einstiegspunkt, Startup/Shutdown
├── config.py                  # Server-Konfiguration (config.yaml + Env-Vars)
│
├── core/
│   ├── registry.py            # DataPoint Registry
│   ├── converter.py           # Type Converter + ConversionResult
│   ├── event_bus.py           # Interner async Event Bus
│   └── mqtt_client.py         # MQTT Publish/Subscribe Wrapper
│
├── models/
│   ├── datapoint.py           # DataPoint Pydantic-Modell
│   ├── binding.py             # AdapterBinding Pydantic-Modell
│   └── types.py               # DataTypeRegistry, DataTypeDefinition
│
├── adapters/
│   ├── base.py                # AdapterBase (ABC)
│   ├── registry.py            # Adapter-Registry (self-registering)
│   ├── knx/
│   │   ├── adapter.py
│   │   └── dpt_registry.py    # KNX DPT-Typen Registry
│   ├── modbus_rtu/
│   │   └── adapter.py
│   ├── modbus_tcp/
│   │   └── adapter.py
│   └── onewire/
│       └── adapter.py
│
├── api/
│   ├── router.py              # FastAPI Router Aggregator
│   ├── auth.py                # JWT + API Key Middleware
│   ├── v1/
│   │   ├── datapoints.py      # /api/v1/datapoints
│   │   ├── bindings.py        # /api/v1/datapoints/{id}/bindings
│   │   ├── search.py          # /api/v1/search
│   │   ├── adapters.py        # /api/v1/adapters
│   │   ├── history.py         # /api/v1/history
│   │   ├── ringbuffer.py      # /api/v1/ringbuffer
│   │   ├── system.py          # /api/v1/system
│   │   ├── config.py          # /api/v1/config (export/import)
│   │   └── websocket.py       # WS /api/v1/ws
│
├── db/
│   ├── database.py            # SQLite Verbindung + Migrations
│   └── migrations/            # Schema-Versionen
│
├── history/
│   ├── base.py                # HistoryPlugin ABC
│   ├── sqlite_plugin.py       # Eingebauter Fallback
│   └── influxdb_plugin.py     # Optionales Plugin
│
├── ringbuffer/
│   └── ringbuffer.py          # RingBuffer (SQLite memory/disk)
│
└── tools/
    └── tws2opentws.py         # TWS Migration CLI-Tool
```

---

## 5. Erweiterbarkeits-Prinzip

Alle zentralen Systeme folgen dem **Registry-Pattern** — neue Komponenten registrieren sich selbst beim Start, der Core enthält kein Hardcoding.

### DataType Registry

```python
class DataTypeDefinition:
    name: str                          # z.B. "FLOAT"
    python_type: type                  # float
    mqtt_serializer: Callable          # value → JSON-string
    mqtt_deserializer: Callable        # JSON-string → value

class DataTypeRegistry:
    _types: dict[str, DataTypeDefinition] = {}

    @classmethod
    def register(cls, definition: DataTypeDefinition): ...

    @classmethod
    def get(cls, name: str) -> DataTypeDefinition: ...
```

### Kern-Datentypen (eingebaut)

| Name | Python-Typ | Anmerkung |
|---|---|---|
| `UNKNOWN` | `bytes` | Fallback, immer vorhanden |
| `BOOLEAN` | `bool` | |
| `INTEGER` | `int` | |
| `FLOAT` | `float` | |
| `STRING` | `str` | |
| `DATE` | `datetime.date` | ISO 8601 Serialisierung |
| `TIME` | `datetime.time` | ISO 8601 Serialisierung |
| `DATETIME` | `datetime.datetime` | ISO 8601 mit Timezone |

### Adapter Registry

```python
class AdapterBase(ABC):
    adapter_type: str                  # z.B. "KNX"
    config_schema: type[BaseModel]     # Pydantic-Schema für /adapters/{type}/schema

    @abstractmethod
    async def connect(self): ...

    @abstractmethod
    async def disconnect(self): ...

    @abstractmethod
    async def read(self, binding: AdapterBinding) -> Any: ...

    @abstractmethod
    async def write(self, binding: AdapterBinding, value: Any): ...
```

### KNX DPT Registry

```python
class DPTDefinition:
    dpt_id: str                        # z.B. "DPT9.001"
    name: str                          # "Temperature"
    data_type: str                     # "FLOAT"
    unit: str                          # "°C"
    encoder: Callable                  # Python-Wert → KNX-Bytes
    decoder: Callable                  # KNX-Bytes → Python-Wert

class DPTRegistry:
    # Unbekannte DPTs → DPTDefinition(data_type="UNKNOWN") — kein Crash
```

### History Plugin

```python
class HistoryPlugin(ABC):
    @abstractmethod
    async def write(self, point: DataPointValue): ...

    @abstractmethod
    async def query(self,
                    datapoint_id: UUID,
                    from_ts: datetime,
                    to_ts: datetime,
                    limit: int) -> list[DataPointValue]: ...

    @abstractmethod
    async def aggregate(self,
                        datapoint_id: UUID,
                        fn: str,           # avg | min | max | last
                        interval: str,     # 1m | 1h | 1d
                        from_ts: datetime,
                        to_ts: datetime) -> list[AggregatedValue]: ...
```

---

## 6. Datenmodell

### DataPoint

```python
class DataPoint(BaseModel):
    id: UUID                           # Systemweit eindeutig
    name: str                          # Freitext, max. 255 Zeichen
    data_type: str                     # Verweis auf DataTypeRegistry
    unit: str | None                   # Einheit für Visualisierung (z.B. "°C")
    tags: list[str]                    # Freie Tags für Gruppierung/Suche
    mqtt_topic: str                    # Auto-generiert: dp/{uuid}/value
    mqtt_alias: str | None             # Optional: alias/{tag}/{name}/value
    created_at: datetime
    updated_at: datetime
```

### AdapterBinding

```python
class AdapterBinding(BaseModel):
    id: UUID
    datapoint_id: UUID                 # FK → DataPoint
    adapter_type: str                  # "KNX" | "MODBUS_RTU" | "MODBUS_TCP" | "ONEWIRE"
    direction: Literal["SOURCE", "DEST", "BOTH"]
    config: dict                       # Adapterspezifisch, validiert via Adapter-Schema
    enabled: bool = True
```

**direction-Semantik:**
- `SOURCE` — Adapter liefert Werte (z.B. KNX Status-GA, 1-Wire Sensor)
- `DEST` — Adapter empfängt Werte (z.B. KNX Schreib-GA, Modbus Write-Register)
- `BOTH` — Lesen und Schreiben über dieselbe Adresse (z.B. Modbus Holding Register)

Ein DataPoint kann mehrere Bindings haben (z.B. Wert von 1-Wire lesen, auf Modbus schreiben).

### MQTT Payload

```json
{
  "v": 21.4,
  "u": "°C",
  "t": "2025-03-26T10:23:41.123Z",
  "q": "good"
}
```

Zusätzlich: `dp/{uuid}/value/raw` → nackter Wert als String (für einfache Konsumenten).

**Quality-Werte:** `good` | `bad` | `uncertain`

### MQTT Topic-Strategie (Hybrid)

```
dp/{uuid}/value       — Maschinen-Topic, stabil, für Automatisierung
dp/{uuid}/value/raw   — Nackter Wert, kein JSON
dp/{uuid}/set         — Schreiben (DEST/BOTH Bindings)
dp/{uuid}/status      — Adapter-Status dieses DataPoints
alias/{tag}/{name}/value  — Alias-Topic, browsbar, für MQTT Explorer / Grafana
```

---

## 7. Typkonvertierung

Konvertierungsverluste werden **still akzeptiert** (keine Laufzeit-Exception, kein Log-Eintrag). Der GUI-Konfigurator wird beim Erstellen von Bindings mit inkompatiblen Typen **visuell gewarnt**.

```python
@dataclass
class ConversionResult:
    value: Any
    loss: bool = False
    loss_description: str = ""   # Nur zur Konfigurationszeit, nicht im Betrieb

# Konvertierungsmatrix (Auswahl)
# FLOAT → INTEGER   : int(value), loss wenn value != int(value)
# FLOAT → BOOLEAN   : bool(value), loss wenn value not in (0.0, 1.0)
# INTEGER → BOOLEAN : bool(value), loss wenn value not in (0, 1)
# STRING → *        : immer verlustbehaftet, explizit markiert
```

---

## 8. Konfiguration

### Ebene 1 — config.yaml (Startup)

```yaml
server:
  host: 0.0.0.0
  port: 8080
  log_level: INFO

mqtt:
  host: localhost
  port: 1883
  username: null
  password: null

database:
  path: /data/opentws.db
  history_plugin: sqlite   # sqlite | influxdb | timescaledb | questdb

ringbuffer:
  storage: memory           # memory | disk
  max_entries: 10000

security:
  jwt_secret: changeme      # In Produktion: aus Env-Var
  jwt_expire_minutes: 1440
```

Umgebungsvariablen überschreiben config.yaml:
```
OPENTWS_MQTT_HOST=192.168.1.10
OPENTWS_DB_PATH=/mnt/data/opentws.db
OPENTWS_SECURITY_JWT_SECRET=...
```

### Ebene 2 — SQLite Datenbank (Laufzeit)

Alle DataPoints, Bindings, Adapter-Configs, API Keys, User, RingBuffer-Config und History-Plugin-Config werden in der Datenbank gespeichert. Änderungen sind sofort aktiv — kein Neustart nötig.

---

## 9. Authentifizierung

Dual-Auth-System:

| Methode | Header | Verwendung |
|---|---|---|
| JWT Bearer Token | `Authorization: Bearer {token}` | Web GUI, interaktive Clients |
| API Key | `X-API-Key: {key}` | Automatisierung, externe Systeme |

Endpoints:
```
POST /api/v1/auth/login    → JWT (+ Refresh Token)
POST /api/v1/auth/refresh  → neues JWT
```

---

## 10. WebSocket

```
WS /api/v1/ws?token={jwt}
```

**Client → Server** (subscribe):
```json
{"action": "subscribe", "ids": ["uuid1", "uuid2"]}
```

**Server → Client** (update):
```json
{"id": "uuid1", "v": 21.4, "u": "°C", "t": "...", "q": "good", "old_v": 21.1}
```

Der Client abonniert nur sichtbare DataPoints — kein Broadcast aller Änderungen.

---

## 11. RingBuffer Debug-Log

- Storage: `memory` (SQLite `:memory:`) oder `disk` (SQLite WAL-Mode)
- Umschaltbar zur Laufzeit via `POST /api/v1/ringbuffer/config`
- Automatisches Überschreiben ältester Einträge bei `max_entries`

```python
@dataclass
class RingBufferEntry:
    ts: datetime
    datapoint_id: UUID
    topic: str
    old_value: Any
    new_value: Any
    source_adapter: str
    quality: str
```

Filterbar via `GET /api/v1/ringbuffer/?q=&adapter=&from=&limit=`

---

## 12. Performance-Erwartungen (Cortex-A72)

| Metrik | Erwartungswert |
|---|---|
| Mosquitto Durchsatz | 50'000–100'000 msg/s |
| Python asyncio Publishes | 5'000–10'000 msg/s |
| Typische Anlage aktive Changes | 50–500 Changes/s |
| Max. DataPoints pro Instanz | 100'000 |

Alias-Topics werden nur bei tatsächlichen Wertänderungen publiziert (kein Periodic Refresh).

---

## 13. Deployment

### Docker Compose (empfohlen)

```yaml
version: '3.8'
services:
  mosquitto:
    image: eclipse-mosquitto:2
    ports: ["1883:1883"]
    volumes: ["./mosquitto:/mosquitto/config"]

  opentws:
    build: .
    ports: ["8080:8080"]
    volumes: ["./data:/data"]
    environment:
      - OPENTWS_MQTT_HOST=mosquitto
    depends_on: [mosquitto]
```

---

## 14. TWS Migration

```
tws_export.xml  →  tools/tws2opentws.py  →  opentws_config.json  →  POST /api/v1/config/import
```

`tws2opentws.py` ist ein separates CLI-Tool (community-pflegbar), kein Core-Bestandteil. Ziel: 80% der typischen TWS-Konfigurationen automatisch migrieren.

---

## 15. Lizenz

MIT License — alle Komponenten, inkl. tws2opentws.py
