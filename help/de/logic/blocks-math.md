---
title: "Bausteine: Mathematik"
---

# Bausteine: Mathematik

Berechnungs-, Statistik- und Zähler-Bausteine.

## Formel {#logic-block-math-formula}

Berechnet einen Ausdruck mit den Variablen `a` (= IN 1) und `b` (= IN 2), z. B. `a + b` oder
`(a - b) * 2`. Zusätzlich lässt sich eine **Ausgangs-Transformation** definieren — eine zweite
Formel mit der Variablen `x` (= Ergebnis der Hauptformel), z. B. für eine Einheitenumrechnung nach
der eigentlichen Berechnung. Für beide Formeln stehen Presets zum Multiplizieren/Dividieren sowie
`abs`, `round`, `min`, `max`, `sqrt`, `floor`, `ceil` und alle `math.*`-Funktionen zur Verfügung;
leer = keine Transformation.

## Skalieren {#logic-block-math-map}

Skaliert einen Wert linear von einem Eingangsbereich (**Min/Max**) in einen Ausgangsbereich
(**Min/Max**) — z. B. einen Rohsensorwert 0–1023 auf 0–100 % umrechnen.

## Begrenzer {#logic-block-clamp}

Begrenzt den Eingangswert auf einen Bereich [**Min**, **Max**]. Werte außerhalb werden auf den
jeweiligen Grenzwert gesetzt (kein Abschneiden/Verwerfen).

## Zufallswert {#logic-block-random-value}

Gibt bei jedem **Trigger**-Signal einen zufälligen Wert zwischen **Minimum** und **Maximum** aus.
**Datentyp** „int" liefert eine Ganzzahl, „float" eine Gleitkommazahl mit einstellbaren
**Nachkommastellen**.

## Statistik {#logic-block-statistics}

Berechnet laufend Minimum, Maximum, Mittelwert und Anzahl über alle seit dem letzten Reset
empfangenen Werte. Der **Reset**-Eingang setzt alle vier Ausgänge zurück.

## Mittelwert {#logic-block-avg-multi}

Berechnet den aktuellen Mittelwert von 2–20 Eingängen sowie zusätzlich **gleitende Mittelwerte**
über feste Zeitfenster (1 Minute, 1 Stunde, 1 Tag, 7/14/30/180/365 Tage) — jeweils als eigener
Ausgang. Jeder neu empfangene Wert wird mit Zeitstempel gespeichert; „Zustand nach Neustart
wiederherstellen" entscheidet, ob diese Zeitreihe einen Server-Neustart übersteht.

## Min/Max Tracker {#logic-block-min-max-tracker}

Verfolgt Minimum und Maximum eines Werts über mehrere Zeitperioden gleichzeitig (täglich,
wöchentlich, monatlich, jährlich, sowie absolut seit Beginn) — jede Periode als eigenes
Ausgangspaar. Die periodenbezogenen Werte setzen sich automatisch beim jeweiligen Perioden-Wechsel
zurück (Tages-/Wochen-/Monats-/Jahreswechsel); nur „absolut" akkumuliert unbegrenzt. Für jede
Periode lässt sich optional ein Startwert vorgeben.

## Verbrauchszähler {#logic-block-consumption-counter}

Berechnet aus einem fortlaufend steigenden Zählerstand (z. B. Stromzähler-Gesamtwert) die
Verbrauchswerte je Periode: täglich, wöchentlich, monatlich, jährlich — sowie zusätzlich den
jeweiligen Wert der **Vorperiode** zum Vergleich (Vortag, Vorwoche, Vormonat, Vorjahr). Für
Zählerstand und jede Periode lässt sich optional ein Startwert vorgeben, z. B. beim erstmaligen
Einrichten mit einem bereits laufenden Zähler.

## Sommer/Winter (DIN) {#logic-block-heating-circuit}

Sommer/Winter-Umschaltung für die Heizungssteuerung nach DIN (Mannheimer Methode), basierend auf
der Außentemperatur am Eingang. Drei feste Messzeitpunkte pro Tag (07:00, 14:00, 21:00) ergeben
ein gewichtetes **Tagesmittel** (`T1 + T2 + 2×T3) / 4`); ein gleitendes **Monatsmittel** aus den
letzten 31 Tagesmitteln glättet zusätzlich. Der **Heizmodus**-Ausgang schaltet EIN, wenn das
Tagesmittel die **Grenztemperatur** unterschreitet, und erst AUS, wenn Grenztemperatur +
**Hysterese** überschritten wird (verhindert häufiges Umschalten). Fehlende Messzeitpunkte werden
beim Start soweit möglich aus der Historie ergänzt; der Zustand übersteht einen Neustart.
