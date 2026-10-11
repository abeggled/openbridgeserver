---
title: "Bausteine: Energie (HEMS)"
---

# Bausteine: Energie (HEMS)

Bausteine für das Energiemanagement (Home Energy Management System, HEMS). **HEMS Lite** bleibt bewusst herstellerunabhängig und
einfach: keine Prognosen, Tarife, Ladepläne oder Fahrzeugverwaltung — nur Ein- und Ausgangswerte
des Logikmoduls.

## Überschussregelung {#logic-block-hems-surplus}

Verteilt den am Netzanschlusspunkt gemessenen Energieüberschuss nach Priorität auf mehrere
Verbraucher, z. B. Wallbox, Warmwassererhitzer, Heizstab, Wärmepumpe oder Poolpumpe. Der Block
regelt sich selbst über einen internen Scheduler (**Regelintervall**, Standard 30 s) und reagiert
zusätzlich auf neue Zählerwerte; ein Regelzyklus läuft aber höchstens einmal pro Intervall.

### Messarten am Netzanschlusspunkt

| Messart | Eingänge | Bedeutung |
|---|---|---|
| Bidirektional über einen Eingang | Netzleistung | positiv = Netzbezug, negativ = Einspeisung |
| Getrennte Eingänge | Netzbezug, Netzeinspeisung | beide positiv (0 bis x W); intern `Netzleistung = Netzbezug − Netzeinspeisung`. Beide Werte müssen gültig sein. |
| Nur Einspeiseleistung | Einspeiseleistung | `0 W` = keine bekannte Einspeisung, positiv = aktuelle Einspeisung |

**Einschränkung bei „Nur Einspeiseleistung":** Bei `0 W` kann der Block nicht unterscheiden, ob
ein exaktes Gleichgewicht oder bereits Netzbezug vorliegt. Er reagiert deshalb konservativ: Solange
keine Einspeisung gemeldet wird, wird die aktuelle Leistung gehalten (nie erhöht). Bleibt die
Einspeisung länger als die **Ausschaltverzögerung** aus, wird danach in jedem Regelzyklus ein Schritt
des Verbrauchers mit der niedrigsten Priorität abgebaut (bei Prozent-Verbrauchern mindestens 10 %),
bis wieder Einspeisung gemeldet wird. Ein Schritt gilt erst als erledigt, wenn der Verbraucher ihn
umgesetzt hat — ein Ein/Aus-Verbraucher durchläuft dabei seine eigene Ausschaltverzögerung und
Mindestlaufzeit.
Frei werdende Leistung geht dabei an keinen anderen Verbraucher, und eine positive Einspeisereserve
wird sofort abgezogen, da sie bei 0 W Einspeisung sicher nicht erreicht ist.

Optionale globale Eingänge: **Freigabe** (false = alles sicher aus), **Einspeisereserve (dyn.)**
(überschreibt den konfigurierten Wert) und **Anlage OK** (false = alles sicher aus). Unverbundene
Eingänge gelten als „frei"; ein verbundener Eingang ohne Wert (z. B. ein noch nicht befülltes
Read-Objekt nach einem Neustart) gilt dagegen als false. Text-Werte werden wie bei allen
Logikbausteinen gelesen: nur `0`, `false`, `no`, `off` und leer sind false.

### Leistungsbudget

```text
Budget = aktuell verwendete Leistung der geregelten Verbraucher − Netzleistung − Einspeisereserve
```

Bereits zugeteilte Leistung bleibt dadurch erhalten, obwohl sie die zuvor gemessene Einspeisung
inzwischen verringert hat. Als „verwendete Leistung" dient die gemessene Leistung des Verbrauchers
(Eingang *Gemessene Leistung*), sonst die aus Sollwert und Maximalleistung geschätzte. Gezählt
werden nur Verbraucher, die der Block selbst eingeschaltet hat: Ein inaktiver oder vom Block
ausgeschalteter Verbraucher, der trotzdem Leistung bezieht (Handbetrieb, eigener Thermostat), ist
normale Hauslast und wird nicht an andere verteilt.

Beispiel: 2 000 W geregelte Last, Zähler −1 000 W, Reserve 200 W → Budget 2 800 W.

### Verbraucher und Priorität

Die Reihenfolge der Liste **ist** die Priorität: oben = höchste. Sortiert wird ausschließlich per
Drag-and-drop; die Positionsnummer ist nur eine Anzeige, doppelte Prioritäten sind ausgeschlossen.
Neue Verbraucher werden unten angefügt. Das Budget wird von oben nach unten verteilt; sinkt es,
wird von unten nach oben reduziert oder abgeschaltet. Jeder Verbraucher hat eine feste interne ID —
Umsortieren verändert deshalb keine bestehenden Verbindungen.

Gemeinsame Einstellungen: Name, *Aktiv*, Regelart, Nenn-/Maximalleistung (fester Wert oder
Eingang), Mindestlaufzeit, Mindestauszeit sowie Ein-/Ausschaltverzögerung (leer = globaler Wert).
Eine **Mindestlaufzeit** verhindert einen sofortigen Lastabwurf: der Verbraucher läuft bis zu ihrem
Ablauf weiter, die Priorität bleibt bestehen.

**Prozent** — Sollwert 0–100 %. Einstellungen: Maximalleistung, Mindestleistung, minimaler/maximaler
Sollwert, Schrittweite und das Verhalten unterhalb der Mindestleistung (auf 0 % setzen oder auf dem
Mindestwert halten, solange die Ausschaltverzögerung läuft). Beispiel: 4 kW von 10 kW → 40 %.

**Ein/Aus** — boolescher Ausgang. Der Verbraucher schaltet ein, sobald das ihm zustehende Budget die
**Einschaltschwelle** (Standard: Nennleistung) für die Einschaltverzögerung durchgehend erreicht, und
aus, wenn es die **Ausschaltschwelle** für die Ausschaltverzögerung unterschreitet. Getrennte
Schwellen bilden eine Hysterese; mit einer Ausschaltschwelle unter der Nennleistung wird ein
bestimmter Netzbezug toleriert.

**Trigger** — einmaliger Impuls (z. B. Waschmaschine über eine API starten). Er löst aus, wenn der
**Mindestüberschuss** durchgehend für die eingestellte Zeit vorhanden war, und ist dann für die
**Sperrzeit** gesperrt. **Erneute Freigabe**: nach Ablauf der Sperrzeit, erst nachdem der
Schwellwert zwischenzeitlich unterschritten wurde (Standard) oder über den **Reset**-Eingang. Eine
optionale **Reservierte Leistung** wird im Auslöse- und im folgenden Zyklus vom Budget der
niedrigeren Prioritäten abgezogen, damit die anlaufende Last nicht schon verteilt ist. Die **Impulsdauer** beträgt
0,1 s bis 1 h. Ein Trigger löst nie wegen eines bloß anhaltenden Überschusses erneut aus, sondern
erst nach seiner erneuten Freigabe; bei „nach Ablauf der Sperrzeit“ mit Sperrzeit 0 also bei jedem
Regelzyklus, solange der Überschuss reicht.

### Ausgänge

Pro Verbraucher: ein Ausgang (Prozentwert / Ein-Aus / Triggerimpuls) und ein Status
(`inactive`, `off`, `on_delay`, `active`, `reduced`, `min_runtime`, `off_delay`, `locked`, `fired`,
`safe`, `safe_hold`, `invalid_input`). Diagnose: Netzleistung, Überschuss, Leistungsbudget, verteilte Leistung,
verbleibendes Budget, Status des Blocks (`ok`, `disabled`, `plant_not_ok`, `invalid_input`,
`stale_input`) und Warnung. Mit **Nur bei Änderung ausgeben** (Standard) werden unveränderte
Ausgänge nicht erneut an direkt verbundene Write-Objekte gesendet. Nachgeschaltete Logikbausteine
(z. B. NICHT, UND) rechnen dagegen immer mit dem zuletzt gesendeten Wert des Ausgangs weiter — er ist
ein Zustand, kein Ereignis; doppelte Schreibvorgänge dahinter verhindert bei Bedarf die Option *Nur bei
Änderung* des Write-Objekts. Wird ein Verbraucher aus
der Liste entfernt, geht sein Ausgang einmalig auf 0 % bzw. `false`, damit ein noch verbundener
Aktor nicht eingeschaltet bleibt. Im Debug-Tab erscheinen alle Ausgänge mit dem zuletzt ausgegebenen Wert; im sicheren Zustand bleiben die Diagnosewerte sichtbar (Netzleistung, soweit bekannt; Überschuss, Budget, verteilt und Rest = 0).

### Fehler- und Neustartverhalten

- Fehlende, nicht numerische, `NaN`- oder veraltete Zählerwerte sowie *Freigabe* = false oder
  *Anlage OK* = false lösen **keine** Aktivierung aus. Standardmäßig gehen alle Prozentwerte auf 0 %,
  alle Schaltausgänge auf `false`, und es werden keine Trigger ausgelöst (Alternative: letzte
  Ausgaben halten — gehalten wird nur ein bereits gesendeter Ausgang; ein Verbraucher ohne einen
  solchen, etwa direkt nach einem Neustart, geht in den sicheren Zustand). Das Ereignis wird im Log
  vermerkt.
- **Veraltete Werte** werden nur geprüft, wenn **Maximales Alter der Zählerwerte** größer als 0 ist
  *und* der Ausgang *Geändert* des Read-Objekts am Block angeschlossen ist (siehe Sensor Watchdog).
  Der Block reagiert, sobald das Alter überschritten ist, nicht erst beim nächsten Regelzyklus. Ein
  nach einem Neustart wiederhergestellter Wert gilt als veraltet, bis der Zähler ein neues Telegramm
  meldet.
- Nach einem Neustart beginnt der Block immer im sicheren Zustand; der Zustand wird nicht
  gespeichert, ein Trigger löst nie aufgrund eines alten Zustands aus.
- Wird das Logikblatt deaktiviert oder der Block gelöscht, endet die Regelung, die Ausgänge werden
  aber — wie bei jedem Logikbaustein — nicht zurückgesetzt. Zum geregelten Abschalten zuerst
  *Freigabe* auf false setzen (sicherer Zustand), dann deaktivieren.
