---
title: Datenmanagement
---

# Datenmanagement

Der Datenmanagement-Tab findest du im Admin-GUI unter **Einstellungen → Datenmanagement**.
Sichtbar und bedienbar ist dieser Tab nur für Administratoren.

## Sicherung erstellen / wiederherstellen (JSON) {#settings-importexport-config}

**Sicherung erstellen** — lädt alle Objekte, Verknüpfungen, Adapter-Instanzen,
KNX-Gruppenadressen, Logikblätter, Visu, eigene Links, App-Einstellungen, Hierarchie, Icons
und den FontAwesome-API-Key als eine JSON-Datei herunter.

**Sicherung wiederherstellen** — spielt eine solche JSON-Datei wieder ein. Es gilt
**Upsert-Semantik**: bestehende Einträge werden aktualisiert, fehlende neu angelegt — Einträge,
die seit der Sicherung gelöscht wurden, bleiben unangetastet erhalten (kein vollständiger
Restore, siehe unten für den vollständigen Weg).

## Datenbanksicherung erstellen / wiederherstellen (SQLite) {#settings-importexport-db}

**Datenbanksicherung erstellen** — lädt die vollständige SQLite-Datenbank herunter, inklusive
aller Historiendaten und Konfigurationen.

**Datenbank wiederherstellen** — spielt eine solche Datei ein und **überschreibt dabei alle
aktuellen Daten vollständig** (kein Upsert wie oben):

- Alle aktuellen Objekte, Verknüpfungen, Adapter und Logikblätter werden ersetzt.
- Die Historiendaten werden durch den Inhalt der hochgeladenen Datei ersetzt.
- Adapter und Logik-Engine werden danach automatisch neu gestartet.
- Daten, die nicht in der Sicherung enthalten sind, gehen verloren.

Dies ist der Weg für einen wirklich vollständigen Restore (z. B. Wiederherstellung nach einem
Totalausfall), im Gegensatz zur additiven JSON-Sicherung oben.

## Meldungsarchiv sichern / wiederherstellen {#settings-importexport-messagearchive}

Analog zur Datenbanksicherung oben, aber für die **separate** SQLite-Datenbank der
Meldungsarchive. Eine Wiederherstellung ersetzt alle aktuellen Meldungsarchive und Meldungen
vollständig — die eigentliche OBS-Konfigurationsdatenbank bleibt davon unberührt.

## Autobackup {#settings-importexport-autobackup}

Tägliche automatische JSON-Sicherung aller Konfigurationsdaten (wie bei „Sicherung erstellen"
oben), lokal im Datenverzeichnis gespeichert. Konfigurierbar sind Uhrzeit und wie viele Tage
an Sicherungen aufbewahrt werden; „Jetzt sichern" löst eine sofortige Sicherung ausserhalb des
Zeitplans aus.

**Wiederherstellen** — wählt eine gespeicherte Autobackup-Sicherung aus und spielt sie ein,
mit derselben Upsert-Semantik wie bei der regulären JSON-Wiederherstellung: bestehende
Einträge werden aktualisiert, fehlende neu angelegt, seit der Sicherung gelöschte Einträge
bleiben erhalten. Für einen wirklich vollständigen Restore aus einem Autobackup: zuerst
Factory-Reset (Gefahrenzone-Tab), dann wiederherstellen.

## KNX-Projekt importieren {#settings-importexport-knx}

Importiert eine ETS-Projektdatei (`.knxproj`, optional passwortgeschützt). Alle
Gruppenadressen (GA, Name, DPT) werden gespeichert, stehen danach im Binding-Formular als
Suchvorschläge zur Verfügung und werden auch in der Sicherung mitgesichert — unabhängig davon,
ob dabei Objekte angelegt werden.

**Objekte anlegen/aktualisieren** (optional) — legt für die importierten Gruppenadressen
DataPoints an, verknüpft mit einer gewählten KNX-Adapter-Instanz. Richtung wählbar:
Lesen/Schreiben, nur Lesen (von Adapter) oder nur Schreiben (auf Adapter).

**Hierarchien anlegen** (optional, unabhängig von den Objekten) — erzeugt Hierarchie-Knoten
aus der Struktur des ETS-Projekts, in bis zu drei Modi gleichzeitig: Topologie
(Gruppenadress-Struktur), Gebäude/Räume und Gewerke — Gebäude/Räume und Gewerke werden nur
angelegt, wenn das ETS-Projekt diese Strukturen tatsächlich enthält. „Bestehende
ETS-Hierarchien dieses Imports ersetzen" ersetzt nur automatisch aus einem früheren ETS-Import
erzeugte Hierarchien; manuell angelegte Hierarchien bleiben davon unberührt. „Angelegte Objekte
automatisch mit Hierarchieknoten verknüpfen" setzt voraus, dass gleichzeitig auch Objekte
angelegt werden.
