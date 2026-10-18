---
title: Allgemeine Einstellungen
---

# Allgemeine Einstellungen

Die allgemeinen Einstellungen findest du im Admin-GUI unter **Einstellungen → Allgemein**.

## Zeitzone, Datums- und Zeitformat {#settings-general}

**Zeitzone** — Alle Zeitangaben im System werden in der hier gewählten Zeitzone dargestellt:
Zeitstempel in Objekten, im Verlauf, im Ringbuffer und in Logeinträgen. Über das Suchfeld
lässt sich die Liste nach Ort oder Kürzel filtern (z. B. „Zurich", „Berlin", „UTC").

**Standard-Datumsformat** und **Standard-Zeitformat** — frei definierbare Formatstrings mit
folgenden Tokens:

| Token | Bedeutung |
|---|---|
| `d` / `dd` | Tag |
| `EE` / `EEE` / `EEEE` | Wochentag kurz / mittel / lang (z. B. „Mo", „Mo.", „Montag") |
| `M` / `MM` / `MMM` / `MMMM` | Monat |
| `yy` / `yyyy` | Jahr |
| `H` / `HH` | Stunde |
| `m` / `mm` | Minute |
| `s` / `ss` | Sekunde |

**Regionalformat** — bestimmt, wie Zahlen, Währungsbeträge und Datumsangaben formatiert
werden (Dezimaltrennzeichen, Tausendertrennzeichen, Reihenfolge von Tag/Monat/Jahr). Das
Regionalformat ist bewusst **unabhängig von der Sprache der Benutzeroberfläche** — Deutsch
in der Schweiz formatiert Zahlen anders als Deutsch in Deutschland (z. B. `1'234.50` vs.
`1.234,50`). „Automatisch" leitet das Format aus der gewählten Oberflächensprache ab; jede
andere Auswahl überschreibt das explizit.

**Währung** — bestimmt das bei Geldbeträgen angezeigte Währungssymbol/-kürzel. „Automatisch"
leitet die Währung aus dem gewählten Regionalformat ab.

Die Vorschau unterhalb der Auswahlfelder zeigt sofort, wie eine Beispielzahl und ein
Beispielbetrag mit der aktuellen Kombination aus Regionalformat und Währung dargestellt
würden — noch bevor gespeichert wird.

## Erscheinungsbild und Sprache {#settings-appearance}

**Darstellung** — steuert das Farbschema der Benutzeroberfläche:

- **System** — folgt der Einstellung des Betriebssystems/Browsers und wechselt automatisch
  mit, wenn sich diese ändert.
- **Hell** — immer helles Farbschema.
- **Dunkel** — immer dunkles Farbschema.

**Sprache** — wechselt die Sprache der Benutzeroberfläche selbst (Menüs, Beschriftungen,
Meldungen). Das ist eine eigene Einstellung, unabhängig vom Regionalformat oben — man kann
z. B. die Oberfläche auf Englisch stellen und trotzdem Zahlen im Schweizer Format anzeigen
lassen.
