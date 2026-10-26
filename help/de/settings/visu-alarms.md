---
title: Visu-Alarme
---

# Visu-Alarme

Den Reiter findest du im Admin-GUI unter **Einstellungen → Visu-Alarme**. Er ist nur für
Administratoren sichtbar.

## Meldungen und Seiten in den Vordergrund holen {#settings-visu-alarms}

Ein Visu-Alarm holt bei einem Ereignis eine Meldung oder eine Visu-Seite auf allen bzw.
ausgewählten Visu-Geräten in den Vordergrund — etwa „Post ist gekommen“, wenn der
Briefkasten-Kontakt auslöst, oder ein Hinweis bei einem Telefonanruf. Der Alarm erscheint über
jeder Seite und auch auf der Übersicht, mit und ohne Anmeldung.

Jeder Alarm hat:

| Bereich | Einstellung |
|---|---|
| **Auslöser** | Datenpunkt, Bedingung (`==`, `!=`, `<`, `>`, `contains` …) und Vergleichswert, z. B. `== true`. Der Alarm startet, sobald die Bedingung **erfüllt wird**. War sie beim Speichern oder beim Start des Servers schon erfüllt, startet er erst beim nächsten Wechsel. |
| **Anzeige** | **Meldungsfenster** mit Titel, Text, Icon und Dringlichkeit (Info blau, Warnung orange, Kritisch rot) — oder **Visu-Seite als Popup**: eine im Editor gestaltete Seite erscheint als Fenster, mit allen Widgets und Live-Werten. |
| **Ende** | **„Quittieren“-Knopf** — beendet den Alarm auf allen Geräten — und/oder **automatisch**, sobald die Bedingung nicht mehr erfüllt ist (z. B. Telefon klingelt nicht mehr). Mindestens eines muss gewählt sein. |
| **Ton** | Optional ein kurzer Signalton. Browser spielen Töne erst ab, nachdem die Seite einmal berührt oder angeklickt wurde. |
| **Geräte** | Die [Visu-Geräte](./visu-devices), auf denen der Alarm erscheint. Nichts angehakt = auf jeder Visu. Ein umbenanntes oder gelöschtes Gerät bleibt rot markiert angehakt, bis es abgehakt wird; solange lässt sich nicht speichern. |

Mit **Speichern** werden alle Alarme auf einmal übernommen; fehlerhafte Alarme sind markiert und
verhindern das Speichern. Ein deaktivierter Alarm bleibt gespeichert, löst aber nicht aus.

### Verhalten im Detail

- Sind mehrere Alarme aktiv, zeigt die Visu den neuesten; ein Hinweis nennt die Zahl der
  wartenden. Nach dem Quittieren erscheint der nächste.
- Eine Seite mit PIN oder „Anmeldung erforderlich“ fragt im Popup wie gewohnt danach.
- Ohne Anmeldung überträgt der Server nur, was im Alarm für die Anzeige steht (Titel, Text, Icon,
  Seite) — keine Datenpunktwerte. Quittieren ist auch ohne Anmeldung möglich, damit es an einem
  Wandpanel funktioniert; es wird nur von der Visu selbst angenommen (nicht von fremden Webseiten)
  und mit Adresse des Geräts im Audit-Log protokolliert.
- Die Alarme sind Teil der App-Einstellungen und damit in Konfigurations-Export, Backup und
  Werksreset enthalten. Aktive Alarme enden bei einem Neustart des Servers.
