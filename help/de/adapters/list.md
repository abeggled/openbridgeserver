---
title: Adapter-Instanzen
---

# Adapter-Instanzen

Adapter binden externe Systeme (KNX, Modbus, MQTT, 1-Wire, Home Assistant, ioBroker,
SNMP, Zeitschaltuhr, Anwesenheitssimulation und weitere) als **Instanzen** an OBS an.
Jede Instanz hat einen Typ, eine eigene Konfiguration und beliebig viele Verknüpfungen
(Bindings) zu Objekten (DataPoints).

## Instanzliste {#adapters-list}

Jede Karte zeigt eine Adapter-Instanz mit:

- **Status-Punkt** — fasst den Verbindungszustand farblich zusammen:

  | Farbe | Bedeutung |
  |---|---|
  | grau | Instanz inaktiv/gestoppt |
  | grün | läuft und verbunden |
  | gelb, pulsierend | läuft, aber (noch) nicht verbunden |
  | gelb | Warnung (eingeschränkter Betrieb) |
  | rot | Fehler |

- **Typ-Badge** — der Adapter-Typ (z. B. KNX, MODBUS_TCP).
- **Status-Badge** — Textform des Status-Punkts (Verbunden / Läuft / Eingeschränkt /
  Inaktiv / Fehler).
- **Verknüpfungen** — Anzahl der Objekt-Bindings dieser Instanz.

Bei Warnung oder Fehler erscheint zusätzlich eine Detailmeldung mit der genauen Ursache.
Ein Klick auf den Pfeil rechts klappt die Instanz auf und zeigt Konfiguration und Aktionen
(siehe unten).

## Neue Instanz erstellen {#adapters-create}

„+ Neue Instanz" öffnet ein Formular: zuerst **Adapter-Typ** und **Name** wählen, danach
erscheint die typ-spezifische Konfigurationsmaske (z. B. Host/Port für KNX oder Modbus TCP,
Broker-Adresse für MQTT). Erst nach dem Erstellen lassen sich Verknüpfungen zu Objekten
anlegen.

## Instanz-Aktionen {#adapters-instance-actions}

Im aufgeklappten Zustand einer Instanz:

- **Verbindung testen** — prüft die aktuell eingegebene Konfiguration, ohne zu speichern.
- **Speichern** — übernimmt Änderungen und verbindet den Adapter neu.
- **Neu verbinden** — trennt und verbindet die bestehende Konfiguration neu, ohne sie zu
  ändern.
- **Importieren** (nur ioBroker) — übernimmt ioBroker-States als neue OBS-Objekte samt
  Verknüpfung.
- **Objekte verwalten** (nur Anwesenheitssimulation) — wählt simulierte Boolean-/
  Integer-Objekte aus und verwaltet deren Bindings.
- **Bindings migrieren** — verschiebt alle Verknüpfungen dieser Instanz auf eine andere
  Instanz desselben Adapter-Typs; am Ziel bereits vorhandene Verknüpfungen werden dabei
  übersprungen.
- **Instanz löschen** — löscht die Instanz unwiderruflich, inklusive aller ihrer
  Verknüpfungen.

„Aktiviert" schaltet die Instanz komplett aus, ohne sie zu löschen — eine deaktivierte
Instanz behält ihre Konfiguration und Bindings, verbindet sich aber nicht.

## Zeitschaltuhr {#adapters-zeitschaltuhr}

Die Zeitschaltuhr ist eine reine **Quelle**: sie schreibt zu definierten Zeitpunkten in
Objekte, liest aber nie aus ihnen. Eine Verknüpfung ist dabei genau **ein Schaltpunkt** —
für mehrere Schaltzeiten am selben Objekt legt man mehrere Verknüpfungen an.

| Schaltuhr-Typ | Schaltet |
|---|---|
| Tagesschaltuhr | täglich bzw. an ausgewählten Wochentagen |
| Jahresschaltuhr | in ausgewählten Monaten (keine Auswahl = alle), wahlweise an einem festen Tag im Monat |
| Feiertagsschaltuhr | an ausgewählten Feiertagen (keine Auswahl = alle Feiertage) |
| Metadaten | kein Schaltpunkt — publiziert den Feiertags- bzw. Ferienstatus automatisch |

Der Schaltzeitpunkt ist entweder eine feste Uhrzeit oder an den Sonnenstand gekoppelt
(Sonnenaufgang, Sonnenuntergang, Sonnenhöchststand oder ein Sonnenhöhenwinkel), jeweils mit
einem Offset in Minuten. Zusätzlich kann ein Schaltpunkt getaktet wiederholen — stündlich
zur angegebenen Minute oder minütlich. Feiertage und Ferien lassen sich pro Schaltpunkt
ignorieren, überspringen, exklusiv schalten oder wie ein Sonntag behandeln.

### Schalt-Wert {#adapters-zeitschaltuhr-value}

Der **Schalt-Wert** wird gegen den Typ des verknüpften Objekts ausgewertet — eine
Zeitschaltuhr bedient damit jeden Objekttyp, nicht nur Ein/Aus:

| Objekttyp | Eingabefeld | Akzeptierte Werte |
|---|---|---|
| Ja/Nein | Ein/Aus-Auswahl | `1`/`0`, `true`/`false`, `on`/`off`, `ein`/`aus` |
| Ganzzahl | Zahlenfeld | Ganzzahl, z. B. `50` |
| Dezimalzahl | Zahlenfeld mit Einheit | Dezimalzahl, z. B. `21.5` |
| Text | Textfeld | wörtlich übernommen — auch `1`, `0`, `on` oder `ein` |
| Datum | Datumsauswahl | ISO 8601, z. B. `2026-12-24` |
| Uhrzeit | Zeitauswahl | ISO 8601, z. B. `08:00:00` |
| Zeitstempel | Datum-/Zeitauswahl | ISO 8601, z. B. `2026-12-24T08:00:00` |
| unbekannt | Textfeld | Heuristik: Ja/Nein-Literal → Ganzzahl → Dezimalzahl → Text |

Das Eingabefeld richtet sich also nach dem Objekt: ein Rollladen-Objekt vom Typ Dezimalzahl
bekommt ein Zahlenfeld mit seiner Einheit, ein Ja/Nein-Objekt eine Ein/Aus-Auswahl.

Passt der Wert nicht zum Objekttyp, erscheint der Fehler direkt unter dem Feld und **das
Speichern wird abgelehnt**. Der Fehler fällt damit beim Anlegen auf statt erst Stunden
später beim Schalten. Lässt sich ein bereits gespeicherter Wert zur Schaltzeit dennoch nicht
umwandeln — etwa weil der Objekttyp nachträglich geändert wurde —, protokolliert OBS eine
Warnung, hinterlegt am Objekt die Diagnose `type_mismatch` und überspringt den Schaltvorgang.

Metadaten-Verknüpfungen haben keinen Schalt-Wert: sie publizieren den Feiertags- bzw.
Ferienstatus selbst.
