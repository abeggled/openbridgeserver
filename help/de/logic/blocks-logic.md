---
title: "Bausteine: Logik"
---

# Bausteine: Logik

Grundlegende logische Verknüpfungen, Signalsteuerung und Speicher-Bausteine des Logikmoduls.

## AND {#logic-block-and}

Ausgang ist **true**, wenn ALLE Eingänge true sind. Die Anzahl der Eingänge lässt sich zwischen
2 und 30 einstellen. Jeder einzelne Eingang und der Ausgang lassen sich unabhängig voneinander
invertieren — ein Klick auf den Port-Namen direkt am Block auf der Arbeitsfläche schaltet die
Negation um (angezeigt mit vorangestelltem „¬").

## OR {#logic-block-or}

Ausgang ist **true**, wenn MINDESTENS EIN Eingang true ist. Eingänge (2–30) und Ausgang lassen
sich wie beim AND-Block einzeln über Klick auf den Port-Namen negieren.

## XOR {#logic-block-xor}

Ausgang ist **true**, wenn GENAU EIN Eingang true ist (bei mehr als zwei Eingängen: eine ungerade
Anzahl true). Eingänge (2–30) und Ausgang lassen sich einzeln negieren.

## NOT {#logic-block-not}

Invertiert den Eingang — ein einzelner Ein- und Ausgang, keine weitere Konfiguration.

## TOR {#logic-block-gate}

Signal-Tor: lässt den Eingang durch, solange „Freigabe" true ist, und sperrt sonst.

- **Verhalten (gesperrt)** — bestimmt, was bei gesperrtem Tor ausgegeben wird: den zuletzt
  durchgelassenen Wert halten (**retain**) oder einen festen **Standardwert**.
- **Freigabe invertieren** — kehrt die Bedeutung des Freigabe-Eingangs um (Tor offen bei false).
- **Zustand nach Neustart wiederherstellen** — bei „retain" wird der gehaltene Wert sonst nach
  einem Neustart verworfen.

## Speicher {#logic-block-memory}

Gibt den beim vorherigen Logiklauf gespeicherten Wert aus und speichert den aktuellen Eingangswert
für den nächsten Lauf. Dieser Baustein ist die **explizite Tick-Grenze** für kontrollierte
Rückkopplungen — ohne ihn würde ein rückgekoppelter Graph in einem einzigen Lauf in eine Endlosschleife
laufen. Der **Reset**-Eingang setzt den gespeicherten Wert auf den konfigurierten Initialwert zurück.
„Zustand nach Neustart wiederherstellen" bestimmt, ob der Speicherinhalt einen Server-Neustart übersteht.

## Änderungsfilter {#logic-block-change-filter}

Gibt den Eingangswert unverändert aus, setzt den **Geändert**-Trigger-Ausgang aber nur, wenn sich
der Wert vom zuletzt empfangenen unterscheidet — wiederholt gleiche Werte lösen keinen neuen
Trigger aus (entspricht Edomis „SendByChange"). Nützlich, um nachgelagerte Aktionen (z. B.
Benachrichtigungen) nicht bei jedem identischen Update erneut auszulösen.

## Vergleich {#logic-block-compare}

Vergleicht den Eingang mit einem konfigurierten Operanden über einen wählbaren Operator
(`>`, `<`, `=`, `>=`, `<=`, `!=`) und gibt das boolesche Ergebnis aus.

## Hysterese {#logic-block-hysteresis}

Schaltet den Ausgang bei Überschreiten der oberen Schwelle (**threshold_on**) ein, erst bei
Unterschreiten der unteren Schwelle (**threshold_off**) wieder aus — verhindert schnelles Takten
(„Flattern") bei Werten, die knapp um einen einzelnen Schwellwert schwanken, z. B. bei
Temperaturregelungen.

## Klemme {#logic-block-merge}

Bündelt mehrere unabhängige Wertquellen (2–30 Eingänge) auf einen gemeinsamen Ausgang: welcher
Eingang zuletzt einen neuen Wert liefert, wird durchgereicht (entspricht Edomis „Klemme"). Anders
als bei anderen Logikblöcken erzeugt diese Node bewusst **keinen eigenen** neuen Ausgabewert bei
jedem Lauf — sie reicht nur weiter, was zuletzt ankam.

## Entscheidung {#logic-block-decision}

Prüft einen Eingangswert gegen mehrere unabhängige Bedingungen; jede Bedingung hat einen eigenen
Trigger-Ausgang, der bei Zutreffen auslöst. Bedingungen werden über „+ Hinzufügen" als Liste
verwaltet (Operator wie `=`, `≠`, `>`, `<`, `zwischen`, Text „enthält"/„beginnt mit"/„endet
mit"/Regex …), keine manuelle JSON-Bearbeitung nötig. Mindestens zwei Bedingungen sind
erforderlich.

## Zuordnung {#logic-block-value-mapping}

Ordnet einem Eingangswert anhand einer geordneten Regelliste genau einen Ergebniswert zu — die
erste zutreffende Regel gewinnt. Der **Ausgangstyp** (BOOL/INT/FLOAT/STRING) legt fest, wie das
Ergebnis interpretiert wird. Optional lässt sich ein **Sonst-Wert** aktivieren, der ausgegeben
wird, wenn keine Regel zutrifft.

## Festwert {#logic-block-const-value}

Gibt einen fest konfigurierten Wert aus — Zahl, Bool oder Text (**Datentyp**). Nützlich als
Schwellwert, Referenzwert oder Konstante, mit der andere Blöcke verglichen oder verrechnet werden.
