---
title: Visu-Geräte
---

# Visu-Geräte

Den Reiter findest du im Admin-GUI unter **Einstellungen → Visu-Geräte**. Er ist nur für
Administratoren sichtbar.

## Startseite und Rücksprung je Gerät {#settings-visu-devices}

Eine Visu läuft oft auf mehreren Geräten gleichzeitig — auf dem Handy, einem Wandpanel im Flur,
einem Visu-PC. Jedes Gerät soll mit einer anderen Seite starten, und ein Wandpanel soll nach
einer Weile ohne Bedienung von selbst wieder auf seine Startseite zurückkehren. Dafür legst du
hier **Geräteprofile** an.

Jedes Profil hat:

| Feld | Bedeutung |
|---|---|
| **Name** | Frei wählbare Bezeichnung, z. B. „Wandpanel Flur“. Erscheint in der Geräte-Auswahl der Visu. |
| **Kennung** | Kurzer Name für den Link, nur Kleinbuchstaben, Ziffern und Bindestriche (z. B. `wandpanel-flur`). Wird beim Tippen des Namens vorgeschlagen. |
| **Startseite** | Die Seite, mit der die Visu auf diesem Gerät öffnet. „Übersicht“ behält das bisherige Verhalten. |
| **Rücksprung nach** | Zeit in Sekunden ohne Berührung, Maus-, Rad- oder Tastatureingabe, nach der die Visu zur Startseite zurückkehrt. `0` = nie. |

Mit **Speichern** werden alle Profile auf einmal übernommen. Zeilen mit Fehlern (leerer Name,
ungültige oder doppelte Kennung, Zeit außerhalb von 0–86400 Sekunden, gelöschte Startseite)
sind markiert und verhindern das Speichern.

### Ein Gerät einem Profil zuordnen

Das Profil wird **auf dem Gerät selbst** gewählt und dort im Browser gespeichert:

- **Per Link:** Jede Zeile zeigt den Link des Profils, z. B.
  `http://obs.local:8080/visu/?device=wandpanel-flur`. Öffne ihn einmal auf dem Gerät oder lege
  ihn dort als Lesezeichen bzw. Kiosk-Startadresse ab. `?device=none` löst das Gerät wieder vom
  Profil.
- **Per Auswahl:** Sobald Profile angelegt sind, zeigt die Übersicht der Visu oben rechts eine
  Auswahl **Gerät**.

Danach öffnet `/visu/` auf diesem Gerät direkt die Startseite des Profils. Änderungen an Startseite
oder Rücksprungzeit im Admin-GUI gelten beim nächsten Laden der Visu auf dem Gerät.

### Verhalten im Detail

- Zurückgesprungen wird nur aus der normalen Ansicht (Seite oder Übersicht) — nie aus dem
  Editor, der Seitenverwaltung oder einer Login- bzw. PIN-Eingabe.
- Ist die Startseite mit PIN oder Login geschützt, fragt die Visu beim Öffnen wie gewohnt danach.
  Das Profil selbst enthält keine Zugangsdaten.
- Wird die Startseite gelöscht, öffnet das Gerät stattdessen die Übersicht; im Reiter ist die Zeile
  markiert, bis eine andere Seite gewählt ist.
- Die Profile sind Teil der App-Einstellungen und damit in Konfigurations-Export, Backup und
  Werksreset enthalten.
