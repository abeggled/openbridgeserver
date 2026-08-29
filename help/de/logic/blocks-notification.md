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

Der Baustein löst automatisch aus, sobald am **Nachricht**-Eingang ein Wert ankommt oder der
**Trigger**-Eingang wahr wird.

## Meldungsarchiv {#logic-block-message-archive}

Schreibt eine Meldung in ein ausgewähltes Meldungsarchiv. **Meldungstyp** und **Schweregrad**
steuern, wie die Meldung im Archiv eingeordnet wird. Titel und Nachricht sind Fallback-Werte,
die nur verwendet werden, wenn die entsprechenden Eingänge (**Titel**/**Nachricht**) nicht
verbunden sind — ein verbundener Eingang überschreibt den Fallback-Text.

Der Baustein löst automatisch aus, sobald am **Nachricht**-Eingang ein Wert ankommt oder der
**Trigger**-Eingang wahr wird.
