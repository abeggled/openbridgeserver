---
title: Objekt-Detail
---

# Objekt-Detail

Zeigt ein einzelnes Objekt vollständig: seinen Live-Wert, alle Eigenschaften und jede Stelle,
mit der es verbunden ist — Adapter-Verknüpfungen, Logik-Nutzung und Hierarchie-Zuordnungen.
Erreichbar über einen Klick auf den Namen eines Objekts in der Liste **Objekte**.

## Übersicht {#datapoints-detail}

Zeigt den Live-Wert (aktualisiert über die WebSocket-Verbindung), dessen Zeitstempel und das
MQTT-Topic (plus Alias, falls gesetzt). Kann mindestens eine aktivierte Verknüpfung Werte
entgegennehmen, erscheint ein Schreib-Element — ein Wahr/Falsch-Umschalter bei BOOLEAN-
Objekten, sonst ein Textfeld; das Schreiben läuft über denselben Weg wie jede andere
Schreibquelle.

## Eigenschaften {#datapoints-detail-properties}

Name, Datentyp, Einheit, Tags sowie die Einstellungen „Wert dauerhaft speichern" /
„Historie aufzeichnen", dazu Erstellungs- und letzter Änderungszeitstempel. „Bearbeiten"
öffnet dasselbe Formular wie beim Anlegen eines Objekts; „Historie" springt zur Seite
**Historie**, vorgefiltert auf dieses Objekt.

## Hierarchie-Zuordnungen {#datapoints-detail-hierarchy}

Zeigt, welchen Hierarchieknoten dieses Objekt aktuell zugeordnet ist, und erlaubt einem
Admin, Zuordnungen über eine Suche im Hierarchiebaum hinzuzufügen oder zu entfernen — siehe
die Hierarchie-Dokumentation unter **Einstellungen** dafür, wie die Hierarchie selbst
verwaltet wird.

## Adapter-Verknüpfungen {#datapoints-detail-bindings}

Jede Adapter-Verknüpfung dieses Objekts, mit Richtung (Lesen/Schreiben/Lesen und Schreiben)
und Aktivierungsstatus. Eine KNX-Verknüpfung zeigt zusätzlich die beteiligte(n)
Gruppenadresse(n), die dafür bekannten KNX-Geräte sowie deren Kommunikationsobjekte, sofern
dieser Kontext verfügbar ist. „Verknüpfung hinzufügen" öffnet das Formular für eine neue
Adapter-Verbindung; jede bestehende Verknüpfung lässt sich von hier aus bearbeiten oder
löschen.

## Logik-Verknüpfungen {#datapoints-detail-logic}

Jeder Logik-Graph-Knoten, der dieses Objekt liest oder beschreibt, mit einem Link, der den
Graphen im **Logik**-Editor öffnet.
