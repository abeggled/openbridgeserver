# open bridge multiprotocol ai server

![**open bridge server** Logo](logo/obs_logo_dark.svg)

**Offene Gebäudeautomations-Plattform — verbindet KNX, Modbus, MQTT, Home Assistant und mehr**

open bridge verbindet verschiedene Gebäudetechnik-Protokolle zu einem einheitlichen System. Alle Werte lassen sich über eine Weboberfläche überwachen, per Logik verknüpfen und über MQTT weitergeben — ohne proprietäre Konfigurationsdateien.

---

## Was kann open bridge?

| Funktion | Beschreibung |
|---|---|
| **Protokolle** | KNX/IP (Tunneling + Routing + KNX IP Secure), Modbus TCP, Modbus RTU, 1-Wire, externes MQTT, Home Assistant, Zeitschaltuhr |
| **Mehrere Instanzen** | Beliebig viele Instanzen pro Protokoll (z. B. 2× KNX, 3× Modbus TCP) |
| **Protokoll-Brücke** | Ein KNX-Wert wird automatisch in ein Modbus-Register geschrieben — und umgekehrt |
| **Logik-Editor** | Visuelle Automatisierungslogik ohne Programmierung: 35+ Blocktypen, Zeitpläne, Formeln, Python-Skripte, Benachrichtigungen, HTTP-Anfragen, Sonnenstand |
| **MQTT** | Stabiler UUID-Topic + lesbarer Alias-Topic; Retain-Unterstützung |
| **Weboberfläche** | Vollständige Bedienung über den Browser — kein separates Programm nötig |
| **Datenbank** | SQLite — keine externe Datenbank erforderlich |
| **Verlauf** | Werteverlauf mit Diagramm, Aggregation nach Zeit (Std / Tag / Woche …); pro Datenpunkt konfigurierbar |
| **Änderungsprotokoll** | Letzten N Wertänderungen einsehbar (RingBuffer) — aktualisiert sich live |
| **Alles sofort** | Änderungen greifen ohne Neustart |
| **Installation** | Docker Compose, direkt als Python-Programm oder als Proxmox LXC-Template |
| **Lizenz** | MIT (kostenlos und quelloffen) |

---

## Inhaltsverzeichnis

1. [Schnellstart — Proxmox LXC](#schnellstart--proxmox-lxc)
2. [Konfiguration](#konfiguration)
3. [Wie funktioniert open bridge?](#wie-funktioniert-open-bridge)
4. [Datenpunkte](#datenpunkte)
5. [Verknüpfungen (Bindings)](#verknüpfungen-bindings)
6. [Suche](#suche)
7. [Adapter](#adapter)
8. [Verlauf (History)](#verlauf-history)
9. [Änderungsprotokoll (RingBuffer)](#änderungsprotokoll-ringbuffer)
10. [Sicherung & Wiederherstellung](#sicherung--wiederherstellung)
11. [Systemstatus](#systemstatus)
12. [Live-Verbindung (WebSocket)](#live-verbindung-websocket)
13. [Logik-Editor](#logik-editor)
14. [Adapter-Konfiguration](#adapter-konfiguration)
15. [MQTT-Topics](#mqtt-topics)
16. [Datentypen](#datentypen)
17. [Einstellungen](#einstellungen)
18. [Hilfsskripte](#hilfsskripte)
19. [Visualisierung (Visu)](#visualisierung-visu)
   - [Grundriss- und Anlagenschema-Widget](#grundriss--und-anlagenschema-widget)
20. [Entwicklung](#entwicklung)
   - [Lokale Entwicklung mit PyCharm](#lokale-entwicklung-mit-pycharm)

---

## Schnellstart — Proxmox LXC

Das LXC-Template enthält ein vollständiges Ubuntu 26.04-System mit **open bridge server** und startet den Dienst automatisch beim Hochfahren des Containers.

**Schritt 1 — Template herunterladen**

1. Auf der [Release-Seite](../../releases/latest) die URL der `.tar.zst`-Datei sowie den SHA512-Hash aus dem Abschnitt **LXC Template** kopieren.
2. In der Proxmox-Weboberfläche zu **Datacenter → Storage → local → CT Templates** navigieren.
3. **Download from URL** klicken.
4. Die kopierte URL einfügen und auf **Query URL** klicken.
5. Als Hash-Algorithmus **SHA512** auswählen.
6. Den kopierten Hash einfügen.
7. Auf **Download** klicken.

![ProxmoxDownloadFromURL](docs/ProxmoxDownloadFromURL.png)

**Schritt 2 — Container erstellen**

1. Im Proxmox-Menü **Create CT** wählen.
2. Als Template das gerade heruntergeladene `ubuntu-plucky-openbridgeserver_…` auswählen.
3. Hostname, Passwort, CPU, RAM und Netzwerk nach Bedarf konfigurieren — empfohlen: mindestens 512 MB RAM.
4. Container starten.

**Schritt 3 — Zugriff**

| Dienst | Adresse |
|---|---|
| **open bridge server** Weboberfläche + API | `http://<container-ip>:8080` |

**Standardzugang:** Benutzername `admin`, Passwort `admin`
⚠️ Das Passwort sofort nach der ersten Anmeldung ändern (Einstellungen → Passwort).

**Sicherheitskonfiguration** (erforderlich):

```bash
# Umgebungsvariablen in /etc/obs.env setzen, z. B.:
OBS_MQTT__HOST=192.168.1.10
# Wird im LXC-Template automatisch beim ersten Start gesetzt (zufällig, pro Container).
# Nur bei manuellem Override setzen:
OBS_SECURITY__JWT_SECRET=<mindestens-32-zufällige-zeichen>

# Dienst neu starten
systemctl restart obs
```

---

## Konfiguration

Die Konfiguration wird in dieser Reihenfolge geladen (höher = Vorrang):

1. Umgebungsvariablen (`OBS_<ABSCHNITT>__<SCHLÜSSEL>`)
2. `config.yaml` (Pfad über `OBS_CONFIG`, Standard: `./config.yaml`)
3. Eingebaute Standardwerte

```yaml
server:
  host: 0.0.0.0               # Netzwerkschnittstelle
  port: 8080                  # Port der Weboberfläche
  log_level: INFO             # Protokollstufe: DEBUG|INFO|WARNING|ERROR

mqtt:
  host: localhost             # Interner Mosquitto-Broker
  port: 1883
  username: null              # Zugangsdaten für internen Broker
  password: null

database:
  path: /data/obs.db      # Datenbankdatei

ringbuffer:
  storage: file               # Änderungsprotokoll: file-only (Datei)
  max_entries: 10000          # Maximale Anzahl Einträge
  max_file_size_bytes: null   # Optional: harte Dateigrenze für den Ringbuffer
  max_age: null               # Optional: maximale Eintrags-Alterung in Sekunden

security:
  jwt_secret: changeme        # Sitzungsschlüssel — unbedingt ändern!
  jwt_expire_minutes: 1440    # Sitzungsdauer (Standard: 24 Stunden)
```

> **Hinweis:** Der `mqtt`-Abschnitt betrifft den **internen** Mosquitto-Broker. Externe MQTT-Broker werden als separate Adapter-Instanzen eingerichtet (siehe [MQTT-Adapter](#mqtt-adapter-externer-broker)).

---

## Wie funktioniert open bridge?

```
┌──────────────────────────────────────────────────────────────┐
│                        open bridge server                    │
│                                                              │
│  ┌─────────────────────┐  Wertänderung  ┌─────────────────┐  │
│  │   Adapter-Instanzen │ ─────────────▶ │   Ereignisbus   │  │
│  │                     │ ◀── schreiben  │  (verteilt an   │  │
│  │  KNX, Modbus,       │                │  alle Abnehmer) │  │
│  │  MQTT, 1-Wire …     │                └──┬──────┬───────┘  │
│  └─────────────────────┘                   │      │          │
│                                     ┌──────▼─┐ ┌──▼──────┐   │
│                                     │ Werte- │ │ Verlauf │   │
│                                     │ Abbild │ │ RingBuf │   │
│                                     │        │ │ MQTT    │   │
│                                     └────────┘ │ WS      │   │
│                                                └─────────┘   │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐   │
│  │                  Logik-Editor                         │   │
│  │  Wertänderung → Graph ausführen → DataPoint schreiben │   │
│  └───────────────────────────────────────────────────────┘   │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐   │
│  │                   REST-API + WebSocket                │   │
│  └───────────────────────────────────────────────────────┘   │
└──────────────────────────────────────────────────────────────┘
```

**Kernprinzipien:**
- **Adapter** lesen Werte aus dem Gebäude (KNX-Telegramm, Modbus-Register, MQTT-Nachricht, …) und melden sie an den Ereignisbus.
- Der **Ereignisbus** verteilt jeden Wert gleichzeitig an: Werteabbild (aktueller Stand), Verlauf, Änderungsprotokoll, MQTT-Broker, WebSocket-Clients und den Logik-Editor.
- Der **Logik-Editor** reagiert auf Wertänderungen, führt Automatisierungslogiken aus und schreibt Ergebnisse zurück in DataPoints.
- **Protokoll-Brücke:** Wenn ein Wert über ein Protokoll empfangen wird, schreibt **open bridge server** ihn automatisch über alle anderen verknüpften Protokolle weiter — ohne zusätzliche Konfiguration.

---

## Datenpunkte

Ein Datenpunkt ist das zentrale Objekt in **open bridge server**. Jeder physische oder virtuelle Wert im System — eine Temperatur, ein Schaltzustand, ein Energiezähler — ist ein Datenpunkt.

```
GET    /api/v1/datapoints?page=0&size=50       # Liste (seitenweise)
POST   /api/v1/datapoints                      # Neu anlegen
GET    /api/v1/datapoints/{id}                 # Einzelnen laden (inkl. aktueller Wert)
PATCH  /api/v1/datapoints/{id}                 # Ändern
DELETE /api/v1/datapoints/{id}                 # Löschen (entfernt auch alle Verknüpfungen)
GET    /api/v1/datapoints/{id}/value           # Nur den aktuellen Wert
```

**Felder:**

| Feld | Beschreibung |
|---|---|
| `name` | Lesbarer Name, z. B. „Wohnzimmer Temperatur" |
| `data_type` | Datentyp: `BOOLEAN`, `INTEGER`, `FLOAT`, `STRING`, `DATE`, `TIME`, `DATETIME` |
| `unit` | Einheit, z. B. `°C`, `%rH`, `kWh`, `lx`, `mm/h`, `nSv/h` |
| `tags` | Schlagwörter zum Gruppieren und Filtern |
| `persist_value` | Letzten Wert beim Neustart wiederherstellen (Standard: `true`) |
| `record_history` | Werteverlauf in der Datenbank speichern (Standard: `true`). Auf `false` setzen um einen Datenpunkt von der History auszuschliessen. |
| `mqtt_topic` | Automatisch vergeben: `dp/{uuid}/value` |
| `mqtt_alias` | Lesbares Alias-Topic, z. B. `alias/klima/wohnzimmer/value` |

```bash
# Temperatur-Datenpunkt anlegen
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

## Verknüpfungen (Bindings)

Eine Verknüpfung verbindet einen Datenpunkt mit einer Adapter-Instanz und einer Adresse (z. B. KNX-Gruppenadresse oder Modbus-Register).

```
GET    /api/v1/datapoints/{id}/bindings
POST   /api/v1/datapoints/{id}/bindings
PATCH  /api/v1/datapoints/{id}/bindings/{binding_id}
DELETE /api/v1/datapoints/{id}/bindings/{binding_id}
```

**Richtungen:**

| Richtung | Bedeutung |
|---|---|
| `SOURCE` | Lesen: Adapter empfängt Werte und leitet sie an **open bridge server** weiter |
| `DEST` | Schreiben: **open bridge server** sendet Werte an den Adapter |
| `BOTH` | Beides gleichzeitig |

**Wert-Transformation (`value_formula`):**

Optional: eine Formel, die auf den Wert angewendet wird, bevor er ins System eingeht (SOURCE) oder herausgeht (DEST). Die Variable ist immer `x`.

```json
{ "value_formula": "x / 10" }
```

| Formel | Wirkung |
|---|---|
| `x * 3600` | Stunden → Sekunden |
| `x / 10` | Festkomma durch 10 |
| `round(x, 2)` | Auf 2 Dezimalstellen runden |
| `max(0, min(100, x))` | Auf 0–100 begrenzen |

Verfügbare Funktionen: `abs`, `round`, `min`, `max` und alle `math.*`-Funktionen. Division durch null und ungültige Ergebnisse werden abgefangen — der ursprüngliche Wert bleibt erhalten.

> **Hinweis:** `round()` verwendet mathematisches Runden (0.5 → aufrunden), nicht das in der Programmierung übliche „Bankers Rounding".

**Wert-Zuordnung (`value_map`):**

Optional: eine Tabelle, die Rohwerte auf andere Werte abbildet — nützlich z. B. bei Enumerationen oder Zustandstexten.

```json
{ "value_map": { "0": "Aus", "1": "Ein", "2": "Standby" } }
```

Der Schlüssel ist immer ein String (der Rohwert wird intern umgewandelt). Gibt es keinen passenden Eintrag, wird der Originalwert unverändert weitergegeben. `value_map` wird nach `value_formula` angewendet.

**Sendefilter** (nur für DEST/BOTH, werden der Reihe nach geprüft):

| Filter | Beschreibung |
|---|---|
| `send_throttle_ms` | Mindestabstand zwischen zwei Schreibvorgängen in Millisekunden |
| `send_on_change` | Nur senden wenn der Wert sich geändert hat |
| `send_min_delta` | Nur senden wenn die Abweichung zum letzten Wert mindestens so gross ist (absolut) |
| `send_min_delta_pct` | Nur senden wenn die Abweichung mindestens so gross ist (prozentual) |

**Beispiel: KNX-Temperatur → Modbus-Register**

```bash
# 1. Datenpunkt anlegen
DP_ID=$(curl -s -X POST http://localhost:8080/api/v1/datapoints \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"name":"Wohnzimmer Temperatur","data_type":"FLOAT","unit":"°C"}' \
  | jq -r .id)

# 2. KNX-Verknüpfung (Lesen von GA 1/2/3)
curl -X POST http://localhost:8080/api/v1/datapoints/$DP_ID/bindings \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"adapter_instance_id": "KNX-UUID", "direction": "SOURCE",
       "config": {"group_address": "1/2/3", "dpt_id": "DPT9.001"}}'

# 3. Modbus-Verknüpfung (Schreiben in Register 100)
curl -X POST http://localhost:8080/api/v1/datapoints/$DP_ID/bindings \
  -H "Authorization: Bearer {token}" \
  -H "Content-Type: application/json" \
  -d '{"adapter_instance_id": "MODBUS-UUID", "direction": "DEST",
       "config": {"unit_id": 1, "register_type": "holding", "address": 100, "data_format": "float32"}}'
```

---

## Suche

Servergestützte Suche über alle Datenpunkte. Gibt nie den gesamten Datenbestand zurück.

```
GET /api/v1/search?q=&tag=&type=&adapter=&page=0&size=50
```

| Parameter | Beschreibung |
|---|---|
| `q` | Suche im Namen |
| `tag` | Nach Schlagwort filtern |
| `type` | Nach Datentyp filtern (z. B. `FLOAT`) |
| `adapter` | Nach Protokoll filtern (z. B. `KNX`) |

---

## Adapter

Jeder Adapter-Typ kann in mehreren unabhängigen Instanzen betrieben werden. Alle Instanzen werden über die Weboberfläche oder die API verwaltet.

Die **Adapter-Konfiguration erfolgt vollständig über die Weboberfläche** — alle Felder werden aus dem JSON-Schema des jeweiligen Adapters dynamisch gerendert. Passwort-Felder erscheinen maskiert. Änderungen greifen sofort ohne Neustart.

```
GET    /api/v1/adapters/instances              # Alle Instanzen mit Status
POST   /api/v1/adapters/instances              # Neue Instanz anlegen
PATCH  /api/v1/adapters/instances/{id}         # Konfiguration ändern + neu verbinden
DELETE /api/v1/adapters/instances/{id}         # Stoppen + löschen
POST   /api/v1/adapters/instances/{id}/restart # Neu verbinden
POST   /api/v1/adapters/instances/{id}/test    # Verbindung testen

GET    /api/v1/adapters/{type}/schema          # JSON-Schema der Instanz-Konfiguration
GET    /api/v1/adapters/{type}/binding-schema  # JSON-Schema der Verknüpfungs-Konfiguration
```

### Anmeldung und Zugangsverwaltung

**open bridge server** unterstützt zwei Anmeldemethoden:

| Methode | Verwendung |
|---|---|
| Benutzername + Passwort → JWT-Token | Weboberfläche, Browser |
| API-Schlüssel (`X-API-Key: obs_…`) | Skripte, Automatisierungen |

```
POST   /api/v1/auth/login                              # Anmelden → Token erhalten
POST   /api/v1/auth/refresh                            # Token erneuern

GET    /api/v1/auth/users                              # Alle Benutzer (nur Admin)
POST   /api/v1/auth/users                              # Benutzer anlegen (nur Admin)
DELETE /api/v1/auth/users/{username}                   # Benutzer löschen (nur Admin)
POST   /api/v1/auth/me/change-password                 # Eigenes Passwort ändern

POST   /api/v1/auth/apikeys                            # API-Schlüssel anlegen
DELETE /api/v1/auth/apikeys/{id}                       # API-Schlüssel widerrufen

POST   /api/v1/auth/users/{username}/mqtt-password     # MQTT-Zugang einrichten
DELETE /api/v1/auth/users/{username}/mqtt-password     # MQTT-Zugang entziehen
```

**MQTT-Zugang:** Der interne Mosquitto-Broker ist passwortgeschützt. Jeder Benutzer kann einen separaten MQTT-Zugang (unabhängig vom Anmeldepasswort) erhalten, um sich direkt mit dem Broker zu verbinden.

---

## Verlauf (History)

Werteverlauf eines Datenpunkts — roh oder als Zusammenfassung.

```
GET /api/v1/history/{id}?from=&to=&limit=
GET /api/v1/history/{id}/aggregate?fn=avg&interval=1h&from=&to=
```

**Zusammenfassungsfunktionen:** `avg` (Durchschnitt), `min`, `max`, `last`

**Zeitintervalle:** `1m`, `5m`, `15m`, `30m`, `1h`, `6h`, `12h`, `1d`

Alle Zeitangaben richten sich nach der in den Einstellungen konfigurierten Zeitzone.

**Aufzeichnung steuern:** Das Feld `record_history` am Datenpunkt kontrolliert, ob Werte in den Verlauf geschrieben werden. Datenpunkte mit `record_history: false` werden vom History-Modul ignoriert. Die Verwaltung erfolgt unter Einstellungen → Verlauf.

---

## Änderungsprotokoll (RingBuffer)

Der RingBuffer speichert die letzten N Wertänderungen als Protokoll. In der Weboberfläche aktualisiert sich die Liste **sofort** (ohne Neuladen), da neue Einträge live über die WebSocket-Verbindung übertragen werden.

```
GET  /api/v1/ringbuffer?q=&adapter=&from=&limit=   # Einträge abfragen
POST /api/v1/ringbuffer/query                       # v2 Query-DSL (Filtergruppen + Pagination + Sortierung)
POST /api/v1/ringbuffer/export/csv                  # CSV-Export der vollständigen gefilterten Ergebnismenge
GET  /api/v1/ringbuffer/stats                       # Anzahl Einträge, Kapazität
POST /api/v1/ringbuffer/config                      # file-only + Kapazität ändern
```

Der Parameter `q` durchsucht sowohl den Namen als auch die ID des Datenpunkts.

`POST /api/v1/ringbuffer/query` verwendet eine Filter-DSL mit klarer Semantik:
- `filters.adapters.any_of`: OR innerhalb der Adapterliste.
- `filters.values`: typbewusste Wertfilter (`eq/ne/gt/gte/lt/lte/between/contains/regex`) passend zu `data_type`.
- `filters.metadata`: filterbare Snapshot-Metadaten aus DataPoint/Binding-Kontext (`tags`, `adapter_types`, `group_addresses`, `topics`, `entity_ids`, `register_types`, `register_addresses`).
- Filtergruppen (`time`, `adapters`, `datapoints`, `values`, `metadata`, `q`) werden per AND kombiniert.
- Zeitfilter unterstützen offene Ränder (`from` ohne `to`, `to` ohne `from`) und die Kombination aus absoluten Grenzen (`from`/`to`) plus relativen Offsets (`from_relative_seconds`/`to_relative_seconds`).
- Pagination über `pagination.limit` + `pagination.offset`, Sortierung über `sort.field` (`id|ts`) und `sort.order` (`asc|desc`).
- Das versionierte Metadatenmodell ist dokumentiert in `docs/ringbuffer-metadata-model-v1.md` (`metadata_version: 1`).

`POST /api/v1/ringbuffer/export/csv` nutzt denselben Request-Body wie `/query`, exportiert aber immer die vollständige gefilterte Ergebnismenge (Pagination der UI wird ignoriert).  
CSV-Spalten: `id`, `ts`, `datapoint_id`, `name`, `topic`, `old_value_json`, `new_value_json`, `source_adapter`, `quality`, `metadata_version`, `metadata_json`.

---

## Sicherung & Wiederherstellung

Vollständige Konfigurationssicherung und -wiederherstellung. Bestehende Einträge werden aktualisiert, fehlende neu angelegt.

```
GET  /api/v1/config/export    # Sicherungsdatei herunterladen (JSON)
POST /api/v1/config/import    # Sicherungsdatei einspielen
```

Die Sicherung enthält: alle Datenpunkte, Verknüpfungen, Adapter-Instanzen und KNX-Gruppenadressen.

**KNX-Projektdatei importieren:**

```
POST /api/v1/knxproj/import   # .knxproj-Datei hochladen (multipart/form-data)
GET  /api/v1/knxproj/ga       # Importierte Gruppenadressen anzeigen
DELETE /api/v1/knxproj/ga     # Alle importierten Adressen löschen
```

Nach dem Import erscheinen die Gruppenadressen als Suchvorschläge im Verknüpfungs-Formular.

---

## Systemstatus

```
GET /api/v1/system/health      # Erreichbarkeit prüfen (kein Login nötig)
GET /api/v1/system/adapters    # Adapter-Status + Anzahl Verknüpfungen
GET /api/v1/system/datatypes   # Alle verfügbaren Datentypen
GET /api/v1/system/settings    # Systemeinstellungen lesen (z. B. Zeitzone)
PUT /api/v1/system/settings    # Systemeinstellungen ändern

GET /api/v1/adapters/knx/dpts  # Alle registrierten KNX-DPT-Typen auflisten
```

---

## Live-Verbindung (WebSocket)

Über die WebSocket-Verbindung werden Wertänderungen und neue RingBuffer-Einträge sofort an alle verbundenen Browser übertragen — kein manuelles Neuladen nötig.

```
WS /api/v1/ws?token={jwt}
```

**Datenpunkt abonnieren:**
```json
{"action": "subscribe", "datapoint_ids": ["uuid-1", "uuid-2"]}
```

**Eingehende Wertänderung:**
```json
{
  "id": "550e8400-e29b-41d4-a716-446655440000",
  "v": 21.4,
  "u": "°C",
  "t": "2026-03-27T10:23:41.123Z",
  "q": "good"
}
```

**Neuer RingBuffer-Eintrag** (an alle Verbindungen, ohne Abo):
```json
{
  "action": "ringbuffer_entry",
  "entry": {
    "ts": "2026-03-27T10:23:41.123Z",
    "datapoint_id": "550e8400-...",
    "name": "Wohnzimmer Temperatur",
    "new_value": 21.4,
    "old_value": 21.1,
    "quality": "good",
    "source_adapter": "KNX"
  }
}
```

**Datenqualität (`q`):**

| Wert | Bedeutung |
|---|---|
| `good` | Wert erfolgreich empfangen, Verbindung aktiv |
| `bad` | Adapter getrennt oder Lesefehler |
| `uncertain` | Verbindung wird wiederhergestellt oder Wert möglicherweise veraltet |

---

## Logik-Editor

### Übersicht

Der Logik-Editor ermöglicht das visuelle Erstellen von Automatisierungsregeln — ohne Programmierkenntnisse. Blöcke werden per Drag & Drop auf einer Arbeitsfläche platziert und mit Verbindungslinien verknüpft.

**Ablauf:**
1. Ein **DP Lesen**-Block beobachtet einen Datenpunkt.
2. Ändert sich der Wert, führt **open bridge server** den gesamten Graphen aus.
3. Die Blöcke werden der Reihe nach berechnet.
4. Ein **DP Schreiben**-Block schreibt das Ergebnis zurück — das löst automatisch alle Adapter, MQTT, den Verlauf und den RingBuffer aus.
5. Der **Trigger**-Block löst den Graphen nach einem Zeitplan aus (z. B. täglich um 07:00 Uhr).

Der Graph kann auch manuell über den **▶ Ausführen**-Button gestartet werden.

**Zustände** (Hysterese, Statistik, Betriebsstunden, Min/Max-Tracker, Verbrauchszähler) werden in der Datenbank gespeichert und überleben einen Neustart.

---

### Blocktypen

#### Konstante

| Block | Ausgänge | Beschreibung |
|---|---|---|
| **Festwert** | Wert | Gibt einen festen Wert aus — Zahl, Ein/Aus oder Text. |

#### Logik

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **UND** | A, B | Aus | Wahr wenn **alle** Eingänge wahr sind. |
| **ODER** | A, B | Aus | Wahr wenn **mindestens ein** Eingang wahr ist. |
| **NICHT** | Ein | Aus | Kehrt den Eingang um. |
| **EXKLUSIV-ODER** | A, B | Aus | Wahr wenn **genau ein** Eingang wahr ist. |
| **Vergleich** | A, B | Ergebnis | Vergleicht zwei Werte. Auswahl: `>` `<` `=` `>=` `<=` `≠` |
| **Hysterese** | Wert | Aus | Schaltet ein wenn der Wert über „Schwelle EIN" steigt, und erst wieder aus wenn er unter „Schwelle AUS" fällt. Verhindert schnelles Hin- und Herschalten. |

#### Datenpunkt

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **DP Lesen** | — | Wert, Geändert | Liest einen Datenpunkt. Löst den Graphen bei Wertänderung automatisch aus. Optionale Filter (Mindestabstand, Mindeständerung) und Wert-Transformation. |
| **DP Schreiben** | Wert, Trigger | — | Schreibt einen Wert in einen Datenpunkt. Optionaler Trigger-Eingang: nur schreiben wenn Trigger wahr. Optionale Filter und Wert-Transformation. |

#### Mathematik

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **Formel** | a, b | Ergebnis | Berechnet einen Ausdruck aus den Eingängen `a` und `b`. Optional: eine zweite Formel zur Transformation des Ergebnisses (Variable `x`). |
| **Skalieren** | Wert | Ergebnis | Rechnet einen Wert von einem Bereich in einen anderen um, z. B. 0–255 → 0–100 %. |
| **Begrenzer** | Wert | Ergebnis | Begrenzt den Wert auf einen Bereich [Min, Max]. Werte darunter oder darüber werden auf den Grenzwert gesetzt. |
| **Statistik** | Wert, Zurücksetzen | Min, Max, Mittelwert, Anzahl | Führt eine laufende Statistik über alle empfangenen Werte. Reset setzt alles zurück. Ergebnisse werden gespeichert und überleben einen Neustart. |
| **Min/Max-Tracker** | Wert | Min tägl., Max tägl., Min wöch., Max wöch., Min monatl., Max monatl., Min jährl., Max jährl., Min abs., Max abs. | Verfolgt Minimal- und Maximalwerte über verschiedene Zeiträume (täglich, wöchentlich, monatlich, jährlich, absolut). Setzt sich automatisch bei Periodengrenze zurück. |
| **Verbrauchszähler** | Wert (Zählerstand) | Täglich, Wöchentlich, Monatlich, Jährlich, Vorheriger Tag, Vorherige Woche, Vorheriger Monat, Vorheriges Jahr | Berechnet Verbrauch aus einem kumulierten Zählerstand. Speichert Vorperiodenwerte für Vergleiche. |
| **Sommer/Winter (DIN)** | Wert (Aussentemperatur) | Heizungsmodus, Tagesdurchschnitt, Monatsdurchschnitt | Steuert die Heizungsumschaltung nach DIN-Norm anhand von drei Tageswerten (T1 ≈ 07:00, T2 ≈ 14:00, T3 ≈ 22:00). Tagesdurchschnitt = (T1 + T2 + 2×T3) / 4. |

#### Text

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **Text verbinden** | 2–20 Eingänge (konfigurierbar) | Ergebnis | Verbindet mehrere Texte zu einem. Optionales Trennzeichen (z. B. `,` oder ` `). |

#### Timer

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **Verzögerung** | Trigger | Trigger | Verzögert ein Signal um N Sekunden. |
| **Impuls** | Trigger | Aus | Gibt für N Sekunden „Wahr" aus, dann „Falsch". |
| **Trigger** | — | Trigger | Löst den Graphen nach einem Zeitplan aus (Cron-Format). Konfigurierbar über Vorlagen, einen visuellen Editor (Min/Std/Tag/Mon/Wochentag) oder direkte Eingabe des Ausdrucks. |
| **Betriebsstunden** | Aktiv, Zurücksetzen | Stunden | Zählt Betriebsstunden solange „Aktiv" wahr ist. Gespeicherter Zählerstand überlebt Neustarts. |

#### Skript

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **Python-Skript** | a, b, c | Ergebnis | Führt Python-Code aus. Eingangswerte sind über `inputs['a']`, `inputs['b']`, `inputs['c']` verfügbar. Das Ergebnis wird mit `result = …` gesetzt. Nur mathematische Funktionen erlaubt — kein Dateizugriff, kein Netzwerk. |

#### KI

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **KI-Logik** | Trigger | Ergebnis | Platzhalter für zukünftige KI-Integration. |

#### MCP

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **MCP-Werkzeug** | Trigger, Eingabe | Ergebnis, Fertig | Ruft ein Werkzeug auf einem externen MCP-Server auf. |

#### Astro

| Block | Ausgänge | Beschreibung |
|---|---|---|
| **Astro Sonne** | Sonnenaufgang, Sonnenuntergang, Tagsüber | Berechnet Sonnenauf- und -untergang für den konfigurierten Standort. Gibt auch aus, ob es gerade hell ist. Konfiguration: Breitengrad, Längengrad. Berücksichtigt die eingestellte Zeitzone. |

#### Benachrichtigung

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **Pushover** | Trigger, Nachricht | Gesendet | Sendet eine Push-Benachrichtigung auf das Handy via [Pushover](https://pushover.net). Konfiguration: App-Token, User-Key, Titel, Priorität. |
| **SMS (seven.io)** | Trigger, Nachricht | Gesendet | Sendet eine SMS via [seven.io](https://seven.io). Konfiguration: API-Schlüssel, Empfänger, Absender. |

#### Integration

| Block | Eingänge | Ausgänge | Beschreibung |
|---|---|---|---|
| **API-Abfrage** | Trigger, Inhalt | Antwort, Statuscode, Erfolg | Sendet eine HTTP-Anfrage an eine externe Adresse. Methode wählbar (GET/POST/PUT/PATCH/DELETE). Antwortformat: JSON oder Text. SSL-Prüfung konfigurierbar. |
| **JSON-Extraktor** | Daten (JSON-Text) | Wert | Parst einen JSON-String und extrahiert einen Wert anhand eines Pfads mit Punktnotation, z. B. `sensors.temperature`. |
| **XML-Extraktor** | Daten (XML-Text) | Wert | Parst einen XML-String und extrahiert einen Wert per XPath-Ausdruck, z. B. `./sensor/temperature`. |

---

### Filter und Transformation bei DP-Blöcken

Beide DataPoint-Blöcke haben drei Tabs: **Verbindung**, **Transformation** und **Filter**. Ein Punkt (•) erscheint im Tab wenn etwas aktiv ist.

#### Transformation

Optionale Formel die auf den Wert angewendet wird. Variable: `x`

Vordefinierte Vorlagen (Beispiele):

| Vorlage | Formel |
|---|---|
| × 1.000 | `x * 1000` |
| × 100 | `x * 100` |
| ÷ 10 | `round(x / 10, 1)` |
| ÷ 100 | `round(x / 100, 2)` |
| Sekunden → Stunden | `x / 3600` |
| Stunden → Sekunden | `x * 3600` |

#### Filter bei DP Lesen

| Filter | Beschreibung |
|---|---|
| Mindestabstand | Wie oft der Graph höchstens ausgelöst wird (z. B. maximal alle 10 Sekunden) |
| Nur bei Änderung | Graph nur auslösen wenn der Wert sich wirklich geändert hat |
| Mindeständerung (absolut) | Nur auslösen wenn der Wert sich um mindestens N geändert hat |
| Mindeständerung (%) | Nur auslösen wenn die Änderung mindestens N Prozent beträgt |

#### Filter bei DP Schreiben

| Filter | Beschreibung |
|---|---|
| Mindestabstand | Wie oft höchstens geschrieben wird |
| Nur bei Änderung | Nicht schreiben wenn der Wert gleich dem zuletzt geschriebenen ist |
| Mindeständerung (absolut) | Nur schreiben wenn der Wert sich um mindestens N geändert hat |

---

### Zeitplan-Konfiguration (Trigger-Block)

Der **Trigger**-Block löst Graphen nach einem Zeitplan aus. Drei Eingabewege, die sich gegenseitig synchronisieren:

**1. Vorlagen** — über 30 vordefinierte Zeitpläne in 4 Gruppen (Minuten-Intervalle, Stunden-Intervalle, Täglich, Wöchentlich/Monatlich)

**2. Visueller Editor** — fünf Felder: Minute / Stunde / Tag / Monat / Wochentag

**3. Direkteingabe** — Standard Cron-Ausdruck

```
0 7 * * *         → täglich um 07:00
*/15 * * * *      → alle 15 Minuten
0 8 * * 1-5       → werktags um 08:00
0 6,18 * * *      → täglich um 06:00 und 18:00
```

Zur Überprüfung: [crontab.guru](https://crontab.guru) (Link direkt im Konfigurations-Panel)

---

### Formel-Referenz

In **allen** Formelfeldern (DP Lesen, DP Schreiben, Formel-Block, Verknüpfungs-Transformation) gilt:

- Variable `x` = der eingehende Wert (immer als Zahl übergeben)
- Kein Import nötig — alle Funktionen direkt verfügbar
- `round()` verwendet mathematisches Runden (0.5 → aufrunden)

| Funktion | Beispiel | Beschreibung |
|---|---|---|
| `abs(x)` | `abs(x - 50)` | Absolutbetrag (immer positiv) |
| `round(x, n)` | `round(x, 2)` | Runden auf n Nachkommastellen |
| `min(a, b)` | `min(x, 100)` | Kleinerer der beiden Werte |
| `max(a, b)` | `max(x, 0)` | Grösserer der beiden Werte |
| `sqrt(x)` | `sqrt(x)` | Quadratwurzel |
| `floor(x)` | `floor(x)` | Abrunden auf ganze Zahl |
| `ceil(x)` | `ceil(x)` | Aufrunden auf ganze Zahl |
| `math.log(x)` | `math.log(x)` | Natürlicher Logarithmus |
| `math.sin(x)` | `math.sin(x)` | Sinus |
| `math.pi` | `x * math.pi / 180` | Kreiszahl π |

**Praktische Beispiele:**

| Ziel | Formel |
|---|---|
| Auf 0–100 begrenzen | `max(0, min(100, x))` |
| Fahrenheit → Celsius | `(x - 32) * 5 / 9` |
| Wh → kWh | `x / 1000` |
| Auf halbe Stufen runden | `round(x * 2) / 2` |
| Negativen Wert abschneiden | `max(0, x)` |

**Formel-Block** (Eingänge `a` und `b`):

```
a * 2 + b              # Eingang a verdoppeln, b addieren
max(a, b)              # Grösseren der beiden Werte nehmen
round((a + b) / 2, 1)  # Mittelwert, 1 Nachkommastelle
abs(a - b)             # Absolute Differenz
```

Zusätzlich kann eine **Ausgangs-Transformation** konfiguriert werden — eine zweite Formel (Variable `x`) die auf das berechnete Ergebnis angewendet wird.

---

### Automatische Typumwandlung

Die Logik-Engine wandelt Werte automatisch um:

| Von | Nach | Regel |
|---|---|---|
| `true`/`false` | Zahl | Wahr → 1.0, Falsch → 0.0 |
| Zahl | Ein/Aus | 0 → Falsch, alles andere → Wahr |
| Text `"123"` | Zahl | 123.0 |
| Text `"true"`, `"on"`, `"1"` | Ein/Aus | Wahr |
| Text `"false"`, `"off"`, `"0"` | Ein/Aus | Falsch |
| Kein Wert | Zahl | 0.0 |

Verbindungen zwischen unterschiedlichen Blocktypen funktionieren damit immer.

---

### Debug-Modus

Zeigt berechnete Zwischenwerte direkt auf den Blöcken an — live und automatisch.

1. Graph öffnen
2. **🔍 Debug**-Button in der Werkzeugleiste klicken
3. Jeder Block zeigt ein gelbes Band mit seinen aktuellen Ausgangswerten
4. Die Anzeige aktualisiert sich automatisch nach jeder Ausführung (Wertänderung, Zeitplan, manueller Start)

| Typ | Darstellung |
|---|---|
| Wahr | `out=✓` |
| Falsch | `out=✗` |
| Zahl | `value=230.45` |
| DP Schreiben | `→ 21.5` |
| Kein Wert | `value=—` |

---

## Adapter-Konfiguration

### KNX-Adapter

**Instanz-Konfiguration — Grundparameter:**

| Feld | Werte | Beschreibung |
|---|---|---|
| `connection_type` | `tunneling` / `tunneling_secure` / `routing` / `routing_secure` | Verbindungstyp (siehe unten) |
| `host` | IP-Adresse | IP der KNX/IP-Zentrale (Tunneling) oder Multicast-Adresse (Routing) |
| `port` | Standard `3671` | Port der KNX/IP-Zentrale |
| `individual_address` | z. B. `1.1.210` | Eigene KNX-Adresse des open bridge Servers |
| `local_ip` | IP-Adresse | Lokale Netzwerkschnittstelle (optional). Bei Routing/Routing Secure: wählt die Netzwerkkarte für Multicast — bei mehreren Netzwerkkarten **empfohlen**. Bei Tunneling/Tunneling Secure: bindet den Socket an eine bestimmte Schnittstelle — meist nur bei Mehrfach-Netzwerkkarten nötig. Leer lassen = automatische Auswahl. |

**Verbindungstypen:**

| `connection_type` | Beschreibung |
|---|---|
| `tunneling` | UDP-Tunneling zur KNX/IP-Zentrale (Standard) |
| `tunneling_secure` | KNX IP Secure Tunneling (verschlüsselt, TCP) |
| `routing` | IP-Multicast-Routing |
| `routing_secure` | KNX IP Secure Routing (verschlüsselt, Multicast) |

**KNX IP Secure — Keyfile-Modus (empfohlen)**

Der einfachste Weg für KNX IP Secure ist der Import der `.knxkeys`-Datei aus ETS:

1. In ETS: **Sicherheit → Schlüsselsicherung exportieren** → `.knxkeys`-Datei speichern
2. In open bridge server: **Einstellungen → Adapter → KNX-Instanz bearbeiten → Keyfile hochladen**
3. Keyfile-Passwort eingeben — open bridge server zeigt alle verfügbaren Tunnel mit PA, User-ID und Anzahl gesicherter Gruppenadressen
4. Gewünschten Tunnel wählen → `individual_address` wird automatisch gesetzt
5. `connection_type` auf `tunneling_secure` (oder `routing_secure`) setzen

| Feld | Beschreibung |
|---|---|
| `knxkeys_file_path` | Wird automatisch gesetzt nach dem Hochladen der Keyfile |
| `knxkeys_password` | Passwort-Feld — Passwort zur `.knxkeys`-Datei |
| `individual_address` | PA des gewählten Tunnels (aus der Tunnel-Liste) |

**KNX IP Secure — Manueller Modus** (nur wenn kein Keyfile vorhanden):

Für `tunneling_secure`:

| Feld | Werte | Beschreibung |
|---|---|---|
| `user_id` | `1`–`127`, Standard `2` | Benutzer-ID am KNX/IP-Gateway |
| `user_password` | Passwort-Feld | Benutzerpasswort |
| `device_authentication_password` | Passwort-Feld | Geräte-Authentifizierungspasswort |

Für `routing_secure`:

| Feld | Werte | Beschreibung |
|---|---|---|
| `backbone_key` | Passwort-Feld | 128-Bit Backbone-Schlüssel als Hex-String (32 Zeichen, z. B. `0102030405060708090a0b0c0d0e0f10`; Trennzeichen `:` und Leerzeichen werden ignoriert) |

> **Hinweis:** Sind `knxkeys_file_path` und `knxkeys_password` gesetzt, haben sie Vorrang vor den manuellen Feldern. Alle Passwort-Felder werden in der Weboberfläche maskiert dargestellt.

**Keyfile API** (für eigene Integrationen):

```
POST /api/v1/knx/keyfile   # .knxkeys hochladen, Tunnel-Liste zurückgeben
DELETE /api/v1/knx/keyfile/{file_id}  # Keyfile löschen
```

Antwort des Upload-Endpunkts:
```json
{
  "file_id": "uuid",
  "file_path": "/data/knxkeys/uuid.knxkeys",
  "project_name": "Mein KNX-Projekt",
  "tunnels": [
    { "individual_address": "1.1.100", "host": "1.1.50", "user_id": 2, "secure_ga_count": 15 },
    { "individual_address": "1.1.101", "host": "1.1.50", "user_id": 3, "secure_ga_count": 15 }
  ],
  "backbone": null
}
```

**Verknüpfungs-Konfiguration:**

| Feld | Beschreibung |
|---|---|
| `group_address` | KNX-Gruppenadresse (dreiteilig, z. B. `27/6/6`) |
| `dpt_id` | DPT-Kennung — Tabelle unten |
| `state_group_address` | Optionale Rückmelde-Adresse für DEST-Verknüpfungen |
| `respond_to_read` | `true`: **open bridge server** beantwortet KNX-Leseanfragen (GroupValueRead) mit dem aktuellen Wert. Standard: `false` |

**Unterstützte DPTs:**

**open bridge server** unterstützt über 85 KNX-Datentypen. Die vollständige Liste ist über `GET /api/v1/adapters/knx/dpts` abrufbar.

**DPT 1 — 1-Bit Boolean**

| DPT | Typische Verwendung |
|---|---|
| `DPT1.001` | Schalten (Ein/Aus) |
| `DPT1.002` | Boolean |
| `DPT1.003` | Freigabe (Enable) |
| `DPT1.007` | Schritt/Richtung |
| `DPT1.008` | Auf/Ab |
| `DPT1.009` | Öffnen/Schliessen |
| `DPT1.010` | Start/Stopp |
| `DPT1.011` | Zustandsanzeige |
| `DPT1.017` | Auslöser (Trigger) |
| `DPT1.018` | Anwesenheit |
| `DPT1.019` | Fenster/Tür |
| `DPT1.021` | Szene A/B |
| `DPT1.022` | Jalousie-Modus |
| `DPT1.023` | Tag/Nacht |
| *(weitere DPT1.x)* | *1-Bit Steuerungen* |

**DPT 2 — 2-Bit Gesteuerter Wert**

| DPT | Typische Verwendung |
|---|---|
| `DPT2.001` | Schaltsteuerung (Priorität + Wert) |
| `DPT2.002` | Boolsche Steuerung |

**DPT 3 — 4-Bit Relativer Steuerwert**

| DPT | Typische Verwendung |
|---|---|
| `DPT3.007` | Dimmen (Richtung + Geschwindigkeit) |
| `DPT3.008` | Jalousie (Richtung + Geschwindigkeit) |

**DPT 4 — 1-Byte Zeichen**

| DPT | Grösse | Typ | Typische Verwendung |
|---|---|---|---|
| `DPT4.001` | 1 Byte | Text | ASCII-Zeichen |
| `DPT4.002` | 1 Byte | Text | ISO-8859-1-Zeichen |

**DPT 5 — 8-Bit Vorzeichenlos**

| DPT | Grösse | Typ | Typische Verwendung |
|---|---|---|---|
| `DPT5.001` | 1 Byte | Zahl (0–100 %) | Dimmen / Jalousie-Position |
| `DPT5.003` | 1 Byte | Zahl (0–360°) | Winkel |
| `DPT5.004` | 1 Byte | Ganzzahl (0–255) | Prozent (unsigned) |
| `DPT5.010` | 1 Byte | Ganzzahl | Zählerwert |

**DPT 6 — 8-Bit Vorzeichenbehaftet**

| DPT | Grösse | Typ | Typische Verwendung |
|---|---|---|---|
| `DPT6.001` | 1 Byte | Ganzzahl (−128…127) | Relativer Wert (%) |
| `DPT6.010` | 1 Byte | Ganzzahl | Impulszähler (vorzeichenbehaftet) |

**DPT 7 — 16-Bit Vorzeichenlos**

| DPT | Grösse | Typ | Typische Verwendung |
|---|---|---|---|
| `DPT7.001` | 2 Byte | Ganzzahl (0–65535) | Impulszähler |
| `DPT7.002` | 2 Byte | Ganzzahl | Zeitraum (ms) |
| `DPT7.003` | 2 Byte | Ganzzahl | Zeitraum (10 ms) |
| `DPT7.004` | 2 Byte | Ganzzahl | Zeitraum (100 ms) |
| `DPT7.005` | 2 Byte | Ganzzahl | Zeitraum (s) |
| `DPT7.006` | 2 Byte | Ganzzahl | Zeitraum (min) |
| `DPT7.007` | 2 Byte | Ganzzahl | Zeitraum (h) |
| `DPT7.011` | 2 Byte | Ganzzahl | Länge (mm) |
| `DPT7.012` | 2 Byte | Ganzzahl | Stromstärke (mA) |
| `DPT7.013` | 2 Byte | Ganzzahl | Helligkeit (lx) |
| `DPT7.600` | 2 Byte | Ganzzahl | Farbtemperatur (K) |

**DPT 8 — 16-Bit Vorzeichenbehaftet**

| DPT | Grösse | Typ | Typische Verwendung |
|---|---|---|---|
| `DPT8.001` | 2 Byte | Ganzzahl | Impulszähler (vorzeichenbehaftet) |
| `DPT8.002` | 2 Byte | Ganzzahl | Zeitraum (ms) |
| `DPT8.005` | 2 Byte | Ganzzahl | Zeitraum (s) |
| `DPT8.010` | 2 Byte | Ganzzahl | Drehzahl-Differenz (1/min) |
| `DPT8.011` | 2 Byte | Ganzzahl | Prozent-Differenz |
| `DPT8.012` | 2 Byte | Ganzzahl | Rotationswinkel (°) |

**DPT 9 — 2-Byte KNX-Gleitkomma (EIS5)**

| DPT | Typische Verwendung |
|---|---|
| `DPT9.001` | Temperatur (°C) |
| `DPT9.002` | Temperaturdifferenz (K) |
| `DPT9.003` | Kelvin/Stunde (K/h) |
| `DPT9.004` | Windgeschwindigkeit (m/s) |
| `DPT9.005` | Luftdruck (Pa) |
| `DPT9.006` | Luftfeuchtigkeit (%) |
| `DPT9.007` | Luftfeuchtigkeit (% rH) |
| `DPT9.008` | CO₂-Konzentration (ppm) |
| `DPT9.009` | Spannung (mV) |
| `DPT9.010` | Leistung (W) |
| `DPT9.011` | Zeit (s) |
| `DPT9.020` | Spannung (mV) |
| `DPT9.021` | Strom (mA) |
| `DPT9.024` | Leistung (kW) |
| `DPT9.025` | Volumenfluss (l/h) |
| `DPT9.026` | Niederschlag (l/m²) |
| `DPT9.027` | Luftdruck (Pa) |
| `DPT9.028` | Windgeschwindigkeit (km/h) |
| `DPT9.029` | Absolute Luftfeuchtigkeit (g/m³) |
| `DPT9.030` | Einstrahlungsdichte (W/m²) |

**DPT 10, 11 — Uhrzeit und Datum**

| DPT | Grösse | Typ | Typische Verwendung |
|---|---|---|---|
| `DPT10.001` | 3 Byte | Text `HH:MM:SS` | Uhrzeit (inkl. Wochentag) |
| `DPT11.001` | 3 Byte | Text `JJJJ-MM-TT` | Datum |

**DPT 12, 13 — 32-Bit Integer**

| DPT | Grösse | Typ | Typische Verwendung |
|---|---|---|---|
| `DPT12.001` | 4 Byte | Ganzzahl (0–4 Mrd.) | Energiezähler (vorzeichenlos) |
| `DPT13.001` | 4 Byte | Ganzzahl (±2 Mrd.) | Impulszähler (vorzeichenbehaftet) |
| `DPT13.010` | 4 Byte | Ganzzahl | Wirkenergie (Wh) |
| `DPT13.013` | 4 Byte | Ganzzahl | Wirkenergie (kWh) |

**DPT 14 — 32-Bit IEEE-754-Gleitkomma (physikalische Grössen)**

| DPT | Typische Verwendung |
|---|---|
| `DPT14.000` | Beschleunigung (m/s²) |
| `DPT14.005` | Winkelgeschwindigkeit (rad/s) |
| `DPT14.007` | Fläche (m²) |
| `DPT14.012` | Kapazität (F) |
| `DPT14.017` | Dichte (kg/m³) |
| `DPT14.019` | Elektrischer Strom (A) |
| `DPT14.020` | Elektrische Feldstärke (V/m) |
| `DPT14.023` | Elektrisches Potential (V) |
| `DPT14.024` | Elektrische Spannung (V) |
| `DPT14.027` | Energie (J) |
| `DPT14.028` | Kraft (N) |
| `DPT14.029` | Frequenz (Hz) |
| `DPT14.033` | Wärmestrom (W) |
| `DPT14.039` | Länge (m) |
| `DPT14.046` | Lichtstrom (lm) |
| `DPT14.050` | Masse (kg) |
| `DPT14.055` | Leistung (W) |
| `DPT14.056` | Leistungsfaktor |
| `DPT14.058` | Druck (Pa) |
| `DPT14.065` | Widerstand (Ω) |
| `DPT14.066` | Winkelauflösung (°) |
| `DPT14.067` | Drehzahl (1/min) |
| `DPT14.068` | Geschwindigkeit (m/s) |
| `DPT14.069` | Drehmoment (Nm) |
| `DPT14.070` | Volumen (m³) |
| `DPT14.071` | Volumenfluss (m³/s) |
| `DPT14.075` | Scheinleistung (VA) |
| *(weitere DPT14.x)* | *Physikalische Mess­grössen* |

**DPT 16, 17, 18, 19 — Text, Szenen, Datum/Zeit**

| DPT | Grösse | Typ | Typische Verwendung |
|---|---|---|---|
| `DPT16.000` | 14 Byte | Text | ASCII-Text (14 Zeichen) |
| `DPT16.001` | 14 Byte | Text | ISO-8859-1-Text (14 Zeichen) |
| `DPT17.001` | 1 Byte | Ganzzahl | Szenennummer (0–63) |
| `DPT18.001` | 1 Byte | Ganzzahl | Szenen-Steuerung (inkl. Lernmodus) |
| `DPT19.001` | 8 Byte | ISO-8601-Text | Datum und Uhrzeit |

**DPT 20 — 1-Byte Enum/Modus**

| DPT | Typische Verwendung |
|---|---|
| `DPT20.001` | HVAC-Modus (Auto/Komfort/Standby/Nacht/Schutz) |
| `DPT20.002` | HVAC-Brennermodus |
| `DPT20.003` | HVAC-Gebläsemodus |
| `DPT20.004` | HVAC-Mastermodus |
| `DPT20.005` | HVAC-Statusmeldung |
| `DPT20.006` | HVAC-Positionswert |
| `DPT20.007` | DALI-Verblend-Modus |
| `DPT20.008` | Steuerungsverhalten |
| `DPT20.011` | Priorität |
| `DPT20.012` | Lichtsteuermodus |
| `DPT20.013` | Heizungsregelungsmodus |
| `DPT20.017` | Belüftungsmodus |
| `DPT20.020` | Alarmschwere |
| `DPT20.021` | Testmodus |
| `DPT20.100` | Gebäude-Betriebsmodus |
| `DPT20.102` | Aktiver Grundmodus |
| `DPT20.105` | Warmwasser-Modus (DHW) |
| `DPT20.111` | Heizklima-Modus |
| `DPT20.113` | Zeitprogramm |
| `DPT20.600` | Ventilator-Modus |
| `DPT20.601` | Heizungstyp |
| `DPT20.602` | Klappenventil-Modus |
| `DPT20.603` | Heizkreis-Modus |
| `DPT20.604` | Heizkörpermodus |
| *(weitere DPT20.x)* | *1-Byte Enums/Modi* |

**DPT 29 — 64-Bit Integer (Smart Metering)**

| DPT | Grösse | Typ | Typische Verwendung |
|---|---|---|---|
| `DPT29.010` | 8 Byte | Ganzzahl | Wirkenergie (Wh), hochauflösend |
| `DPT29.011` | 8 Byte | Ganzzahl | Scheinenergie (VAh) |
| `DPT29.012` | 8 Byte | Ganzzahl | Blindenergie (VARh) |

**DPT 219, 240 — Spezielle Typen**

| DPT | Grösse | Typ | Typische Verwendung |
|---|---|---|---|
| `DPT219.001` | 2 Byte | Ganzzahl | AlarmInfo (Modus + Statusbits) |
| `DPT240.800` | 3 Byte | JSON-Text | Jalousie-Kombination (Höhe % + Lamellen %) |

> **Hinweis für KNX-Dimmer:** Zwei separate Verknüpfungen anlegen — eine DEST für die Schreib-Adresse, eine SOURCE für die Rückmelde-Adresse.

---

### Modbus-TCP-Adapter

**Instanz-Konfiguration:**

| Feld | Standard | Beschreibung |
|---|---|---|
| `host` | — | IP-Adresse der Modbus-Gegenstelle |
| `port` | `502` | TCP-Port |
| `timeout` | `3.0` | Verbindungs-Timeout in Sekunden |

**Verknüpfungs-Konfiguration:**

| Feld | Werte | Beschreibung |
|---|---|---|
| `unit_id` | `1` | Modbus-Slave-ID (Geräteadresse) |
| `register_type` | `holding`, `input`, `coil`, `discrete_input` | Registertyp |
| `address` | Ganzzahl | Registeradresse (0-basiert) |
| `count` | `1` | Anzahl zu lesender Register |
| `data_format` | `uint16`, `int16`, `uint32`, `int32`, `float32`, `uint64`, `int64` | Datenformat |
| `scale_factor` | `1.0` | Rohwert × Faktor = Messwert |
| `byte_order` | `big` / `little` | Byte-Reihenfolge im Register |
| `word_order` | `big` / `little` | Wort-Reihenfolge bei 32/64-Bit-Werten |
| `poll_interval` | `1.0` | Abfrageintervall in Sekunden (nur SOURCE/BOTH) |

> **Praxistipp:** Die meisten Steuerungen (Siemens, Beckhoff …) verwenden `big`/`big`. Bei offensichtlich falschem Wert zuerst `word_order` auf `little` wechseln.

---

### Modbus-RTU-Adapter

Gleiche Verknüpfungs-Konfiguration wie TCP. Zusätzliche Instanz-Felder: `port` (z. B. `/dev/ttyUSB0`), `baudrate`, `parity`, `stopbits`, `bytesize`, `timeout`.

---

### 1-Wire-Adapter

Liest Temperatursensoren über den Linux-Systemordner (`/sys/bus/w1/…`). Auf Windows funktioniert der Adapter nicht, startet aber ohne Fehlermeldung.

**Instanz-Konfiguration:**

| Feld | Standard | Beschreibung |
|---|---|---|
| `poll_interval` | `30.0` | Abfrageintervall in Sekunden |
| `w1_path` | `/sys/bus/w1/devices` | Pfad zum 1-Wire-Systemordner |

**Verknüpfungs-Konfiguration:**

| Feld | Beschreibung |
|---|---|
| `sensor_id` | Sensor-ID, z. B. `28-0000012345ab` |
| `sensor_type` | Sensortyp, z. B. `DS18B20` (Standard) |

Verfügbare Sensor-IDs können über den Verbindungstest abgerufen werden.

---

### MQTT-Adapter (externer Broker)

Verbindet sich mit einem **externen** MQTT-Broker (getrennt vom internen Mosquitto).

**Instanz-Konfiguration:** `host`, `port`, `username`, `password`

**Verknüpfungs-Konfiguration:**

| Feld | Beschreibung |
|---|---|
| `topic` | Topic zum Empfangen (SOURCE/BOTH) |
| `publish_topic` | Topic zum Senden (DEST/BOTH) — Standard: gleich wie `topic` |
| `retain` | Retain-Flag beim Senden setzen |

---

### Home-Assistant-Adapter

Verbindet **open bridge server** bidirektional mit einer Home-Assistant-Instanz. Empfängt Zustandsänderungen in Echtzeit über WebSocket (`state_changed`-Ereignisse) und schreibt Werte über die HA-REST-API (Dienst-Aufrufe).

**Instanz-Konfiguration:**

| Feld | Standard | Beschreibung |
|---|---|---|
| `host` | `homeassistant.local` | Hostname oder IP-Adresse der HA-Instanz |
| `port` | `8123` | Port der HA-Weboberfläche |
| `token` | — | Long-Lived Access Token (Passwort-Feld) |
| `ssl` | `false` | HTTPS/WSS verwenden |

**Verknüpfungs-Konfiguration:**

| Feld | Beschreibung |
|---|---|
| `entity_id` | Home-Assistant-Entity-ID, z. B. `sensor.wohnzimmer_temperatur` |
| `attribute` | Optionales Attribut statt dem Hauptzustand, z. B. `unit_of_measurement` |
| `service_domain` | Dienst-Domain für Schreibbefehle, wird automatisch aus der Entity abgeleitet wenn leer |
| `service_name` | Dienst-Name: Standard `turn_on`/`turn_off` für Boolean, `set_value` sonst |
| `service_data_key` | Schlüssel für den Wert im Dienst-Aufruf, z. B. `brightness` oder `value` |

Textzustände wie `"on"`/`"off"`, `"true"`/`"false"` werden automatisch in Boolean-Werte umgewandelt. Numerische Texte werden als Zahl übergeben.

---

### ioBroker-Adapter

Verbindet **open bridge server** bidirektional mit einer ioBroker-Instanz über Socket.IO. Werte werden beim Verknüpfen initial gelesen und danach in Echtzeit über `stateChange`-Ereignisse aktualisiert; Schreibbefehle werden per `setState` an ioBroker gesendet.

**Instanz-Konfiguration:**

| Feld | Standard | Beschreibung |
|---|---|---|
| `host` | `iobroker.local` | Hostname oder IP-Adresse der ioBroker-Instanz |
| `port` | `8084` | Port des ioBroker Socket.IO/Web-Adapters |
| `username` | — | Optionaler Benutzername |
| `password` | — | Optionales Passwort (Passwort-Feld) |
| `ssl` | `false` | HTTPS verwenden |
| `path` | `/socket.io` | Socket.IO-Pfad |
| `access_token` | — | Optionaler Bearer/OAuth-Token (Passwort-Feld) |

**Verknüpfungs-Konfiguration:**

| Feld | Beschreibung |
|---|---|
| `state_id` | ioBroker-State-ID, z. B. `0_userdata.0.wohnzimmer.temperatur` |
| `command_state_id` | Optional abweichender State für Schreibbefehle, z. B. ein `.SET`-State |
| `ack` | Ack-Flag beim Schreiben (`false` = Befehl, `true` = bestätigter Status) |
| `source_data_type` | Optionaler Datentyp für eingehende Werte: `string`, `int`, `float`, `bool`, `json` |
| `json_key` | Optionaler Schlüssel zum Extrahieren eines Werts aus JSON |

Textzustände wie `"on"`/`"off"`, `"true"`/`"false"` werden automatisch in Boolean-Werte umgewandelt. Numerische Texte werden als Zahl übergeben. Für getrennte Status- und Befehlsobjekte kann `state_id` auf den Status und `command_state_id` auf den Befehls-State zeigen.

Entwicklungs- und Review-Notizen zur aktuellen Implementierung stehen in [`docs/iobroker-adapter.md`](docs/iobroker-adapter.md).

---

### Zeitschaltuhr-Adapter

Erzeugt zeitgesteuerte Ereignisse ohne externe Hardware — für tageszeit- oder sonnenstandsbasierte Automatisierungen, Feiertags- und Ferienlogik.

**Instanz-Konfiguration:**

| Feld | Standard | Beschreibung |
|---|---|---|
| `latitude` | `47.5` | Breitengrad für Sonnenstandsberechnung |
| `longitude` | `8.0` | Längengrad für Sonnenstandsberechnung |
| `altitude` | `400.0` | Höhe über NN in Metern |
| `timezone` | (App-Zeitzone) | IANA-Zeitzone; leer = Systemzeitzone von **open bridge server** verwenden |
| `holiday_country` | `CH` | ISO-3166-Ländercode für Feiertagskalender |
| `holiday_subdivision` | — | Kanton/Bundesland, z. B. `ZH` oder `BY` |
| `holiday_language` | `de` | Sprache für Feiertagsnamen |
| `vacation_1_start` … `vacation_6_end` | — | Bis zu 6 Ferienperioden im Format `JJJJ-MM-TT` |

**Verknüpfungs-Konfiguration:**

| Feld | Werte | Beschreibung |
|---|---|---|
| `timer_type` | `daily`, `annual`, `meta` | `daily` = täglich wiederkehrend; `annual` = einmaliges Datum; `meta` = Metadaten-Ausgang (Feiertag, Ferien) |
| `meta_type` | `holiday_today`, `holiday_tomorrow`, `holiday_name_today`, `holiday_name_tomorrow`, `vacation_1`…`vacation_6` | Für `timer_type = meta`: welcher Metadatenwert ausgegeben wird |
| `time_ref` | `absolute`, `sunrise`, `sunset`, `solar_noon`, `solar_altitude` | Zeitreferenz |
| `hour` / `minute` | `0`–`23` / `0`–`59` | Absolute Uhrzeit oder Offset zur Zeitreferenz |
| `offset_minutes` | Ganzzahl | Versatz zur Zeitreferenz in Minuten (positiv = später) |
| `solar_altitude_deg` | `-18`–`90` | Sonnenstand-Schwellwert in Grad (nur `solar_altitude`) |
| `sun_direction` | `rising`, `setting` | Aufsteigende oder absteigende Sonnenbahn (nur `solar_altitude`) |
| `weekdays` | Liste `[0–6]` | Wochentage (0 = Montag). Leer = alle. |
| `months` | Liste `[1–12]` | Monate. Leer = alle. |
| `day_of_month` | `0`–`31` | Tag im Monat; `0` = alle. |
| `every_hour` | `true`/`false` | Jede Stunde zur konfigurierten Minute auslösen |
| `every_minute` | `true`/`false` | Jede Minute auslösen |
| `holiday_mode` | `ignore`, `skip`, `only`, `as_sunday` | Verhalten an Feiertagen |
| `vacation_mode` | `ignore`, `skip`, `only`, `as_sunday` | Verhalten in Ferienperioden |
| `value` | Text | Wert der beim Auslösen geschrieben wird (Standard: `"1"`) |

**Feiertagsmodi:**

| Modus | Verhalten |
|---|---|
| `ignore` | Feiertage/Ferien werden wie normale Tage behandelt |
| `skip` | An diesen Tagen wird nicht ausgelöst |
| `only` | Auslösen nur an Feiertagen/Ferien |
| `as_sunday` | Feiertag/Ferientag wird für die Wochentagsprüfung als Sonntag (6) behandelt |

---

## MQTT-Topics

**open bridge server** verwendet zwei parallele Topic-Strategien:

| Topic | Beschreibung |
|---|---|
| `dp/{uuid}/value` | Stabil — ändert sich nie, sicher für Automatisierungen. Mit Retain gespeichert. |
| `dp/{uuid}/set` | Auf diesen Topic schreiben um einen Wert zu setzen |
| `dp/{uuid}/status` | Verbindungsstatus des Adapters (mit Retain) |
| `alias/{tag}/{name}/value` | Lesbar und durchsuchbar (nur wenn `mqtt_alias` gesetzt) |

**Nachrichtenformat (`dp/{uuid}/value`):**

```json
{ "v": 21.4, "u": "°C", "t": "2026-03-27T10:23:41.123Z", "q": "good" }
```

| Schlüssel | Bedeutung |
|---|---|
| `v` | Wert |
| `u` | Einheit |
| `t` | Zeitstempel (ISO 8601) |
| `q` | Qualität: `good` / `bad` / `uncertain` |

**Wert setzen:**
```bash
mosquitto_pub -t "dp/550e8400-.../set" -m '{"v": 22.5}'
```

---

## Datentypen

| Typ | Beschreibung | MQTT-Format |
|---|---|---|
| `BOOLEAN` | Ein/Aus | `true` / `false` |
| `INTEGER` | Ganze Zahl | Zahl |
| `FLOAT` | Dezimalzahl | Zahl |
| `STRING` | Text | Zeichenkette |
| `DATE` | Datum | `JJJJ-MM-TT` |
| `TIME` | Uhrzeit | `HH:MM:SS` |
| `DATETIME` | Datum und Uhrzeit | ISO 8601 mit Zeitzone |
| `UNKNOWN` | Unbekannt | Hexadezimal-Text |

Typumwandlungen sind verlustfrei wo möglich — bei Verlust wird eine Meldung ins Protokoll geschrieben.

---

## Einstellungen

Die Einstellungen sind über die Weboberfläche erreichbar (⚙ in der Seitenleiste).

**Allgemein:**
- **Zeitzone** — alle Zeitangaben in der Oberfläche werden in dieser Zeitzone dargestellt (Verlauf, RingBuffer, History-Suche, Astro-Block)
- **KNX-Projektdatei importieren** — ETS-Projektdatei (`.knxproj`) hochladen, um Gruppenadressen als Suchvorschläge im Verknüpfungs-Formular zu nutzen

**Verlauf:** Übersicht aller Datenpunkte mit History-Aufzeichnung. Datenpunkte mit deaktivierter Aufzeichnung (`record_history: false`) werden zuerst angezeigt. Aufzeichnung per Datenpunkt ein- und ausschalten.

**Passwort:** Eigenes Anmeldepasswort ändern

**Benutzer** (nur Administratoren): Benutzer anlegen, löschen, MQTT-Zugang verwalten

**API-Schlüssel:** Schlüssel für die Anbindung externer Systeme erstellen und widerrufen

**Sicherung:** Vollständige Konfiguration herunterladen oder einspielen

---

## Hilfsskripte

### Import-EtsGaCsv.ps1 — ETS-GA-Export importieren

Das Skript `scripts/Import-EtsGaCsv.ps1` liest einen ETS-GA-CSV-Export und legt je Gruppenadresse
automatisch einen DataPoint mit passendem Typ und Einheit an. Anschliessend wird eine
Verknüpfung zur angegebenen KNX-Adapter-Instanz erstellt.

**Voraussetzungen:** PowerShell 5.1 oder neuer, erreichbare **open bridge server**-Instanz, gültiger API-Schlüssel.

**Parameter:**

| Parameter | Pflicht | Beschreibung |
|---|---|---|
| `-Url` | ja | Basis-URL der **open bridge server**-Instanz, z.B. `http://localhost:8080` |
| `-ApiKey` | ja | API-Schlüssel (`obs_…`) |
| `-File` | ja | Pfad zur ETS-GA-CSV-Datei |
| `-Adapter` | ja | Name der KNX-Adapter-Instanz in **open bridge server** |
| `-LogFile` | nein | Pfad für Fehlerprotokoll; ohne Angabe werden Fehler auf der Konsole ausgegeben |
| `-Direction` | nein | Verknüpfungsrichtung: `SOURCE` (Standard), `DEST` oder `BOTH` |
| `-Encoding` | nein | Zeichenkodierung der CSV-Datei: `UTF8` (Standard) oder `Default` (ANSI/Windows-1252). ETS 5 exportiert i.d.R. ANSI, ETS 6 UTF-8. |

**CSV-Format (ETS 5/6 GA-Export):**

Der Export erfolgt in ETS über *Gruppenadressliste exportieren → CSV*. Das Skript erkennt Semikolon- und
Komma-Trennzeichen sowie deutschsprachige und englischsprachige Spaltenköpfe automatisch.

```
"Group name";"Address";"Central";"Unfiltered";"Description";"Comment";"DatapointType";"Security"
"Wohnzimmer Temperatur";"1/1/1";"";"";"";"";DPST-9-1;Auto
"Wohnzimmer Helligkeit";"1/1/2";"";"";"";"";DPST-9-2;Auto
"Rolllade EG Auf/Ab";"1/2/1";"";"";"";"";DPST-1-8;Auto
```

DPT-Angaben im Format `DPST-X-Y` (Haupt- und Subtyp) oder `DPT-X` (nur Haupttyp) werden
automatisch in das **open bridge server**-Format (`DPT9.001`) umgewandelt und der passende Datentyp (`FLOAT`,
`INTEGER`, `BOOLEAN`, `STRING`) sowie die Einheit werden gesetzt. Fehlt der DPT, wird `FLOAT`
ohne Einheit verwendet.

**Beispiel:**

```powershell
.\scripts\Import-EtsGaCsv.ps1 `
    -Url    http://localhost:8080 `
    -ApiKey obs_abc123 `
    -File   C:\Projekte\GA_Export.csv `
    -Adapter "KNX/IP"
```

ETS 5 (ANSI-Kodierung):

```powershell
.\scripts\Import-EtsGaCsv.ps1 `
    -Url      http://localhost:8080 `
    -ApiKey   obs_abc123 `
    -File     C:\Projekte\GA_Export.csv `
    -Adapter  "KNX/IP" `
    -Encoding Default
```

Mit Fehlerprotokoll:

```powershell
.\scripts\Import-EtsGaCsv.ps1 `
    -Url     http://localhost:8080 `
    -ApiKey  obs_abc123 `
    -File    C:\Projekte\GA_Export.csv `
    -Adapter "KNX/IP" `
    -LogFile C:\Projekte\import_errors.log
```

Das Skript läuft bei Einzelfehlern durch. Am Ende werden Anzahl der erfolgreich importierten,
übersprungenen (Zeilen ohne Adresse) und fehlgeschlagenen GAs ausgegeben.

---

## Visualisierung (Visu)

Die Visu-Oberfläche ist eine separate Single-Page-App (erreichbar unter `/visu/`), mit der interaktive Bedienoberflächen — sogenannte **Visu-Seiten** — erstellt und im Vollbildmodus auf Displays oder Tablets angezeigt werden können. Jede Seite besteht aus frei platzierbaren Widgets, die Datenpunkte anzeigen oder steuern.

### Grundriss- und Anlagenschema-Widget

Das **Grundriss-Widget** ermöglicht es, einen Gebäudegrundriss oder ein Anlagenschema als interaktiven Hintergrund in eine Visu-Seite einzubinden. Auf dem Bild lassen sich Bereiche (Polygone) definieren, beschriften und mit Aktionen verknüpfen — sowie Mini-Widgets direkt auf dem Plan platzieren.

#### Bild einbinden

Im Konfigurations-Panel des Widgets kann ein Bild hochgeladen werden (SVG, PNG oder JPG). Das Bild wird als Base64-Data-URL direkt im Konfig-JSON gespeichert — kein separater Upload-Endpunkt nötig. Bei Dateien über 2 MB erscheint ein Hinweis; für Grundrisse wird **SVG empfohlen**, da es verlustfrei skaliert.

Die **Rotation** des Bildes lässt sich in 90°-Schritten einstellen (0° / 90° / 180° / 270°), um Landscape-Grafiken direkt im Portrait-Modus verwenden zu können. 

#### Bereiche (Polygone) zeichnen

Mit dem Polygon-Werkzeug im Vollbild-Canvas lassen sich Bereiche auf dem Grundriss einzeichnen:

1. Im Konfigurations-Panel auf **Neuer Bereich** klicken — der Fullscreen-Canvas öffnet sich.
2. Durch Klicken auf die Arbeitsfläche werden Eckpunkte des Polygons gesetzt.
3. Das Polygon wird geschlossen, indem der erste Punkt erneut angeklickt oder **Enter** gedrückt wird.

Jedem Bereich können folgende Eigenschaften zugewiesen werden:

| Eigenschaft | Beschreibung |
|---|---|
| **Name** | Bezeichnung des Bereichs (z. B. „Wohnzimmer") |
| **Beschriftung anzeigen** | Schaltet die Textbeschriftung auf dem Plan ein/aus |
| **Beschriftungsfarbe** | Textfarbe der Bereichsbeschriftung |
| **Beschriftungsposition** | Durch Klick auf den Bereich im Canvas frei positionierbar |
| **Aktion bei Klick** | `Keine` oder `Navigation` — bei Navigation: Ziel-Visu-Seite auswählen |

#### Navigation zwischen Seiten

Wenn als Klick-Aktion **Navigation** gewählt wird, öffnet sich eine Seitenauswahl. Die gewählte Visu-Seite wird beim Klick auf den Bereich im Viewer direkt aufgerufen. So lassen sich z. B. Etagenpläne miteinander verknüpfen — Klick auf einen Raum öffnet eine Detailansicht.

#### Mini-Widgets platzieren

Auf dem Grundriss können beliebige **Mini-Widgets** (z. B. Schalter, Temperaturanzeige, Dimmregler) direkt auf dem Plan positioniert werden:

1. Im Konfigurations-Panel auf **Mini-Widget hinzufügen** klicken und den Widget-Typ wählen.
2. Auf **Positionieren** klicken — der Fullscreen-Canvas öffnet sich.
3. Das Mini-Widget per **Drag & Drop** an die gewünschte Stelle auf dem Plan ziehen.

Für jedes Mini-Widget lassen sich einstellen:

| Eigenschaft | Beschreibung |
|---|---|
| **Widget-Typ** | Beliebiger Visu-Widget-Typ (Schalter, Anzeige, Dimmer, …) |
| **Datenpunkt** | Steuert den Wert des Widgets (Hauptdatenpunkt) |
| **Status-Datenpunkt** | Optionaler zweiter Datenpunkt für den Anzeigestatus |
| **Breite / Höhe** | Größe des Mini-Widgets in Pixeln |
| **Sichtbar** | Blendet das Widget im Viewer ein oder aus |

Mini-Widgets drehen sich beim Rotieren des Grundrisses nicht mit — sie bleiben immer aufrecht und werden anhand der Bildkoordinaten korrekt über dem Grundriss positioniert.

---

## Entwicklung

### Lokale Entwicklung mit PyCharm

Das Repository enthält vorkonfigurierte [PyCharm](https://www.jetbrains.com/de-de/pycharm/)-Startkonfigurationen im Verzeichnis `.run/`. Nach dem Öffnen des Projekts stehen sie direkt in der Run-Auswahl zur Verfügung.

#### Einmalige Einrichtung

**1. Python-Umgebung anlegen**

```bash
cd openbridgeserver
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt -r requirements_dev.txt
```

In PyCharm unter **Settings → Project → Python Interpreter** den Interpreter `.venv/bin/python` auswählen.

**2. Frontend-Abhängigkeiten installieren**

```bash
cd gui && npm install
```

**3. Konfigurationsdatei anlegen**

```bash
cp config.example.yaml config.yaml
```

Folgende Werte in `config.yaml` anpassen:

```yaml
mqtt:
  username: obs
  password: change-this-mqtt-service-password   # muss mit .env übereinstimmen

database:
  path: /absoluter/pfad/zum/projekt/data/obs.db  # lokaler Pfad, kein /data

mosquitto:
  passwd_file: /absoluter/pfad/zum/projekt/data/mosquitto/passwd
  reload_pid: null
  reload_command: null
  service_username: obs
  service_password: change-this-mqtt-service-password
```

**4. Umgebungsvariablen einrichten**

```bash
cp .env.example .env   # falls noch nicht vorhanden
```

Die `.env`-Datei enthält das MQTT-Passwort, mit dem der Docker-Mosquitto initialisiert wird — dieser Wert muss mit `mqtt.password` in `config.yaml` übereinstimmen.

#### Starten

| Run-Konfiguration | Beschreibung |
|---|---|
| **OBS Mosquitto (Docker)** | Startet den MQTT-Broker via Docker |
| **OBS Backend** | Startet den FastAPI-Server auf `localhost:8080` |
| **OBS GUI (Admin)** | Startet den Vite-Dev-Server auf `localhost:5173` |
| **OBS Full Dev Stack** | Startet alle drei gleichzeitig (Compound) |

> **Voraussetzung:** Docker Desktop muss laufen (für den Mosquitto-Broker).

#### Erreichbare Dienste im Dev-Modus

| Dienst | Adresse |
|---|---|
| Admin-GUI | http://localhost:5173 |
| API (Swagger) | http://localhost:8080/docs |
| MQTT | localhost:1883 |

**Standardzugang:** `admin` / `admin`

#### Tests ausführen

```bash
# Nur Unit- und Adapter-Tests (kein Docker nötig)
pytest tests/unit/ tests/adapters/

# Alle Tests inkl. Integration (Docker muss laufen)
pytest tests/
```

#### Lint lokal (identisch zu GitHub CI)

```bash
# Nur prüfen (gleiches Verhalten wie CI-Job)
./tools/lint.sh --check

# Mit Auto-Fix
./tools/lint.sh --fix
```

---

### Starten ohne Docker

```bash
# Mosquitto (temporär)
docker run -d -p 1883:1883 eclipse-mosquitto:2

# Konfiguration
cp config.example.yaml config.yaml

# Server mit automatischem Neustart bei Codeänderungen
uvicorn obs.main:create_app --factory --reload --host 0.0.0.0 --port 8080
```

### Datenbankstruktur

Die Datenbank wird automatisch aktualisiert — jede neue Version fügt fehlende Tabellen und Spalten hinzu, ohne bestehende Daten zu verlieren. Aktuelle Version: **V21**.

| Tabelle | Inhalt |
|---|---|
| `datapoints` | Alle Datenpunkte (inkl. `persist_value`- und `record_history`-Flag) |
| `adapter_bindings` | Verknüpfungen zwischen Datenpunkten und Adaptern (inkl. `value_map`) |
| `adapter_instances` | Adapter-Instanzen |
| `users` | Benutzerkonten |
| `api_keys` | API-Schlüssel (nur als Hashwert gespeichert) |
| `history_values` | Werteverlauf (inkl. `source_adapter`) |
| `logic_graphs` | Logik-Graphen (inkl. gespeichertem Block-Zustand) |
| `app_settings` | Systemeinstellungen (z. B. Zeitzone) |
| `datapoint_last_values` | Letzter bekannter Wert je Datenpunkt — wird beim Start wiederhergestellt |

---

## Translations
We use [Weblate](https://hosted.weblate.org/projects/open-bridge-server)  to support language translations. Contributions are always welcome.

## Lizenz

MIT — kostenlos und quelloffen.
