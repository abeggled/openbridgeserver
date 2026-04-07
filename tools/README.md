# tools/

## testdata_generator.py

Generiert konfigurierbaren Test-Traffic für KNX, Modbus TCP und MQTT.
Jedes Protokoll läuft als unabhängiger async-Task; alle drei können parallel laufen.
Der Generator ersetzt eine echte Feldinstallation zum Testen des open bridge servers.

### Voraussetzungen

```bash
pip install pyyaml xknx pymodbus aiomqtt
```

Nur die Pakete der tatsächlich verwendeten Protokolle sind erforderlich.

### Starten

```bash
# Mit eigener Konfiguration
python tools/testdata_generator.py /pfad/zur/config.yaml

# Ohne Argument: sucht testdata_generator_example.yaml im selben Verzeichnis
python tools/testdata_generator.py
```

---

## Konfiguration (YAML)

Die Konfigurationsdatei besteht aus bis zu drei Top-Level-Abschnitten: `knx`, `modbus`, `mqtt`.
Nicht benötigte Abschnitte einfach weglassen — der Generator startet nur die konfigurierten Protokolle.

### Wert-Modi

Jedes Signal (Telegramm, Register, Topic) verwendet einen der folgenden Modi:

| Modus      | Beschreibung                                      | Parameter                            |
|------------|---------------------------------------------------|--------------------------------------|
| `fixed`    | Konstanter Wert                                   | `value: <wert>`                      |
| `sine`     | Sinuskurve zwischen `min` und `max`               | `min`, `max`, `period` (s, def. 60)  |
| `random`   | Zufällig gleichverteilt zwischen `min` und `max`  | `min`, `max`                         |
| `ramp`     | Lineare Rampe von `min` nach `max`, dann Neustart | `min`, `max`, `period` (s, def. 60)  |
| `sequence` | Zyklisch durch eine Liste von Werten              | `values: [a, b, c, …]`               |
| `toggle`   | Wechselt bei jedem Schritt zwischen `true`/`false`| —                                    |

---

### KNX

Läuft als KNX/IP Tunneling **Server** (Gateway-Simulator) — kein physischer KNX-Bus nötig.
Der open bridge server KNX-Adapter verbindet sich als Client zu diesem Server.

**Adapter-Konfiguration im open bridge server:**
```
connection_type: tunneling
host: 127.0.0.1          # IP der Maschine, auf der der Generator läuft
port: 3671               # muss mit knx.port übereinstimmen
```

**Konfigurationsstruktur:**
```yaml
knx:
  host: 0.0.0.0          # Netzwerkinterface (0.0.0.0 = alle, 127.0.0.1 = nur lokal)
  port: 3671             # KNX/IP UDP-Port (Standardport; <1024 braucht root → z.B. 4001 verwenden)
  max_events_per_second: 3.0   # Optionale globale Rate-Begrenzung (default 3.0)

  telegrams:
    - group_address: "1/1/1"   # KNX-Gruppenadresse (Pflicht)
      dpt_id: "DPT9.001"       # DPT-ID aus der DPT-Registry (Pflicht)
      interval: 5.0            # Sekunden zwischen Telegrammen
      mode: sine
      min: 18.0
      max: 25.0
      period: 120.0
```

**Häufige DPT-IDs:**

| DPT       | Typ                     | Wertebereich   |
|-----------|-------------------------|----------------|
| DPT1.001  | Boolean (EIN/AUS)       | true / false   |
| DPT5.001  | 1-Byte Prozent          | 0 – 100        |
| DPT5.010  | 1-Byte Ganzzahl         | 0 – 255        |
| DPT9.001  | 2-Byte Float (°C)       | −273 – 670760  |
| DPT9.004  | 2-Byte Float (lux)      | 0 – 670760     |
| DPT9.007  | 2-Byte Float (% Feuchte)| 0 – 100        |
| DPT14.056 | 4-Byte Float            | IEEE 754       |

**Beispiel:**
```yaml
knx:
  host: 0.0.0.0
  port: 4001

  telegrams:
    - group_address: "1/1/1"   # Temperatur, Sinuskurve 18–25 °C
      dpt_id: "DPT9.001"
      interval: 5.0
      mode: sine
      min: 18.0
      max: 25.0
      period: 120.0

    - group_address: "1/1/2"   # Helligkeit, Rampe 0–100 %
      dpt_id: "DPT5.001"
      interval: 3.0
      mode: ramp
      min: 0
      max: 100
      period: 60.0

    - group_address: "1/1/3"   # Schalter, Toggle
      dpt_id: "DPT1.001"
      interval: 10.0
      mode: toggle

    - group_address: "1/1/4"   # Fixwert 21.5 °C
      dpt_id: "DPT9.001"
      interval: 30.0
      mode: fixed
      value: 21.5

    - group_address: "1/1/5"   # Sequenz 0 → 25 → 50 → 75 → 100 %
      dpt_id: "DPT5.001"
      interval: 5.0
      mode: sequence
      values: [0, 25, 50, 75, 100]
```

---

### Modbus TCP

Läuft als Modbus **Slave/Server** — open bridge server pollt als Master.

**Adapter-Konfiguration im open bridge server:**
```
host: 127.0.0.1          # IP der Maschine, auf der der Generator läuft
port: 502                # muss mit modbus.port übereinstimmen
unit_id: 1
```

**Konfigurationsstruktur:**
```yaml
modbus:
  host: 0.0.0.0
  port: 502              # Port <1024 braucht root → z.B. 5001 verwenden
  unit_id: 1

  registers:
    - register_type: holding   # holding | input | coil | discrete_input
      address: 0               # Registeradresse (0-basiert)
      data_format: uint16      # uint16 | uint32 | int16 | int32 | float32
      scale_factor: 1.0        # Rohwert × scale_factor = physikalischer Wert
      mode: sine               # Wert-Modus (s. Tabelle oben)
      min: 0
      max: 100
      interval: 2.0            # Sekunden zwischen Updates
```

**Register-Typen:**

| Typ               | FC  | Lesen/Schreiben | Daten         |
|-------------------|-----|-----------------|---------------|
| `holding`         | 3   | R/W             | 16-Bit-Wörter |
| `input`           | 4   | R               | 16-Bit-Wörter |
| `coil`            | 1   | R/W             | Boolean       |
| `discrete_input`  | 2   | R               | Boolean       |

**Hinweis `scale_factor`:** Der Rohwert im Register entspricht `physikalischer Wert / scale_factor`.
Denselben `scale_factor` im open bridge server Binding konfigurieren.

**Beispiel:**
```yaml
modbus:
  host: 0.0.0.0
  port: 5001
  unit_id: 1

  registers:
    - register_type: holding   # Temperatur 18.0–25.0 °C als uint16 (Rohwert ×0.1)
      address: 0
      data_format: uint16
      scale_factor: 0.1
      mode: sine
      min: 180
      max: 250
      interval: 2.0
      period: 120.0

    - register_type: holding   # Windgeschwindigkeit als float32 (2 Register)
      address: 2
      data_format: float32
      scale_factor: 1.0
      mode: random
      min: 0.0
      max: 15.0
      interval: 3.0

    - register_type: coil      # Relais-Status, Toggle
      address: 0
      mode: toggle
      interval: 8.0
```

---

### MQTT

Verbindet sich mit einem bestehenden Broker und **publiziert** Werte.
open bridge server MQTT-Adapter **abonniert** diese Topics.

**Konfigurationsstruktur:**
```yaml
mqtt:
  host: localhost
  port: 1883
  username: myuser       # optional
  password: mypassword   # optional

  topics:
    - topic: pfad/zum/topic    # MQTT-Topic (Pflicht)
      interval: 5.0            # Sekunden zwischen Publizierungen
      retain: false            # optional, default false
      payload_template: null   # optional, s. unten
      mode: sine               # Wert-Modus (s. Tabelle oben)
      min: 0.0
      max: 100.0
```

**`payload_template`:** Ermöglicht JSON- oder beliebige Text-Payloads.
Der Platzhalter `###DP###` wird durch den generierten Wert ersetzt.

```yaml
payload_template: '{"sensor":"test","value":###DP###,"unit":"°C"}'
# → publiziert z.B.: {"sensor":"test","value":21.3,"unit":"°C"}
```

**Beispiel:**
```yaml
mqtt:
  host: localhost
  port: 1883

  topics:
    - topic: _testdata/temperature   # Temperatur als Plain-Float
      interval: 5.0
      mode: sine
      min: 18.0
      max: 25.0
      period: 120.0

    - topic: _testdata/switch        # Schalter mit Retain
      interval: 10.0
      mode: toggle
      retain: true

    - topic: _testdata/sensor/json   # JSON-Payload
      interval: 4.0
      mode: ramp
      min: 0.0
      max: 100.0
      period: 60.0
      payload_template: '{"sensor":"test","value":###DP###,"unit":"%"}'

    - topic: _testdata/setpoint      # Sequenz
      interval: 15.0
      mode: sequence
      values: [16.0, 18.0, 20.0, 22.0, 24.0]
      retain: true
```

---

## testdata-generator.service — systemd (Debian/Ubuntu)

### 1. Anpassen

Die folgenden drei Stellen in `testdata-generator.service` an die eigene Umgebung anpassen:

| Parameter                         | Default                             | Bedeutung                        |
|-----------------------------------|-------------------------------------|----------------------------------|
| `User` / `Group`                  | `obs`                               | Systembenutzer für den Prozess   |
| `WorkingDirectory`                | `/opt/openbridgeserver`             | Installationspfad des Projekts   |
| `ExecStart` (letztes Argument)    | `…testdata_generator_example.yaml` | Pfad zur Konfigurationsdatei     |

### 2. Installieren

```bash
sudo cp /opt/openbridgeserver/tools/testdata-generator.service \
        /etc/systemd/system/

# Benutzer anlegen (falls noch nicht vorhanden)
sudo useradd -r -s /bin/false obs

sudo systemctl daemon-reload
```

### 3. Starten und aktivieren

```bash
# Sofort starten und beim Boot automatisch starten
sudo systemctl enable --now testdata-generator

# Nur starten (ohne Autostart)
sudo systemctl start testdata-generator
```

### 4. Betrieb

```bash
sudo systemctl status testdata-generator
sudo journalctl -u testdata-generator -f
sudo systemctl stop testdata-generator
sudo systemctl disable testdata-generator
```

Der Service startet bei einem Absturz automatisch neu (`Restart=on-failure`).
Bei einem sauberen Stop via `systemctl stop` wird er **nicht** neu gestartet.
