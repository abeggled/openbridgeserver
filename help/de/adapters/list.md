---
title: Adapter-Instanzen
---

# Adapter-Instanzen

Adapter binden externe Systeme (KNX, Modbus, MQTT, 1-Wire, Home Assistant, ioBroker,
SNMP, Zeitschaltuhr, Anwesenheitssimulation und weitere) als **Instanzen** an OBS an.
Jede Instanz hat einen Typ, eine eigene Konfiguration und beliebig viele Verknüpfungen
(Bindings) zu Objekten (DataPoints).

## Instanzliste {#adapters-list}

Jede Karte zeigt eine Adapter-Instanz mit:

- **Status-Punkt** — fasst den Verbindungszustand farblich zusammen:

  | Farbe | Bedeutung |
  |---|---|
  | grau | Instanz inaktiv/gestoppt |
  | grün | läuft und verbunden |
  | gelb, pulsierend | läuft, aber (noch) nicht verbunden |
  | gelb | Warnung (eingeschränkter Betrieb) |
  | rot | Fehler |

- **Typ-Badge** — der Adapter-Typ (z. B. KNX, MODBUS_TCP).
- **Status-Badge** — Textform des Status-Punkts (Verbunden / Läuft / Eingeschränkt /
  Inaktiv / Fehler).
- **Verknüpfungen** — Anzahl der Objekt-Bindings dieser Instanz.

Bei Warnung oder Fehler erscheint zusätzlich eine Detailmeldung mit der genauen Ursache.
Ein Klick auf den Pfeil rechts klappt die Instanz auf und zeigt Konfiguration und Aktionen
(siehe unten).

## Neue Instanz erstellen {#adapters-create}

„+ Neue Instanz" öffnet ein Formular: zuerst **Adapter-Typ** und **Name** wählen, danach
erscheint die typ-spezifische Konfigurationsmaske (z. B. Host/Port für KNX oder Modbus TCP,
Broker-Adresse für MQTT). Erst nach dem Erstellen lassen sich Verknüpfungen zu Objekten
anlegen.

## Instanz-Aktionen {#adapters-instance-actions}

Im aufgeklappten Zustand einer Instanz:

- **Verbindung testen** — prüft die aktuell eingegebene Konfiguration, ohne zu speichern.
- **Speichern** — übernimmt Änderungen und verbindet den Adapter neu.
- **Neu verbinden** — trennt und verbindet die bestehende Konfiguration neu, ohne sie zu
  ändern.
- **Importieren** (nur ioBroker) — übernimmt ioBroker-States als neue OBS-Objekte samt
  Verknüpfung.
- **Objekte verwalten** (nur Anwesenheitssimulation) — wählt simulierte Boolean-/
  Integer-Objekte aus und verwaltet deren Bindings.
- **Bindings migrieren** — verschiebt alle Verknüpfungen dieser Instanz auf eine andere
  Instanz desselben Adapter-Typs; am Ziel bereits vorhandene Verknüpfungen werden dabei
  übersprungen.
- **Instanz löschen** — löscht die Instanz unwiderruflich, inklusive aller ihrer
  Verknüpfungen.

„Aktiviert" schaltet die Instanz komplett aus, ohne sie zu löschen — eine deaktivierte
Instanz behält ihre Konfiguration und Bindings, verbindet sich aber nicht.
