---
title: Sicherheit
---

# Sicherheit

Der Sicherheit-Tab findest du im Admin-GUI unter **Einstellungen → Sicherheit**. Sichtbar und
bedienbar ist dieser Tab nur für Administratoren.

## URL-Ziel-Allowlist {#settings-security}

Logikblätter und API-Proxies dürfen öffentliche HTTP/HTTPS-Ziele direkt aufrufen. Interne,
private oder reservierte IP-Bereiche (z. B. `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`)
werden dagegen standardmässig blockiert — das schützt vor SSRF-Angriffen (Server-Side Request
Forgery), bei denen ein Logikblatt oder Adapter dazu missbraucht werden könnte, interne
Dienste im eigenen Netz anzusprechen, die eigentlich nicht von aussen erreichbar sein sollen.

Wer bewusst ein internes Ziel ansprechen möchte (z. B. eine weitere OBS-Instanz oder ein
anderes vertrauenswürdiges System im eigenen Netz), muss es explizit in dieser Allowlist
freigeben. Eine Freigabe erlaubt dem Backend aktiv, diese interne Adresse anzufragen — falsch
gesetzte Einträge können den SSRF-Schutz und die Netzsegmentierung abschwächen.

Die Allowlist wird als YAML-Datei auf dem Server gespeichert (Pfad in der Karte sichtbar), nicht
in der Datenbank.

## Ziel prüfen {#settings-security-check}

Testet, ob eine bestimmte URL aktuell erlaubt oder blockiert würde — ohne dafür erst einen
Logikblatt-Lauf oder Adapter-Aufruf zu starten. Das Ergebnis zeigt die aufgelösten IP-Adressen
und, falls blockiert, einen Grund. Ist das Ziel blockiert, kann direkt aus dem Ergebnis heraus
das vorgeschlagene Ziel zur Allowlist hinzugefügt werden.

## Freigegebene Ziele {#settings-security-entries}

Verwaltet die Allowlist-Einträge manuell: Ziel (Host, IP oder CIDR-Bereich, z. B.
`10.38.113.23/32`) plus optionaler Grund für die Nachvollziehbarkeit. Einträge lassen sich
jederzeit wieder entfernen; „Neu laden" holt den aktuellen Stand der YAML-Datei erneut vom
Server.
