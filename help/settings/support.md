---
title: Support
---

# Support

Der Support-Tab findest du im Admin-GUI unter **Einstellungen → Support**. Sichtbar und
bedienbar ist dieser Tab nur für Administratoren.

## Debug-Log Einstellungen {#settings-support-debug}

Aktiviert für **exakt 5 Minuten** detaillierteres lokales Logging, damit die zusätzlichen
Diagnoseeinträge im danach erstellten Support-Paket enthalten sind. Typischer Ablauf: Debug
aktivieren, das Problem reproduzieren, danach das Support-Paket erstellen — der In-Memory-
Logpuffer wird dabei ins Paket übernommen.

Dabei wird **nichts versendet und kein Remote-Zugriff geöffnet** — alles bleibt lokal. Nach
Ablauf der 5 Minuten oder manuellem Deaktivieren wird automatisch wieder das vorherige
Loglevel hergestellt.

## Support-Paket erstellen {#settings-support-package}

Erzeugt ein Diagnosepaket (JSON-Datei) mit Systeminformationen, Adapterstatus,
Historie-/Monitor-Statistiken und Warnungen — lokal generiert und heruntergeladen, **nie
automatisch versendet**. Sensible Werte (Passwörter, Tokens usw.) werden vor dem Export
zentral entfernt.

## Support-Paket analysieren {#settings-support-viewer}

Öffnet eine zuvor heruntergeladene `obs_support`-JSON-Datei lokal im Browser, um sie
strukturiert zu prüfen — Installationsdaten, Laufzeit/Ressourcen, Adapterübersicht,
Historie-/Monitor-Kennzahlen sowie eine durchsuchbare Liste von Warnungen und
Debug-Log-Einträgen. Die Datei wird dabei **weder hochgeladen noch gespeichert** — die
Auswertung passiert vollständig im Browser. Nützlich, um ein von einem anderen System
erhaltenes Support-Paket zu untersuchen, ohne Zugriff auf dieses System zu haben.
