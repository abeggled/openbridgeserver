---
title: "Bausteine: Integration"
---

# Bausteine: Integration

Bausteine zur Anbindung externer Systeme: Netzwerk-Aktionen, HTTP-APIs, Kalender und das
Extrahieren von Werten aus strukturierten Textformaten.

## Wake on LAN {#logic-block-wake-on-lan}

Sendet ein Wake-on-LAN Magic-Paket per UDP-Broadcast, wenn der **Trigger**-Eingang wahr ist.
**MAC-Adresse**, **Broadcast-IP** und **UDP-Port** werden direkt im Konfigurations-Panel auf
Gültigkeit geprüft (ungültige Werte werden rot markiert mit Fehlertext).

Der **Auslösemodus** legt fest, wann ein wahrer Trigger auslöst:

- **Bei jedem Event** (Standard für neu platzierte Bausteine): Jedes neu eingehende `true` löst
  aus — auch ein wiederholtes `true` ohne vorheriges `false`. Ein Event auf einem anderen,
  nicht mit dem Trigger verbundenen Datenpunkt löst dagegen nicht aus.
- **Nur bei steigender Flanke**: Löst nur beim Wechsel von `false` auf `true` aus. Bausteine aus
  älteren Versionen behalten dieses Verhalten, bis der Modus umgestellt wird.

Impulse von Timer/Cron, Change Filter und Flankenerkennung lösen in beiden Modi jedes Mal aus.

## Host Check (Ping) {#logic-block-host-check}

Pingt einen **Host**/eine IP-Adresse und liefert **Erreichbar** (Bool) sowie **Latenz (ms)**
zurück, wenn der **Trigger**-Eingang wahr ist — Empfehlung: mit einem Timer-/Cron-Baustein
verbinden, um regelmässig zu prüfen. **Timeout** und **Ping-Anzahl** sind konfigurierbar.

Der Host bleibt bewusst statisch: Variablen werden hier nicht ausgewertet.

Der **Auslösemodus** legt fest, wann ein wahrer Trigger auslöst:

- **Bei jedem Event** (Standard für neu platzierte Bausteine): Jedes neu eingehende `true` löst
  aus — auch ein wiederholtes `true` ohne vorheriges `false`. Ein Event auf einem anderen,
  nicht mit dem Trigger verbundenen Datenpunkt löst dagegen nicht aus.
- **Nur bei steigender Flanke**: Löst nur beim Wechsel von `false` auf `true` aus. Bausteine aus
  älteren Versionen behalten dieses Verhalten, bis der Modus umgestellt wird.

Impulse von Timer/Cron, Change Filter und Flankenerkennung lösen in beiden Modi jedes Mal aus.

## JSON Extractor {#logic-block-json-extractor}

Parst einen JSON-String (**Daten**-Eingang) und extrahiert Werte über Schlüsselpfade in
Punkt-Notation (z. B. `sensors.temperature`). Über **+** lassen sich mehrere benannte Ausgänge
anlegen; jede Zeile zeigt bei zuletzt empfangenen Daten eine Live-Vorschau des extrahierten
Werts. Eine erkannte-Pfade-Dropdown-Liste (aus den zuletzt empfangenen Daten) füllt die gerade
aktive Ausgangszeile automatisch. Die Pfadauswahl bleibt erhalten, sobald einmal Daten
angekommen sind — auch wenn eine spätere Ausführung (z. B. nach dem Hinzufügen eines Ausgangs
über **+**) keine neuen Daten liefert. Im Debug-Modus erscheinen die Ausgänge unter ihren
konfigurierten Namen. Eine ältere Single-Pfad-Konfiguration wird als Legacy-Hinweis mit
Ein-Klick-Upgrade auf mehrere Ausgänge angezeigt.

### Variablen in Pfaden

Die Pfade dürfen Variablen in der Schreibweise `###NAME###` enthalten. Sie werden vor der
Auswertung einmalig pro Logiklauf ersetzt — in der konfigurierten Anwendungs-Zeitzone, alle
Werte desselben Laufs stammen vom selben Zeitpunkt. Das Menü **Variable einfügen** unter jedem
Pfad trägt den Platzhalter ein, darunter erscheint der **aufgelöste Pfad**.

| Variable | Bedeutung | Beispiel |
|---|---|---|
| `###H###` / `###HH###` | Stunde ohne/mit führender Null | `7` / `07` |
| `###m###` / `###mm###` | Minute | `5` / `05` |
| `###s###` / `###ss###` | Sekunde | `9` / `09` |
| `###d###` / `###dd###` | Tag | `8` / `08` |
| `###EE###` / `###EEE###` / `###EEEE###` | Wochentag kurz/mittel/voll | `Mo` / `Mo.` / `Montag` |
| `###M###` / `###MM###` / `###MMM###` / `###MMMM###` | Monat numerisch bzw. als Name | `6` / `06` / `Juni` / `Juni` |
| `###yy###` / `###yyyy###` | Jahr | `26` / `2026` |
| `###DATE###` / `###TIME###` | Standard-Datums-/Zeitformat der Einstellungen | `08.06.2026` / `07:05:09` |
| `###TS###` | ISO-8601-Zeitstempel mit Zeitzone | `2026-06-08T07:05:09+02:00` |
| `###OBS1###`, `###OBS2###`, … | aktueller Wert des unter **Variablen** zugeordneten Objekts | |

Gross-/Kleinschreibung zählt: `m` = Minute, `M` = Monat. Beispiel: `[###H###].text.value`
liest um 7 Uhr das Array-Element 7. Eingesetzte Werte werden nicht erneut ausgewertet.
Unbekannte Platzhalter bleiben wörtlich stehen und werden im Editor markiert; ein
nicht konfigurierter oder leerer `###OBSn###`-Slot sowie ein nicht auffindbarer Pfad liefern
`null` und einen Hinweis im Konfigurationspanel.

## XML Extractor {#logic-block-xml-extractor}

Parst einen XML-String (**Daten**-Eingang) und extrahiert Werte über XPath-Ausdrücke in
ElementTree-Syntax (z. B. `.//temperature`). Bedienung identisch zum JSON Extractor: mehrere
benannte Ausgänge über **+**, Live-Vorschau je Zeile, erkannte-Pfade-Dropdown und
Legacy-Upgrade-Banner für eine bestehende Single-Pfad-Konfiguration.

Variablen in XPath-Ausdrücken funktionieren wie beim JSON Extractor (siehe dort), z. B. `./hours/hour[###H###]/text/value` oder `.//forecast[@hour='###HH###']/value`. Ein ungültiger XPath bricht den Graphen nicht ab: der Ausgang liefert `null` plus Hinweis.

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

Im **Pfad** und in der **Query** der URL sind Variablen erlaubt (z. B. `…/kalender/###yyyy###.ics`
oder `?raum=###OBS1###`, Liste siehe JSON Extractor); Schema, Host, Benutzerinfo und Port bleiben
fest. Ändert sich die aufgelöste URL, wird der Kalender beim nächsten Lauf neu geladen.

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
URL, Header, Auth-Feldern oder Body einsetzen — deren aktuelle Werte werden vor dem Versand eingesetzt. Zusätzlich stehen die Datums-/Zeitvariablen (`###HH###`, `###yyyy###`, `###DATE###`, `###TS###` …, siehe JSON Extractor) zur Verfügung; im Schema, Host und Port der URL sind Variablen nicht erlaubt. Weitere
Einstellungen: Request-/Response-Content-Type, benutzerdefinierte Header (als JSON-Objekt oder
über eine Header-Datei unter `/run/secrets`), Timeout, SSL-Zertifikatsprüfung sowie
Authentifizierung (keine, Basic, Digest oder Bearer-Token — auch als Datei unter
`/run/secrets`). Ausgänge: **Antwort**, **Status** (HTTP-Statuscode) und **Erfolg**
(Trigger bei 2xx-Antwort).
