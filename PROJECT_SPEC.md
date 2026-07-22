# open bridge server — Project Specification

**Version:** 0.1  
**Lizenz:** MIT  
**Stand:** 2026-03-26

---

## Projektzusammenfassung

open bridge server ist ein Open-Source Multiprotokoll-Server für Gebäude- und Industrieautomation. Er verbindet KNX, Modbus RTU, Modbus TCP und 1-Wire über ein zentrales, modulares Objektsystem und stellt alle Daten via MQTT bereit. Vollständige Konfiguration über REST API; Web GUI nutzt dieselbe API.

Zielgruppe: Bestehende Timberwolf Server (TWS) Anwender und Neueinsteiger in der Gebäudeautomation.

---

## Nicht-funktionale Anforderungen

| Anforderung | Wert |
|---|---|
| Zielplattform | Linux, ARM Cortex-A72 (ARMv8-A) und x86_64 |
| Programmiersprache | Python 3.13+ |
| Max. DataPoints pro Instanz | 100'000 |
| Lizenz | MIT (alle Komponenten) |
| API-First | Web GUI nutzt ausschliesslich die öffentliche API |
| Erweiterbarkeit | Alle Subsysteme über Registry-Pattern erweiterbar |
| Kein Neustart | Laufzeitkonfiguration sofort aktiv ohne Neustart |

---

## Implementierungs-Reihenfolge (empfohlen)

### Phase 1 — Fundament
1. Projektstruktur und `config.py` (config.yaml + Env-Vars)
2. SQLite Datenbank + Migrations-System (`aiosqlite`)
3. DataType Registry mit 8 Kern-Datentypen
4. DataPoint Modell (Pydantic)
5. AdapterBinding Modell (Pydantic)
6. Type Converter mit ConversionResult

### Phase 2 — Core
7. Interner async Event Bus
8. MQTT Client Wrapper (`aiomqtt`)
9. DataPoint Registry (in-memory + DB-Sync)
10. AdapterBase ABC + Adapter Registry

### Phase 3 — Adapter
11. KNX Adapter (`xknx`) + DPT Registry (Basis-DPTs)
12. Modbus TCP Adapter (`pymodbus`)
13. Modbus RTU Adapter (`pymodbus`)
14. 1-Wire Adapter (`pyownet` — Client von externem `owserver`/OWFS)

### Phase 4 — API
15. FastAPI Setup + Authentifizierung (JWT + API Key)
16. `/api/v1/datapoints` CRUD + Pagination
17. `/api/v1/datapoints/{id}/bindings` CRUD
18. `/api/v1/search` (serverseitig gefiltert)
19. `/api/v1/adapters` (Status + Schema + Test)
20. `/api/v1/system` (Health + Adapter-Status + DataTypes)
21. WebSocket `/api/v1/ws` (selektives Subscribe)

### Phase 5 — Erweiterte Features
22. RingBuffer Debug-Log (SQLite memory/disk)
23. `/api/v1/ringbuffer` API
24. History Plugin ABC + SQLite Plugin
25. `/api/v1/history` API
26. Import/Export `/api/v1/config`

### Phase 6 — Tools
27. `tws2opentws.py` CLI-Migrations-Tool
28. Docker Compose Setup

---

## Kernentscheidungen (nicht mehr offen)

| Thema | Entscheidung | Begründung |
|---|---|---|
| MQTT Broker | Mosquitto extern | Stabilität, Entkopplung, Debugging |
| Async | asyncio überall | Non-blocking, ARM-effizient |
| Datenbank | SQLite | Zero-dependency, ARM-optimiert |
| MQTT Payload | JSON `{v, u, t, q}` + raw Topic | Einheit + Quality für Visualisierung |
| MQTT Topics | Hybrid (UUID + Alias) | Stabile Automatisierung + browsbare Struktur |
| Typkonvertierung | Silent, GUI-Hinweis | Kein Laufzeit-Overhead |
| Authentifizierung | JWT (GUI) + API Key (Automatisierung) | Dual-Use |
| API Versionierung | `/api/v1/...` von Beginn an | Migrationssicherheit |
| Echtzeit-Updates | WebSocket mit selektivem Subscribe | Performance |
| Historisierung | Plugin-System, SQLite als Fallback | Flexibilität, kein InfluxDB-Lock-in |
| RingBuffer | SQLite memory/disk, umschaltbar | Debug ohne Overhead |

---

## DataTypes

```python
# Kern-Datentypen — beim Start in DataTypeRegistry registriert
UNKNOWN    # Fallback für unbekannte Typen, speichert Raw-Bytes
BOOLEAN    # bool
INTEGER    # int
FLOAT      # float
STRING     # str
DATE       # datetime.date, ISO 8601
TIME       # datetime.time, ISO 8601
DATETIME   # datetime.datetime, ISO 8601 mit Timezone
```

Neue Datentypen werden über `DataTypeRegistry.register()` hinzugefügt — kein Core-Code nötig.

---

## AdapterBinding direction

```
SOURCE  — Adapter liefert Werte ins System (Sensor, Status-GA)
DEST    — System schreibt Werte zum Adapter (Aktor, Schreib-GA)
BOTH    — Lesen und Schreiben über dieselbe Adresse (Modbus Holding Register)
```

**Wichtig:** Ein KNX-Dimmer verwendet zwei separate Bindings:
- Schreib-GA: `direction=DEST`
- Status-GA: `direction=SOURCE`

---

## MQTT Payload Spezifikation

```json
{
  "v": 21.4,
  "u": "°C",
  "t": "2025-03-26T10:23:41.123Z",
  "q": "good"
}
```

| Key | Typ | Beschreibung |
|---|---|---|
| `v` | any | Wert (typ-abhängig serialisiert) |
| `u` | string \| null | Einheit aus DataPoint |
| `t` | string | ISO 8601 Timestamp mit Millisekunden |
| `q` | string | Quality: `good` \| `bad` \| `uncertain` |

---

## API Übersicht

```
POST   /api/v1/auth/login
POST   /api/v1/auth/refresh

GET    /api/v1/datapoints?page=0&size=50
POST   /api/v1/datapoints
GET    /api/v1/datapoints/{id}
PATCH  /api/v1/datapoints/{id}
DELETE /api/v1/datapoints/{id}
GET    /api/v1/datapoints/{id}/value

GET    /api/v1/datapoints/{id}/bindings
POST   /api/v1/datapoints/{id}/bindings
PATCH  /api/v1/datapoints/{id}/bindings/{binding_id}
DELETE /api/v1/datapoints/{id}/bindings/{binding_id}

GET    /api/v1/search?q=&tag=&type=&adapter=&page=0&size=50

GET    /api/v1/adapters
GET    /api/v1/adapters/{type}/schema
POST   /api/v1/adapters/{type}/test
PATCH  /api/v1/adapters/{type}/config

GET    /api/v1/history/{id}?from=&to=&limit=
GET    /api/v1/history/{id}/aggregate?fn=avg&interval=1h&from=&to=

GET    /api/v1/ringbuffer?q=&adapter=&from=&limit=
GET    /api/v1/ringbuffer/stats
POST   /api/v1/ringbuffer/config

GET    /api/v1/system/health
GET    /api/v1/system/adapters
GET    /api/v1/system/datatypes

GET    /api/v1/config/export
POST   /api/v1/config/import

WS     /api/v1/ws?token={jwt}
```

---

## Bekannte TWS-Schwächen — open bridge server Lösungsansätze

| TWS-Problem | open bridge server Lösung |
|---|---|
| Nicht alle KNX DPTs eingebaut | DPT Registry erweiterbar, unbekannte DPTs → UNKNOWN (kein Crash) |
| Langsames GUI beim Verknüpfen | Serverseitige Pagination + Suche, nie Full-Load |
| Proprietäre Konfiguration | Export/Import als JSON, tws2opentws.py Migrations-Tool |
| Vendor Lock-in | MIT Lizenz, vollständig Open Source |
| InfluxDB Abhängigkeit | History Plugin-System, SQLite als Fallback |

---

## Offene Punkte (für spätere Phasen)

- Web GUI Technologie (React / Vue / HTMX — noch nicht entschieden)
- Visualisierungs-Modul (separates Projekt, baut auf open bridge server API auf)
- Benutzerrollen und Berechtigungen (aktuell: single-user + API Keys)
- BACnet Adapter (zukünftig, community)
- OPC-UA Adapter (zukünftig, community)
- Clustering / Hochverfügbarkeit

---

## Entwicklungsumgebung Setup

```bash
# Repository
git clone https://github.com/abeggled/openbridgeserver
cd openbridgeserver

# Python Environment
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Mosquitto (Docker)
docker run -d -p 1883:1883 eclipse-mosquitto:2

# Konfiguration
cp config.example.yaml config.yaml

# Starten
python -m obs
```

---

## Weiterführende Dokumente

- `ARCHITECTURE.md` — Vollständige Systemarchitektur
- `docs/api.md` — Generiert via FastAPI OpenAPI (auto)
- `docs/adapters/knx.md` — KNX Adapter Konfigurationsreferenz
- `docs/adapters/modbus.md` — Modbus Adapter Konfigurationsreferenz
- `docs/migration/tws.md` — TWS Migrations-Anleitung
