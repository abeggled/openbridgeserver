---
title: "Bausteine: Zeit"
---

# Bausteine: Zeit

Zeitgesteuerte Auslöser, Verzögerungen, Zähler und Sequenzen.

## Trigger {#logic-block-timer-cron}

Löst automatisch nach einem Cron-Zeitplan aus (Minute Stunde Tag Monat Wochentag). Die
Konfiguration bietet drei ineinandergreifende Ebenen:

- **Vorgefertigte Zeitpläne** — häufige Muster wie „Alle 5 Minuten", „Täglich um 07:00" oder
  „Werktags (Mo–Fr) um 06:00" per Dropdown.
- **Zeitplan anpassen** — ein visueller Editor mit einem Eingabefeld je Cron-Feld (Minute, Stunde,
  Tag, Monat, Wochentag; `0`=Sonntag). Unterstützt `*` (jeden), `*/5` (alle 5), Bereiche (`1-5`)
  und Listen (`1,3`).
- **Ausdruck** — der rohe Cron-Ausdruck direkt editierbar, mit Link zu crontab.guru zum
  Nachschlagen komplexerer Muster.

Alle drei Ebenen sind synchron — eine Änderung an einer Stelle aktualisiert die anderen.

Der Zeitplan wird in der eingestellten Anwendungs-Zeitzone ausgewertet (**Einstellungen →
Allgemein**), nicht in UTC — ein Trigger „Täglich um 07:00" löst also um 07:00 Ortszeit aus und
folgt automatisch der Zeitumstellung (Sommer-/Winterzeit).

## Datum/Zeit {#logic-block-datetime}

Gibt das aktuelle Datum und die aktuelle Uhrzeit in der eingestellten Anwendungs-Zeitzone aus
(Ausgänge **Datum**, **Zeit**, **Benutzerdefiniert**). Das benutzerdefinierte Format verwendet
dieselben Formatierungs-Tokens wie unter **Einstellungen → Allgemein** (`d`/`dd`, `EE`/`EEE`/`EEEE`,
`M`/`MM`/`MMM`/`MMMM`, `yy`/`yyyy`, `H`/`HH`, `m`/`mm`, `s`/`ss`).

## Verzögerung {#logic-block-timer-delay}

Verzögert ein Trigger-Signal um eine konfigurierte Anzahl Sekunden, bevor es am Ausgang erscheint.

## Impuls {#logic-block-timer-pulse}

Gibt bei einem Trigger-Signal einen Impuls aus, der für eine konfigurierte Dauer (Sekunden)
anliegt.

## Betriebsstunden {#logic-block-operating-hours}

Zählt Betriebsstunden, solange der **Aktiv**-Eingang wahr ist. Der **Reset**-Eingang setzt den
Zähler auf null zurück. „Zustand nach Neustart wiederherstellen" bestimmt, ob der Zählerstand
einen Server-Neustart übersteht.

## Sequenz {#logic-block-value-sequence}

Schreibt eine Folge von Werten mit konfigurierbaren Pausen dazwischen — z. B. für Blink- oder
Ablaufsteuerungen. Jeder **Schritt** definiert ein Zielobjekt (leer = reine Pause ohne Schreiben),
den zu schreibenden Wert und die Wartezeit (ms) bis zum nächsten Schritt; Schritte lassen sich per
Pfeil-Buttons verschieben, duplizieren und entfernen — „Blink-Vorlage" legt eine fertige
Ein/Aus-Sequenz an.

- **Ausführung** — Einmal, eine feste Anzahl Wiederholungen, oder solange der
  **Bedingung**-Eingang wahr ist.
- **Bei neuem Trigger** — was passiert, wenn während einer laufenden Sequenz erneut getriggert
  wird: Ignorieren, Neu starten (von vorne), oder Einreihen (nach Ende der aktuellen anhängen).
- **Abbrechen, wenn Bedingung false wird** — nur bei „Solange Bedingung wahr ist": bricht eine
  laufende Sequenz sofort ab, sobald die Bedingung nicht mehr erfüllt ist.

## Sensor Watchdog {#logic-block-timer-sensor-watchdog}

Überwacht bis zu 10 Eingänge auf das Ausbleiben neuer Werte. Jeder Eingang hat einen eigenen
**Timeout** (Sekunden), **Fault-Value** und optionalen Anzeigenamen. Solange auf einem Eingang
regelmässig ein Wert eintrifft, wird er unverändert an den zugehörigen Ausgang durchgereicht;
bleibt ein neuer Wert länger als der konfigurierte Timeout aus, liefert der Ausgang stattdessen
den Fault-Value — so lange, bis wieder ein Wert eintrifft.

Der Baustein arbeitet über einen internen, periodischen Scheduler und erkennt einen abgelaufenen
Timeout auch dann, wenn im restlichen Graphen kein anderes Ereignis eintritt — anders als eine
Nachbildung aus Verzögerung/Impuls-Bausteinen, die nur auf ein neues Trigger-Signal reagieren und
sich nicht selbst „aufwecken".

Sobald ein Eingang neu in den Fault-Zustand wechselt, liefert **Fehlertext** eine Meldung wie
„Keine Daten von &lt;Name&gt;" und **Fehler-Trigger** einen einmaligen Impuls — z. B. um eine
Benachrichtigung auszulösen. Erholt sich ein Eingang wieder (neuer Wert trifft ein), wird kein
erneuter Trigger ausgelöst.
