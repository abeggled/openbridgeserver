---
title: "Bausteine: Benachrichtigung"
---

# Bausteine: Benachrichtigung

Bausteine zum Versenden von Benachrichtigungen und zum Schreiben von Meldungen in ein
Meldungsarchiv.

## Benachrichtigung {#logic-block-notify-message}

Sendet eine Nachricht über einen konfigurierten MESSAGE-Adapter. Nach Auswahl des Adapters
zeigt der Baustein die auf dieser Adapter-Instanz aktiven, konfigurierten **Ziele** als
Checkboxen an — nur dort ausgewählte Ziele erhalten die Nachricht. Titel und Nachricht sind
Fallback-Werte: Ist der **Nachricht**-Eingang verbunden, wird dessen Wert anstelle des
Fallback-Texts gesendet. Ohne Datenpunkt-Kontext im Logikblock werden MESSAGE-Platzhalter im
Text unverändert mitgesendet.

Ist ein Pushover-Ziel ausgewählt, bietet der Baustein die
[Pushover-Optionen](../adapters/list#adapters-message-pushover) an: Bild-URL, URL, URL-Titel,
Priorität (-2 bis 2, bei 2 mit Retry und Expire), Sound, Formatierung, TTL und Zeitstempel.
Die zusätzlichen Eingänge **URL**, **URL-Titel** und **Bild-URL** überschreiben die statischen
Werte; diese unterstützen dieselben Variablen wie der Titel, eingesetzte Werte werden in URL und Bild-URL URL-kodiert. Bei HTML-Formatierung werden eingesetzte Variablenwerte und ein über den Eingang verbundener Nachrichtentext für Pushover HTML-maskiert; HTML-Auszeichnung gehört in die Fallback-Nachricht. Andere Provider bleiben unberührt. Eine gespeicherte Priorität 2 ohne Retry und Expire wird wie bisher als Priorität 1 gesendet und im Log vermerkt.
**Gesendet** wird nur ausgelöst, wenn alle Ziele erfolgreich versendet haben — ein Bild, das
nicht geladen werden kann, lässt den Versand scheitern.

**Titel** und **Fallback-Nachricht** unterstützen Variablen: Datum/Zeit (`###HH###`, `###DATE###`,
`###TS###` …) und über **Variablen** zugeordnete Objekte (`###OBS1###` …), Liste siehe
[JSON Extractor](./blocks-integration#logic-block-json-extractor). Der über den Eingang
verbundene Nachrichtentext wird nicht ausgewertet. Ist ein `###OBSn###`-Slot nicht konfiguriert
oder ohne Wert, wird nichts gesendet und der Fehler am Baustein angezeigt.

Der Baustein löst automatisch aus, sobald am **Nachricht**-Eingang ein Wert ankommt oder der
**Trigger**-Eingang wahr wird.

## Meldungsarchiv {#logic-block-message-archive}

Schreibt eine Meldung in ein ausgewähltes Meldungsarchiv. **Meldungstyp** und **Schweregrad**
steuern, wie die Meldung im Archiv eingeordnet wird. Titel und Nachricht sind Fallback-Werte,
die nur verwendet werden, wenn die entsprechenden Eingänge (**Titel**/**Nachricht**) nicht
verbunden sind — ein verbundener Eingang überschreibt den Fallback-Text.

Die Fallback-Texte unterstützen dieselben Variablen wie die Benachrichtigung (Datum/Zeit und
`###OBSn###`, siehe [JSON Extractor](./blocks-integration#logic-block-json-extractor));
bei einem nicht auflösbaren Slot wird nichts archiviert und der Fehler angezeigt.

Der Baustein löst automatisch aus, sobald am **Nachricht**-Eingang ein Wert ankommt oder der
**Trigger**-Eingang wahr wird.
