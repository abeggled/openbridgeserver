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
Nach Ablauf der Verzögerung läuft das Logikblatt erneut, und der Ausgang löst die nachgelagerten
Blöcke einmal aus — wie der Block **Trigger** zu seinem Zeitpunkt.

Der Ausgang ist ein Impuls: Er sendet nur in diesem Lauf und in allen anderen Läufen des
Logikblatts nichts — auch nicht über nachgelagerte Logik wie **NOT**. Ein angeschlossenes
**Objekt schreiben** schreibt also nur beim Auslösen.

- **Erneuter Trigger:** Trifft während einer laufenden Verzögerung ein neuer Trigger ein, beginnt
  die Verzögerung von vorn. Der Ausgang löst also einmal aus, eine Verzögerung nach dem letzten
  Trigger.
- **Auslösung:** Der Block startet bei einer steigenden Flanke am Eingang, bei jedem einzelnen
  Impuls (z. B. vom **Änderungsfilter** oder vom Block **Trigger**) und bei jedem erneut empfangenen
  Wahr-Telegramm eines direkt angeschlossenen **Objekt lesen** — auch wenn der Eingang dazwischen
  nicht auf falsch zurückfällt. Liegt eine Bedingung dazwischen (etwa ein Vergleich), zählt nur
  deren steigende Flanke.
- **Speichern und Neustart:** Eine laufende Verzögerung bleibt erhalten. Sie löst auch nach dem
  Speichern des Logikblatts oder einem Neustart des Servers zum geplanten Zeitpunkt aus; ist dieser
  bereits vorbei, sofort.
- **Abbruch:** Deaktivieren oder Löschen des Logikblatts verwirft eine laufende Verzögerung. Liegt
  der Eingang nach dem erneuten Aktivieren noch an, startet sie beim nächsten Durchlauf des
  Logikblatts neu.
- **Rückkopplung:** Der Ausgang darf — auch über weitere Blöcke — zurück an den eigenen Eingang
  führen, ohne **Speicher** dazwischen. So entsteht etwa ein Signal, das sich alle N Sekunden
  wiederholt.
- **Ketten:** Nach Ablauf der Verzögerung beginnt eine neue Logik-Kaskade, wie beim Block
  **Trigger**. Eine Schleife über Datenpunkte — auch zurück ins eigene Logikblatt, etwa ein Blinker
  oder ein periodisches Signal — läuft deshalb unbegrenzt weiter. Bei einer Verzögerung unter einer
  Sekunde zählt der Lauf dagegen zur Kaskade des auslösenden Werts, und eine solche Schleife wird
  wie jede andere nach höchstens zehn Stufen gestoppt.

## Impuls {#logic-block-timer-pulse}

Gibt bei einem Trigger-Signal einen Impuls aus, der für eine konfigurierte Dauer (Sekunden)
anliegt: Der Ausgang wird sofort wahr und nach Ablauf der Dauer wieder falsch. Bei beiden Wechseln
läuft das Logikblatt erneut, damit die nachgelagerten Blöcke den neuen Zustand erhalten. Den
Ausgang am besten direkt an den **Wert** von **Objekt schreiben** ohne eigenen Trigger anschließen:
Dann folgt der Datenpunkt dem Impuls. Mit **Nur bei Änderung ausgeben** schreibt es nur bei einem
Wechsel und nicht bei jedem Lauf des Logikblatts. Löst **Objekt schreiben** dagegen über seinen eigenen Trigger
im selben Lauf aus, der auch den Impuls startet, schreibt es noch den Zustand vor dem Impuls.

- **Erneuter Trigger:** Ein neuer Trigger während des Impulses verlängert ihn — der Ausgang bleibt
  wahr, bis die Dauer ab dem letzten Trigger abgelaufen ist (nachtriggerbar, z. B. für
  Treppenhauslicht).
- **Ketten:** Das Ende des Impulses beginnt — wie der Ablauf einer **Verzögerung** — eine neue
  Logik-Kaskade (bei einer Dauer unter einer Sekunde nicht).
- **Auslösung:** wie bei **Verzögerung** — steigende Flanke, einzelner Impuls oder erneutes
  Wahr-Telegramm am Eingang. Ein Bewegungsmelder, der während der Anwesenheit zyklisch „1“ sendet,
  hält den Impuls also an.
- **Speichern und Neustart:** Ein laufender Impuls bleibt im Zustand des Blocks erhalten. Er endet
  auch nach dem Speichern des Logikblatts oder einem Neustart des Servers zum geplanten Zeitpunkt;
  ist dieser bereits vorbei, sofort.
- **Deaktivieren:** In einem deaktivierten Logikblatt läuft nichts. Ist der Impuls inzwischen
  abgelaufen, fällt der Ausgang beim erneuten Aktivieren auf falsch.
- **Löschen:** Wird das Logikblatt oder der Impuls-Block gelöscht, endet auch der Impuls; ein
  Datenpunkt, den er geschrieben hat, behält seinen letzten Wert.

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

Überwacht bis zu 10 Eingänge auf das Ausbleiben neuer Telegramme. Jeder Eingang hat einen eigenen
**Timeout** (Sekunden), **Fault-Value**, optionalen Anzeigenamen und optionale **Wiederholung**
(Sekunden). Solange auf einem Eingang regelmässig ein Telegramm eintrifft, wird sein Wert
unverändert an den zugehörigen Ausgang durchgereicht; bleibt ein neues Telegramm länger als der
konfigurierte Timeout aus, liefert der Ausgang stattdessen den Fault-Value — so lange, bis wieder
ein Telegramm eintrifft.

**Wichtig beim Verdrahten:** Pro Eingang müssen sowohl **Wert** als auch **Geändert** vom
vorgeschalteten Objekt-lesen-Block verbunden werden. Nur ein echtes neues Telegramm (Geändert =
true) setzt den Timeout zurück — ein unverändert erneut gesendeter Wert zählt dabei ausdrücklich
weiterhin als Lebenszeichen (wichtig z. B. für Kontaktsensoren, deren Wert lange gleich bleiben
kann, obwohl sie aktiv senden). Ein bloßes Neu-Auswerten des Graphen (z. B. durch ein anderes
Ereignis oder den eigenen periodischen Scheduler) zählt dagegen **nicht** als Lebenszeichen. Ist
nur **Wert** verbunden, meldet der Eingang nach dem ersten Timeout einmalig einen Fehler und
erholt sich danach nie wieder — kein Bug, sondern die erwartete, sicherheitshalber
„fail-stale"-Reaktion auf die fehlende Geändert-Verbindung.

Der Baustein arbeitet über einen internen, periodischen Scheduler und erkennt einen abgelaufenen
Timeout auch dann, wenn im restlichen Graphen kein anderes Ereignis eintritt — anders als eine
Nachbildung aus Verzögerung/Impuls-Bausteinen, die nur auf ein neues Trigger-Signal reagieren und
sich nicht selbst „aufwecken".

Sobald ein Eingang neu in den Fault-Zustand wechselt, liefert **Fehlertext** eine Meldung wie
„Keine Daten von &lt;Name&gt;" und **Fehler-Trigger** einen Impuls — z. B. um eine Benachrichtigung
auszulösen. Ist **Wiederholung** auf 0 (Standard) gesetzt, geschieht das nur einmalig beim
Auslösen; mit einem Wert größer 0 löst der Trigger zusätzlich in diesem Intervall erneut aus,
solange der Eingang weiter ausbleibt — z. B. Prüfung alle 10 s, aber nur stündlich erneut
benachrichtigen. Erholt sich ein Eingang wieder (neues Telegramm trifft ein), wird kein weiterer
Trigger ausgelöst und die Wiederholung beginnt beim nächsten Ausbleiben von vorne.
