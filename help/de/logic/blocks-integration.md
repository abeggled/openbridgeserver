---
title: "Bausteine: Integration"
---

# Bausteine: Integration

Bausteine zur Anbindung externer Systeme: Netzwerk-Aktionen, HTTP-APIs, Kalender und das
Extrahieren von Werten aus strukturierten Textformaten.

## Wake on LAN {#logic-block-wake-on-lan}

Sendet ein Wake-on-LAN Magic-Paket per UDP-Broadcast, sobald der **Trigger**-Eingang wahr wird.
**MAC-Adresse**, **Broadcast-IP** und **UDP-Port** werden direkt im Konfigurations-Panel auf
Gültigkeit geprüft (ungültige Werte werden rot markiert mit Fehlertext).

## Host Check (Ping) {#logic-block-host-check}

Pingt einen **Host**/eine IP-Adresse und liefert **Erreichbar** (Bool) sowie **Latenz (ms)**
zurück. Löst bei einer steigenden Flanke am **Trigger**-Eingang aus — Empfehlung: mit einem
Timer-/Cron-Baustein verbinden, um regelmässig zu prüfen. **Timeout** und **Ping-Anzahl** sind
konfigurierbar.

## JSON Extractor {#logic-block-json-extractor}

Parst einen JSON-String (**Daten**-Eingang) und extrahiert Werte über Schlüsselpfade in
Punkt-Notation (z. B. `sensors.temperature`). Über **+** lassen sich mehrere benannte Ausgänge
anlegen; jede Zeile zeigt bei zuletzt empfangenen Daten eine Live-Vorschau des extrahierten
Werts. Eine erkannte-Pfade-Dropdown-Liste (aus den zuletzt empfangenen Daten) füllt die gerade
aktive Ausgangszeile automatisch. Eine ältere Single-Pfad-Konfiguration wird als
Legacy-Hinweis mit Ein-Klick-Upgrade auf mehrere Ausgänge angezeigt.

## XML Extractor {#logic-block-xml-extractor}

Parst einen XML-String (**Daten**-Eingang) und extrahiert Werte über XPath-Ausdrücke in
ElementTree-Syntax (z. B. `.//temperature`). Bedienung identisch zum JSON Extractor: mehrere
benannte Ausgänge über **+**, Live-Vorschau je Zeile, erkannte-Pfade-Dropdown und
Legacy-Upgrade-Banner für eine bestehende Single-Pfad-Konfiguration.

## Substring / RegEx {#logic-block-substring-extractor}

Extrahiert Text aus einem String (**Daten**-Eingang). Der **Modus** bestimmt, welche weiteren
Felder erscheinen:

- **links_von / rechts_von**: Text vor/nach einem **Suchbegriff**, wahlweise beim ersten oder
  letzten Vorkommen.
- **zwischen**: Text zwischen einer **Start-** und einer **End-Markierung**.
- **ausschneiden**: feste **Startposition** (0-basiert) und **Länge** (-1 = bis Ende).
- **regex**: ein Python-**RegEx-Muster** mit optionalen **Flags** (z. B. `i` für
  case-insensitive) und wählbarer **Capture-Gruppe** (0 = gesamter Treffer); ein Link öffnet
  regex101.com zum Testen.

Ein Test-Bereich zeigt live das Ergebnis für zuletzt empfangene Daten oder manuell eingegebenen
Testtext.

## iCalendar {#logic-block-ical}

Lädt periodisch ein iCal-/ICS-File von einer **URL** (**Aktualisierungsintervall** in Minuten,
**Maximale Kalendergrösse** als Schutz gegen übergrosse Downloads) und wertet Termine aus. Der
**RAW**-Ausgang liefert den rohen Kalendertext unabhängig von Filtern.

Über **Filter hinzufügen** lassen sich beliebig viele benannte Filter definieren; jeder Filter
erzeugt 4 Ausgänge (Array aller passenden Termine, nächstes Datum, Morgen als Bool, Heute als
Bool). Ein Filter kann reguläre Ausdrücke auf **Titel**, **Ort** und/oder **Beschreibung**
anwenden, verknüpft über **UND**/**ODER**, und wahlweise Gross-/Kleinschreibung beachten. Ein
leeres Musterfeld wird ignoriert (kein Ausschlusskriterium).

## API Client {#logic-block-api-client}

Sendet HTTP-Anfragen (GET/POST/PUT/PATCH/DELETE) an eine konfigurierbare **URL**; der
**Trigger**-Eingang löst die Anfrage aus. Der **Ziel prüfen**-Button zeigt vorab, ob die
konfigurierte URL laut dem serverseitigen SSRF-Schutz erlaubt ist oder blockiert würde
(Administratoren können ein blockiertes Ziel direkt aus diesem Dialog freigeben).

Über **Variablen** lassen sich Datenpunkte als Platzhalter (`###OBS1###`, `###OBS2###`, …) in
URL oder Body einsetzen — deren aktuelle Werte werden vor dem Versand eingesetzt. Weitere
Einstellungen: Request-/Response-Content-Type, benutzerdefinierte Header (als JSON-Objekt oder
über eine Header-Datei unter `/run/secrets`), Timeout, SSL-Zertifikatsprüfung sowie
Authentifizierung (keine, Basic, Digest oder Bearer-Token — auch als Datei unter
`/run/secrets`). Ausgänge: **Antwort**, **Status** (HTTP-Statuscode) und **Erfolg**
(Trigger bei 2xx-Antwort).
