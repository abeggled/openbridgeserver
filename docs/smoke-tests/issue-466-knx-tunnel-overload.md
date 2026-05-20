# Smoke-Test: KNX-Tunnel-Pool-Überlastungs-Warnung (Issue #466)

Diese Anleitung verifiziert, dass die UI sichtbar warnt, wenn der KNX/IP-Tunnel-Pool
eines Gateways mehrfach in kurzer Zeit Disconnects produziert. Sie ist für Reviewer
des zugehörigen Pull-Requests gedacht und beschreibt drei Pfade — wähle den, der
zu deinem Setup passt.

| Pfad | Setup | Was es zeigt | Aufwand |
|---|---|---|---|
| **A — UI-Sichtkontrolle** | Lokales Backend + GUI, Adapter angelegt, Browser-Devtools | Reine UI-Reaktion: Ampel, Badge, Sidebar-Bubble, Dashboard-Kachel | 5 min |
| **B — Backend-Verhalten** | `pytest` lokal | Detection + Severity-Lifecycle Ende-zu-Ende | 30 s |
| **C — End-to-End mit Hardware** | Drei `obs`-Stacks an einem realen KNX/IP-Gateway | Definitiver Beweis am echten Pool-Overload | 30 min, Hardware nötig |

Bei minimaler Reviewer-Zeit reicht **A + B**. Pfad C ist die Goldstandard-Verifikation
für jemanden mit Hardware-Zugriff.

---

## Erwartetes Verhalten (alle Pfade)

Wenn binnen 5 min **3 oder mehr** Tunnel-Disconnects auf demselben KNX-Adapter eingehen:

- Backend publisht ein `AdapterStatusEvent(severity="warning",
  detail="KNX-Tunnel-Slot wahrscheinlich von anderem Client belegt — Gateway-Pool überlastet.")`
  ohne das `connected`-Flag zu kippen.
- REST-Antwort `/api/v1/adapters/instances` enthält für diese Instanz
  `severity: "warning"` und `status_detail: "KNX-Tunnel-Slot …"`.
- Folge-Disconnects im aktiven Warn-Fenster führen **nicht** zu weiteren
  Warning-Events (kein Event-Spam).
- Nachdem das 5-min-Fenster ohne neue Disconnects vergeht und ein Reconnect-Event
  eintrifft, kippt der Status zurück auf `severity="ok"`.

## Erwartete UI-Reaktion

| Stelle | Vorher | Nach Schwellenüberschreitung |
|---|---|---|
| Adapter-Karte (`/adapters`) Ampel | grün (Verbunden) | **gelb** (Eingeschränkt) |
| Adapter-Karte Badge | "Verbunden" / `success` | "Eingeschränkt" / `warning` |
| Adapter-Karte Detail-Banner | ausgeblendet | gelb, mit `KNX-Tunnel-Slot …` |
| Sidebar-Menüpunkt "Adapter" | ohne Bubble | **`⚠ 1`** in amber/gelb |
| Dashboard (`/`) Kachel "Aktive Warnungen" | ausgeblendet | sichtbar, Einklicker zu `/adapters` |

---

## Pfad A — UI-Sichtkontrolle (ohne Hardware)

Diese Variante zwingt die GUI-Komponenten in den Warn-Zustand, ohne dass ein
echter KNX-Gateway oder ein Backend-Trick nötig wäre. Sie testet ausschließlich
die UI — das Backend-Verhalten deckt Pfad B ab.

### Voraussetzungen

- Vite-Dev-Server für die GUI (`npm run dev`) — wichtig: der Pinia-Store ist
  **nur** im Dev-Build über die Browser-Konsole greifbar. Im
  Production-Container-Build (`/gui_dist/`) ist `__vue_app__` minifiziert.
- Backend (lokal `python -m obs` oder Docker-Container) auf dem Port, den
  der Vite-Proxy in `gui/vite.config.js` als `proxy['/api'].target` anspricht.
- **Keine** Browser-Extension nötig (das Snippet unten greift Pinia direkt
  über die Vue-App-Instance).

### Vorbereitung

```bash
# Backend
python -m obs                            # default: Port 8080

# In zweitem Terminal: GUI Dev-Server
cd gui && npm install && npm run dev     # öffnet http://localhost:5173
```

Falls das Backend auf einem anderen Port läuft (z. B. Docker-Stack auf 8083),
in `gui/vite.config.js` temporär `proxy['/api'].target` anpassen
**(diesen Edit vor dem Commit revertieren!)**.

Im Browser `http://localhost:5173` öffnen, einloggen (`admin`/`admin`).
Falls noch keine KNX-Instanz vorhanden ist: unter `/adapters` eine anlegen
(`Adapter-Typ = KNX`, Defaults reichen). Sie verbindet sich mangels Gateway
nicht — irrelevant, wir manipulieren gleich nur den Frontend-State.

### Snippet — Pinia greifen und Polling sperren

Browser-Devtools öffnen (Cmd-Opt-I / F12) → Tab **Console**, einmalig
einfügen und ausführen:

```js
// Vue-App-Instance → Pinia → adapters-Store (alles ohne Extension).
const app    = document.querySelector('#app').__vue_app__
const pinia  = app.config.globalProperties.$pinia
const store  = pinia._s.get('adapters')
const knx    = store.instances.find(a => a.adapter_type === 'KNX')

// Background-Polling sperren, damit der Mock nicht alle 10–30 s
// vom Backend überschrieben wird. (Revert: store.fetchAdapters = __origFetch)
window.__origFetch    = store.fetchAdapters
store.fetchAdapters   = async () => {}

// Convenience-Setter für die drei Zustände:
window.setSev = (sev, detail = '') => { knx.severity = sev; knx.status_detail = detail }

console.log('✅ Pinia gesperrt. Verwende setSev("warning", "…") / setSev("error", "…") / setSev("ok").')
```

### Schritt 1 — Warning-Zustand prüfen

In der Konsole:

```js
setSev('warning', 'KNX-Tunnel-Slot wahrscheinlich von anderem Client belegt — Gateway-Pool überlastet.')
```

Auf der **`/adapters`**-Seite — am Eintrag "Smoke466 KNX" (oder Deinem
KNX-Adapter) — sollten **alle vier** folgenden Punkte erfüllt sein:

| # | Wo? | Vorher | Nachher |
|---|---|---|---|
| 1 | Kleine Status-Ampel **ganz links neben dem Adapternamen** | grüner Punkt | **gelber Punkt** (kein Pulsieren) |
| 2 | Badge **rechts neben dem KNX-Typ-Label** | grünes "Verbunden" | **gelbes "Eingeschränkt"** |
| 3 | **Unter** dem Card-Header (zwischen Header und Verknüpfungs-Zeile) | nicht vorhanden | gelber Hinweis-Streifen mit Warn-Icon + Detail-Text |
| 4 | Sidebar links, Menüpunkt **"Adapter"** | nur Icon + Label | zusätzlich **gelbe `⚠ 1`**-Bubble rechts |

Sidebar-Bubble bleibt auch sichtbar, wenn Du die Sidebar einklappst
(Pfeil-Button unten links) — sie wandert dann als kleiner Marker an
das Icon.

Anschließend in den **Dashboard-Bereich `/` (Übersicht) navigieren**:

| # | Wo? | Erwartung |
|---|---|---|
| 5 | Zwischen den vier StatCards oben und dem "Adapter Status"-Grid | **neue Kachel** "Aktive Warnungen (1)" mit gelbem linken Rand |
| 6 | Innerhalb der Warnungs-Kachel | Zeile mit Adaptername + KNX-Badge + gelbem "Warnung"-Badge + Detail-Text |
| 7 | Klick auf die Zeile | Navigation zurück nach `/adapters` |
| 8 | In der "Adapter Status"-Kachel rechts daneben | dieselbe KNX-Zeile zeigt jetzt **gelben Punkt** + **"Eingeschränkt"**-Badge statt grün |

### Schritt 2 — Error-Zustand prüfen

```js
setSev('error', 'KNX Backbone-Key ungültig (kein Hex-String)')
```

| # | Wo? | Erwartung |
|---|---|---|
| 9 | Adapter-Karte | **roter Punkt**, Badge **"Fehler"** (rot), roter Detail-Banner |
| 10 | Sidebar-Bubble | wird **rot** statt gelb |
| 11 | Dashboard-Kachel-Border links | wird **rot** |
| 12 | Dashboard-Adapter-Status-Zeile | **roter Punkt** + "Fehler"-Badge |

### Schritt 3 — Recovery prüfen

```js
setSev('ok', '')
```

| # | Wo? | Erwartung |
|---|---|---|
| 13 | Adapter-Karte | grüne Ampel, "Verbunden"-Badge (sofern `connected: true`), Detail-Banner verschwunden |
| 14 | Sidebar-Bubble | weg |
| 15 | Dashboard-Warnungs-Kachel | weg (komplett ausgeblendet) |
| 16 | Dashboard-Adapter-Status-Zeile | wieder grüner Punkt + "Verbunden" |

### Aufräumen

```js
store.fetchAdapters = window.__origFetch    // Polling wieder einschalten
```

Damit übernimmt der echte Backend-Status wieder die Anzeige. Alternativ:
Browser-Tab neu laden.

### Bonus: Backend-Pfad live verifizieren

Der `severity="error"`-Pfad lässt sich auch echt am Backend triggern, ohne
Console-Trick: PATCH die KNX-Instanz auf eine ungültige `routing_secure`-Config.
`_publish_status(..., severity="error")` läuft dann durch die ganze Kette
bis ins REST-Response.

```bash
TOKEN=$(curl -s -X POST http://localhost:8083/api/v1/auth/login \
        -H 'Content-Type: application/json' \
        -d '{"username":"admin","password":"admin"}' \
      | python3 -c "import sys,json;print(json.load(sys.stdin)['access_token'])")

ID=$(curl -s -H "Authorization: Bearer $TOKEN" \
       http://localhost:8083/api/v1/adapters/instances \
     | python3 -c "import sys,json;[print(a['id']) for a in json.load(sys.stdin) if a['adapter_type']=='KNX']")

# Schaltet severity → 'error'
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     http://localhost:8083/api/v1/adapters/instances/$ID \
     -d '{"config":{"connection_type":"routing_secure","host":"239.0.0.1","backbone_key":"KEIN-HEX-AAAA"}}' \
   | python3 -c "import sys,json;a=json.load(sys.stdin);print(f\"severity={a['severity']} detail={a['status_detail']}\")"

# Zurück zu severity='ok' (Tunneling auf echtes Gateway)
curl -s -X PATCH -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
     http://localhost:8083/api/v1/adapters/instances/$ID \
     -d '{"config":{"connection_type":"tunneling","host":"192.168.178.89","port":3671,"individual_address":"1.1.250"}}' \
   | python3 -c "import sys,json;a=json.load(sys.stdin);print(f\"severity={a['severity']} detail={a['status_detail']}\")"
```

Erwartete Console-Ausgabe:

```
severity=error detail=KNX Backbone-Key ungültig (kein Hex-String): ...
severity=ok    detail=Connected to 192.168.178.89:3671
```

In der UI (bei aktivem Polling) sieht man die Umschaltung innerhalb von
≤10 s — rote Ampel ↔ grüne Ampel.

---

## Pfad B — Backend-Verhalten (ohne Hardware, ohne UI)

```bash
pytest tests/adapters/test_knx.py::TestTunnelOverloadDetection -v
```

Erwartung: 11 Tests grün. Sie decken ab:
- Sliding-Window prunet alte Disconnects (`test_disconnect_older_than_window_is_pruned`)
- Default-Schwelle 3/300 s aus Issue (`test_config_defaults_match_issue_spec`)
- Warning erst ab Threshold, nicht vorher (`test_below_threshold_publishes_no_warning`,
  `test_threshold_reached_publishes_warning_status`)
- Kein Event-Spam (`test_further_disconnects_do_not_republish_warning`)
- Recovery nach Quiet-Window (`test_connected_after_quiet_period_clears_warning`,
  `test_reconnect_during_active_window_does_not_clear_warning`)
- xknx-Callback-Routing (`test_xknx_disconnected_callback_records_event`,
  `test_xknx_connected_callback_runs_reconnect_handler`,
  `test_xknx_connecting_callback_is_ignored`)

---

## Pfad C — End-to-End mit echter Hardware

Setup:

- Ein KNX/IP-Gateway mit kleinem Tunnel-Pool (typisch 2–4 Slots; viele MDT-/Weinzierl-Modelle).
- Drei `obs`-Stacks gleichzeitig laufen lassen, alle mit demselben Gateway
  als Tunneling-Ziel konfiguriert (`connection_type: tunneling`,
  gleicher `host`, gleiche oder unterschiedliche `individual_address` —
  wichtig: alle drei wollen einen Slot).

Vorgehen:

1. Stack 1 starten und verbinden lassen.
2. Stack 2 starten und verbinden lassen.
3. Stack 3 starten. Sobald der Gateway-Pool voll ist, beginnt das
   Pingpong (`xknx.log: Received DisconnectRequest from tunnelling server.`).
4. UI eines beliebigen der drei Stacks öffnen.
5. Innerhalb von ~2 min sollte derselbe Effekt wie in Pfad A auftreten:
   gelbe Ampel + Badge + Detail-Banner + Sidebar-Bubble + Dashboard-Kachel.
6. Stack 3 stoppen → Pool ist wieder im Limit. Nach 5 min Stille kippt der
   Status zurück auf grün/Verbunden (Recovery).

Falls das Gateway einen größeren Pool hat als die Anzahl Stacks: zusätzliche
Drittclients (ETS, Home-Assistant `xknx`) parallel laufen lassen, um den Pool
gezielt zu überfüllen.

---

## Erwartetes Verhalten beim echten Pingpong (nicht-offensichtlich)

Wenn die Pool-Überlastung *aktiv* ist (jeder Reconnect-Versuch scheitert),
flackert die Severity der betroffenen Instanz im Polling zwischen
`warning` und `error`:

- `_record_disconnect()` setzt nach 3 Disconnects severity → `warning`.
- Der nächste fehlgeschlagene Reconnect-Versuch läuft durch
  `_publish_status(False, "Tunnel could not be established", severity="error")`
  und überschreibt `_last_severity` → wieder `error`.
- Bei einer weiteren Disconnect-Welle (selten, weil `_warning_active`
  re-publish im selben Fenster blockiert) kommt warning evtl. zurück.

Das ist gewollt: `error` ist die härtere Aussage ("Adapter ist nicht
verbunden") und dominiert die diagnostische `warning` ("Pool wahrscheinlich
überlastet"). Bei einem Code-Review **kein Bug**.

In der UI sieht das als Wechsel zwischen rotem und gelbem Status aus.
Sobald das Gateway einen Slot freigibt, wechselt der Status stabil auf
`ok` / grün.

## Bei Fehlverhalten

- **UI bleibt grün trotz Warn-Detail im REST-Response:**
  Browser-Cache / Frontend-Build prüfen — `npm run build` neu, dann Hard-Reload (Cmd-Shift-R).
- **REST-Response liefert `severity: "ok"` obwohl Backend-Logs Warnung zeigen:**
  Adapter-Instance frisch nach `_publish_status`-Erweiterung neu gestartet?
  `_last_severity` ist In-Memory; ein Adapter-Restart resettet auf `"ok"`.
- **Detail-Text auf Englisch / weicht ab:**
  Quellzeichenkette steht als `TUNNEL_OVERLOAD_DETAIL` in
  `obs/adapters/knx/adapter.py`. Ändern dort, nicht in der UI.
