# OBS Cloud Gateway — Specification

**Version:** 0.2 (multi-tenant)
**Lizenz:** MIT
**Teil von:** OBS Monorepo (`gateway/`)
**Stand:** 2026-05-03

---

## 1. Projektziel

Das OBS Cloud Gateway ermöglicht den Zugriff auf einen OBS-Server von ausserhalb des lokalen
Netzwerks, **ohne dass der OBS-Server eine öffentliche IP-Adresse benötigt oder Ports geöffnet
werden müssen**.

Beide Seiten — OBS-Server und Mobile App — bauen je eine **ausgehende** Verbindung zum
Gateway-Dienst auf. Der Gateway-Dienst koppelt die beiden Verbindungen zu einem transparenten
Tunnel.

Der Gateway-Dienst ist **mandantenfähig**: ein einzelner Gateway-Server kann beliebig viele
OBS-Instanzen (Kunden) bedienen, vollständig voneinander isoliert, mit eigenen Secrets und
konfigurierbaren Limits pro Instanz.

---

## 2. Kernprinzip

```
OBS Instanz A             Gateway-Dienst              App (Kunde A)
(kein offener Port)       (öffentliche IP)

  ── outbound WS ──────────► Hub ◄────── outbound HTTPS ──
                              │
                         ◄────┤────►  (Bytes, transparent)

OBS Instanz B                 │                         App (Kunde B)
  ── outbound WS ──────────► Hub ◄────── outbound HTTPS ──
                         ◄────┤────►

           (Instanzen sind vollständig isoliert)
```

Der Gateway-Dienst **versteht den Inhalt nicht** — er leitet Bytes durch. Das gesamte
OBS-Protokoll (REST, WebSocket, HTTP) läuft unverändert durch den Tunnel. Aus Sicht der
Visu-SPA ist es ein normaler HTTP/WebSocket-Server.

---

## 3. Technologie-Stack

| Komponente | Technologie | Begründung |
|---|---|---|
| Gateway-Server | Go (net/http, gorilla/websocket) | Hohe Concurrency, kleiner Speicherfootprint, Single Binary |
| Instanz-Registry | SQLite (mattn/go-sqlite3) | Kein externer DB-Server nötig, einfaches Backup |
| OBS-seitiger Client | Python (asyncio + websockets) | Passt zum OBS-Backend-Stack |
| App-seitiger Zugriff | HTTPS/WSS via Standard-WebView | Kein spezieller Client nötig |
| OBS-Authentifizierung | HMAC-SHA256, pro-Instanz-Secret | Kein JWT-Server nötig, stateless |
| Admin-Authentifizierung | Bearer-Token (Admin-Key) | Einfach, sicher für interne API |
| Secret-Speicherung | AES-GCM-Verschlüsselung mit Master-Key | Secret verlässt OBS nie im Klartext |
| Transport | WebSocket über TLS (WSS) | Firewall-freundlich, bidirektional |
| Deployment | Docker, einzelner Container | Selbst-hostbar oder Cloud |

---

## 4. Architektur

### Verzeichnisstruktur

```
obs/
└── gateway/
    ├── server/                        # Gateway-Dienst (Go)
    │   ├── main.go
    │   ├── config/
    │   │   └── config.go              # Env-Konfiguration
    │   ├── db/
    │   │   ├── schema.sql             # SQLite Schema
    │   │   ├── db.go                  # DB-Verbindung + Migrations
    │   │   └── instances.go           # CRUD für Instanzen
    │   ├── admin/
    │   │   └── handler.go             # Admin REST API
    │   ├── relay/
    │   │   ├── hub.go                 # Aktive Session-Verwaltung (in-memory)
    │   │   ├── session.go             # Einzelne Tunnel-Session
    │   │   └── pipe.go                # Byte-Weiterleitung
    │   ├── auth/
    │   │   └── token.go               # HMAC-Token-Verifikation + AES-GCM
    │   └── Dockerfile
    ├── client/                        # OBS-seitiger Client (Python)
    │   ├── gateway_client.py
    │   └── http_proxy.py
    └── docker-compose.yml
```

### Zwei Datenschichten

```
SQLite (persistente Instanz-Registry)        In-Memory Hub (aktive Sessions)
─────────────────────────────────────        ──────────────────────────────
instances                                    map[instance_id]*Session
  id, instance_id, name                        ObsConn *websocket.Conn
  secret_encrypted, secret_iv                  State (WAITING/CONNECTED)
  max_connections, enabled                     ActiveConnections int
  created_at, last_seen_at                     BytesRelayed int64
  bytes_relayed_total
  notes (Freitext für Admin)
```

Die SQLite-DB ist die einzige persistente Datenhaltung. Der in-memory Hub enthält nur den
flüchtigen Verbindungszustand und wird bei einem Neustart geleert — OBS-Clients reconnecten
automatisch.

---

## 5. Datenbankschema

```sql
-- gateway/server/db/schema.sql

CREATE TABLE instances (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id       TEXT    NOT NULL UNIQUE,  -- z.B. "kunde-mueller-halle-b"
    name              TEXT    NOT NULL,          -- Anzeigename: "Kunde Müller, Halle B"
    secret_encrypted  BLOB    NOT NULL,          -- AES-GCM verschlüsseltes HMAC-Secret
    secret_iv         BLOB    NOT NULL,          -- 12-byte IV für AES-GCM
    enabled           INTEGER NOT NULL DEFAULT 1,
    max_connections   INTEGER NOT NULL DEFAULT 5,     -- Max gleichzeitige App-Verbindungen
    max_payload_kb    INTEGER NOT NULL DEFAULT 10240, -- Max Payload pro Request in KB
    notes             TEXT,                      -- Freitext für den Administrator
    created_at        DATETIME NOT NULL DEFAULT (datetime('now')),
    last_seen_at      DATETIME,                  -- Letzter erfolgreicher OBS-Connect
    bytes_relayed     INTEGER NOT NULL DEFAULT 0 -- Kumulative Bytes (für Monitoring)
);

CREATE TABLE connection_log (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    instance_id  TEXT    NOT NULL,
    event        TEXT    NOT NULL,  -- 'obs_connect' | 'obs_disconnect' | 'app_connect' | 'app_disconnect'
    remote_addr  TEXT,
    ts           DATETIME NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_instances_instance_id ON instances(instance_id);
CREATE INDEX idx_log_instance_id       ON connection_log(instance_id);
CREATE INDEX idx_log_ts                ON connection_log(ts);
```

---

## 6. Admin API

Alle Admin-Endpunkte erfordern den Header `Authorization: Bearer {GATEWAY_ADMIN_KEY}`.

```
# Instanzen verwalten
GET    /admin/instances                        → Alle Instanzen auflisten
POST   /admin/instances                        → Neue Instanz anlegen (generiert Secret)
GET    /admin/instances/{instance_id}          → Einzelne Instanz
PATCH  /admin/instances/{instance_id}          → Konfiguration ändern (limits, enabled, notes)
DELETE /admin/instances/{instance_id}          → Instanz löschen

# Übersicht & Monitoring
GET    /health                                 → Öffentlicher Health-Check
GET    /admin/status                           → Gateway-Status (Sessions, Uptime, etc.)
GET    /admin/instances/{instance_id}/log      → Verbindungslog einer Instanz

# Secret rotieren
POST   /admin/instances/{instance_id}/rotate-secret  → Neues Secret generieren
```

### POST /admin/instances — Neue Instanz anlegen

Request:
```json
{
  "instance_id": "kunde-mueller-halle-b",
  "name": "Kunde Müller, Halle B",
  "max_connections": 5,
  "notes": "Inbetriebnahme 2026-05-10"
}
```

Response:
```json
{
  "instance_id": "kunde-mueller-halle-b",
  "name": "Kunde Müller, Halle B",
  "secret": "a7f3e2b1c4d58f9e0a1b2c3d4e5f6789",
  "gateway_url": "wss://gateway.obs-cloud.example.com",
  "client_url": "https://gateway.obs-cloud.example.com/client/kunde-mueller-halle-b/",
  "max_connections": 5,
  "created_at": "2026-05-03T14:30:00Z"
}
```

Das `secret` wird **nur einmal** in der Response zurückgegeben. Im Gateway wird nur der
AES-GCM-Ciphertext gespeichert. Bei Verlust muss das Secret via `rotate-secret` neu generiert
werden.

### GET /admin/status — Gateway-Status

```json
{
  "status": "ok",
  "uptime_seconds": 86400,
  "instances_total": 47,
  "instances_enabled": 45,
  "sessions_waiting": 38,
  "sessions_active": 12,
  "bytes_relayed_total": 1073741824
}
```

### GET /admin/instances — Alle Instanzen

```json
{
  "instances": [
    {
      "instance_id": "kunde-mueller-halle-b",
      "name": "Kunde Müller, Halle B",
      "enabled": true,
      "max_connections": 5,
      "active_connections": 2,
      "obs_connected": true,
      "last_seen_at": "2026-05-03T14:28:00Z",
      "bytes_relayed": 52428800,
      "notes": "Inbetriebnahme 2026-05-10"
    }
  ],
  "total": 47
}
```

### PATCH /admin/instances/{instance_id} — Konfiguration ändern

```json
{
  "enabled": false,
  "max_connections": 10,
  "notes": "Lizenz abgelaufen, Zugang gesperrt"
}
```

Felder sind optional — nur gesetzte Felder werden aktualisiert. `enabled: false` trennt eine
bestehende OBS-Verbindung sofort (Gateway sendet Close-Frame).

---

## 7. OBS-Connect mit Multi-Tenant-Verifikation

```go
// gateway/server/relay/hub.go

type Hub struct {
    sessions map[string]*Session    // instance_id → aktive Session
    db       *db.DB                 // Instanz-Registry
    mu       sync.RWMutex
}

func (h *Hub) HandleObsConnect(w http.ResponseWriter, r *http.Request) {
    token := extractBearerToken(r)
    if token == "" {
        http.Error(w, "Unauthorized", 401)
        return
    }

    // 1. instance_id aus Token extrahieren (ohne Verifikation)
    instanceID, err := auth.ParseInstanceID(token)
    if err != nil {
        http.Error(w, "Invalid token format", 401)
        return
    }

    // 2. Instanz in DB nachschlagen
    instance, err := h.db.GetInstance(instanceID)
    if err != nil || !instance.Enabled {
        http.Error(w, "Unknown or disabled instance", 403)
        return
    }

    // 3. HMAC-Signatur mit dem instanz-spezifischen Secret prüfen
    //    (Secret wird aus DB entschlüsselt, nie persistent im Speicher)
    if err := auth.VerifyToken(token, instance.SecretEncrypted, instance.SecretIV); err != nil {
        http.Error(w, "Invalid signature", 401)
        return
    }

    // 4. WebSocket upgraden
    conn, err := upgrader.Upgrade(w, r, nil)
    if err != nil {
        return
    }

    // 5. Bestehende Session ersetzen (Reconnect-Fall)
    h.mu.Lock()
    if old, exists := h.sessions[instanceID]; exists {
        old.close()
    }
    session := &Session{
        InstanceID:     instanceID,
        ObsConn:        conn,
        State:          StateWaiting,
        MaxConnections: instance.MaxConnections,
        MaxPayloadKB:   instance.MaxPayloadKB,
        CreatedAt:      time.Now(),
    }
    h.sessions[instanceID] = session
    h.mu.Unlock()

    // 6. Metadaten aktualisieren
    h.db.UpdateLastSeen(instanceID)
    h.db.LogEvent(instanceID, "obs_connect", r.RemoteAddr)

    // 7. Session bedienen bis Verbindung getrennt wird
    session.serve()
    h.db.LogEvent(instanceID, "obs_disconnect", r.RemoteAddr)
}
```

---

## 8. Sicherheitsdesign: Secret-Speicherung

### Warum nicht bcrypt

bcrypt ist für Passwort-Hashing konzipiert: das Klartextpasswort wird zur Verifikation benötigt,
der Hash erlaubt keine Rekonstruktion. Für HMAC-Verifikation muss der Gateway das Klartext-Secret
kennen — er muss die Signatur selbst berechnen. Daher wird das Secret verschlüsselt gespeichert,
nicht gehasht.

### AES-GCM-Verschlüsselung

```
Gateway-Deployment:
  GATEWAY_MASTER_KEY = random 32 bytes   (Umgebungsvariable, nie in DB)

In SQLite pro Instanz:
  secret_encrypted = AES-256-GCM-Encrypt(instance_secret, GATEWAY_MASTER_KEY)
  secret_iv        = random 12 bytes (einmalig bei Instanz-Erstellung)
```

```go
// gateway/server/auth/token.go

func VerifyToken(token string, encryptedSecret, iv, masterKey []byte) error {
    // 1. Secret für diese Anfrage entschlüsseln
    secret, err := aesGCMDecrypt(encryptedSecret, iv, masterKey)
    if err != nil {
        return errors.New("Secret-Entschlüsselung fehlgeschlagen")
    }
    defer zeroBytes(secret) // Secret sofort nach Verwendung aus Speicher löschen

    // 2. Token parsen: instance_id:timestamp:hmac_signature
    parts := strings.SplitN(token, ":", 3)
    if len(parts) != 3 {
        return errors.New("ungültiges Token-Format")
    }
    instanceID, tsStr, receivedSig := parts[0], parts[1], parts[2]

    // 3. Zeitfenster prüfen (±5 Minuten toleriert Clock-Skew)
    ts, err := strconv.ParseInt(tsStr, 10, 64)
    if err != nil {
        return errors.New("ungültiger Timestamp")
    }
    if abs(time.Now().Unix()-ts) > 300 {
        return errors.New("Token abgelaufen")
    }

    // 4. HMAC neu berechnen und vergleichen
    mac := hmac.New(sha256.New, secret)
    mac.Write([]byte(instanceID + ":" + tsStr))
    expectedSig := hex.EncodeToString(mac.Sum(nil))

    if !hmac.Equal([]byte(receivedSig), []byte(expectedSig)) {
        return errors.New("ungültige Signatur")
    }

    return nil
}
```

### Sicherheitseigenschaften

| Eigenschaft | Umsetzung |
|---|---|
| Kein offener Port auf OBS | Nur ausgehende Verbindungen |
| Pro-Instanz-Isolation | Eigenes Secret pro Instanz — Kompromittierung betrifft nur diese Instanz |
| Secret-Schutz im Gateway | AES-GCM mit Master-Key aus Env-Variable, nie im Klartext in der DB |
| Secret nur einmalig sichtbar | Nur bei Erstellung und Rotation, danach nur als Ciphertext |
| Replay-Schutz | Timestamp im Token, ±5-Minuten-Fenster |
| Instanz sofort sperrbar | `enabled: false` trennt bestehende Verbindung sofort |
| Admin-API abgesichert | Separater `GATEWAY_ADMIN_KEY`, empfohlen: nicht öffentlich erreichbar |
| Kunden-Isolation | Verbindungen verschiedener Instanzen werden nie zusammengeführt |
| Gateway kennt Inhalt nicht | Bytes nach TLS-Terminierung ungeparsed weitergeleitet |

### Was der Gateway-Dienst sieht

Sichtbar: welche `instance_id` verbunden ist, IP-Adressen (Connection Log), übertragene Bytes,
Verbindungszeitpunkte.

Nicht sichtbar: Inhalte der Daten, OBS-Datenpunkte, Messwerte, Konfigurationen,
Benutzeranmeldedaten, PINs.

---

## 9. Verbindungslimit pro Instanz

```go
// gateway/server/relay/session.go

func (s *Session) HandleAppConnection(w http.ResponseWriter, r *http.Request) {
    s.mu.Lock()
    if s.ActiveConnections >= s.MaxConnections {
        s.mu.Unlock()
        http.Error(w, "Too many connections for this instance", 429)
        return
    }
    s.ActiveConnections++
    s.mu.Unlock()

    defer func() {
        s.mu.Lock()
        s.ActiveConnections--
        s.mu.Unlock()
    }()

    // ... Request proxyen oder WebSocket tunneln
}
```

`max_connections` ist pro Instanz konfigurierbar und ermöglicht differenzierte Service-Tiers,
zum Beispiel: Free: 2 gleichzeitige App-Verbindungen, Standard: 5, Pro: unbegrenzt.

---

## 10. OBS-seitiger Gateway-Client (Python)

### Konfiguration in config.yaml

```yaml
# obs/config.yaml
gateway:
  enabled: true
  server_url: "wss://gateway.obs-cloud.example.com"
  instance_id: "kunde-mueller-halle-b"          # Vom Gateway-Admin vergeben
  secret: "a7f3e2b1c4d58f9e0a1b2c3d4e5f6789"   # Einmalig bei Instanz-Erstellung erhalten
  reconnect_interval: 30                         # Sekunden bis Reconnect-Versuch
  # App-Zugriff via: https://gateway.obs-cloud.example.com/client/kunde-mueller-halle-b/
```

### Gateway-Client

```python
# gateway/client/gateway_client.py

import asyncio
import json
import hmac as hmac_lib
import hashlib
import time
import httpx
import websockets
import logging

logger = logging.getLogger(__name__)

class GatewayClient:
    """
    Baut eine ausgehende WS-Verbindung zum Gateway auf.
    Empfängt HTTP-Proxy-Requests und beantwortet sie via lokale OBS-API.
    """

    def __init__(self, config: dict):
        self.server_url = config['server_url']
        self.instance_id = config['instance_id']
        self.secret = config['secret'].encode()
        self.reconnect_interval = config.get('reconnect_interval', 30)
        self.local_obs_url = "http://127.0.0.1:8000"
        self._running = False

    def _make_token(self) -> str:
        """HMAC-SHA256 Token: instance_id:timestamp:signature"""
        timestamp = str(int(time.time()))
        payload = f"{self.instance_id}:{timestamp}"
        signature = hmac_lib.new(
            self.secret,
            payload.encode(),
            hashlib.sha256
        ).hexdigest()
        return f"{payload}:{signature}"

    async def connect_and_serve(self):
        """Hauptschleife mit automatischem Reconnect"""
        self._running = True
        while self._running:
            try:
                await self._run_session()
            except websockets.exceptions.InvalidStatus as e:
                if e.response.status_code in (401, 403):
                    logger.error(
                        "Gateway: Authentifizierung fehlgeschlagen "
                        "(instance_id oder secret falsch). Kein Retry."
                    )
                    self._running = False
                    return
                logger.warning(f"Gateway abgelehnt ({e}), Retry in {self.reconnect_interval}s")
                await asyncio.sleep(self.reconnect_interval)
            except Exception as e:
                logger.warning(f"Gateway getrennt ({e}), Retry in {self.reconnect_interval}s")
                await asyncio.sleep(self.reconnect_interval)

    async def _run_session(self):
        url = f"{self.server_url}/obs/connect"
        headers = {"Authorization": f"Bearer {self._make_token()}"}

        async with websockets.connect(url, extra_headers=headers) as ws:
            client_url = (
                f"{self.server_url.replace('wss://', 'https://')}"
                f"/client/{self.instance_id}/"
            )
            logger.info(f"Gateway verbunden | App-URL: {client_url}")

            async for message in ws:
                request = json.loads(message)
                response = await self._handle_request(request)
                await ws.send(json.dumps(response))

    async def _handle_request(self, req: dict) -> dict:
        """Leitet HTTP-Request an lokale OBS-API weiter"""
        async with httpx.AsyncClient() as client:
            try:
                r = await client.request(
                    method=req['method'],
                    url=f"{self.local_obs_url}{req['path']}",
                    params=req.get('query'),
                    headers=req.get('headers', {}),
                    content=req.get('body', b''),
                    timeout=25.0,
                )
                return {
                    "id": req['id'],
                    "status_code": r.status_code,
                    "headers": dict(r.headers),
                    "body": r.content.decode('utf-8', errors='replace'),
                }
            except Exception as e:
                logger.error(f"Lokaler OBS-Request fehlgeschlagen: {e}")
                return {
                    "id": req['id'],
                    "status_code": 502,
                    "headers": {},
                    "body": f"Gateway-Client-Fehler: {e}",
                }
```

### Integration in OBS-Backend

```python
# backend/main.py (Erweiterung)
from gateway.client.gateway_client import GatewayClient

@asynccontextmanager
async def lifespan(app: FastAPI):
    # ... bestehende Startup-Logik ...

    if settings.gateway.enabled:
        client = GatewayClient(settings.gateway.model_dump())
        asyncio.create_task(client.connect_and_serve())
        logger.info(f"Gateway-Client gestartet: {settings.gateway.instance_id}")

    yield
    # Shutdown-Logik ...
```

---

## 11. Deployment

### Umgebungsvariablen

```bash
# Pflicht
GATEWAY_ADMIN_KEY="64-byte-zufaelliger-admin-schluessel"   # Admin-API Zugang
GATEWAY_MASTER_KEY="32-byte-zufaelliger-master-key"        # AES-256-GCM für Secrets

# Optional
GATEWAY_MAX_SESSIONS=500              # Hard-Limit über alle Instanzen
GATEWAY_SESSION_TIMEOUT=3600          # Inaktivitäts-Timeout in Sekunden
GATEWAY_DB_PATH="/data/gateway.db"    # SQLite-Pfad (persistent mounten!)
GATEWAY_TLS_CERT="/certs/cert.pem"
GATEWAY_TLS_KEY="/certs/key.pem"
GATEWAY_LOG_LEVEL="info"
GATEWAY_PORT=8443
```

### Docker Compose

```yaml
# gateway/docker-compose.yml
version: '3.8'
services:
  obs-gateway:
    image: obs-gateway:latest
    build: ./server
    ports:
      - "443:8443"
    environment:
      GATEWAY_ADMIN_KEY: "${GATEWAY_ADMIN_KEY}"
      GATEWAY_MASTER_KEY: "${GATEWAY_MASTER_KEY}"
      GATEWAY_MAX_SESSIONS: "500"
      GATEWAY_SESSION_TIMEOUT: "3600"
      GATEWAY_DB_PATH: "/data/gateway.db"
      GATEWAY_TLS_CERT: "/certs/cert.pem"
      GATEWAY_TLS_KEY: "/certs/key.pem"
    volumes:
      - gateway-data:/data           # SQLite persistent
      - ./certs:/certs:ro
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wget", "-q", "-O-", "http://localhost:8443/health"]
      interval: 30s
      timeout: 5s
      retries: 3

volumes:
  gateway-data:
```

### Admin-API absichern (nginx)

Die Admin-API (`/admin/*`) sollte **nicht direkt aus dem Internet erreichbar** sein:

```nginx
# nginx vorgelagert
location /admin/ {
    allow 10.0.0.0/8;      # Nur aus dem internen Netz / VPN
    allow 172.16.0.0/12;
    deny all;
    proxy_pass http://127.0.0.1:8443;
}

location / {
    proxy_pass http://127.0.0.1:8443;
    proxy_http_version 1.1;
    proxy_set_header Upgrade $http_upgrade;
    proxy_set_header Connection "upgrade";
    proxy_read_timeout 3600s;
}
```

---

## 12. Betreiber-Workflow

### Neuen Kunden onboarden

```bash
# 1. Instanz anlegen
curl -s -X POST https://gateway.obs-cloud.example.com/admin/instances \
  -H "Authorization: Bearer ${GATEWAY_ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{
    "instance_id": "kunde-mueller-halle-b",
    "name": "Kunde Müller, Halle B",
    "max_connections": 5,
    "notes": "Inbetriebnahme 2026-05-10"
  }'

# Response (secret nur einmalig sichtbar):
# {
#   "instance_id": "kunde-mueller-halle-b",
#   "secret": "a7f3e2b1c4d58f9e0a1b2c3d4e5f6789",
#   "client_url": "https://gateway.obs-cloud.example.com/client/kunde-mueller-halle-b/",
#   ...
# }

# 2. Dem Kunden sicher mitteilen (verschlüsselte E-Mail o.ä.):
#    gateway_url:  wss://gateway.obs-cloud.example.com
#    instance_id:  kunde-mueller-halle-b
#    secret:       a7f3e2b1c4d58f9e0a1b2c3d4e5f6789
#    client_url:   https://gateway.obs-cloud.example.com/client/kunde-mueller-halle-b/

# 3. Status prüfen (nach OBS-Start beim Kunden)
curl -s https://gateway.obs-cloud.example.com/admin/instances/kunde-mueller-halle-b \
  -H "Authorization: Bearer ${GATEWAY_ADMIN_KEY}"
# → "obs_connected": true
```

### Kunden-Zugang sofort sperren

```bash
curl -s -X PATCH \
  https://gateway.obs-cloud.example.com/admin/instances/kunde-mueller-halle-b \
  -H "Authorization: Bearer ${GATEWAY_ADMIN_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"enabled": false, "notes": "Vertrag beendet 2026-12-31"}'
# → Bestehende OBS-Verbindung wird sofort getrennt, App-Zugriff blockiert
```

### Secret rotieren (bei Kompromittierung)

```bash
curl -s -X POST \
  https://gateway.obs-cloud.example.com/admin/instances/kunde-mueller-halle-b/rotate-secret \
  -H "Authorization: Bearer ${GATEWAY_ADMIN_KEY}"
# → { "secret": "neues-geheimnis-xyz..." }
# Neues Secret an Kunden kommunizieren → Kunde aktualisiert config.yaml → OBS-Neustart
```

---

## 13. Skalierung und Limiten

| Parameter | Default | Scope |
|---|---|---|
| Max. Sessions gesamt | 500 | Gateway-Server (`GATEWAY_MAX_SESSIONS`) |
| Max. App-Verbindungen | 5 | Pro Instanz (DB, jederzeit änderbar) |
| Max. Payload pro Request | 10 MB | Pro Instanz (DB, jederzeit änderbar) |
| Session-Timeout (Inaktivität) | 3600s | Gateway-Server (`GATEWAY_SESSION_TIMEOUT`) |
| Reconnect-Intervall OBS | 30s | OBS `config.yaml` |
| Token-Zeitfenster | 300s | Fix (Security) |

Ein einzelner Gateway-Server mit 2 vCPU / 1 GB RAM unterstützt typisch 200–500 gleichzeitig
verbundene OBS-Instanzen. Die meisten befinden sich im WAITING-Zustand und erzeugen kaum Last.

Für grössere Deployments: mehrere Gateway-Server hinter einem Load-Balancer mit geteilter SQLite
auf einem NFS-Volume, oder Migration zu PostgreSQL (nur Treiberänderung, kein Code-Umbau nötig
bei Verwendung von `database/sql`).

---

## 14. Implementierungsreihenfolge

### Phase G1 — Gateway-Server Fundament
1. Go-Modul-Setup, Verzeichnisstruktur
2. SQLite-Schema, DB-Layer und Migrations (`db/`)
3. AES-GCM Secret-Ver-/Entschlüsselung (`auth/`)
4. Admin-API: Instanzen CRUD inkl. Secret-Generierung (`admin/handler.go`)
5. HMAC-Token-Verifikation mit DB-Lookup (`auth/token.go`)
6. Hub mit Multi-Tenant-Session-Verwaltung (`relay/hub.go`)
7. OBS-Connect-Endpunkt

### Phase G2 — Relay-Logik
8. HTTP-Proxy für App-Requests (mit Timeout und Payload-Limit)
9. WebSocket-Tunneling bidirektional (`relay/pipe.go`)
10. Verbindungslimit pro Instanz (429-Handling)
11. Connection-Log in SQLite
12. `/health` und `/admin/status` Endpunkte

### Phase G3 — OBS-Client
13. `GatewayClient` Python-Klasse mit Reconnect-Logik
14. HMAC-Token-Generierung
15. HTTP-Request-Handler und WebSocket-Upgrade-Handling
16. Integration in OBS-Backend `lifespan`
17. Konfigurationsblock in `config.yaml` und Pydantic-Settings

### Phase G4 — Integration & Deployment
18. Verbindungstyp "Gateway" in `ServerConfig.vue` (Mobile App)
19. Docker-Image, Compose-File, nginx-Konfiguration
20. End-to-End-Test: Admin anlegt Instanz → OBS verbindet → App verbindet → Live-Daten

---

## 15. Abgrenzung

- **Kein** VPN-Ersatz — ausschliesslich für Visu-Zugriff
- **Keine** Ende-zu-Ende-Verschlüsselung auf Applikationsebene — TLS reicht für diesen Anwendungsfall
- **Keine** Persistenz von Nachrichten — Gateway puffert nichts
- **Keine** Authentifizierung von App-Benutzern — das übernimmt OBS via JWT/PIN
- **Kein** Load-Balancing zwischen mehreren OBS-Instanzen hinter einer `instance_id`
- **Keine** eingebaute Web-Oberfläche für Admin — nur REST API (ein Admin-UI kann separat gebaut werden)
