# AGENTS/CLAUDE Alias Note

`AGENTS.MD` is the canonical agent-instructions file in this repository.
`CLAUDE.md` is a symlink to this same file for tool compatibility.
You only need to read one of them; reading both is redundant.

# AGENTS.MD

This file provides guidance to AI coding agents when working with code in this repository.

## Project Overview

**open bridge server** is an open-source multiprotocol building automation server (MIT-licensed replacement for the proprietary Timberwolf Server). It bridges KNX, Modbus RTU/TCP, 1-Wire, MQTT, SNMP, Home Assistant, ioBroker, Anwesenheitssimulation (presence simulation), and Zeitschaltuhr into a unified system with a FastAPI REST/WebSocket API and a Vue-based admin GUI.

## Common Commands

```bash
# Run the server
tools/with-venv python -m obs

# Run all tests
tools/with-venv pytest tests/

# Run a single test file
tools/with-venv pytest tests/unit/test_converter.py

# Run a specific test
tools/with-venv pytest tests/unit/test_converter.py::test_float_to_int

# Run only adapter + unit tests (no Docker needed)
tools/with-venv pytest tests/adapters/ tests/unit/

# Run contract tests — verify external library API surfaces (no Docker needed)
tools/with-venv pytest tests/contracts/

# Run integration tests (requires Docker for Mosquitto)
tools/with-venv pytest tests/integration/

# Run with coverage report
tools/with-venv pytest tests/ --cov=obs --cov-report=term-missing

# Lint (same checks as CI)
tools/with-venv ./tools/lint.sh --check

# Format + autofix
tools/with-venv ./tools/lint.sh --fix

# Docker Compose (full stack)
docker compose up -d

# Docker Compose (Mosquitto only — for local dev outside Docker)
docker compose up -d mosquitto

# Build release artifacts locally (only requires Docker)
./tools/build-local.sh docker    # Docker image (via docker compose build obs + version stamp)
./tools/build-local.sh lxc       # Proxmox LXC template (.tar.zst); rootfs cached in ~/.cache/obs-lxc-builder/
./tools/build-local.sh bundle    # app bundle only, no rootfs (fast)
./tools/build-local.sh all       # docker + lxc

# Admin GUI dev server (proxies /api to localhost:8080)
cd gui && npm run dev
```

## Pre-Push Gate (verbindlich)

CI im PR-Workflow baut das Frontend **nicht** — `npm run build` läuft erst beim Release-Tag.
Ein Production-Build validiert die Frontend-Abhängigkeiten (Module-Resolution, Import-Pfade,
Typen) Ende-zu-Ende — Schwächen, die Vitest-Mocks verdecken können, fallen erst hier auf. Vor
**jedem** Push, der Frontend-Code berührt, alle Stufen lokal grün laufen lassen:

```bash
tools/with-venv ./tools/lint.sh --check
cd gui && npm run build     # validiert reale Abhängigkeiten/Imports, was Vitest nicht tut
cd gui && npm run test
cd gui && npm run test:coverage
# bei Backend-Änderungen zusätzlich:
tools/with-venv pytest tests/unit tests/adapters tests/contracts  # ohne Docker
tools/with-venv pytest tests/integration                          # mit Docker
```

Wenn rot: nicht pushen, sondern fixen. Gilt für Haupt-Sessions **und** für Subagents — bitte
explizit in den Subagent-Prompt schreiben, weil deren Kontext leer startet.

Zusätzlich für i18n-Änderungen (Admin GUI + Visu) gilt ein harter Diff-Gate:

```bash
# Diff-basierter i18n Hard-Gate (Hardcoded user-facing strings + locale parity)
./scripts/check-i18n-hardcoded-strings.sh
```

Für GUI-Änderungen gilt zusätzlich ein weicher Coverage-Nachzieh-Hinweis:

```bash
node tools/gui-coverage-summary.mjs --changed-only --threshold=70
```

Wenn geänderte Dateien unter dem Schwellwert liegen, im Abschlussbericht konkret nennen
und möglichst passende Vitests ergänzen. Der Hinweis ist bewusst kein lokaler Hard-Fail;
`npm run test` und `npm run test:coverage` bleiben dagegen verpflichtende Gates und
brechen bei Fehlern ab.

Optional als lokaler Push-Hook aktivieren:

```bash
git config core.hooksPath .githooks
```

Danach läuft der i18n-Gate automatisch vor jedem `git push`.

## Test Coverage Gate (verbindlich)

**Coverage darf durch neuen Code nie sinken.** Jede neue Python-Datei oder -Funktion in `obs/`
muss mit Tests abgedeckt sein, die die Gesamtabdeckung mindestens auf dem Niveau vor der Änderung
halten. Konkret:

- **Neuer Code → neue Tests.** Wer eine neue API-Route, einen neuen Adapter oder eine neue
  Hilfsfunktion hinzufügt, liefert zugehörige Tests (Unit oder Integration) im selben Commit.
- **Refactorings** dürfen die Coverage nicht senken.
- **Vor jedem Push mit Backend-Änderungen** Coverage prüfen:

```bash
# Baseline messen (vor der Änderung):
tools/with-venv pytest tests/unit tests/adapters tests/contracts --cov=obs --cov-report=term-missing

# Nach der Änderung nochmals — Gesamtprozentsatz muss ≥ Baseline sein:
tools/with-venv pytest tests/unit tests/adapters tests/contracts --cov=obs --cov-report=term-missing
# mit Docker auch:
tools/with-venv pytest tests/integration --cov=obs --cov-append --cov-report=term-missing
```

Wenn der Coverage-Prozentsatz gefallen ist: Tests ergänzen, nicht pushen.
Gilt für Haupt-Sessions **und** für Subagents — bitte explizit in den Subagent-Prompt schreiben.

## Local Development Setup

### Prerequisites
- Python 3.13+, Docker Desktop, Node.js 20.19+ or 22+ (use nvm; `.nvmrc` pins v22)

### One-time setup

```bash
# 1. Create local venv
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements_dev.txt

# 2. Config files
cp config.example.yaml config.yaml   # then edit (see below)
cp .env.example .env                  # set OBS_MQTT_PASSWORD

# 3. Frontend deps
cd gui && npm install
```

### Python venv resolver for agents and worktrees

Local checks must use the project venv through `tools/with-venv`:

```bash
tools/with-venv python -m pytest tests/unit
tools/with-venv ./tools/lint.sh --check
```

The resolver checks `OBS_VENV`, then the current worktree's `.venv`, then the main Git worktree's
`.venv`. If no usable venv exists, it exits with a hard error and explicitly forbids installing
dependencies into the worktree.

For linked worktrees, prefer a local `.venv` symlink to the main worktree venv:

```bash
tools/link-worktree-venvs
```

Do not run `pip install ...` in issue or PR worktrees. Install or update Python dependencies only
in the shared project venv, then let worktrees resolve that environment through `.venv` or
`OBS_VENV`.

### GUI Node dependencies for agents and worktrees

Do not run `npm install` or `npm ci` in issue or PR worktrees. GUI dependencies are shared from the
main worktree through a local `gui/node_modules` symlink:

```bash
tools/link-worktree-node-modules
```

The pre-push hook runs this linker automatically before GUI Vitest gates if `gui/node_modules` is
missing. If the main worktree dependencies are missing or stale, update them in the main worktree,
not in the issue/PR worktree.

### config.yaml — required local overrides

The defaults assume a Docker deployment (`/data/obs.db`, `/mosquitto/passwd/passwd`).
For local dev, override these keys:

```yaml
mqtt:
  username: obs
  password: <value of OBS_MQTT_PASSWORD in .env>

database:
  path: <absolute-project-path>/data/obs.db

mosquitto:
  passwd_file: <absolute-project-path>/data/mosquitto/passwd
  reload_pid: null
  reload_command: null
  service_username: obs
  service_password: <same as mqtt.password>
```

**Important:** `mqtt.password` and `mosquitto.service_password` must match `OBS_MQTT_PASSWORD` in `.env`,
which is the password the Dockerized Mosquitto is initialized with. Mismatch → `MqttConnectError: Not authorized`.

### PyCharm run configurations

Shared configs live in `.run/` and are loaded automatically by PyCharm:

| Config | Description |
|---|---|
| **OBS Mosquitto (Docker)** | Starts MQTT broker via `docker compose up mosquitto` |
| **OBS Backend** | Runs `python -m obs` with project venv |
| **OBS GUI (Admin)** | Runs `npm run dev` in `gui/`, serves on `localhost:5173` |
| **OBS GUI (Visu)** | Runs `npm run dev` in `frontend/`, serves on `localhost:5174` |
| **OBS Full Dev Stack** | Compound — launches all four at once |

### Dev URLs

| Service | URL |
|---|---|
| Admin GUI | http://localhost:5173 |
| API docs (Swagger) | http://localhost:8080/docs |
| MQTT | localhost:1883 |

Default login: `admin` / `admin`

### GUI architecture

- `gui/` — Admin GUI (Vue 3 + Vite), dev server on port 5173, built to `gui_dist/` (served by FastAPI at `/`)
- `frontend/` — Visu SPA (Vue 3 + TypeScript), built to `frontend_dist/` (served by FastAPI at `/visu`)
- Both proxy `/api` to `localhost:8080` during dev via `vite.config`

### Internationalisation (i18n)

Both frontends use **vue-i18n v9** (Composition API mode, `legacy: false`).

| File | Purpose |
|---|---|
| `gui/src/i18n.js` | i18n instance; locale auto-detected from `localStorage` → browser language → fallback `de` |
| `gui/src/locales/de.json` | German source strings (authoritative) |
| `gui/src/locales/en.json` | English translations |
| `gui/src/components/ui/LocaleSwitcher.vue` | Dropdown wired into Settings → Appearance |
| `frontend/src/i18n.ts` | Same setup for the Visu SPA (TypeScript) |
| `frontend/src/locales/{de,en}.json` | Visu locale files |
| `.weblate` | Weblate CLI (`wlc`) config for community translations |

#### Rules — every new feature must follow these

**No hardcoded user-facing strings.** Any text visible to the user — labels, button text, placeholders, tooltips, error messages, badge text, empty-state messages — must go through `$t()` / `t()`. This includes strings returned from utility functions (`utils/`, composables) that end up rendered in a template.

**`de.json` is the source of truth.** Always add new keys to both `de.json` (German) and `en.json` (English) in the same commit. Use Python (`json.dumps(..., ensure_ascii=False)`) when writing locale files programmatically — never shell heredocs, which strip non-ASCII characters.

#### Key namespace conventions

Admin GUI (`gui/src/locales/`):

| Namespace | Used for |
|---|---|
| `nav.*` | Sidebar navigation labels; page titles in TopBar |
| `topbar.*` | TopBar actions (e.g. `topbar.logout`) |
| `sidebar.*` | Connection status labels |
| `login.*` | Login view |
| `dashboard.*` | Dashboard view (stats, adapter status, live values, warnings) |
| `datapoints.*` | Data points list + detail view |
| `adapters.*` | Adapter instances view; `adapters.status.*` for badge labels |
| `history.*` | History view |
| `ringbuffer.*` | Monitor / ring-buffer view |
| `logs.*` | Log view |
| `logic.*` | Logic engine view |
| `settings.*` | Settings view |
| `common.*` | Shared across views (save, cancel, delete, error, warning, …) |
| `hierarchy.*` | Hierarchy manager |

Visu SPA (`frontend/src/locales/`):

| Namespace | Used for |
|---|---|
| `login.*`, `pin.*` | Auth views |
| `common.*` | Shared UI strings |
| `widgets.common.*` | Strings shared across widget configs |
| `widgets.<name>.*` | Per-widget config strings |

#### Usage patterns

**Template-only** — no script import needed when `t()` is not called from JS:
```vue
<label>{{ $t('datapoints.form.name') }}</label>
<input :placeholder="$t('datapoints.searchPlaceholder')" />
<button :title="$t('common.delete')">…</button>
```

**Script usage** — import `useI18n` only when `t()` is called in `<script setup>`:
```js
import { useI18n } from 'vue-i18n'
const { t } = useI18n()

// computed properties, functions, error messages:
const label = computed(() => t('adapters.status.connected'))
const msg = t('ringbuffer.queryFailed')
```

Do **not** add `const { t } = useI18n()` if the component only uses `$t()` in its template — TypeScript will flag `t` as declared but never used.

**Parametrized strings** — use named placeholders in the JSON value, pass an object as the second argument:
```json
{ "bindings": "{n} Verknüpfungen", "areasCount": "{n} Bereiche" }
```
```vue
{{ $t('dashboard.adapterStatus.bindings', { n: a.bindings }) }}
{{ $t('widgets.grundriss.areasCount', { n: cfg.areas.length }) }}
```

**Reactive option arrays** — when an array of `{ label, value }` objects is used in a `v-for` select/radio, it must be `computed` so labels update when the locale changes:
```js
// Wrong — labels are frozen at component creation time
const MODES = [{ value: 'a', label: t('key.a') }, …]

// Correct — labels recompute on locale change
const MODES = computed(() => [{ value: 'a', label: t('key.a') }, …])
```

**Pure utility functions** (`utils/`, plain `.js`/`.ts` files without a Vue setup context) cannot call `t()`. Instead, return locale key strings and translate at the callsite:
```js
// utils/adapterStatus.js
export function adapterStatusLabel(a) {
  if (!a.running) return 'adapters.status.inactive'
  if (a.connected) return 'adapters.status.connected'
  return 'adapters.status.running'
}

// In the template:
{{ $t(adapterStatusLabel(a)) }}
```

#### Checklist for new features

When adding a new view, widget config, or significant UI component:

1. Add all user-facing strings to `gui/src/locales/de.json` **and** `en.json` under the appropriate namespace, in the same commit.
2. Use `$t('namespace.key')` in templates; `t('namespace.key')` in script (after `const { t } = useI18n()`).
3. Only add `useI18n()` to a component's script if `t()` is actually called there.
4. Make option/select arrays `computed` if they contain translated labels.
5. Pure utility functions must return keys, not translated strings.
6. Run `npm run build` in `gui/` (and `frontend/` if Visu was touched) — a missing key does not throw at dev time but will surface as `[namespace.key]` in production.

#### Hard-Gate (local + CI)

`./scripts/check-i18n-hardcoded-strings.sh` enforces two checks on the PR/push diff:

1. Changed files under `gui/src` and `frontend/src` with extension `.vue`, `.js`, `.ts` are scanned file-wide for hardcoded user-facing strings outside `$t()` / `t()`.
2. If locale files changed, `de.json` and `en.json` are compared pairwise (`gui/src/locales/` and `frontend/src/locales/`); missing counterpart keys fail the gate.

Allowlisted technical literals are maintained in `scripts/i18n-allowlist.txt`. Keep the list minimal and reviewable.

##### `gui/src/utils/hierarchyDepthOptions.js`

This utility builds option labels that mix runtime tree node names with translated structural level names. It accepts an optional `t` function from the caller. Composite labels are assembled from translated parts and runtime data using local variables (not `label:` assignments) to stay clean of the i18n guard ASSIGN_RE scan. Call it from a `computed()` and pass `t` to keep labels reactive on locale changes.

#### E2E tests (Playwright) and locale

The Playwright suite lives in `tests/gui/` and requires a running full stack (backend + both frontends, typically via `docker compose up`). Run with `npx playwright test` from `tests/gui/`.

**Locale requirement:** `tests/gui/playwright.config.ts` sets `locale: 'de'` in the global `use` config. This tells Playwright's Chromium to simulate a German browser (`navigator.language = 'de'`), so `detectLocale()` always picks German and all test assertions against German strings work. Do not remove this setting — without it every test that checks a translated string will fail when run on a machine with an English OS/browser.

#### Adding a new locale

1. Add `src/locales/<lang>.json` (copy `de.json` as a starting point)
2. Import it in `i18n.js` / `i18n.ts` and add to `messages` + `SUPPORTED_LOCALES`
3. Run `wlc push gui-admin` to upload source; community translates on Weblate; `wlc pull gui-admin` to pull back

**Weblate** components are named `gui-admin` (Admin GUI) and `frontend-visu` (Visu SPA) under the `openbridgeserver` project on [hosted.weblate.org](https://hosted.weblate.org/projects/openbridgeserver/). Source language is `de`; file format is `JSON (simple)`. CLI tool: `pip install wlc`; credentials in `~/.config/weblate` or via `WLC_URL` / `WLC_KEY` env vars.

## Architecture

### Startup Sequence (`obs/main.py`)

Services initialize in this fixed order (reverse-order shutdown):
0. Log buffer (`LogBufferHandler.install`) — attached to root logger before the async startup; broadcasts all log records via WebSocket
1. SQLite DB + migrations
2. EventBus (async pub/sub)
3. MQTT client (connects to Mosquitto)
4. DataPoint Registry (loads from DB)
5. RingBuffer
6. History plugin
7. WebSocket Manager
8. WriteRouter (MQTT `dp/{uuid}/set` → adapters; cross-protocol propagation)
9. Protocol adapters (`@register` decorator triggers self-registration on import)
10. Logic Engine
11. Autobackup Scheduler (`init_autobackup_scheduler` from `obs/api/v1/autobackup.py`)

### Core Data Flow

```
Protocol Adapter → DataValueEvent (EventBus) → [Registry, RingBuffer, History, WebSocket, WriteRouter]
                                                       ↓
                                               DEST/BOTH Bindings → other adapters
```

- **DataPoint**: a named, typed value with a stable UUID. MQTT topic `dp/{uuid}/value`.
- **AdapterBinding**: connects a DataPoint to a protocol endpoint. `direction` is `SOURCE` (reads), `DEST` (writes), or `BOTH`.
- **WriteRouter**: when a SOURCE/BOTH binding fires a `DataValueEvent`, the router automatically writes to all DEST/BOTH bindings of the same DataPoint (skipping the originating binding to prevent loops). Also handles `dp/{uuid}/set` MQTT writes from external clients.

### Registry Pattern

All major subsystems use the same singleton pattern:
- `init_*()` constructs and stores a module-level singleton
- `get_*()` retrieves it (raises `RuntimeError` if not initialized)

Adapters self-register at import time via `@register` (from `obs/adapters/registry.py`). The adapter import block in `obs/main.py` is therefore the authoritative list of enabled adapters.

### Adding a New Adapter

1. Create `obs/adapters/<name>/adapter.py`
2. Subclass `AdapterBase`, set `adapter_type`, `config_schema`, `binding_config_schema`
3. Implement `connect`, `disconnect`, `read`, `write`
4. Decorate the class with `@register`
5. Add the import to the adapter block in `obs/main.py`

### Configuration

Priority (highest → lowest): environment variables → `config.yaml` → built-in defaults.

Env var pattern: `OBS_<SECTION>__<KEY>` (double underscore for nesting), e.g.:
- `OBS_MQTT__HOST=192.168.1.10`
- `OBS_SECURITY__JWT_SECRET=...`
- `OBS_DATABASE__PATH=/data/obs.db`

History backend is configured at runtime via the Admin UI (stored in `app_settings` table), not in `config.yaml`.

### Authentication

Dual-auth: JWT Bearer token (`Authorization: Bearer {token}`) and API Key (`X-API-Key: {key}`). Default credentials: `admin` / `admin`.

### Test Structure

| Directory | Scope | External deps |
|---|---|---|
| `tests/unit/` | Pure logic (converter, models, DPT registry, etc.) | None |
| `tests/adapters/` | Adapter unit tests with mocked EventBus | None |
| `tests/contracts/` | External library API surface contracts (see below) | None |
| `tests/integration/` | Full FastAPI app + real SQLite + real MQTT | Docker (Mosquitto) |

Integration tests spin up a `eclipse-mosquitto` Docker container on port 18830 automatically via the session-scoped `mosquitto_port` fixture. The `make_binding()` helper in `tests/adapters/conftest.py` creates mock bindings for adapter tests.

#### Contract tests (`tests/contracts/`)

One file per external library. Each test verifies the exact import paths and API surface that OBS uses — constructor parameter names, method signatures, return types. They run without Docker and complete in ~2 seconds.

**Purpose:** catch breaking changes when Renovate/Dependabot bumps a dependency. A library that renames a constructor parameter (e.g. `aiomqtt` 1.x→2.x changed `host=` to `hostname=`) will fail a contract test immediately rather than failing silently at runtime.

| File | Library | Key checks |
|---|---|---|
| `test_aiomqtt_contract.py` | `aiomqtt` | `Client(hostname=, port=, username=, password=)`, `publish/subscribe/messages`, `Message` fields |
| `test_astral_contract.py` | `astral` | `LocationInfo.observer`, `sun()` keys, `SunDirection`, `time_at_elevation()` |
| `test_croniter_contract.py` | `croniter` | `croniter(expr, now).get_next(datetime)` — exact manager.py pattern |
| `test_fastapi_contract.py` | `fastapi` | Routing, 422 response shape (`detail` list with `loc`/`msg`), `HTTPException`, `APIRouter` |
| `test_holidays_contract.py` | `holidays` | `country_holidays()`, `.items()`, date membership, `subdiv` kwarg |
| `test_jose_contract.py` | `python-jose` | `jwt.encode/decode` roundtrip, `JWTError`, expiry handling |
| `test_pydantic_contract.py` | `pydantic` | v2 APIs: `model_dump()`, `model_validate()`, `field_validator(mode=)`, `model_config` |
| `test_pymodbus_contract.py` | `pymodbus` | Import path `from pymodbus.client import Async*`, all read/write method names, `connected` |
| `test_slowapi_contract.py` | `slowapi` | `Limiter`, `_rate_limit_exceeded_handler`, `RateLimitExceeded` |
| `test_socketio_contract.py` | `python-socketio` | `AsyncClient`, `@sio.event`, `connect/emit/disconnect` as coroutines |
| `test_xknx_contract.py` | `xknx` | `Telegram`, `GroupAddress`, `DPTArray/DPTBinary`, APCI types |
| `test_icalendar_contract.py` | `icalendar` + `recurring_ical_events` | `Calendar.from_ical()`, `component.name`, `component.get()`, `of(cal).between(start, end)` |
| `test_pysnmp_contract.py` | `pysnmp` | `getCmd/setCmd/nextCmd`, `CommunityData`, `UsmUserData`, `UdpTransportTarget`, `ObjectType/ObjectIdentity`; tries v6 path (`hlapi.v3arch.asyncio`) first, falls back to v5 (`hlapi.asyncio`) |

#### Response shape helpers (`tests/integration/conftest.py`)

`assert_datapoint_shape()`, `assert_datapoint_page_shape()`, `assert_value_out_shape()`, `assert_auth_token_shape()` — call these after any integration test that reads a response body to verify all expected fields are present. Catches silent Pydantic/FastAPI serialization regressions that still return HTTP 200.

#### Dependency version bounds

`requirements.txt` caps all volatile libraries at the next major version (e.g. `pydantic>=2.13.3,<3`, `aiomqtt>=2.5.1,<3`). Renovate PRs are therefore scoped to minor/patch upgrades by default; a deliberate edit is required to cross a major version boundary. Coverage config lives in `.coveragerc`.

### Linting

`ruff` is the sole linter/formatter. Config in `.ruff.toml`: line length 150, target Python 3.13. Run `./tools/lint.sh --check` for the exact same lint path as GitHub CI, or `./tools/lint.sh --fix` for local autofix. Tests have relaxed rules (no `assert` warnings, no type annotations required, no docstrings). Contract tests (`tests/contracts/`) additionally suppress E402 (module-level import not at top of file) because `pytest.importorskip()` must precede the library imports it guards — a standard pytest pattern.

## Release & CI

### Workflows

PR/branch quality workflows:

| Workflow | Purpose |
|---|---|
| `.github/workflows/lint.yml` | Ruff lint + format-check via `./tools/lint.sh --check` |
| `.github/workflows/unittest.yml` | Python test matrix + coverage upload |
| `.github/workflows/e2e.yml` | Full-stack Playwright E2E |
| `.github/workflows/i18n-guard.yml` | Hard gate for i18n in changed frontend files + locale parity checks |

GitHub CI enforcement (required for merge gate):

1. Open `Settings` → `Branches` in `abeggled/openbridgeserver`.
2. Edit the branch protection rule for `main`.
3. Enable `Require status checks to pass before merging`.
4. Add `i18n-guard` as a required status check.

Two workflows run on every tag push (`push: tags: '*'`):

| Workflow | Purpose |
|---|---|
| `.github/workflows/release.yml` | Creates the GitHub release, builds and pushes the Docker image to GHCR |
| `.github/workflows/lxc-template.yml` | Builds the Proxmox LXC template and app bundle, uploads both as release assets |

`lxc-template.yml` never creates a release — it only uploads assets to the release that `release.yml` already created. The LXC build takes several minutes, so no race condition exists.

### Versioning

- The version is derived from the **topmost `## ` headline in `RELEASENOTES.md`**, with the RC suffix (e.g. `-RC1`) appended from the git tag when applicable.
- `obs/version` is committed to the repo as `dev-version` (indicates a local dev environment). Both `release.yml` and `lxc-template.yml` overwrite it with the real version at build time before packaging.
- `obs/__init__.py` reads `obs/version` at import time to expose `__version__`. Do not hardcode the version there.
- To start a new release cycle: add a new `## <version>` headline at the top of `RELEASENOTES.md`. No other file needs to change.

### Docker Image Tags

| Release type | Tags applied |
|---|---|
| Stable release | `latest`, `<version>`, `<short-git-hash>` |
| Release candidate | `<version-RC#>`, `<short-git-hash>` |

RCs never receive the `latest` tag.

### LXC Template

- Base OS: **Ubuntu 26.04 LTS (Resolute)** — chosen for native Python 3.14 support
- The build job runs as a **matrix** (two parallel jobs): `amd64` on `ubuntu-latest`, `arm64` on `ubuntu-24.04-arm` (native runner — no QEMU)
  - `amd64` uses `archive.ubuntu.com`; `arm64` uses `ports.ubuntu.com/ubuntu-ports` (including security updates)
- Three release assets are produced:
  - `openbridgeserver-lxc_<version>_amd64.tar.zst` — full Proxmox CT template (x86-64)
  - `openbridgeserver-lxc_<version>_arm64.tar.zst` — full Proxmox CT template (ARM64)
  - `openbridgeserver-app-bundle_<version>.tar.gz` — arch-agnostic app archive (`obs/`, `gui_dist/`, `frontend_dist/`, `requirements.txt`, `obs-update`) used for in-place updates; built once from the amd64 job
- App installs to `/opt/obs/`, Python venv at `/opt/obs/venv/`, data volume at `/data/`
- Installed version tracked in `/opt/obs/version` (written by `obs-update` after each install)
- `obs-update` script at `/usr/local/bin/obs-update` presents an interactive version picker (all RCs + up to two stable releases, sorted semantically). It self-updates on every install by copying the `obs-update` from the extracted bundle.

#### Mosquitto in the LXC template

Mosquitto is installed as a systemd service alongside OBS. Key paths:

| Path | Purpose |
|---|---|
| `/etc/mosquitto/mosquitto.conf` | Broker config (no anonymous, MQTT 1883, WebSocket 9001) |
| `/etc/mosquitto/passwd` | Password file managed by OBS |
| `/etc/obs.env` | Runtime environment vars including generated MQTT credentials |
| `/opt/obs/obs-first-boot.sh` | First-boot credential generator |

**First-boot credential setup:** `obs-first-boot.service` is a oneshot unit that runs once before `mosquitto.service` and `obs.service` on the first boot. It generates a 32-character random MQTT password, creates the Mosquitto passwd file for the `obs` service account, and appends all `OBS_MQTT__*` / `OBS_MOSQUITTO__*` / `OBS_SECURITY__*` variables to `/etc/obs.env`. A flag file `/etc/obs-first-boot-done` prevents it from running again on subsequent boots.

OBS reloads Mosquitto after passwd file changes via `OBS_MOSQUITTO__RELOAD_COMMAND=systemctl reload mosquitto`.

### Release Notes

`RELEASENOTES_FOOTER.md` contains an invisible HTML comment `<!-- LXC_INSERT -->` that marks where the LXC checksum block is injected by `lxc-template.yml`. Do not remove this marker.
