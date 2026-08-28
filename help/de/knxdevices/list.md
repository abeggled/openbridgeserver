---
title: KNX-Geräte
---

# KNX-Geräte

Diese Ansicht zeigt alle physikalischen KNX-Geräte aus dem zuletzt importierten
KNX-Projekt (ETS-Export), inklusive ihrer Kommunikationsobjekte und Gruppenadressen —
unabhängig davon, ob daraus bereits Objekt-Verknüpfungen (Bindings) in OBS entstanden sind.

## Geräteliste {#knxdevices-list}

Die Kopfzeile zeigt die Anzahl importierter Geräte sowie den Hinweis, dass es sich um den
Stand des zuletzt importierten KNX-Projekts handelt — ein neuer Import ersetzt diesen
Stand vollständig. „KNX-Projekt importieren" (nur für Admins sichtbar) führt zu
**Einstellungen → Datenmanagement**, wo eine ETS-Projektdatei hochgeladen wird.

## Suche und Filter {#knxdevices-filters}

- **Suchfeld** — durchsucht PA (physikalische Adresse), Name, Hersteller und
  Bestellnummer.
- **Hersteller** und **Bestellnummer** — schränken zusätzlich auf exakte oder
  teilweise Übereinstimmung ein.
- **Hierarchie** — filtert auf Geräte, die einem bestimmten Knoten/Ast der
  Objekt-Hierarchie zugeordnet sind.

Alle Filter kombinieren sich (UND-Verknüpfung); „Suchen" wendet sie an.

## Tabelle {#knxdevices-table}

Ein Klick auf eine Zeile öffnet das Gerät im Detail-Panel rechts. Die Spalte
**Hierarchien** zeigt die dem Gerät zugeordneten Hierarchie-Pfade als Chips; **Applikation**
zeigt die Referenz auf die KNX-Applikationsprogramm-ID aus dem ETS-Export.

## Gerätedetails {#knxdevices-detail}

Nach Auswahl eines Geräts zeigt das Panel:

- **Stammdaten** — Hersteller, Bestellnummer, Applikations-Referenz.
- **Hierarchie-Zuordnung** — welchen Knoten/Ästen der Objekt-Hierarchie dieses Gerät
  zugeordnet ist. Admins können die Zuordnung hier direkt bearbeiten und speichern.
- **Kommunikationsobjekte** — jedes Kommunikationsobjekt des Geräts mit seinem
  Datenpunkttyp (DPT) und den verknüpften Gruppenadressen. Für jede Gruppenadresse zeigt
  „Gebundene Datenpunkte", welche OBS-Objekte darüber lesen (Lesen), schreiben (Schreiben)
  oder beides tun, und ob diese Verknüpfung aktuell aktiviert oder deaktiviert ist.

Ist noch kein Gerät ausgewählt, bleibt das Panel leer mit einem entsprechenden Hinweis.
