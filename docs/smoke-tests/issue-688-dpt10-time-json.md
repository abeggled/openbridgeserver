# Issue #688: KNX DPT10 Time JSON Serialization

Diese Notiz dokumentiert den Fix fuer KNX-Zeitdatenpunkte mit DPT10.001.
Der Pull Request ist bewusst eng auf DPT10/TIME und die Serialisierung von
nicht direkt JSON-faehigen Python-Werten begrenzt.

## Ausgangslage

Auf der CM5-OBS-Instanz wurde nach dem Import des aktuellen KNX-Projekts der
Zeitdatenpunkt `8/6/4` als KNX `TIME` / `DPT10.001` angelegt. Beim Schreiben
oder Weiterreichen des Werts erschien im Log eine Typabweichung:

```text
WriteRouter: skip ... due to type mismatch (expected=time got=str)
```

Die Gruppenadresse war fachlich korrekt typisiert. Der Fehler lag im OBS-Code:
Der KNX-DPT10-Decoder gab bisher einen ISO-String wie `10:30:00` zurueck,
obwohl der interne OBS-Typ fuer den Datenpunkt `TIME` ein `datetime.time`
erwartet.

## Ursache

DPT10.001 ist in KNX eine Uhrzeit. OBS modelliert diesen Datenpunkttyp intern
als Python `datetime.time`. Durch den String-Rueckgabewert des Decoders sah der
WriteRouter einen falschen Typ:

| Ebene | Erwartet | Bisher |
|---|---|---|
| OBS-Datenpunkttyp `TIME` | `datetime.time` | `str` |
| JSON/WebSocket/MQTT/History | JSON-kompatibler Wert | direkte `json.dumps`-Serialisierung |
| Ergebnis | sauberer Zeitwert | Typkonflikt oder Serialisierungsrisiko |

## Umsetzung

- `DPT10.001` dekodiert jetzt auf `datetime.time`.
- `DPT11.001` dekodiert jetzt auf `datetime.date`, damit live empfangene
  DATE-Werte und aus der Persistenz wiederhergestellte DATE-Werte denselben
  internen Typ verwenden.
- Ungueltige oder zu kurze DPT10/DPT11-Telegramme werfen einen Decode-Fehler
  und werden vom KNX-Adapter als `uncertain` statt als gueltiger Ersatzwert
  publiziert.
- Die bestehende Kodierung bleibt abwaertskompatibel und akzeptiert weiterhin
  `datetime.time`, ISO-Strings und Sekundenwerte.
- Fuer JSON-Grenzen wurde ein zentraler Helper eingefuehrt:
  `obs.core.json`.
- Dieser Helper wandelt Werte mit `isoformat()` an den relevanten Ausgaengen in
  JSON-kompatible Werte um.
- Angepasst wurden Registry, Ringbuffer, History-Plugins, MQTT und WebSocket.

Damit bleibt der interne Datenpunkttyp korrekt, waehrend externe Schnittstellen
weiterhin lesbare ISO-Werte erhalten. MQTT unterscheidet dabei den Kontext:
Ohne Payload-Template bleibt der Rohpayload abwaertskompatibel `10:30:00`;
innerhalb eines Payload-Templates wird der Wert JSON-kompatibel als
`"10:30:00"` eingesetzt.

History-Readbacks bleiben eine JSON-/Anzeigegrenze: gespeicherte DATE/TIME-Werte
werden dort als ISO-Strings zurueckgegeben, nicht als Python-Objekte.

## Abgrenzung

Dieser PR enthaelt keine Aenderungen an:

- ioBroker-Reconnect-Verhalten
- CM5-Deployment oder Docker-Konfiguration
- Visu-Layout, Taster-Widget oder Szenenlogik
- KNX-Gruppenadressimport oder ETS-Projektstruktur

## Verifikation

Lokal ausgefuehrt:

```bash
.venv/bin/python -m pytest tests/unit/test_write_router.py tests/test_write_router_value_event.py tests/adapters/test_mqtt_adapter.py tests/unit/test_dpt_registry.py tests/unit/test_knx_dpt_codecs.py tests/unit/test_json_serialization.py tests/unit/test_ringbuffer_baseline.py tests/unit/test_ringbuffer_query_v2.py tests/integration/test_ringbuffer_filters.py -q
.venv/bin/python -m ruff check .
.venv/bin/python -m ruff format --check .
```

Ergebnis:

```text
325 passed
All checks passed!
221 files already formatted
```

GitHub Checks fuer PR #688:

- `test (3.12)`: passed
- `test (3.13)`: passed
- `Ruff`: passed
- `i18n-guard`: passed
- `CodeQL`: passed
- `monitor-baseline`: passed
- `playwright`: passed
- `codecov/patch`: passed

## Smoke-Test auf einer OBS-Instanz

Nach Deployment oder lokalem Start:

1. Einen KNX-Datenpunkt mit `DPT10.001` und OBS-Typ `TIME` anlegen oder aus
   einem ETS-Projekt importieren.
2. Einen Telegrammwert auf dem Bus empfangen, z. B. `10:30:00`.
3. Im OBS-Monitor pruefen, dass der Datenpunkt aktualisiert wird.
4. Im Log darf kein Typfehler `expected=time got=str` mehr erscheinen.
5. Ueber WebSocket oder History darf der Wert als JSON-kompatibler
   ISO-Zeitwert erscheinen.
6. Bei einem MQTT-DEST-Binding ohne Payload-Template muss der Publish-Payload
   roh `10:30:00` sein. In einem Payload-Template muss der eingesetzte Wert
   JSON-kompatibel `"10:30:00"` sein.

## Erwartetes Verhalten

Intern:

```python
datetime.time(10, 30, 0)
```

Extern an JSON-Grenzen:

```json
"10:30:00"
```

MQTT-DEST ohne Payload-Template:

```text
10:30:00
```

Dieses Verhalten trennt den fachlich korrekten OBS-Datentyp von der noetigen
JSON-Darstellung und verhindert den urspruenglichen Router-Konflikt.
