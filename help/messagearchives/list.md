---
title: Meldungsarchive
---

# Meldungsarchive

Meldungsarchive speichern strukturierte Ereignismeldungen (System, Sicherheit,
Benachrichtigungen, Automatisierungen, Adapter-Diagnosen u. a.) getrennt von der
laufenden OBS-Datenbank — jedes Archiv ist eine eigene Datenbankdatei mit eigener
Aufbewahrungsgrenze. Diese Ansicht ist nur für Admins vollständig nutzbar.

## Archivliste {#messagearchives-list}

- **Integrität prüfen** — prüft die Datenbankdatei(en) auf strukturelle Konsistenz.
- **Neues Archiv** — legt ein weiteres Archiv mit eigener ID, Farbe und
  Aufbewahrungsgrenzen an.

Die Liste links zeigt alle vorhandenen Archive mit Farbe, Name und aktueller
Eintragsanzahl; ein Klick wählt das Archiv für das Detail-Panel und die Meldungstabelle
rechts aus.

## Archivdetails {#messagearchives-detail}

Ausgewähltes oder neu angelegtes Archiv:

- **Bearbeiten** — Name, Beschreibung, Standard-Meldungstyp (vorbelegter Typ für neue
  Einträge, sofern eine sendende Stelle keinen eigenen angibt), Farbe sowie die
  Aufbewahrungsgrenzen (maximale Eintragsanzahl und/oder maximales Alter in Tagen). Die
  Archiv-ID lässt sich nur beim Anlegen setzen, danach nicht mehr ändern.
- **JSONL exportieren** / **CSV exportieren** — lädt alle Einträge des Archivs in einem
  der beiden Formate herunter.
- **Leeren** — löscht alle Einträge des Archivs unwiderruflich, das Archiv selbst bleibt
  bestehen.
- **Löschen** — löscht das gesamte Archiv inklusive aller Einträge unwiderruflich.

## Meldungen {#messagearchives-entries}

Die Tabelle zeigt die Einträge des ausgewählten Archivs. Filter (Volltextsuche über
Titel/Text, Schweregrad, Status, Typ) lassen sich kombinieren; „Aktualisieren" lädt mit
den aktuellen Filtern neu. Jede Zeile zeigt Zeitpunkt, Titel und Meldungstext, Typ,
Schweregrad, Status und die meldende Quelle.
