---
title: Logikmodul
---

# Logikmodul

Das Logikmodul ist ein visueller Graph-Editor für eigene Automatisierungen: Funktionsblöcke
(„Objekt lesen", „Objekt schreiben", logische Verknüpfungen, Mathematik, Zeitsteuerung,
Textverarbeitung u. v. m.) werden per Drag & Drop platziert und über Kanten miteinander
verbunden. Jedes **Logikblatt** ist ein eigenständiger Graph mit eigenem Aktiv/Inaktiv-Status.

## Werkzeugleiste {#logic-toolbar}

- **Logikblatt-Auswahl** — wechselt zwischen den vorhandenen Graphen; deaktivierte Graphen
  sind entsprechend gekennzeichnet.
- **+ Neu** / **Speichern** — legt ein neues Logikblatt an bzw. speichert Änderungen am
  aktuellen.
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
- **Umbenennen** / **Duplizieren** — ändert Name/Beschreibung des Logikblatts bzw. legt
  eine vollständige Kopie als neues Logikblatt an.
- **Export** / **Import** — lädt den aktuellen Graphen als JSON-Datei herunter bzw. legt
  aus einer solchen Datei ein neues Logikblatt an — nützlich zum Sichern oder Übertragen
  einzelner Graphen zwischen Installationen.
- **Löschen** — löscht das Logikblatt unwiderruflich.

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
