# tools/

> 🇩🇪 [Deutsche Version](README.de.md)

## build-local.sh — Local Build Artifacts

Builds release artifacts locally. Only requirement: Docker.

```bash
tools/build-local.sh COMMAND [OPTIONS]
```

### Commands

| Command  | Description |
|----------|-------------|
| `docker` | Build Docker image (via `docker compose build obs`) |
| `lxc`    | Build Proxmox LXC template (`.tar.zst`) — requires `--privileged` |
| `bundle` | Build app bundle without rootfs (much faster than `lxc`) |
| `all`    | `docker` + `lxc` in sequence |
| `clean`  | Remove artifacts in `dist/`, rootfs cache, and builder image |

### Options

| Option | Default | Description |
|--------|---------|-------------|
| `--version VER` | `git describe` (see below) | Override version manually |
| `--image NAME` | `localhost/openbridgeserver` | Docker image name / registry prefix |
| `--push` | — | Push image to registry after build |
| `--repo OWNER/REPO` | Auto-detected from `git remote origin` | GitHub repo for `obs-update` script |
| `--output DIR` | `dist/` | Output directory for LXC/bundle artifacts |
| `--no-cache` | — | Rebuild builder image and rootfs cache from scratch |

### Examples

```bash
# Build Docker image for the current working copy
tools/build-local.sh docker

# Build Docker image with an explicit version and push to a registry
tools/build-local.sh --version 2026.6.0 --push --image ghcr.io/owner/openbridgeserver docker

# Build LXC template (~10 min on first run)
tools/build-local.sh lxc

# Build app bundle only (obs/, gui_dist/, frontend_dist/) — fast
tools/build-local.sh bundle

# Build all artifacts at once
tools/build-local.sh all

# Remove build artifacts, rootfs cache, and builder image
tools/build-local.sh clean
```

### Local Docker Image Naming Schema

Every `docker` build produces exactly **two** tags pointing to the same image digest:

```
localhost/openbridgeserver:<version>     ← long version tag
localhost/openbridgeserver:<hash>        ← short hash tag
```

#### Version tag

Format: `<releasenotes-version>[‑RC<n>][‑<commits>‑<hash>][‑dirty]`

| Segment | Source | Meaning |
|---------|--------|---------|
| `<releasenotes-version>` | Top `## ` headline in `RELEASENOTES.md` | Current release version (e.g. `2026.6.0`) |
| `‑RC<n>` | Git tag suffix, if present | Release candidate identifier |
| `‑<commits>‑<hash>` | `git describe` | Commits since last tag + commit hash |
| `‑dirty` | `git status` | Working tree has uncommitted changes |

Examples:

| Situation | Version tag |
|-----------|-------------|
| Exactly on a release tag, clean tree | `2026.6.0` |
| Exactly on an RC tag, clean tree | `2026.6.0-RC1` |
| 22 commits after the RC tag, clean | `2026.6.0-RC4-22-e633092` |
| 22 commits after the RC tag, with changes | `2026.6.0-RC4-22-e633092-dirty` |

#### Hash tag

Format: `<hash>[‑dirty]`

The short commit hash (`git rev-parse --short HEAD`), optionally followed by `‑dirty` — identical to the hash segment in the version tag. Serves as an immutable identifier for the exact build state:

```
localhost/openbridgeserver:e633092          ← clean build
localhost/openbridgeserver:e633092-dirty    ← build from a dirty working tree
```

> **Note:** The `‑dirty` suffix always appears **in both tags** consistently — a `dirty` version tag and a clean hash tag cannot occur together.

#### obs/version inside the image

The stamped value in `/app/obs/version` (visible under *Settings → Info*) matches the version tag **without** the `‑<commits>‑<hash>[‑dirty]` part:

| Version tag | obs/version in image |
|-------------|---------------------|
| `2026.6.0-RC4-22-e633092-dirty` | `2026.6.0-RC4` |
| `2026.6.0-RC1` | `2026.6.0-RC1` |
| `2026.6.0` | `2026.6.0` |

The value is passed via `--build-arg OBS_VERSION=...` to the Docker build — the files in the working tree (`obs/version`, `gui/package.json`) are **never modified**.

### Notes

- The `lxc` and `bundle` commands use a builder Docker image (`obs-lxc-builder`) that is built automatically on first run and kept in the Docker layer cache.
- The debootstrap base system is cached in `~/.cache/obs-lxc-builder/` — downloaded once, then reused. Reset with `--no-cache` or `tools/build-local.sh clean`.
- Cross-architecture LXC builds are not supported locally; the output architecture matches the host. Multi-arch Docker images are only built in CI.

---

## testdata_generator.py

Generates configurable test traffic for KNX, Modbus TCP, and MQTT.
Each protocol runs as an independent async task; all three can run in parallel.
The generator replaces a real field installation for testing the open bridge server.

### Prerequisites

```bash
pip install pyyaml xknx pymodbus aiomqtt
```

Only the packages for the protocols you actually use are required.

### Starting

```bash
# With a custom configuration file
python tools/testdata_generator.py /path/to/config.yaml

# Without argument: looks for testdata_generator_example.yaml in the same directory
python tools/testdata_generator.py
```

---

## Configuration (YAML)

The configuration file consists of up to three top-level sections: `knx`, `modbus`, `mqtt`.
Simply omit sections you don't need — the generator only starts the configured protocols.

### Value Modes

Each signal (telegram, register, topic) uses one of the following modes:

| Mode       | Description                                       | Parameters                           |
|------------|---------------------------------------------------|--------------------------------------|
| `fixed`    | Constant value                                    | `value: <val>`                       |
| `sine`     | Sine curve between `min` and `max`                | `min`, `max`, `period` (s, def. 60)  |
| `random`   | Uniformly random between `min` and `max`          | `min`, `max`                         |
| `ramp`     | Linear ramp from `min` to `max`, then restart     | `min`, `max`, `period` (s, def. 60)  |
| `sequence` | Cycles through a list of values                   | `values: [a, b, c, …]`               |
| `toggle`   | Alternates between `true`/`false` on each step    | —                                    |

---

### KNX

Runs as a KNX/IP Tunneling **Server** (gateway simulator) — no physical KNX bus required.
The open bridge server KNX adapter connects to this server as a client.

**Adapter configuration in open bridge server:**
```
connection_type: tunneling
host: 127.0.0.1          # IP of the machine running the generator
port: 3671               # must match knx.port
```

**Configuration structure:**
```yaml
knx:
  host: 0.0.0.0          # Network interface (0.0.0.0 = all, 127.0.0.1 = local only)
  port: 3671             # KNX/IP UDP port (standard; <1024 requires root → use e.g. 4001)
  max_events_per_second: 3.0   # Optional global rate limit (default 3.0)

  telegrams:
    - group_address: "1/1/1"   # KNX group address (required)
      dpt_id: "DPT9.001"       # DPT ID from the DPT registry (required)
      interval: 5.0            # Seconds between telegrams
      mode: sine
      min: 18.0
      max: 25.0
      period: 120.0
```

**Common DPT IDs:**

| DPT       | Type                      | Value range    |
|-----------|---------------------------|----------------|
| DPT1.001  | Boolean (ON/OFF)          | true / false   |
| DPT5.001  | 1-byte percent            | 0 – 100        |
| DPT5.010  | 1-byte integer            | 0 – 255        |
| DPT9.001  | 2-byte float (°C)         | −273 – 670760  |
| DPT9.004  | 2-byte float (lux)        | 0 – 670760     |
| DPT9.007  | 2-byte float (% humidity) | 0 – 100        |
| DPT14.056 | 4-byte float              | IEEE 754       |

**Example:**
```yaml
knx:
  host: 0.0.0.0
  port: 4001

  telegrams:
    - group_address: "1/1/1"   # Temperature, sine curve 18–25 °C
      dpt_id: "DPT9.001"
      interval: 5.0
      mode: sine
      min: 18.0
      max: 25.0
      period: 120.0

    - group_address: "1/1/2"   # Brightness, ramp 0–100 %
      dpt_id: "DPT5.001"
      interval: 3.0
      mode: ramp
      min: 0
      max: 100
      period: 60.0

    - group_address: "1/1/3"   # Switch, toggle
      dpt_id: "DPT1.001"
      interval: 10.0
      mode: toggle

    - group_address: "1/1/4"   # Fixed value 21.5 °C
      dpt_id: "DPT9.001"
      interval: 30.0
      mode: fixed
      value: 21.5

    - group_address: "1/1/5"   # Sequence 0 → 25 → 50 → 75 → 100 %
      dpt_id: "DPT5.001"
      interval: 5.0
      mode: sequence
      values: [0, 25, 50, 75, 100]
```

---

### Modbus TCP

Runs as a Modbus **Slave/Server** — open bridge server polls as master.

**Adapter configuration in open bridge server:**
```
host: 127.0.0.1          # IP of the machine running the generator
port: 502                # must match modbus.port
unit_id: 1
```

**Configuration structure:**
```yaml
modbus:
  host: 0.0.0.0
  port: 502              # Port <1024 requires root → use e.g. 5001
  unit_id: 1

  registers:
    - register_type: holding   # holding | input | coil | discrete_input
      address: 0               # Register address (0-based)
      data_format: uint16      # uint16 | uint32 | int16 | int32 | float32
      scale_factor: 1.0        # raw value × scale_factor = physical value
      mode: sine               # Value mode (see table above)
      min: 0
      max: 100
      interval: 2.0            # Seconds between updates
```

**Register types:**

| Type              | FC  | Read/Write | Data          |
|-------------------|-----|------------|---------------|
| `holding`         | 3   | R/W        | 16-bit words  |
| `input`           | 4   | R          | 16-bit words  |
| `coil`            | 1   | R/W        | Boolean       |
| `discrete_input`  | 2   | R          | Boolean       |

**Note on `scale_factor`:** The raw register value equals `physical value / scale_factor`.
Configure the same `scale_factor` in the open bridge server binding.

**Example:**
```yaml
modbus:
  host: 0.0.0.0
  port: 5001
  unit_id: 1

  registers:
    - register_type: holding   # Temperature 18.0–25.0 °C as uint16 (raw ×0.1)
      address: 0
      data_format: uint16
      scale_factor: 0.1
      mode: sine
      min: 180
      max: 250
      interval: 2.0
      period: 120.0

    - register_type: holding   # Wind speed as float32 (2 registers)
      address: 2
      data_format: float32
      scale_factor: 1.0
      mode: random
      min: 0.0
      max: 15.0
      interval: 3.0

    - register_type: coil      # Relay status, toggle
      address: 0
      mode: toggle
      interval: 8.0
```

---

### MQTT

Connects to an existing broker and **publishes** values.
The open bridge server MQTT adapter **subscribes** to these topics.

**Configuration structure:**
```yaml
mqtt:
  host: localhost
  port: 1883
  username: myuser       # optional
  password: mypassword   # optional

  topics:
    - topic: path/to/topic     # MQTT topic (required)
      interval: 5.0            # Seconds between publications
      retain: false            # optional, default false
      payload_template: null   # optional, see below
      mode: sine               # Value mode (see table above)
      min: 0.0
      max: 100.0
```

**`payload_template`:** Enables JSON or arbitrary text payloads.
The placeholder `###DP###` is replaced with the generated value.

```yaml
payload_template: '{"sensor":"test","value":###DP###,"unit":"°C"}'
# → publishes e.g.: {"sensor":"test","value":21.3,"unit":"°C"}
```

**Example:**
```yaml
mqtt:
  host: localhost
  port: 1883

  topics:
    - topic: _testdata/temperature   # Temperature as plain float
      interval: 5.0
      mode: sine
      min: 18.0
      max: 25.0
      period: 120.0

    - topic: _testdata/switch        # Switch with retain
      interval: 10.0
      mode: toggle
      retain: true

    - topic: _testdata/sensor/json   # JSON payload
      interval: 4.0
      mode: ramp
      min: 0.0
      max: 100.0
      period: 60.0
      payload_template: '{"sensor":"test","value":###DP###,"unit":"%"}'

    - topic: _testdata/setpoint      # Sequence
      interval: 15.0
      mode: sequence
      values: [16.0, 18.0, 20.0, 22.0, 24.0]
      retain: true
```

---

## testdata-generator.service — systemd (Debian/Ubuntu)

### 1. Customize

Adjust the following settings in `testdata-generator.service` for your environment:

| Parameter                      | Default                         | Meaning                           |
|--------------------------------|---------------------------------|-----------------------------------|
| `User` / `Group`               | `obs`                           | System user for the process       |
| `WorkingDirectory`             | `/opt/openbridgeserver`         | Project installation path         |
| `ExecStart` (Python)           | `/usr/bin/python3`              | Path to Python (`which python3`)  |
| `ExecStart` (last argument)    | `…tools/config.yaml`            | Path to the configuration file    |

### 2. Install

```bash
sudo cp /opt/openbridgeserver/tools/testdata-generator.service \
        /etc/systemd/system/

# Create user if it doesn't exist yet
sudo useradd -r -s /bin/false obs

sudo systemctl daemon-reload
```

### 3. Start and enable

```bash
# Start immediately and enable at boot
sudo systemctl enable --now testdata-generator

# Start only (without autostart)
sudo systemctl start testdata-generator
```

### 4. Operations

```bash
sudo systemctl status testdata-generator
sudo journalctl -u testdata-generator -f
sudo systemctl stop testdata-generator
sudo systemctl disable testdata-generator
```

The service restarts automatically on crash (`Restart=on-failure`).
A clean stop via `systemctl stop` does **not** trigger a restart.
