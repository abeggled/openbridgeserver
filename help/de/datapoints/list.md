---
title: Objekte
---

# Objekte

Ein Objekt (DataPoint) ist die zentrale Dateneinheit von open bridge server — jeder Sensor-
oder Aktorwert, jede von einem Adapter oder der Logik verwaltete Größe wird als Objekt
abgebildet. Diese Liste zeigt alle im System angelegten Objekte.

## Objektliste {#datapoints-list}

Die Kopfzeile zeigt die Gesamtzahl aller Objekte. Über „Neu" legt ein Admin ein neues,
zunächst bindungsloses Objekt an — Verknüpfungen (Bindings) zu Adaptern werden separat
über die jeweilige Adapter-Instanz eingerichtet.

## Suche und Filter {#datapoints-filters}

- **Suchfeld** — durchsucht Name, UUID und Konfiguration.
- **Typ** — schränkt auf einen Datentyp ein (z. B. FLOAT, BOOL, STRING).
- **Adapter** — Mehrfachauswahl nach Adapter-**Typ** (z. B. KNX, Modbus); zeigt Objekte mit
  einer Verknüpfung zu einer beliebigen Instanz der gewählten Typen.
- **Tag** — Mehrfachauswahl über die im System vorkommenden Tags.
- **Qualität** — filtert nach dem zuletzt gemeldeten Qualitätsstatus (**Gut** / **Unbekannt** /
  **Schlecht**).
- **Hierarchieknoten** — filtert auf einen oder mehrere Knoten/Äste der Objekt-Hierarchie;
  die Suche findet auch Knoten, die nicht in den zuletzt ausgewählten Bäumen liegen.

Alle Filter kombinieren sich (UND-Verknüpfung) und aktualisieren die Liste automatisch.
„Alle Filter zurücksetzen" erscheint, sobald mindestens ein Filter aktiv ist, und setzt
Suche, Typ, Adapter, Tag, Qualität und Hierarchie-Auswahl in einem Schritt zurück.

## Tabelle {#datapoints-table}

- **Name** — verlinkt auf die Objekt-Detailseite; darunter erscheinen ggf. die
  Hierarchie-Pfade, denen das Objekt zugeordnet ist. Ein Klick auf einen Pfad-Baustein
  filtert die Liste direkt auf diesen Knoten bzw. Ast.
- **Typ** und **Tags** — Tags sind anklickbar und setzen den Tag-Filter.
- **Wert** — der zuletzt bekannte, live über die WebSocket-Verbindung aktualisierte Wert.
- **Qualität** — Badge mit dem zuletzt gemeldeten Status; ein zusätzliches „!"-Badge
  erscheint bei einem erkannten Typkonflikt zwischen Adapter und Objekt-Datentyp.
- **Aktionen** (nur für Admins vollständig sichtbar) — Details öffnen, Bearbeiten,
  Duplizieren (kopiert alle Eigenschaften und Adapter-Verknüpfungen, aber keine aktuellen
  Werte oder Historie) und Löschen (entfernt auch alle Verknüpfungen des Objekts).

Die Liste lädt beim Scrollen automatisch weitere Einträge nach.

## Anlegen/Bearbeiten-Formular {#datapoints-form}

Öffnet sich über „Neu" auf dieser Seite oder über „Bearbeiten" auf der Objekt-Detailseite
bzw. in der Tabellenzeile. Beide Modi teilen sich dieselben Felder, nur der Titel
unterscheidet sich.

- **Name** — Pflichtfeld, frei wählbar.
- **Datentyp** — der Werttyp (z. B. FLOAT, BOOL, STRING); bestimmt, wie geschriebene Werte
  validiert und dargestellt werden.
- **Einheit** — optional, aus einer kategorisierten Liste (Temperatur, Elektrizität, …)
  oder als Freitext über „Andere".
- **Tags** — optional, kommagetrennt; werden für Filter und Hierarchie-Ansichten genutzt.
- **MQTT Alias** — ein optionales zweites MQTT-Topic, unter dem der Wert zusätzlich zum
  eigenen Topic des Objekts veröffentlicht wird.
- **Wert dauerhaft speichern** — behält den letzten Wert, sodass er nach einem Neustart
  sofort wieder verfügbar ist, statt leer zu starten.
- **Historie aufzeichnen** — speichert Wertänderungen in der Historie-Datenbank, sodass sie
  auf der Seite **Historie** erscheinen.
- **Externes Schreiben erlauben** — erlaubt jedem MQTT-Client mit Zugriff auf den Broker,
  diesen Wert zu setzen — nicht nur den eigenen Adaptern und der Logik von OBS.
