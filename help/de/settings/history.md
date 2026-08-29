---
title: Historie DB
---

# Historie DB

Der Historie-DB-Tab findest du im Admin-GUI unter **Einstellungen → Historie DB**.

## Datenbank-Backend {#settings-history-db}

Wählt, wo historische Werte gespeichert werden: **SQLite** (intern, Standard, keine externe
Datenbank nötig), **InfluxDB** (v1, v2 oder v3 — je nach Version werden URL, Zugangsdaten und
Datenbank/Bucket/Organisation abgefragt) oder **PostgreSQL/TimescaleDB** (per Connection-DSN).
„Verbindung testen" prüft die Konfiguration, ohne sie zu übernehmen; „Speichern & aktivieren"
übernimmt sie **sofort, ohne Neustart**.

Der „Standard-Zeitraum" gilt nur für History-API-Aufrufe, die keinen expliziten `from`-Parameter
mitgeben.

## Objekt-Filter {#settings-history-filter}

Legt pro Objekt fest, ob dessen Werte überhaupt in der Historie-DB gespeichert werden. Objekte
mit deaktivierter Historisierung werden von der Aufzeichnung ausgeschlossen — typische
Kandidaten sind Zeit, Datum oder Systemwerte ohne historische Relevanz. „Alle aktivieren" /
„Alle deaktivieren" wirken auf die aktuell gefilterte/durchsuchte Liste.
