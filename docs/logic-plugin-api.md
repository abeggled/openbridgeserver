# Logic Block Plugin API

This document describes how to write custom logic block types for **open bridge server** using the plugin API. Plugins add new node types to the logic editor without modifying OBS source code.

---

## Overview

A **logic block plugin** is a Python class that:

1. Subclasses `LogicNodePlugin`
2. Declares `type_name` — a unique string identifier
3. Implements `node_type_def()` — returns UI metadata (label, ports, config schema, colour)
4. Implements `evaluate()` — pure synchronous evaluation function

Decorate the class with `@register_node_type` and it is added to the node registry at import time. OBS discovers and imports your module at startup, after which the block appears in the GUI palette like any built-in block.

---

## How plugins are loaded

OBS loads plugins once during startup, **before** the logic engine initialises. Two discovery paths run in order:

| Path | When to use |
|---|---|
| **Python entry points** (`obs.logic_blocks`) | Distributable pip packages |
| **`plugins/` directory** | Local files, rapid iteration |

Both paths are additive — plugins from both sources are merged into the registry.

### Entry points (pip-installable packages)

Declare the entry point in your package's `pyproject.toml`:

```toml
[project.entry-points."obs.logic_blocks"]
my_plugin = "my_package.plugin_module"
```

Install the package into the same Python environment as OBS:

```bash
pip install obs-plugin-shadow-control
```

OBS imports the declared module at startup via `importlib.metadata.entry_points`. The `@register_node_type` decorator runs automatically on import.

### `plugins/` directory (local files)

Set `plugins_dir` in `config.yaml` to a directory OBS will scan for `*.py` files:

```yaml
plugins_dir: /opt/obs/plugins
```

Or via environment variable:

```bash
OBS_PLUGINS_DIR=/opt/obs/plugins
```

Any `*.py` file in that directory (not starting with `_`) is imported at startup. Files are processed in alphabetical order. When `plugins_dir` is configured, OBS also starts a background file-watcher so changes are picked up **without a restart** — see [Hot-reload](#hot-reload) below.

---

## Hot-reload

When `plugins_dir` is configured, OBS watches the directory at runtime using `watchfiles`. File changes are picked up automatically — no server restart required.

| Event | What happens |
|---|---|
| File **added** | Module is imported; new node types appear in the palette immediately |
| File **modified** | Stale registrations for that file are removed, module is re-imported fresh |
| File **deleted** | All node types contributed by that file are unregistered |

Log output during a reload cycle:

```
INFO  obs.logic.plugin_loader: Plugin hot-reload active — watching /opt/obs/plugins
INFO  obs.logic.plugin_loader: Plugin reloaded: shadow_control.py — types: ['shadow_control']
INFO  obs.logic.plugin_loader: Plugin unloaded: shadow_control.py — removed types: ['shadow_control']
```

If the reloaded file has a syntax error or the class is missing `@register_node_type`, the load fails and the old registration is already gone:

```
WARNING obs.logic.plugin_loader: Plugin reload produced no types: shadow_control.py
ERROR   obs.logic.plugin_loader: Plugin failed to load: shadow_control.py
Traceback ...
```

Fix the file and save — the watcher fires again immediately.

> **Note:** Hot-reload applies only to the `plugins/` directory. Pip-installed entry-point plugins require a full OBS restart to pick up changes.

---

## Plugin interface

Import everything you need from `obs.logic.plugin_api`:

```python
from obs.logic.plugin_api import (
    LogicNodePlugin,
    NodeTypeDef,
    NodeTypePort,
    register_node_type,
)
```

### `LogicNodePlugin` (ABC)

```python
class LogicNodePlugin(ABC):
    type_name: str          # unique identifier, e.g. "shadow_control"

    @classmethod
    @abstractmethod
    def node_type_def(cls) -> NodeTypeDef: ...

    @classmethod
    @abstractmethod
    def evaluate(
        cls,
        node_id: str,
        inputs: dict[str, Any],
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]: ...
```

#### `type_name`

Must be globally unique across all built-in and plugin node types. Use a descriptive snake_case string, e.g. `shadow_control`, `moving_colors`, `pid_controller`. Clashes with built-in names or other plugins are logged as a warning; the last-registered plugin wins.

#### `node_type_def() -> NodeTypeDef`

Returns the static metadata OBS uses to render the block in the GUI palette and validate graph data. Called once per registration. See [NodeTypeDef reference](#nodeypedof-reference) below.

#### `evaluate(node_id, inputs, config, state) -> (outputs, new_state)`

Called every time the graph executes and this node is reached. Must be **synchronous and side-effect-free** with respect to external I/O (no HTTP calls, no file writes). For async operations, use the built-in `api_client` block as a companion and wire it to your plugin block's output.

| Parameter | Type | Description |
|---|---|---|
| `node_id` | `str` | Stable ID of this node instance in the graph |
| `inputs` | `dict[str, Any]` | Resolved upstream values, keyed by port ID. Missing connections arrive as `None`. |
| `config` | `dict[str, Any]` | The node's data dict from the GUI editor. Fields not set by the user may be absent — always use `.get()` with a default. |
| `state` | `dict[str, Any]` | Mutable persistent dict. Survives between graph executions. Persisted to the database after each graph run. |

**Return value:** `(outputs, new_state)`

- `outputs` — dict keyed by output port ID. Any port not in the dict gets `None`.
- `new_state` — the updated state dict. You can mutate `state` in-place and return it, or return a new dict.

---

## `NodeTypeDef` reference

```python
class NodeTypeDef(BaseModel):
    type: str               # must match LogicNodePlugin.type_name
    label: str              # display name in the palette
    category: str           # palette group (see below)
    description: str = ""   # tooltip text
    inputs: list[NodeTypePort] = []
    outputs: list[NodeTypePort] = []
    config_schema: dict[str, Any] = {}   # JSON schema for node data fields
    color: str = "#475569"  # hex colour of the node header
```

### Categories

Use one of the existing categories to have your block grouped with related built-ins, or invent a new one (it will appear as its own group in the palette):

| Category | Built-in blocks using it |
|---|---|
| `logic` | AND, OR, NOT, XOR, Gate, Hysteresis, Compare |
| `datapoint` | Read DP, Write DP |
| `math` | Formula, Scale, Limiter, Statistics, Min/Max tracker |
| `string` | Concatenate |
| `timer` | Delay, Pulse, Trigger (cron), Operating hours |
| `script` | Python script |
| `ai` | AI logic |
| `astro` | Astro sun |
| `notification` | Pushover, SMS |
| `integration` | API request, JSON extractor, XML extractor, iCal |

### `NodeTypePort`

```python
class NodeTypePort(BaseModel):
    id: str             # internal handle ID used in edges and evaluate() inputs/outputs
    label: str          # display label on the node
    type: str = "value" # "value" (default) or "trigger"
```

Port IDs must be unique within the inputs list and within the outputs list. They are used as keys in the `inputs` dict passed to `evaluate()` and in the `outputs` dict you return.

### `config_schema`

A flat dict where each key is a field name and the value is a descriptor:

```python
config_schema={
    "threshold": {
        "type": "number",
        "default": 20,
        "min": 0,     # optional
        "max": 90,    # optional
        "label": "Min elevation (\u00b0)",
    },
    "mode": {
        "type": "string",
        "enum": ["linear", "stepped"],
        "default": "linear",
        "label": "Curve mode",
    },
    "api_key": {
        "type": "string",
        "subtype": "password",  # renders as a masked input
        "label": "API key",
    },
}
```

Values arrive in `config` as strings (from JSON storage) even for `"type": "number"` fields. Always coerce explicitly:

```python
threshold = float(config.get("threshold") or 20)
```

---

## Type coercion

The executor does **not** coerce values before passing them to `evaluate()`. Inputs may be `None`, `bool`, `int`, `float`, or `str`. Copy these helpers into your plugin:

```python
def _to_num(v, default=0.0):
    if v is None: return default
    if isinstance(v, bool): return 1.0 if v else 0.0
    try: return float(v)
    except (TypeError, ValueError): return default

def _to_bool(v):
    if v is None: return False
    if isinstance(v, str):
        return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)
```

---

## Working example: Shadow Control

Calculates a blind position from sun elevation and indoor temperature, with an override input.

```python
# plugins/shadow_control.py
from __future__ import annotations
from typing import Any
from obs.logic.plugin_api import LogicNodePlugin, NodeTypeDef, NodeTypePort, register_node_type


@register_node_type
class ShadowControl(LogicNodePlugin):
    type_name = "shadow_control"

    @classmethod
    def node_type_def(cls) -> NodeTypeDef:
        return NodeTypeDef(
            type="shadow_control",
            label="Beschattungssteuerung",
            category="integration",
            description="Berechnet die Jalousieposition aus Sonnenh\u00f6he und Innentemperatur.",
            inputs=[
                NodeTypePort(id="sun_elevation", label="Sonnenh\u00f6he (\u00b0)"),
                NodeTypePort(id="indoor_temp",   label="Innentemperatur"),
                NodeTypePort(id="override",      label="Override aktiv"),
                NodeTypePort(id="override_pos",  label="Override Position"),
            ],
            outputs=[
                NodeTypePort(id="position", label="Position (0\u2013100)"),
                NodeTypePort(id="active",   label="Automatik aktiv"),
            ],
            config_schema={
                "threshold_elevation": {
                    "type": "number", "default": 20, "min": 0, "max": 90,
                    "label": "Mindest-Sonnenh\u00f6he (\u00b0)",
                },
                "temp_threshold": {
                    "type": "number", "default": 22, "min": 10, "max": 40,
                    "label": "Aktivierung ab Innentemperatur (\u00b0C)",
                },
            },
            color="#d97706",
        )

    @classmethod
    def evaluate(
        cls,
        node_id: str,
        inputs: dict[str, Any],
        config: dict[str, Any],
        state: dict[str, Any],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if _to_bool(inputs.get("override")):
            pos = _to_num(inputs.get("override_pos"), default=0.0)
            return {"position": pos, "active": False}, state

        elevation = _to_num(inputs.get("sun_elevation"))
        indoor    = _to_num(inputs.get("indoor_temp"), default=999.0)
        threshold = float(config.get("threshold_elevation") or 20)
        temp_th   = float(config.get("temp_threshold") or 22)

        if elevation < threshold or indoor < temp_th:
            return {"position": 0.0, "active": False}, state

        position = min(100.0, (elevation - threshold) * 2)
        return {"position": round(position, 1), "active": True}, state


def _to_num(v: Any, default: float = 0.0) -> float:
    if v is None: return default
    if isinstance(v, bool): return 1.0 if v else 0.0
    try: return float(v)
    except (TypeError, ValueError): return default

def _to_bool(v: Any) -> bool:
    if v is None: return False
    if isinstance(v, str): return v.strip().lower() not in ("0", "false", "no", "off", "")
    return bool(v)
```

---

## Multiple plugins in one file

A single file can register any number of node types:

```python
# plugins/my_blocks.py
@register_node_type
class BlockA(LogicNodePlugin):
    type_name = "block_a"
    ...

@register_node_type
class BlockB(LogicNodePlugin):
    type_name = "block_b"
    ...
```

---

## Distributing as a pip package

Recommended layout:

```
obs-plugin-shadow-control/
├── pyproject.toml
└── shadow_control/
    ├── __init__.py
    └── plugin.py
```

`pyproject.toml`:

```toml
[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[project]
name = "obs-plugin-shadow-control"
version = "1.0.0"
dependencies = []

[project.entry-points."obs.logic_blocks"]
shadow_control = "shadow_control.plugin"
```

Install into the OBS Python environment:

```bash
# LXC / bare-metal
source /opt/obs/venv/bin/activate
pip install obs-plugin-shadow-control
systemctl restart obs

# Docker (rebuild image or exec into container)
docker exec obs pip install obs-plugin-shadow-control
docker compose restart obs
```

No `plugins_dir` configuration is needed when using entry points.

---

## Using persistent state

The `state` dict is persisted to the database after each graph run and restored when the logic engine starts. Use it for anything that needs to survive a restart:

```python
@classmethod
def evaluate(cls, node_id, inputs, config, state):
    total = state.get("total", 0.0)
    total += _to_num(inputs.get("value"))
    state["total"] = total
    return {"total": total}, state
```

Keep the state dict **JSON-serialisable** — use only `str`, `int`, `float`, `bool`, `list`, and `dict` as values. Datetime objects, class instances, or numpy arrays will cause a serialisation error at runtime.

---

## Debugging

**Verify the plugin loaded** — look for log lines at startup:

```
INFO  obs.logic.plugin_loader: Plugin loaded (file): shadow_control.py — types: ['shadow_control']
INFO  obs.logic.plugin_loader: Plugin loaded (entry point): shadow_control = shadow_control.plugin
```

If the block is missing from the palette, look for errors in the startup log:

```
ERROR obs.logic.plugin_loader: Plugin failed to load: shadow_control.py
Traceback (most recent call last): ...
```

**Errors during execution** are logged at WARNING level:

```
WARNING obs.logic.executor: Node abc123 (shadow_control) error: ...
```

The node returns `{}` on error, so downstream nodes receive `None` on all its outputs.

**Debug mode** in the GUI shows the live output values of your block after each graph run — no extra instrumentation needed.

---

## Known limitations

- **Hot-reload is `plugins/` directory only.** Pip-installed entry-point plugins require a full OBS restart after installation or upgrade (`systemctl restart obs`).
- **Synchronous only.** Do not perform blocking I/O inside `evaluate()`. For HTTP-based integrations, chain an `api_client` block in the graph and wire its response into your plugin block.
- **No custom GUI components.** The config panel is rendered generically from `config_schema`. Custom Vue components are not supported.
- **Minimal type validation.** OBS does not validate `inputs` or `config` values before calling `evaluate()`. Your code must handle `None` and unexpected types gracefully.
