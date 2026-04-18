# Änderungsprotokoll

Alle wesentlichen Änderungen an open bridge server werden hier festgehalten.

---

## [Unveröffentlicht — visu-Branch]

### Neu

**RingBuffer — Live-Aktualisierung über WebSocket**
- Neue Einträge werden sofort an alle geöffneten Browser übertragen — kein manuelles Neuladen mehr nötig
- Status-Badge „Live" / „Offline" zeigt den Verbindungszustand an
- Eingehende Einträge werden auf der Clientseite gefiltert (aktive Filter werden berücksichtigt)
- Das Änderungsprotokoll enthält weiterhin einen „↻ Aktualisieren"-Button für das vollständige Neuladen

**Logik-Editor — Formeln und Rundung**
- `round()` verwendet jetzt mathematisches Runden (0.5 → aufrunden) statt dem in der Programmierung üblichen „Bankers Rounding". `round(21.16, 1)` gibt jetzt korrekt `21.2` zurück.
- Gilt für alle Formelfelder: DP Lesen, DP Schreiben, Formel-Block, Verknüpfungs-Transformation, Python-Skript

**Logik-Editor — Formel-Block mit Ausgangs-Transformation**
- Der **Formel**-Block hat ein zweites Formelfeld: „Ausgangs-Transformation"
- Nach der Berechnung der Hauptformel (`a`, `b`) kann das Ergebnis mit einer weiteren Formel transformiert werden (Variable `x`)
- Gleiche Benutzeroberfläche wie beim DP Schreiben: Vorlagen-Dropdown + freies Formelfeld

**Logik-Editor — Trigger-Block repariert und umbenannt**
- Fehler behoben: Der Trigger-Block löste angeschlossene Blöcke nicht aus, weil das interne Trigger-Signal nie weitergegeben wurde
- Umbenannt: „CronTrigger" → **„Trigger"** (kürzer und verständlicher)

**Einstellungen — Zeitzone**
- Neue Zeitzone-Auswahl unter Einstellungen → Allgemein
- Alle Zeitangaben in der Oberfläche werden in der gewählten Zeitzone dargestellt: Verlauf, Änderungsprotokoll, History-Suche, Astro-Block
- Kompaktes Dropdown: Normalerweise nur die gewählte Zeitzone sichtbar — Klick öffnet die Auswahlliste mit Suchfeld
- Suchfeld wird automatisch fokussiert, Eingabe von `Enter` wählt den ersten Treffer, `Escape` schliesst

**Einstellungen — KNX-Projektdatei**
- Der Bereich „KNX Projekt importieren" wurde vom Tab „Sicherung" in den Tab **Allgemein** verschoben

**7 neue Blocktypen im Logik-Editor**

| Block | Kategorie | Beschreibung |
|---|---|---|
| **Astro Sonne** | Astro | Berechnet Sonnenauf- und -untergang für den konfigurierten Standort. Ausgänge: Aufgangszeit, Untergangszeit, Tagsüber (Ein/Aus). Berücksichtigt die eingestellte Zeitzone. |
| **Begrenzer** | Mathematik | Begrenzt den Eingangswert auf einen konfigurierbaren Bereich [Min, Max]. |
| **Statistik** | Mathematik | Laufende Statistik über alle empfangenen Werte: Minimum, Maximum, Mittelwert, Anzahl. Reset-Eingang setzt zurück. |
| **Betriebsstunden** | Timer | Zählt Betriebsstunden solange „Aktiv" wahr ist. |
| **Pushover** | Benachrichtigung | Push-Benachrichtigung auf das Handy via Pushover-Dienst. |
| **SMS (seven.io)** | Benachrichtigung | SMS-Versand via seven.io. |
| **API-Abfrage** | Integration | HTTP-Anfragen an externe Adressen (GET/POST/PUT/PATCH/DELETE). |

**Zeitplan-Editor (Trigger-Block)**
- Vorlagen-Dropdown mit über 30 vordefinierten Zeitplänen
- Visueller 5-Feld-Editor (Minute / Stunde / Tag / Monat / Wochentag)
- Direkteingabe des Cron-Ausdrucks
- Alle drei Eingabewege synchronisieren sich gegenseitig

**WebSocket Live-Debug im Logik-Editor**
- Debug-Wertebänder aktualisieren sich automatisch nach jeder Ausführung — ohne manuellen Klick
- Ausführungen durch Wertänderungen, Zeitpläne und manuellen Start werden alle angezeigt

---

### Fehlerbehebungen

**Wertzuordnung — N-Werte und Modbus-Fliesskommazahlen (#208)**
- Die Wertzuordnung (value_map) unterstützt jetzt beliebig viele Einträge — z.B. `{"0": "Aus", "1": "Init", "2": "Aktiv", ..., "10": "Standby"}` (bisher war nur 2-Wert-Logik dokumentiert)
- Fehler behoben: Modbus und ähnliche Adapter liefern ganzzahlige Werte als Fliesskomma (z.B. `5.0` statt `5`). Die Suche im Wörterbuch schlug daher fehl, weil `"5.0" != "5"`. Ganzzahlige Fliesskommazahlen werden jetzt vor der Suche normalisiert (`5.0 → "5"`)
- Gilt für alle Stellen, die `apply_value_map` nutzen: Adapter-Bindings (MQTT, Modbus, …), Logik-Editor (DP Lesen, DP Schreiben)
- Benutzerdefiniertes JSON-Feld vergrössert und mit sofortiger Fehleranzeige bei ungültigem JSON versehen

**API-Client-Block — Response Content-Typ (#208)**
- Bezeichnung korrigiert: „Response-Content-Typ" → „Response Content-Typ"
- Auswahlwerte auf MIME-Typen umgestellt: `json` → `application/json`, `text` → `text/plain`
- Bestehende Blöcke mit den alten Werten (`json`, `text`) funktionieren weiterhin (Rückwärtskompatibilität)

**Statistik — Zustand ging bei jeder Ausführung verloren**
- Ursache: Python-Fallstrick — ein leeres Wörterbuch `{}` gilt als „falsch". Der Ausdruck `zustand or {}` erstellte bei leerem Zustand immer ein neues, weggeworfenes Objekt statt das übergebene zu verwenden. Alle Änderungen gingen verloren.
- Lösung: `zustand if zustand is not None else {}`
- Betraf auch die Hysterese: Zustand ging zwischen Ausführungen verloren

**Statistik — Zustand überlebt Neustart nicht**
- Die Akkumulatoren (Minimum, Maximum, Summe, Anzahl) werden jetzt nach jeder Ausführung in der Datenbank gespeichert
- Beim Serverstart wird der gespeicherte Zustand wiederhergestellt
- Ein erneutes Speichern des Graphen überschreibt den laufenden Zustand nicht

**DP Lesen — gelegentlich falsche Nullwerte**
- Ursache: Bei ereignisgesteuerter Ausführung bekam nur der auslösende DP-Lesen-Block seinen Wert. Alle anderen DP-Lesen-Blöcke im selben Graphen erhielten „kein Wert", was zu 0.0 weiterverarbeitet wurde
- Lösung: Vor jeder Ausführung werden alle DP-Lesen-Blöcke mit dem zuletzt bekannten Wert aus dem Werteabbild befüllt. Der auslösende Wert hat weiterhin Vorrang.

**History — „Bis"-Feld berücksichtigte Zeitzone nicht**
- Das Enddatum in der Verlaufsansicht wurde immer als Ortszeit des Browsers interpretiert, unabhängig von der eingestellten Zeitzone
- Behoben: alle Datums-/Zeitfelder in der Verlaufsansicht verwenden jetzt die konfigurierte Zeitzone

**Verschiedenes**
- Löschen-Dialog bei Adaptern war unsichtbar (Anzeigefehler)
- Handle-Punkte an Blöcken verschoben sich beim Darüberfahren seitwärts (Vue Flow CSS-Überschreibung)
- DP Schreiben zeigte keinen Debug-Wert an
- DP Schreiben schrieb immer, auch wenn der Trigger-Eingang nicht erfüllt war

---

## [0.1.0] — 2026-03-26

### Neu

**Grundgerüst (Phase 1)**
- Konfiguration: YAML-Datei + Umgebungsvariablen, Einstellungen haben Priorität über Standardwerte
- Datenbank: SQLite mit automatischer Aktualisierung bei neuen Versionen
- Datenpunkt-Modell mit 8 eingebauten Datentypen
- Verknüpfungs-Modell mit Richtung (Lesen / Schreiben / Beides)

**Kern (Phase 2)**
- Ereignisbus: Wertänderungen und Adapterstatus werden an alle Abnehmer verteilt
- MQTT-Anbindung an internen Mosquitto-Broker
- Werteabbild: aktueller Stand aller Datenpunkte im Arbeitsspeicher
- Schreibdienst: MQTT `dp/{uuid}/set` → Adapter-Schreibbefehl; Protokoll-Brücke (KNX ↔ Modbus ↔ MQTT)

**Adapter (Phase 3)**
- KNX/IP: Tunneling und Routing, 22+ DPTs
- Modbus TCP: alle 4 Registertypen, 7 Datenformate
- Modbus RTU: Seriellverbindung
- 1-Wire: Temperatursensoren über Linux-Systemordner
- MQTT (externer Broker): bidirektionale Anbindung

**REST-API (Phase 4)**
- Vollständige Datenverwaltung über API
- Anmeldung mit Benutzername/Passwort (Token) oder API-Schlüssel
- Benutzerverwaltung (Admin-Berechtigungen)
- MQTT-Zugang pro Benutzer konfigurierbar

**Erweiterte Funktionen (Phase 5)**
- Verlauf: Werteverlauf mit Rohabfrage und Zusammenfassung
- Änderungsprotokoll (RingBuffer): letzten N Wertänderungen, Speicher oder Datei
- Sicherung & Wiederherstellung: komplette Konfiguration als JSON-Datei

**Bereitstellung (Phase 6)**
- Docker: mehrstufige Erstellung, schlankes Image, kein Root-Benutzer
- Docker Compose: open bridge server + Mosquitto mit Statusprüfungen
- Konfigurationsbeispiele für alle Einstiegspunkte

**Logik-Editor (Phase 7)**
- Visuelle Arbeitsfläche mit Drag & Drop
- 15 eingebaute Blocktypen in 6 Kategorien: Konstante, Logik, Datenpunkt, Mathematik, Timer, Skript
- Automatische Ausführung bei Wertänderungen
- Manuelle Ausführung über Schaltfläche oder API
- Filter und Wert-Transformation für DP-Blöcke
- KNX-Projektdatei-Import (`.knxproj`) für Gruppenadress-Suchvorschläge

### Bekannte Einschränkungen

- 1-Wire-Adapter benötigt Linux; unter Windows wird er deaktiviert, startet aber ohne Fehler
- Einfaches Rollenmodell: nur „Administrator" und „Benutzer"; feinere Rechteverwaltung geplant
