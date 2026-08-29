---
title: "Bausteine: Objekt-Zugriff"
---

# Bausteine: Objekt-Zugriff

Die beiden Bausteine, über die der Logikgraph mit OBS-Objekten (DataPoints) verbunden wird — direkt
über EventBus/MQTT, unabhängig von Adapter-Verknüpfungen (Bindings).

## Objekt lesen {#logic-block-datapoint-read}

Gibt den aktuellen Wert eines Objekts aus und löst den **Geändert**-Trigger-Ausgang bei jeder
Wertänderung aus. Die Konfiguration ist in drei Reiter aufgeteilt:

- **Verbindung** — Objekt über die Suche auswählen.
- **Transformation** — wandelt den gelesenen Wert um, bevor er ausgegeben wird:
  - **Formel** (Variable `x`) — z. B. `x * 100`; verfügbare Presets zum Multiplizieren/Dividieren
    oder eine eigene Formel. Verfügbare Funktionen: `abs`, `round`, `min`, `max`, `sqrt`, `floor`,
    `ceil` und alle `math.*`-Funktionen. Leer = keine Transformation.
  - **Wertzuordnung** — bildet einzelne Werte auf andere ab (z. B. `0/1` ↔ `off/on`), als
    JSON-Objekt mit beliebig vielen Einträgen; wird **nach** der Formel angewendet.
- **Filter** — bestimmt, wann tatsächlich getriggert wird:
  - **Zeitlicher Filter** — Mindest-Zeitabstand zwischen zwei Auslösungen; Auslösungen innerhalb
    des Intervalls werden verworfen.
  - **Wert-Filter** — „Nur auslösen wenn Wert sich geändert hat" (unterdrückt Duplikate) sowie eine
    **Mindest-Abweichung** absolut und/oder relativ (%) gegenüber dem letzten Wert (nur für
    numerische Werte; sind beide aktiv, müssen beide Bedingungen erfüllt sein; leer = inaktiv).

## Objekt schreiben {#logic-block-datapoint-write}

Schreibt den am **Wert**-Eingang anliegenden Wert in ein Objekt, ausgelöst über den separaten
**Trigger**-Eingang. Dieselben drei Konfigurationsreiter wie bei „Objekt lesen":

- **Verbindung** — Zielobjekt auswählen.
- **Transformation** — Formel und Wertzuordnung, hier angewendet auf den Wert, **bevor** er
  geschrieben wird.
- **Filter** — Mindest-Zeitabstand zwischen zwei **Schreibvorgängen**; „Nur schreiben wenn Wert
  sich geändert hat" sowie eine Mindest-Abweichung (nur absolut — beim Schreiben gibt es keine
  relative Variante).
