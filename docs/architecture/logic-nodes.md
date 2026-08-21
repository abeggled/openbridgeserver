# Logic node architecture

How the built-in Logic function blocks are structured, which dependencies are allowed between
them, and how to add a new block. Applies to everything below `obs/logic/`.

## Why

Every built-in function block used to be declared in one central list in `obs/logic/node_types.py`.
Unrelated blocks — boolean operators, timers, notifications, HTTP integration — shared one file, so
a one-line change to a single block produced a diff (and a review) in the context of the complete
catalogue, and every parallel branch touching any block conflicted with every other one.

The catalogue is now assembled from one module per function block. A change to a block touches that
module and its tests; shared infrastructure is only touched when the shared contract itself changes.

## Layout

```text
obs/logic/
├── capabilities.py          # authorization capability identifiers per node type
├── executor.py              # shared execution dispatcher (see "Execution")
├── graph_analysis.py        # shared graph/topology helpers
├── manager.py               # scheduling, persistence, runtime
├── models.py                # NodeTypeDef, NodeTypePort, FlowData, …
├── registry.py              # assembles the catalogue, lookup helpers
├── validation.py            # persistence-time validation shared by API and manager
├── node_types.py            # deprecated compatibility facade → registry.py
└── nodes/
    ├── __init__.py          # docstring only — no imports, no side effects
    ├── base.py              # helpers shared by node modules (port())
    ├── logic/               # one package per node category …
    │   ├── __init__.py      # category registry: NODE_TYPES
    │   ├── and_node.py      # one module per function block: NODE_TYPE
    │   ├── …
    ├── datapoint/
    ├── math/
    ├── string/
    ├── timer/
    ├── astro/
    ├── notification/
    ├── integration/
    ├── script/
    └── ai/
```

All of these rules are enforced by the guardrail tests in
`tests/unit/logic/test_node_architecture.py`; uniqueness, the category match and the classification
rule additionally raise from `obs/logic/registry.py` at import time:

- **The package name is the category.** A module in `obs/logic/nodes/timer/` must declare
  `category="timer"`. The set of packages equals the set of keys in `BUILTIN_NODE_CATEGORIES`.
- **One function block per module**, exported as exactly one module-level `NODE_TYPE`.
- **Every node module is registered** in the `NODE_TYPES` tuple of its category package.
- **Category `__init__.py` files are registration only** — imports, the `NODE_TYPES` tuple and
  `__all__`. No functions, no classes, no business logic.
- **Dependency direction.** Node modules, category packages and `nodes/base.py` may import
  `obs.logic.models`, `obs.logic.nodes.base` and unrelated shared helpers, but never
  `obs.api.*`, `obs.logic.manager`, `obs.logic.executor` or `obs.logic.registry`. The dependency
  always points *towards* the node, never out of it.
- **`nodes/base.py` must not import a concrete node module** — it is the shared bottom layer. It is
  also the only place for helpers shared between blocks: every other module inside a category
  package must be a function block, and categories stay flat (no nested sub-packages).
- **`registry.py` only combines category registries.** It imports category packages, never single
  node modules, and defines nothing beyond the catalogue assembly and the lookup helpers.
- **Node type identifiers are unique** and every registered type is either implemented in the
  dispatcher or listed as intentionally non-executing.
- **No node classifies itself.** `has_external_side_effect` / `required_capability` come from
  `obs/logic/capabilities.py` via the registry; a node module setting them fails on import.
- **Every registered category is rendered by the palette** — the category ids in
  `gui/src/components/logic/NodePalette.vue` and the registered categories must match, otherwise a
  block would be registered but invisible in the editor.

`obs/logic/nodes/__init__.py` deliberately has no import side effects, so a single node module can
be imported — and reviewed — without pulling in the rest of the catalogue.

## Contract of a function block

A node module declares the complete node-specific contract: metadata, ports, configuration schema
and defaults.

```python
"""Node definition for the ``clamp`` function block (Begrenzer)."""

from __future__ import annotations

from obs.logic.models import NodeTypeDef
from obs.logic.nodes.base import port

NODE_TYPE = NodeTypeDef(
    type="clamp",  # stable public identifier — persisted in graphs
    label="Begrenzer",
    category="math",  # must equal the package name
    description="Begrenzt den Eingangswert auf [Min, Max].",
    inputs=[port("value", "Wert")],
    outputs=[port("result", "Ergebnis")],
    config_schema={
        "min": {"type": "number", "default": 0, "label": "Minimum"},
        "max": {"type": "number", "default": 100, "label": "Maximum"},
    },
    color="#7c3aed",
)
```

`type` is a **persisted public identifier**: saved graphs, exports and the logic editor address
blocks by it. Renaming or removing one is a breaking change; unknown types are replaced by a
`missing_node` placeholder on import.

The module file name drops the redundant category prefix (`math_map` → `math/scale.py`,
`timer_delay` → `timer/delay.py`, `string_concat` → `string/concat.py`); blocks whose identifier
carries no prefix keep it (`clamp`, `random_value`, `wake_on_lan`). Python keywords need a suffix:
`and` → `logic/and_node.py`.

`has_external_side_effect` and `required_capability` must **not** be set in the node module — the
registry rejects a self-classified node on import. Classification is derived centrally from
`obs/logic/capabilities.py` when the catalogue is assembled, so a node that is listed neither in
`LOGIC_NODE_CAPABILITIES` nor in `PURE_LOGIC_NODE_TYPES` stays unclassified and is denied by the
Logic run preflight until it has been reviewed. Without that rule a new node module could declare
`has_external_side_effect=False` and be executed as a pure block without ever passing that review.

## Registry

`obs/logic/registry.py` combines the per-category tuples into the public catalogue:

```python
BUILTIN_NODE_CATEGORIES  # {category: (NodeTypeDef, …)} — catalogue order
BUILTIN_NODE_TYPES  # flat, classified catalogue (order is API-visible)
NODE_TYPE_REGISTRY  # {type: NodeTypeDef}
get_node_type(type_)  # single lookup, None when unknown
list_node_types()  # complete catalogue, served by GET /api/v1/logic/node-types
```

Registration is **explicit** — imports, no runtime package scanning: deterministic, checkable by a
type checker, and a duplicate or misplaced registration raises on import instead of silently
changing the catalogue.

The catalogue order is grouped by category and follows the palette order in
`gui/src/components/logic/NodePalette.vue`. The palette regroups by category itself, so this order
only decides the sequence inside a category.

`obs/logic/node_types.py` remains as a **deprecated compatibility facade** that re-exports
`BUILTIN_NODE_TYPES`, `NODE_TYPE_REGISTRY`, `get_node_type` and `list_node_types`, so branches
developed in parallel keep working. New code imports from `obs.logic.registry`.

`get_node_type()` and `list_node_types()` are also the intended merge point for dynamically
discovered (non-built-in) node types: such a lookup extends these two functions in `registry.py`
and nothing else.

## Execution

Node *behaviour* lives in exactly two **explicitly documented shared handlers**:

| Handler | Responsibility |
|---|---|
| `GraphExecutor._eval_node` (`obs/logic/executor.py`) | per-tick evaluation: `case "<type>":` branch per block |
| `LogicManager` (`obs/logic/manager.py`) | orchestration around a tick: scheduling, DataPoint wiring, async side effects, persisted block state |

Node-specific *execution* branches anywhere else are not allowed — they belong either in the node's
own module or in one of these two handlers. A few shared modules legitimately key on node types for
non-execution concerns; those are listed below and are the complete set.

Extracting per-node executors out of the dispatcher is a separate, behaviour-preserving step and
intentionally not part of the structural split (see issue
[#1109](https://github.com/abeggled/openbridgeserver/issues/1109), migration step 6). When it
happens, a node module gains its own `execute()` and the registry maps `type → executor`; the node
contract above is designed to absorb that without moving files again.

Dispatcher coverage is enforced: every registered node type must have a `case` branch, and a branch
for an unregistered type fails the guardrail test. The `LogicManager` branches are documented, not
test-enforced.

Two registered types are intentionally never executed and fall through the dispatcher's `case _`
no-op branch: `comment` (purely visual annotation) and `ai_logic` (placeholder). They are listed as
`NON_EXECUTING_NODE_TYPES` in the guardrail test; every other registered type must have a branch.

### Node-specific knowledge outside node modules

Besides the two handlers above, these shared modules still key on individual node types. This is the
complete set — when a new block needs handling in one of them, extend the existing structure there
and update this table, rather than opening a node-specific branch somewhere new:

| Location | Node-specific knowledge | Why it is not (yet) in the node module |
|---|---|---|
| `obs/logic/validation.py` — `_DURATION_FIELDS` | duration bounds for `timer_delay`, `timer_pulse`, `api_client` | persistence-time validation runs before execution; a per-node validation hook on the node contract is the intended replacement |
| `obs/logic/graph_analysis.py` — `TICK_BOUNDARY_NODE_TYPES` | `memory` is the explicit tick boundary | a graph-topology property, evaluated without instantiating nodes |
| `obs/api/v1/logic.py` | `comment` excluded from layout-only diffs (as is the cosmetic `data.label` block name of every node); `datapoint_read`/`datapoint_write` for usage reporting; `missing_node` placeholder on import, and its shape canonicalized on read (pre-#1157 generated label marker dropped, a missing type carried in `label` promoted to `original_type`) | API-level concerns (change detection, usage lists, import fallback, read-time migrations) |

Note that `_DURATION_FIELDS` duplicates bounds the node definitions already declare in their
`config_schema` (`min`). Deriving them generically would silently widen validation to every
config field carrying a `min` (for example `input_count`) and reject graphs that are accepted
today, so it is deliberately left as an explicit table.

## Adding a new function block

1. **Create the node module** `obs/logic/nodes/<category>/<block>.py` with a single `NODE_TYPE`.
   Use an existing category package; only create a new one when the block genuinely belongs to a
   new category (then also add it to `CATEGORY_IDS` in `NodePalette.vue` and to
   `BUILTIN_NODE_CATEGORIES`).
2. **Register it** in the `NODE_TYPES` tuple of the category's `__init__.py` — the only catalogue
   file a new block touches. `registry.py` stays untouched.
3. **Classify it** in `obs/logic/capabilities.py`: either add it to `LOGIC_NODE_CAPABILITIES` with
   its capability (for blocks with external side effects) or to `PURE_LOGIC_NODE_TYPES`. Without
   this the block cannot be executed.
4. **Implement it** as a `case "<type>":` branch in `GraphExecutor._eval_node`, unless the block is
   purely visual.
5. **Add focused tests** in `tests/unit/logic/nodes/<category>/test_<block>.py`, mirroring the
   source layout, plus execution tests for the dispatcher branch.
6. Frontend, translations and the block table in `README.md` / `README.de.md` follow the normal GUI
   and i18n rules.

Beyond the capability classification (step 3) and the dispatcher branch (step 4) — both of which are
part of implementing the block, not of defining the catalogue — no shared file is involved: the
catalogue contract tests pick up a new block automatically. `PUBLISHED_NODE_TYPES` in
`tests/unit/logic/test_node_registry.py` only needs an edit when an identifier is deliberately
**removed** — that is the breaking direction.

## Tests

| Scope | Location |
|---|---|
| One function block (metadata, defaults, ports) | `tests/unit/logic/nodes/<category>/test_<block>.py` |
| Catalogue contract (uniqueness, classification, API, compatibility) | `tests/unit/logic/test_node_registry.py` |
| Architectural guardrails (layout, dependency direction, dispatcher coverage) | `tests/unit/logic/test_node_architecture.py` |
| Cross-cutting validation on the API/persistence boundary | `tests/unit/test_logic_node_type_defaults.py` |
| Execution behaviour | `tests/unit/test_logic_*.py`, `tests/integration/test_logic_*.py` |
