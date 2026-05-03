# OBS Verbindungswege — Specification

**Version:** 0.1 (pre-implementation)
**Lizenz:** MIT
**Teil von:** OBS Dokumentation
**Stand:** 2026-05-03

---

## 1. Übersicht

Die OBS Mobile App unterstützt drei Verbindungswege zum OBS-Server. Jeder Weg deckt andere Betriebsszenarien ab und hat unterschiedliche Anforderungen an Infrastruktur und Konfiguration.

| Weg | Szenario | Offener Port nötig | TLS Pflicht | Aufwand |
|---|---|---|---|---|
| **1. Direkt** | Gleiche Netzwerk, VPN | Nein | Nein | Minimal |
| **2. Reverse Proxy** | Öffentlicher Zugriff | Ja (80/443) | Ja | Mittel |
| **3. Cloud Gateway** | Kein öffentlicher Zugang | Nein | Ja (Gateway-seitig) | Mittel |

---

## 2. Weg 1: Direkt (Lokal / VPN)

### Anwendungsfälle
- App und OBS im selben WLAN (Heimnetz, Betriebsnetz)
- Zugriff via VPN (WireGuard, OpenVPN, Tailscale)
- Entwicklung und Testing

### Funktionsweise

```
Mobile App ──── HTTP/HTTPS ────► OBS-Server (lokale IP)
               (direkt, kein Zwischenhop)
```

Die App verbindet sich direkt auf die IP-Adresse oder den Hostnamen des OBS-Servers im lokalen Netz.

### Konfiguration OBS

Keine speziellen Änderungen nötig. OBS läuft mit Standardkonfiguration.

```yaml
# config.yaml — keine Änderungen für Direktzugang
server:
  host: "0.0.0.0"
  port: 8000
```

### Konfiguration App (Benutzer)

```
Verbindungstyp: Direkt
Server-URL:     http://192.168.1.42:8000
                oder
                http://obs.local:8000   (falls mDNS verfügbar)
```

### Hinweise

- HTTP ist für lokale Verbindungen explizit erlaubt (App-Konfiguration `allowMixedContent`)
- Bei VPN: die VPN-interne IP des OBS-Servers verwenden
- mDNS (`obs.local`) funktioniert je nach Netzwerk- und Betriebssystem-Konfiguration nicht zuverlässig — IP-Adresse ist zuverlässiger

---

## 3. Weg 2: Reverse Proxy (Extern)

### Anwendungsfälle
- Zugriff von ausserhalb des Heimnetzes ohne VPN
- Professionelle Installationen mit eigenem Domainname
- Zertifikats-basierte Zugangskontrolle (mTLS)
- OBS hat eine öffentliche IP oder ist hinter einem Router mit Port-Forwarding

### Funktionsweise

```
Mobile App ──── HTTPS ────► nginx/Traefik ──── HTTP ────► OBS-Server
               (Internet)   (öffentliche IP)   (lokal)
```

### Konfiguration: nginx (empfohlen)

```nginx
# /etc/nginx/sites-available/obs
server {
    listen 443 ssl;
    server_name obs.example.com;

    ssl_certificate     /etc/letsencrypt/live/obs.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/obs.example.com/privkey.pem;

    # WebSocket-Support (für OBS Live-Daten)
    location / {
        proxy_pass         http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header   Upgrade $http_upgrade;
        proxy_set_header   Connection "upgrade";
        proxy_set_header   Host $host;
        proxy_set_header   X-Real-IP $remote_addr;
        proxy_read_timeout 3600s;   # WebSocket-Verbindungen lange offen halten
    }
}

server {
    listen 80;
    server_name obs.example.com;
    return 301 https://$host$request_uri;
}
```

### Option: Mutual TLS (mTLS) — Zertifikats-Authentifizierung

Für erhöhte Sicherheit kann nginx so konfiguriert werden, dass nur Clients mit einem gültigen Client-Zertifikat Zugang erhalten. Die Mobile App importiert das Client-Zertifikat einmalig bei der Einrichtung.

```nginx
server {
    listen 443 ssl;
    server_name obs.example.com;

    ssl_certificate     /etc/ssl/obs/server.crt;
    ssl_certificate_key /etc/ssl/obs/server.key;

    # Client-Zertifikat erzwingen
    ssl_client_certificate /etc/ssl/obs/ca.crt;
    ssl_verify_client      on;

    location / {
        proxy_pass http://127.0.0.1:8000;
        # ... (wie oben)
    }
}
```

#### Client-Zertifikat erstellen (Betreiber-Workflow)

```bash
# 1. CA erstellen (einmalig)
openssl genrsa -out ca.key 4096
openssl req -new -x509 -days 3650 -key ca.key -out ca.crt \
  -subj "/CN=OBS Client CA"

# 2. Client-Zertifikat für einen Benutzer erstellen
openssl genrsa -out client.key 2048
openssl req -new -key client.key -out client.csr \
  -subj "/CN=Daniel Mobile"
openssl x509 -req -days 365 -in client.csr \
  -CA ca.crt -CAkey ca.key -CAcreateserial -out client.crt

# 3. P12-Bundle für App-Import erstellen
openssl pkcs12 -export -out client.p12 \
  -inkey client.key -in client.crt -certfile ca.crt \
  -passout pass:temporaeres-passwort
```

Das `.p12`-File wird an den App-Benutzer übermittelt (z.B. via AirDrop, E-Mail). Er importiert es in der App, gibt das Passwort ein, danach kann das File gelöscht werden.

### Konfiguration: Traefik (Alternative)

```yaml
# traefik/dynamic/obs.yml
http:
  routers:
    obs:
      rule: "Host(`obs.example.com`)"
      entryPoints: ["websecure"]
      service: obs
      tls:
        certResolver: letsencrypt

  services:
    obs:
      loadBalancer:
        servers:
          - url: "http://127.0.0.1:8000"
        passHostHeader: true
```

### Konfiguration App (Benutzer)

```
Verbindungstyp: Extern (Reverse Proxy)
Server-URL:     https://obs.example.com
Client-Zertifikat: [Importiert: Daniel Mobile, gültig bis 2027-05-03]
```

---

## 4. Weg 3: Cloud Gateway

Vollständig beschrieben in `GATEWAY_SPEC.md`. Hier die relevante Betreiber-Konfiguration.

### Anwendungsfälle
- OBS hinter CGNAT (kein Port-Forwarding möglich)
- Provisorische Installationen ohne feste IP
- Benutzer ohne eigene DNS/Server-Infrastruktur

### Konfiguration OBS

```yaml
# config.yaml
gateway:
  enabled: true
  server_url: "wss://gateway.obs-cloud.example.com"
  instance_id: "mein-haus-obs"          # Eindeutiger Name, frei wählbar
  secret: "32-zeichen-zufaelliger-key"  # openssl rand -hex 32
  reconnect_interval: 30
```

### Konfiguration App (Benutzer)

```
Verbindungstyp: Cloud Gateway
Gateway-URL:    https://gateway.obs-cloud.example.com
Instance-ID:    mein-haus-obs
```

Der Benutzer erhält `Gateway-URL` und `Instance-ID` vom OBS-Betreiber. Das `secret` bleibt beim Betreiber — der App-Benutzer braucht es nicht.

---

## 5. Verbindungstyp-Auswahl: Entscheidungsbaum

```
Kann ich den OBS-Server über eine lokale IP oder VPN erreichen?
├── Ja → Weg 1: Direkt
└── Nein
    ├── Hat der OBS-Server eine öffentliche IP / Domain?
    │   ├── Ja → Weg 2: Reverse Proxy
    │   └── Nein (z.B. CGNAT, kein Port-Forwarding)
    │       └── Weg 3: Cloud Gateway
    └── Will ich ohne VPN auskommen, aber trotzdem maximale
        Kontrolle behalten?
        └── Weg 2: Reverse Proxy (mit eigenem Server)
```

---

## 6. Sicherheitsvergleich

| Aspekt | Direkt | Reverse Proxy | Gateway |
|---|---|---|---|
| Transport-Verschlüsselung | Optional (HTTP/HTTPS) | Erzwungen (HTTPS) | Erzwungen (WSS) |
| Zugangskontrolle | OBS JWT/PIN | OBS JWT/PIN + optional mTLS | OBS JWT/PIN |
| Angriffsfläche | Nur im LAN/VPN | Öffentlich erreichbar | Nur Gateway-Endpunkt öffentlich |
| Gateway kennt Inhalt | — | — | Nein (TLS, Byte-Relay) |
| Betreiber-Aufwand | Minimal | Mittel | Mittel |

### Empfehlungen

Für **Heimanwendungen** mit gelegentlichem Fernzugriff ist Weg 3 (Gateway) der einfachste Weg ohne eigene Infrastruktur.

Für **professionelle Installationen** mit fester IP und eigenem Domainname ist Weg 2 (Reverse Proxy) vorzuziehen — volle Kontrolle, keine externe Abhängigkeit.

Weg 1 (Direkt) ist immer die erste Wahl, wenn ein VPN vorhanden ist (z.B. WireGuard auf dem Heimrouter).

---

## 7. Mehrere Verbindungen in der App

Die App unterstützt mehrere gespeicherte Verbindungen. Dies ist nützlich für:
- OBS-Instanz Zuhause + OBS-Instanz im Büro
- Produktiv-System + Test-System
- Verschiedene Liegenschaften

Beim App-Start wählt der Benutzer die gewünschte Verbindung aus einer Liste, oder die zuletzt verwendete Verbindung wird automatisch versucht.

```
Meine Verbindungen
├── 🟢 Zuhause (Direkt)          zuletzt verbunden: vor 2 Min.
├── ⚪ Büro (Reverse Proxy)      zuletzt verbunden: gestern
└── ⚪ Ferienwohnung (Gateway)   zuletzt verbunden: vor 3 Wochen

[+ Neue Verbindung hinzufügen]
```
