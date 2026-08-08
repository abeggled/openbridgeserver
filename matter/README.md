# matterbridge-obs

A [Matterbridge](https://matterbridge.io) dynamic platform plugin that bridges open bridge
server (OBS) DataPoints into Matter — usable from Apple Home, Google Home, Amazon Alexa and
SmartThings without a hub or cloud skill. Background: issue
[#56](https://github.com/abeggled/openbridgeserver/issues/56), spec discussion
[#357](https://github.com/abeggled/openbridgeserver/discussions/357).

## Architecture

```
OBS Core  →  Mosquitto MQTT (dp/{uuid}/value, dp/{uuid}/set)  →  matterbridge-obs  →  Matter
                                      ↑
                    OBS REST API (GET /api/v1/datapoints/, one-time discovery at startup)
```

This plugin does **not** talk to OBS-specific protocol adapters (KNX, Modbus, …) — it is an
external MQTT/REST client like any other, using exactly the interfaces OBS already exposes.
No OBS core changes were needed for this slice.

## Opt-in via tags

A DataPoint is exposed to Matter only if it carries a tag of the form `matter:<devicetype>`
(edit tags in the OBS Admin GUI's DataPoint form). Supported in this first slice:

| Tag | Matter device type | Direction |
|---|---|---|
| `matter:temperature` | TemperatureSensor | read-only |
| `matter:onoff` | OnOff (generic outlet/actuator) | read/write |

More device types (ContactSensor, DimmableLight, Thermostat, …) and a proper `matter_config`
UI are planned for later slices (M3/M4 in the #357 spec) — the tag convention is a deliberately
minimal stand-in until then.

## Prerequisites for local development

Matterbridge plugins must **never** declare `matterbridge` (or `@matter`/`@project-chip`) as a
dependency — a second bundled copy of matter.js causes runtime instability. Instead, link
against a local Matterbridge install for type-checking:

```bash
npm install -g matterbridge
cd matter
npm install
npm run link   # npm link matterbridge
npm run build
npm test       # only exercises matterbridge-independent modules (config, obsApiClient, deviceTypeRegistry)
```

## Running it against a local OBS

This plugin runs inside the **official** `luligu/matterbridge` Docker image (see
`docker-compose.yml`, profile `matter`) — there's no custom Dockerfile to build. The `matter/`
directory itself is bind-mounted into the container as a plugin source directory.

1. `npm run build` in `matter/` (produces `dist/`, which is what gets mounted in). The Docker
   mount (below) is **writable, not read-only** — Matterbridge runs its own
   `npm link matterbridge` inside the container for every locally-added plugin at every startup
   (visible in its logs as "Linking matterbridge to local plugin matterbridge-obs..."), so no
   manual `npm link`/`npm unlink` dance is needed for the Docker path at all — that's purely a
   local `tsc`/editor convenience (see Prerequisites above). A read-only mount makes that
   automatic link fail with "Error linking matterbridge to plugin … The plugin is disabled" —
   confirmed by testing; keep the mount writable.
2. Create an API key in the OBS Admin GUI (Settings → API Keys) for this plugin.
3. `docker compose up -d mosquitto` (or the "OBS Mosquitto" PyCharm run config) and start OBS
   itself directly via PyCharm ("OBS Backend").
4. `COMPOSE_PROFILES=matter docker compose up -d matterbridge` (or the "OBS Matterbridge"
   PyCharm run config).
5. One-time plugin registration (Matterbridge doesn't auto-discover mounted plugins):
   ```bash
   docker exec -it obs-matterbridge matterbridge --add /root/Matterbridge/matterbridge-obs
   docker restart obs-matterbridge   # picks up the newly registered plugin
   ```
6. The first start fails fast with a clear error ("config field obsApiKey is required …") since
   the injected default config has an empty `obsApiKey`/`mqttPassword`. Open the Matterbridge
   frontend (`http://localhost:8283` by default — the container runs with `network_mode: host`,
   so this is a normal host port, not a compose-mapped one) to fill in the plugin config (OBS API
   URL and the API key from step 2, MQTT host/port/credentials) via the schema-driven form, then
   restart the plugin from the frontend. Once configured, you'll also get the Matter pairing QR
   code / setup code for your controller app (Alexa app, Apple Home, …) from the same frontend.

**Networking note:** Matter requires full host network access for mDNS discovery
(`network_mode: host` in `docker-compose.yml`) — the container is therefore *not* on the same
Docker bridge network as `mosquitto`/`obs`. With host networking, `localhost` *inside* the
container correctly means "the Docker host", so `obsApiUrl: http://localhost:8080` and
`mqttHost: localhost` (the shipped defaults in `matterbridge-obs.config.json`) work out of the
box against a locally-run OBS + `docker compose up -d mosquitto` — confirmed by testing.

**Verified locally end-to-end (2026-08-07):** built the plugin, ran it against a real OBS
instance + Mosquitto, tagged two DataPoints (`matter:onoff`, `matter:temperature`), and
confirmed in the Matterbridge logs that both were discovered via the REST API and registered as
Matter endpoints (`TemperatureSensor` device type `0x0302`, `OnOffPlugInUnit`). Publishing a
value to `dp/{uuid}/value` over MQTT correctly updated the corresponding Matter attribute in
real time (`TemperatureMeasurement.measuredValue: null → 2150` for 21.50°C,
`OnOff.onOff: false → true`). The write path (`addCommandHandler('on'/'off')` →
`dp/{uuid}/set`) is wired the same way but needs a real paired controller (Alexa/HomeKit) to
trigger — not verified in this pass.

## Standalone Matter LXC template

For Proxmox/LXC-only installs (no Docker), the same plugin also ships as a **separate, standalone
LXC template** — deliberately not embedded into the main `openbridgeserver-lxc` template, to
avoid adding a permanent Node.js footprint to every OBS install (see the #56 discussion). It runs
`matterbridge` as a systemd service instead of a container, otherwise identical: same plugin,
same `/root/Matterbridge/matterbridge-obs` layout, same frontend-driven config, talking to an
existing OBS instance purely over the network.

Build locally:

```bash
./tools/build-local.sh matter-lxc
```

Produces `dist/matterbridge-obs-lxc_<version>_<arch>.tar.zst` (+ `.sha256`), built by
`tools/_matter-lxc-inner.sh` (modeled on the main template's `tools/_lxc-inner.sh`, much
smaller — no Python/venv, no GUI/Visu build, just Node.js 24 + `matterbridge` (global npm
install) + this plugin, one `matterbridge.service` systemd unit). Plugin registration
(`matterbridge --add`) runs for real at build time, inside the chroot — confirmed working by
inspecting the packaged rootfs and running `matterbridge --version`/`node --version` inside it via
a privileged Docker chroot. Actually booting it under systemd needs a real Proxmox host (`pct
restore` the `.tar.zst`, or equivalent) — not verified in this pass, this sandbox has no Proxmox.

Not yet built: a GitHub Actions release workflow for this template (its own versioning/trigger
scheme — tracking `matterbridge` upstream releases vs. its own tags — is an open decision, see
the #56 comment) and an `obs-update`-style updater script (`scripts/obs-update` is too
OBS-specific to reuse as-is).

## Out of scope for this slice

See issue #56 for the full roadmap: `matter_config` DB table + REST API + Visu integration,
additional device types, CI/release automation for the LXC template.
