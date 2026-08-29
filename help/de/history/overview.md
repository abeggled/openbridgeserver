---
title: Historie
---

# Historie

Zeigt historische Werte eines einzelnen Objekts als Diagramm und optional als Tabelle —
zum Nachvollziehen von Wertverläufen, unabhängig vom Live-Wert auf der Objekt-Detailseite.
Die zugrunde liegenden Werte werden im Ringbuffer gespeichert (siehe **Monitor**); ob und
wie lange ein Objekt historisiert wird, steuert dessen „Historie aufzeichnen"-Einstellung.

## Auswahl und Zeitraum {#history-controls}

- **Objekt** — durchsucht Name/UUID; nur ein Objekt gleichzeitig darstellbar.
- **Von** / **Bis** — der abzufragende Zeitraum.
- **Modus**:
  - **Raw** — jeder einzelne gespeicherte Wert, unverändert.
  - **Aggregiert** — zu gleich langen Intervallen zusammengefasste Werte (siehe unten).
- **Funktion** (nur bei Aggregiert) — wie die Werte innerhalb eines Intervalls verdichtet
  werden: Mittelwert, Minimum, Maximum oder letzter Wert.
- **Intervall** (nur bei Aggregiert) — die Breite eines Aggregationsschritts, von 1 Minute
  bis 1 Tag. Ein größeres Intervall glättet das Diagramm und reduziert die Punktzahl bei
  langen Zeiträumen.

„Laden" fragt die Werte für die aktuelle Auswahl ab. Ein Wechsel des Objekts leert die
Anzeige; eine bereits laufende Abfrage wird verworfen, falls währenddessen Objekt oder
Auswahl geändert werden — es zählt immer nur die zuletzt gestartete Abfrage.

## Diagramm und Rohdaten {#history-results}

Das Diagramm zeigt die geladene Reihe über der Zeit, mit der Anzahl geladener Punkte in
der Kopfzeile. Im **Raw**-Modus erscheint zusätzlich eine Tabelle mit jedem einzelnen
Wert: Zeitstempel, Wert (inkl. Einheit, falls vorhanden), Qualität (**Gut**/**Unbekannt**)
und der Adapter, von dem der Wert stammt. Ist im gewählten Zeitraum kein Wert vorhanden,
erscheint ein entsprechender Hinweis statt eines leeren Diagramms.
