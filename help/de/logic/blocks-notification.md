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
Text unverändert mitgesendet. Die **Priorität** reicht von -2 (sehr niedrig) bis 1 (hoch).

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
