---
title: Monitor
---

# Monitor

Der Monitor zeigt Wertänderungen live an, sobald sie im System eintreffen, und hält eine
konfigurierbar große Historie davon im Ringbuffer vor. Anders als die Objekt-Historie
(siehe **Historie**) ist der Monitor kein reines Aufzeichnungswerkzeug für einzelne
Objekte, sondern ein durchsuchbarer, filterbarer Live-Strom **über alle Objekte**.

## Werkzeugleiste {#ringbuffer-toolbar}

- **⚙ Konfigurieren** (nur Admins) — öffnet die Monitor-Konfiguration: Speichermodell
  (memory oder disk), maximale Einträge, maximaler Speicherplatz, maximale Retention nach
  Alter sowie — im segmentierten Modus — die Segment-Rotationsgrenzen. Der Monitor lässt
  sich hier auch vollständig deaktivieren (löscht dabei alle bisherigen Einträge) oder
  wieder aktivieren.
- **Segmente** — nur sichtbar im segmentierten Speichermodus; öffnet eine Übersicht der
  einzelnen Speicher-Segmente mit Status (Aktiv/Geschlossen/Altdaten/Isoliert), Größe,
  Zeitspanne und Integrität. Ein roter Punkt auf dem Button zeigt an, dass mindestens ein
  Segment ein Problem hat (z. B. beschädigt oder isoliert).
- **↻ Aktualisieren** — lädt die Tabelle mit den aktuell gesetzten Filtern neu.
- **⏸ Pause / ▶ Resume** — pausiert das Einreihen neuer Live-Einträge in die Tabelle
  (bereits geladene Einträge bleiben sichtbar); beim Fortsetzen werden während der Pause
  aufgelaufene Einträge nachgetragen.
- **Status-Badge** — fasst WebSocket-Verbindung und Pause-Zustand zusammen (Live /
  Pausiert / Offline).
- Die Zahlen rechts (Einträge / Kapazität · Speichermodell · ggf. Speicherplatz und
  Retention) zeigen den aktuellen Auslastungsstand; das „ⓘ"-Symbol daneben erklärt kurz
  das Rotationsverhalten (älteste Einträge werden bei Erreichen des Limits überschrieben).

### Filter-Sets und Zeitfilter

Über die Topleiste unterhalb der Werkzeugleiste lassen sich **Filter-Sets** anlegen,
bearbeiten und als Chips anheften — jedes Set kombiniert Hierarchie-Knoten, einzelne
Objekte, KNX-Geräte, Tags, Adapter, Volltextsuche und optional einen Wertfilter (z. B.
„Temperatur > 25"). Mehrere angeheftete Sets werden mit ODER verknüpft; innerhalb eines
Sets mit UND (außer Hierarchie/Objekt, die sich mit ODER ergänzen). Sets lassen sich als
„geteilt" markieren, damit andere Benutzer sie ebenfalls anheften können. Der
**Zeitfilter** schränkt zusätzlich auf einen Zeitraum oder einen Zeitpunkt ± Spanne ein.
Über „Export" lässt sich die aktuell gefilterte Ansicht als CSV/TSV mit wählbaren
Formatoptionen (Trennzeichen, Zeichenkodierung, Zusatzspalten) herunterladen.

## Live-Tabelle {#ringbuffer-table}

Jede Zeile ist eine einzelne Wertänderung: Zeitstempel, Objekt (verlinkt zur
Detailseite), neuer und vorheriger Wert, Qualität und der auslösende Adapter. Passt eine
Zeile zu mindestens einem angehefteten Filter-Set, wird sie farblich hervorgehoben
(passend zur Set-Farbe); ein Titel-Tooltip nennt die treffenden Sets. Ohne gesetzten
Filter zeigt die Tabelle alle Wertänderungen im System.
