---
title: API Keys
---

# API Keys

API Keys findest du im Admin-GUI unter **Einstellungen → API Keys**.

## API Keys verwalten {#settings-apikeys}

Ein API Key erlaubt programmatischen Zugriff auf die REST-API, ohne Benutzername und Passwort
zu verwenden.

**+ API Key** — vergibt einen Namen (frei wählbar, z. B. „Home Assistant") und erzeugt den
Schlüssel. **Der Schlüssel wird nur direkt nach dem Erstellen einmalig angezeigt** — danach
lässt er sich nicht mehr abrufen, nur ein neuer erstellt werden.

**Löschen** — widerruft den Schlüssel sofort; bereits laufende Integrationen, die ihn
verwenden, verlieren den Zugriff.

**Berechtigungen (nur Administratoren)** — schränkt einen einzelnen Schlüssel auf eine
Teilmenge der verfügbaren API-Fähigkeiten ein, unabhängig von den Rechten des Benutzers, der
ihn erstellt hat. Änderungen müssen ausdrücklich bestätigt werden, bevor sie gespeichert
werden können; speichert währenddessen jemand anderes dieselbe Berechtigungsliste, wird das
erkannt und die Änderung abgelehnt, statt die andere Änderung stillschweigend zu überschreiben.
