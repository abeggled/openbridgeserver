---
title: Benutzer
---

# Benutzer

Die Benutzerverwaltung findest du im Admin-GUI unter **Einstellungen → Benutzer**. Sichtbar
und bedienbar ist dieser Tab nur für Administratoren.

## Benutzerverwaltung {#settings-users}

**Neuer Benutzer** — legt Benutzername, Passwort und optional Admin-Rechte fest. MQTT-Zugriff
kann direkt beim Anlegen mitaktiviert werden (siehe unten).

Jede Zeile der Liste zeigt:

- **Status** — „Aktuelles Konto" für den eigenen Account, „MQTT ohne Passwort" (Warnung) wenn
  MQTT aktiviert, aber noch kein MQTT-Passwort gesetzt ist, sonst „Bereit".
- **Bereiche** — Zusammenfassung der über den Rechte-Editor zugewiesenen Hierarchie-Bereiche
  und Rollen (siehe nächster Abschnitt).
- **MQTT** — ob der Benutzer sich am MQTT-Broker anmelden kann, und ob dafür ein separates
  MQTT-Passwort gesetzt ist. Das MQTT-Passwort ist unabhängig vom Login-Passwort.

**MQTT-Passwort setzen/entfernen** — über die Schraubenschlüssel- bzw. Papierkorb-Icons pro
Zeile. Ohne gesetztes Passwort kann sich der Benutzer trotz aktiviertem MQTT-Zugriff nicht am
Broker anmelden.

**Benutzer löschen** — nicht für den eigenen Account möglich. Vor dem Löschen wird geprüft, was
betroffen ist: Visu-Seiten, Logikblätter und RingBuffer-Filtersets können an einen **Nachfolger**
übertragen werden; API-Schlüssel werden dagegen **immer sofort widerrufen** (nie übertragen), da
nur der bisherige Inhaber das zugehörige Geheimnis kennt.

## Rechte-Editor {#settings-users-rights}

Über „Rechte bearbeiten" (nur sichtbar, wenn mindestens 2 Benutzer existieren) öffnet sich ein
mehrstufiger Editor für die Hierarchie-Rechte eines Benutzers:

1. **Rolle** — eine von vier Basisrollen: **Gast** (nur Lesezugriff), **Bewohner** (Lesen,
   Schreiben, Aktivieren), **Operator** (voller Betriebszugriff inkl. Erzeugen) oder
   **Eigentümer** (alle Aktionen). Jede Rolle startet mit ihren gespeicherten direkten
   Zuweisungen — eine neu ausgewählte Rolle beginnt ohne Bereiche.
2. **Bereiche** — pro Hierarchie-Bereich wird festgelegt, ob die Rolle **vererbt** (vom
   übergeordneten Bereich übernommen), ausdrücklich **erlaubt** oder ausdrücklich **verboten**
   wird. Ein direktes Verbot für einen Bereich bleibt beim Bearbeiten erhalten und kann hier
   nicht überschrieben werden.
3. **Vorschau** — zeigt die tatsächlich berechneten Rechte (erlaubt/verboten) pro Aktion und
   Bereich samt Begründung, bevor gespeichert wird.
4. **Bestätigen** — abschliessende Bestätigung der Änderung.

Zusätzlich lässt sich pro Benutzer separat freigeben, ob er neue Logikblätter erstellen,
importieren und duplizieren darf — inklusive einer eigenen Option für Logikblätter, die
Zentralanlagen steuern.
