---
title: "Bausteine: Text"
---

# Bausteine: Text

Bausteine zur Textverarbeitung sowie ein rein visueller Dokumentations-Baustein.

## String Verketten {#logic-block-string-concat}

Verkettet 2–20 Texte zu einem gemeinsamen Ergebnis. Jeder Eingang kann entweder dynamisch über
eine Kante verbunden oder als **statischer Text** direkt im Konfigurationspanel vorbelegt werden —
ist ein Eingang verbunden, hat der ankommende Wert Vorrang vor dem statischen Text. Leere
Eingänge/Felder ergeben einen leeren Teilstring. Optional lässt sich ein **Trennzeichen**
zwischen den Teilen festlegen (leer = ohne).

## String Suchen/Ersetzen {#logic-block-string-replace}

Ersetzt Treffer in einem Text über eine geordnete Liste von Regeln — die Regeln werden von oben
nach unten nacheinander angewendet, jede Regel arbeitet auf dem Ergebnis der vorherigen. Pro Regel
wählbar:

- **Modus** — Suchtext (Plain) oder Regulärer Ausdruck (RegEx); bei RegEx sind Gruppenverweise wie
  `\1` oder `\g<name>` im Ersetzen-Feld möglich.
- **Groß-/Kleinschreibung beachten** und **Alle Vorkommen ersetzen** (statt nur das erste).
- Ein leeres Ersetzen-Feld entfernt die Treffer ersatzlos.

Regeln lassen sich per Pfeil-Buttons in ihrer Reihenfolge verschieben, hinzufügen und entfernen.

## Kommentar {#logic-block-comment}

Freier Mehrzeiliger Text zur Dokumentation direkt auf der Arbeitsfläche — rein visuell, hat
keinerlei Einfluss auf die Ausführung des Graphen. Der Kommentar-Block lässt sich direkt auf der
Arbeitsfläche per Ziehen an der Ecke in Breite und Höhe verändern (keine Einstellung im
Konfigurationspanel nötig).
