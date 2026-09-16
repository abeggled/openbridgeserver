---
title: Logikmodul
---

# Logikmodul {#logic}

Das Logikmodul ist ein visueller Graph-Editor für eigene Automatisierungen: Funktionsblöcke
(„Objekt lesen", „Objekt schreiben", logische Verknüpfungen, Mathematik, Zeitsteuerung,
Textverarbeitung u. v. m.) werden per Drag & Drop platziert und über Kanten miteinander
verbunden. Jedes **Logikblatt** ist ein eigenständiger Graph mit eigenem Aktiv/Inaktiv-Status.

## Werkzeugleiste {#logic-toolbar}

- **Logikblatt-Auswahl** — öffnet einen Auswahldialog zum Wechseln des Logikblatts und zum
  Verwalten seiner Hierarchie-Zuordnungen (siehe [Logik öffnen](#logic-graph-picker)).
- **+ Neu** / **Speichern** — legt ein neues Logikblatt an (siehe [Neues Logikblatt](#logic-new-sheet))
  bzw. speichert Änderungen am aktuellen.
- **▶ Ausführen** — prüft Berechtigungen und führt den Graphen einmalig manuell aus (nur
  bei aktivierten Graphen möglich).
- **Debug** — schaltet den Debug-Modus um: nach jeder Ausführung zeigt jeder Block seine
  zuletzt berechneten Werte direkt im Konfigurationspanel an.
- **Raster** / **Einrasten** — „Raster" blendet das Hintergrundraster ein/aus (rein
  visuell, für alle Benutzer verfügbar); „Einrasten" lässt Blöcke beim Verschieben am
  Raster einrasten (nur Admins, da es Positionen verändert). Die Rastergröße lässt sich
  daneben in Pixel einstellen.
- **Aktivieren/Deaktivieren** — schaltet den gesamten Graphen scharf oder inaktiv, ohne
  ihn zu löschen; ein inaktiver Graph lässt sich weiterhin bearbeiten, aber nicht
  automatisch oder manuell ausführen.
- **Kopieren** / **Einfügen** — kopiert die aktuell ausgewählten Blöcke (inkl. ihrer
  Verbindungen untereinander) in die Zwischenablage und fügt sie versetzt wieder ein.
  „Speichern" ist danach nötig, um die Änderung zu übernehmen.
- **Umbenennen** — ändert Name/Beschreibung des Logikblatts.
- **Duplizieren** — fragt den Namen der Kopie ab (vorbelegt mit aktuellem Namen + „ (Kopie)")
  und legt sie anschließend als neues, eigenständiges Logikblatt an.
- **Export** / **Import** — lädt den aktuellen Graphen als JSON-Datei herunter bzw. legt
  aus einer solchen Datei ein neues Logikblatt an — nützlich zum Sichern oder Übertragen
  einzelner Graphen zwischen Installationen.
- **Löschen** — löscht das Logikblatt unwiderruflich.

## Logik öffnen {#logic-graph-picker}

Der Auswahldialog bietet die zugeordneten Hierarchien Ebene für Ebene zum Durchklicken an;
Logikblätter ohne Zuordnung erscheinen dort in einem eigenen Ordner „Nicht zugeordnet". Ein
weiterer Ordner „Alle anzeigen" listet stattdessen ausnahmslos jedes vorhandene Logikblatt
flach und alphabetisch sortiert auf — unabhängig davon, ob und wo es einsortiert ist, damit
sich auch ohne Hierarchien-Kenntnis ein Überblick verschaffen lässt. Deaktivierte Graphen
sind entsprechend gekennzeichnet, das aktuell geöffnete Logikblatt ist durch einen Punkt
links vor dem Namen markiert.

Jede Zeile bietet:

- **Hierarchie zuweisen** (siehe unten) — fügt eine weitere Hierarchie-Position hinzu.
- **Aus Hierarchie entfernen** — nur beim Durchklicken einer Hierarchie verfügbar, löst die
  Logik ausschließlich von der aktuell durchsuchten Position; war dies die letzte Position,
  erscheint die Logik danach bei „Nicht zugeordnet".
- **Verknüpfungen** (siehe unten) — nur in „Alle anzeigen" verfügbar.
- **Löschen** — löscht das Logikblatt nach Sicherheitsabfrage vollständig und unwiderruflich,
  inklusive aller Hierarchie-Zuordnungen.

Der Button „Logik-Hierarchie bearbeiten" führt zur Hierarchie-Verwaltung
(Einstellungen → Hierarchie), auf der ausschließlich die Baumstruktur selbst (Bäume und
Knoten) gepflegt wird — die Zuordnung einzelner Logikblätter erfolgt vollständig hier in
diesem Dialog.

### Hierarchie zuweisen {#logic-graph-picker-assign}

Fügt der Logik eine weitere Hierarchie-Position hinzu — additiv, bestehende Zuordnungen an
anderen Positionen bleiben dabei unangetastet (kein Ersetzen). Das Suchfeld ist dasselbe
Element wie bei den Filtersets im Monitor-Bereich; es bietet sowohl die oberste Ebene einer
Hierarchie selbst (z. B. „Beschattung") als auch ihre Unterknoten (z. B. „Beschattung ›
Erdgeschoss") zur Auswahl an.

### Verknüpfungen {#logic-graph-picker-links}

Nur in „Alle anzeigen" verfügbar — dort gibt es keine einzelne „aktuell durchsuchte" Position,
von der sich mit „Aus Hierarchie entfernen" entfernen ließe. Öffnet stattdessen ein Popup, das
alle Hierarchie-Positionen der Logik auf einen Blick zeigt (als vollständiger Pfad, z. B.
„Beschattung › Erdgeschoss") und jede davon einzeln entfernbar macht, ohne dafür erst zur
jeweiligen Position navigieren zu müssen. Eine Logik ohne jede Zuordnung zeigt hier einen
entsprechenden Hinweis.

## Neues Logikblatt {#logic-new-sheet}

Der Dialog zum Anlegen eines neuen Logikblatts fragt Name, optionale Beschreibung sowie ein
optionales Suchfeld „Hierarchie-Knoten" ab (dasselbe Element wie bei den Filtersets im
Monitor-Bereich). Darüber lässt sich das neue Logikblatt direkt bei der Anlage einer oder
mehreren Hierarchie-Positionen zuordnen — sowohl der obersten Ebene einer Hierarchie selbst
(z. B. „Beschattung") als auch einem ihrer Unterknoten (z. B. „Beschattung › Erdgeschoss").
Bleibt das Feld leer, landet das neue Logikblatt wie gewohnt auf oberster Ebene bei „Nicht
zugeordnet" — die Zuordnung lässt sich jederzeit nachträglich über den Button „Hierarchie
zuweisen" im Auswahldialog „Logik öffnen" ändern.

## Arbeitsfläche {#logic-canvas}

Links liegt die **Blockpalette** (nur Admins), gegliedert nach Kategorien (Logik,
Objekt-Zugriff, Mathematik, Text, Zeit u. a.) — ein Block wird per Drag & Drop von dort auf
die Arbeitsfläche gezogen. Auf der Arbeitsfläche selbst:

- Blöcke lassen sich verschieben, per Klick auswählen (Mehrfachauswahl mit Umschalt/Strg)
  und über ihre Anschlusspunkte mit Kanten verbinden.
- Eine gelbe Warnzeile am oberen Rand erscheint, wenn der Graph strukturelle Probleme hat
  (z. B. ein Zyklus oder doppelt belegte Anschlüsse) — diese müssen vor dem Speichern
  behoben werden.
- Unten links liegen Zoom-Steuerung, unten rechts eine verschiebbare Übersichtskarte
  (Minimap) der gesamten Arbeitsfläche.
- Ohne ausgewähltes Logikblatt bleibt die Fläche leer mit einem Hinweis, ein Blatt zu
  wählen oder neu anzulegen.

## Block-Konfiguration {#logic-node-config}

Ein Klick auf einen Block öffnet rechts das Konfigurationspanel: Name des Blocks (frei
editierbar, oben im Panel) sowie die block-spezifischen Einstellungen (z. B. welches
Objekt gelesen/geschrieben wird, die Formel bei einem Rechenblock, das Zeitmuster bei
einer Zeitsteuerung). Ist der Debug-Modus aktiv, kommt ein zweiter Reiter mit den zuletzt
berechneten Ein-/Ausgabewerten dieses Blocks hinzu — dort lassen sich Werte auch testweise
überschreiben, um ohne echte Eingangsdaten zu testen. Die Panelbreite lässt sich am linken
Rand per Ziehen anpassen.
