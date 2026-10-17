---
title: "Blocks: Data Point Access"
---

# Blocks: Data Point Access

The two blocks that connect the logic graph to OBS objects (data points) — directly via
EventBus/MQTT, independent of adapter bindings.

## Read Object {#logic-block-datapoint-read}

Outputs a data point's current value and fires the **Changed** trigger output on every value
change. Configuration is split into three tabs:

- **Connection** — select the object via search.
- **Transformation** — converts the read value before it's output:
  - **Formula** (variable `x`) — e.g. `x * 100`; presets for multiplying/dividing, or a custom
    formula. Available functions: `abs`, `round`, `min`, `max`, `sqrt`, `floor`, `ceil`, and every
    `math.*` function. Empty = no transformation.
  - **Value mapping** — maps individual values to others (e.g. `0/1` ↔ `off/on`), as a JSON object
    with any number of entries; applied **after** the formula.
- **Filter** — determines when it actually triggers:
  - **Time filter** — minimum time between two triggers; triggers within the interval are
    discarded.
  - **Value filter** — "Only trigger when the value has changed" (suppresses duplicates), plus a
    **minimum delta** absolute and/or relative (%) versus the last value (numeric values only; if
    both are active, both conditions must be met; empty = inactive).

## Write Object {#logic-block-datapoint-write}

Writes the value on the **Value** input to an object, triggered via the separate **Trigger**
input. Same three configuration tabs as "Read Object":

- **Connection** — select the target object.
- **Transformation** — formula and value mapping, applied to the value here **before** it's
  written.
- **Filter** — minimum time between two **writes**; "Only write when the value has changed", plus
  a minimum delta (absolute only — there's no relative variant for writing).
