---
title: Visu-Widgets
---

# Visu-Widgets {#visu-widgets}

Widgets sind die Bausteine einer Visu-Seite. Sie werden im Visu-Editor aus der Palette auf die
Seite gezogen und im Eigenschaften-Panel rechts konfiguriert.

## Terminliste {#widget-terminliste}

Die **Terminliste** zeigt die Termine der nächsten Tage als Liste, ein Eintrag pro Tag:
**Heute**, **Morgen** und danach das Datum (z. B. `Mi. 15.07.26`), dahinter die Termine des
Tages. Termine mit Uhrzeit beginnen mit ihrer Startzeit, ganztägige Termine stehen vor den
anderen. Mehrere Kalender lassen sich in einem Widget zusammenführen, jeder in seiner eigenen
Farbe, etwa Abfuhrtermine orange und private Termine grün.

### Kalender anbinden

Das Widget liest keine Kalender-URL, sondern **Objekte**. Den Kalender lädt der Logikbaustein
[iCalendar](../logic/blocks-integration#logic-block-ical) auf dem Server. So bleibt auch eine
private Kalenderadresse geheim, denn die Konfiguration einer Visu-Seite kann jeder Betrachter
lesen.

1. Ein **Objekt** vom Typ `STRING` anlegen, z. B. „Kalender Abfuhr“. Beim Objekt
   **Historisierung aktiv** ausschalten: Jede Aktualisierung schreibt die komplette Terminliste, die
   Historie würde damit schnell wachsen.
2. Im Logikmodul einen Baustein **iCalendar** mit der ICS-Adresse des Kalenders anlegen. Bei
   Google Kalender steht sie in den Einstellungen des jeweiligen Kalenders unter *Kalender
   integrieren* (*Privatadresse im iCal-Format* oder, für öffentliche Kalender, *Öffentliche
   Adresse im iCal-Format*).
3. Einen **Filter** hinzufügen (ohne Muster passt er auf alle Termine) und dessen Ausgang
   **Array** mit einem Baustein **Objekt schreiben** auf das Objekt aus Schritt 1 verbinden. Am
   Schreibbaustein **Nur schreiben wenn Wert sich geändert hat** einschalten.
4. Im Visu-Editor das Widget **Terminliste** einfügen und das Objekt als Kalenderquelle wählen.

Für einen weiteren Kalender oder eine weitere Kategorie (z. B. ein Filter nur auf „Papier“)
wird je ein weiteres Objekt beschrieben und als zusätzliche Quelle eingetragen.

### Einstellungen

- **Beschriftung** — optionaler Titel über der Liste.
- **Kalenderquellen** — bis zu acht Objekte, je mit **Farbe** und **Name**. Der Name erscheint
  als Tooltip über dem Termin.
- **Anzahl Tage** — Zeitraum ab heute (1–366 Tage, Standard 7).
- **Trennzeichen** — trennt die Termine eines Tages: Semikolon, Stern oder Zeilenumbruch (je
  Termin eine Zeile).
- **Datumsformat** — Format für Tage ab übermorgen, mit denselben Platzhaltern wie unter
  [Zeitzone, Datums- und Zeitformat](../settings/general#settings-general), z. B.
  `EEE dd.MM.yy` → `Mi. 15.07.26` oder `EEEE d. MMMM` → `Mittwoch 15. Juli`.
- **Startzeit anzeigen**, **Ort anzeigen** — blenden Uhrzeit bzw. Ort des Termins ein oder aus.
- **Tage ohne Termine anzeigen** — listet auch freie Tage (mit „–“), statt sie auszulassen.

Die Termine werden in der Zeitzone des Servers ausgewertet. Um Mitternacht rückt die Liste
selbstständig weiter.
