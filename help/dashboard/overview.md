---
title: Übersicht
---

# Übersicht

Die Übersicht ist die Startseite des Admin-GUI und zeigt den aktuellen System-Status auf
einen Blick — sie aktualisiert sich live über die WebSocket-Verbindung, ein manuelles
Neuladen ist nicht nötig.

## Kennzahlen {#dashboard-stats}

Die vier Kacheln oben zeigen:

- **Objekte** — Anzahl aller angelegten DataPoints.
- **Aktive Adapter-Instanzen** — Anzahl der Adapter-Instanzen, die aktuell laufen.
- **WS-Status** — Status der WebSocket-Verbindung des Admin-GUI zum Server (**Live** /
  **Offline**). Bei „Offline" aktualisieren sich Live-Werte und Status auf dieser Seite
  nicht mehr automatisch; ein Neuladen der Seite stellt die Verbindung neu her.
- **Server** — Grundzustand des Servers (**Online** / **Fehler**) aus dem Health-Check.

## Aktive Warnungen {#dashboard-warnings}

Erscheint nur, wenn mindestens ein Adapter im Zustand **Warnung** oder **Fehler** ist —
sonst bleibt der Bereich vollständig ausgeblendet. Jeder Eintrag zeigt Adapter-Name, Typ
und Schweregrad; ein Klick führt zur Adapter-Liste unter **Adapter**, wo sich die Ursache
im Detail nachvollziehen lässt.

## Monitor / Retention {#dashboard-ringbuffer}

Kompakter Auszug aus dem Monitor — vollständige Details und Konfiguration über
„Zum Monitor →".

- **Budget-Auslastung** — aktuell belegter Speicherplatz, ggf. im Verhältnis zum
  konfigurierten Maximum. Ohne konfiguriertes Maximum steht hier „unbegrenzt".
- **Segmente** — Anzahl der aktuell gespeicherten Ringbuffer-Segmente.
- Ist die Gesamt-Retention unbegrenzt konfiguriert, erscheint dazu ein separater Hinweis,
  da das betriebsrelevant ist (unbegrenztes Wachstum des Speicherbedarfs).
- Ist der Monitor deaktiviert, werden keine Wertänderungen aufgezeichnet — über
  „Konfigurieren" lässt er sich direkt von hier aktivieren (nur für Admins sichtbar).
- „Segment-Details" öffnet die vollständige Segmentliste in einem Dialog, ohne die
  Übersicht zu verlassen.

## Adapter-Status {#dashboard-adapters}

Zeigt bis zu allen konfigurierten Adapter-Instanzen mit farbigem Status-Punkt, Badge und
Anzahl der Verknüpfungen (Bindings). Der Status-Punkt fasst zusammen:

| Zustand | Bedeutung |
|---|---|
| grau | Instanz inaktiv/gestoppt |
| grün | läuft und verbunden |
| gelb, pulsierend | läuft, aber (noch) nicht verbunden |
| gelb | Warnung |
| rot | Fehler |

„Alle →" führt zur vollständigen Adapter-Liste unter **Adapter**.

## Live-Werte {#dashboard-values}

Die zuletzt bekannten Werte der ersten zehn Objekte (DataPoints), inklusive MQTT-Topic
und Qualitätsangabe (`good` / `uncertain` / `bad`). Werte, die seit dem Laden der Seite
über die WebSocket-Verbindung aktualisiert wurden, sind farblich hervorgehoben. „Alle →"
führt zur vollständigen, durchsuch- und filterbaren Objektliste unter **Objekte**.
