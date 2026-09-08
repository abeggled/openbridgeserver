---
title: Hierarchie
---

# Hierarchie

Der Hierarchie-Tab findest du im Admin-GUI unter **Einstellungen → Hierarchie**.

## Hierarchien {#settings-hierarchy}

Bildet eine baumartige Struktur (Gebäude, Räume, Gewerke, Topologie …) ab, der Objekte
zugeordnet werden können — sie dient sowohl der Navigation/Gruppierung im GUI als auch als
Grundlage für die Bereichsvergabe im Rechte-Editor (siehe Einstellungen → Benutzer).

Mehrere Hierarchien können parallel existieren; jede hat einen eigenen Modus:

- **Topologie** — folgt der KNX-Gruppenadress-Struktur.
- **Gebäudestruktur** — räumliche Gliederung, meist aus einem ETS-Projekt importiert.
- **Gewerke → Funktion** — nach Gewerk gruppiert.

**Aus ETS importieren** — erzeugt eine Hierarchie automatisch aus der räumlichen bzw.
funktionalen Struktur eines ETS-Projekts (Gebäude-/Gewerke-Modus); DataPoints lassen sich dabei
optional automatisch über ihre Gruppenadresse mit den passenden Knoten verknüpfen. Derselbe
Import ist auch direkt beim KNX-Projekt-Import im Datenmanagement-Tab verfügbar.

Knoten lassen sich manuell umbenennen, hinzufügen und wieder löschen (Löschen eines Astes
entfernt auch alle Unterknoten). Die „Anzeigestart-Ebene" bestimmt, ab welcher Ebene der
verkürzte Pfad in Objekt-Listen angezeigt wird — der vollständige Pfad bleibt dabei stets als
Tooltip sichtbar.

## Logiken zuordnen

Dieselben Hierarchien lassen sich auch verwenden, um Logiken zu gruppieren — eine Logik kann
dabei gleichzeitig in mehreren, unabhängigen Hierarchien einsortiert sein (z. B. einer
technischen Gliederung nach Beschattung/Licht/Steckdosen und parallel dazu einer topologischen
nach Raum). Diese Zuordnung ist rein organisatorisch und hat keinen Einfluss auf
Berechtigungen — Logiken behalten ihr eigenes, unabhängiges Rechte-Modell.

Im Logikeditor führt der Button „Logik öffnen" zu einem Auswahldialog, der die Hierarchien
Ebene für Ebene zum Durchklicken anbietet. Logiken ohne jede Zuordnung erscheinen dort in einem
eigenen Ordner „Nicht zugeordnet" auf oberster Ebene.

Zum Einsortieren gibt es hier in den Einstellungen eine Logik-Palette: Eine Logik von dort auf
einen Knoten ziehen verknüpft sie dort — bestehende Verknüpfungen in anderen Ordnern oder
Hierarchien bleiben davon unberührt. Wird eine Logik direkt auf den Titel einer Hierarchie
(statt auf einen ihrer Unterordner) gezogen, landet sie unmittelbar auf deren oberster Ebene,
ganz ohne dass zuvor ein Unterordner angelegt werden muss.
