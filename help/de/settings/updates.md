---
title: Updates
---

# Updates

Den Updates-Tab findest du im Admin-GUI unter **Einstellungen → Updates**.

## Update-Prüfung und Update-Kanal {#settings-updates}

OBS prüft selbständig, ob für die installierte Version eine neuere Version verfügbar ist.
Ist das der Fall, erscheint auf jeder Seite des Admin-GUI ein Hinweis mit dem Kanal, der
installierten und der neuesten Version, dem Zeitpunkt der letzten erfolgreichen Prüfung und
einer Schaltfläche für die **Release Notes**. Ohne verfügbares Update gibt es keinen Hinweis;
der aktuelle Stand bleibt hier im Tab einsehbar.

Die Prüfung erkennt Updates nur — installiert wird weiterhin über den bestehenden Weg
(`obs-update` bzw. ein neues Docker-Image).

**Update-Kanal** — bestimmt, welche Versionen als Update gelten:

| Kanal | Gemeldet werden |
|---|---|
| Stable | ausschließlich reguläre Releases (z. B. `2026.11.0`) |
| Pre-Release | reguläre Releases und Release Candidates (z. B. `2026.11.0-RC1`), aber keine Nightlies |
| Nightly | ausschließlich tägliche Nightly-Builds (`nightly-YYYYMMDD`) |

Ein Nightly wird nie als Stable- oder Pre-Release-Update gemeldet. Bei der Installation bzw.
beim ersten Start mit dieser Funktion wählt OBS den Kanal passend zur installierten Version
und passt ihn an, wenn später z. B. per `obs-update` von einem Nightly auf ein reguläres Release
gewechselt wird. Hat ein Administrator den Kanal selbst gewählt, bleibt diese Wahl bestehen;
nur Administratoren können ihn ändern. Draft-Releases und Tags außerhalb dieses Schemas
werden ignoriert.

**Prüfintervall** — nach jedem Start erfolgt die erste Prüfung mit einem zufälligen Versatz
von 0 bis 719 Minuten, danach alle zwölf Stunden. So verteilen sich die Anfragen vieler
Installationen gleichmäßig. Mit **Jetzt prüfen** löst ein Administrator sofort eine Prüfung
aus. Die Abfrage läuft im Server, nicht im Browser; das Ergebnis wird zwischengespeichert.

**Fehler** — schlägt eine Prüfung fehl (z. B. keine Internetverbindung), bleibt der letzte
erfolgreich ermittelte Stand erhalten und wird als möglicherweise veraltet gekennzeichnet.
OBS versucht es erst beim nächsten regulären Termin erneut. Für Installationen ohne
Internetzugang lässt sich die Online-Prüfung in der `config.yaml` abschalten:

```yaml
update_check:
  enabled: false
```

**Release Notes** — das Popup zeigt nur die Versionen des aktiven Kanals, die neuer als die
installierte Version und nicht neuer als die neueste Version sind, chronologisch und je
Version getrennt.

### Systemdatenpunkte

Für Visu und Logik legt OBS automatisch fünf Datenpunkte an, die wie andere Datenpunkte
gelesen, aber weder umbenannt, dupliziert, gelöscht noch beschrieben werden können (weder
über das Admin-GUI noch über API, MQTT, Adapter oder Logik — ein Logikblatt kann sie lesen, aber
nicht als Ziel eines Schreib-Bausteins oder einer Wertsequenz verwenden). Ihre IDs sind auf allen Installationen
gleich, damit Visu- und Logik-Konfigurationen übertragbar bleiben.

| Datenpunkt | Typ | Inhalt |
|---|---|---|
| OBS Update Channel | STRING | `stable`, `pre-release` oder `nightly` |
| OBS Current Version | STRING | installierte Version |
| OBS Latest Version | STRING | neueste Version im aktiven Kanal |
| OBS Last Check | DATETIME | Zeitpunkt des letzten abgeschlossenen Prüfversuchs |
| OBS Update Available | BOOLEAN | `true`, wenn im aktiven Kanal ein Update verfügbar ist |

Kanal und installierte Version stehen sofort nach dem Start bereit. **OBS Latest Version** und
**OBS Update Available** haben bis zur ersten erfolgreichen Prüfung die Qualität `uncertain`,
damit nicht fälschlich „kein Update“ gemeldet wird. Ein System-Datenpunkt darf über
DEST-Verknüpfungen an Geräte weitergegeben werden (z. B. auf eine KNX-Gruppenadresse), aber
keine SOURCE-Verknüpfung erhalten. Zeitschaltuhr und Anwesenheitssimulation können einen
System-Datenpunkt nicht verwenden. Eine neue Verknüpfung erhält den aktuellen Wert sofort, ebenso
jede Adapter-Instanz, sobald sie (wieder) verbunden ist. Ein
KNX-Projekt- oder CSV-Import legt für eine Gruppenadresse, an die ein System-Datenpunkt sendet,
keinen zusätzlichen Datenpunkt an.
