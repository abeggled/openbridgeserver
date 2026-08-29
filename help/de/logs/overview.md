---
title: Logs
---

# Logs

Zeigt die Applikations-Logs des OBS-Backends live an — technische Diagnosemeldungen aus
dem Serverprozess selbst, nicht zu verwechseln mit den **Meldungsarchiven** (strukturierte
Ereignismeldungen für Betriebszwecke) oder der **Historie** (Objekt-Wertverläufe).

## Log-Level {#logs-level}

Der Level-Auswahl oben rechts ändert den aktuell wirksamen Log-Level des laufenden
Backend-Prozesses (DEBUG/INFO/WARNING/ERROR) — nur für Admins wirksam; bei
Nicht-Admins wird die Änderung vom Server stillschweigend abgelehnt. Ein niedrigerer
Level (z. B. DEBUG) erzeugt deutlich mehr Meldungen, ist aber nicht persistent — nach
einem Neustart des Servers gilt wieder der konfigurierte Standard-Level. „Aktualisieren"
lädt die Liste einmalig neu; das Status-Badge zeigt, ob neue Einträge zusätzlich live über
die WebSocket-Verbindung eintreffen.

## Filter und Tabelle {#logs-table}

- **Suchfeld** — durchsucht Logger-Name und Meldungstext (nur clientseitig, wirkt auf die
  bereits geladenen Einträge).
- **Level-Filter** — schränkt auf einen einzelnen Level ein (serverseitig angewendet,
  löst einen Neuladen aus).
- **Anzahl** — wie viele der neuesten Einträge geladen werden (100/200/500).

Live über WebSocket eintreffende neue Einträge werden oben in die Tabelle eingefügt und
respektieren die aktuell gesetzten Filter; die Gesamtzahl bleibt dabei auf die gewählte
Anzahl begrenzt.
