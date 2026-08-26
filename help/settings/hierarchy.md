---
title: Hierarchie
---

# Hierarchie

Der Hierarchie-Tab findest du im Admin-GUI unter **Einstellungen → Hierarchie**.

## Gerätestruktur {#settings-hierarchy}

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
