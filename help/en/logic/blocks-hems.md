---
title: "Blocks: Energy (HEMS)"
---

# Blocks: Energy (HEMS)

Blocks for energy management (home energy management system, HEMS). **HEMS Lite** deliberately stays vendor-independent and simple:
no forecasts, tariffs, charging schedules or vehicle management — only input and output values of
the Logic module.

## Surplus Control {#logic-block-hems-surplus}

Distributes the energy surplus measured at the grid connection point across several consumers by
priority, e.g. wallbox, water heater, heating element, heat pump or pool pump. The block runs its
own internal scheduler (**control interval**, default 30 s) and also reacts to new meter values, but
a control cycle runs at most once per interval.

### Measurement modes at the grid connection point

| Mode | Inputs | Meaning |
|---|---|---|
| Bidirectional via one input | Grid power | positive = grid import, negative = export |
| Separate inputs | Grid import, Grid export | both positive (0 to x W); internally `grid power = import − export`. Both values must be valid. |
| Feed-in power only | Feed-in power | `0 W` = no known feed-in, positive = current feed-in |

**Limitation of "Feed-in power only":** at `0 W` the block cannot tell an exact balance from grid
import. It therefore reacts conservatively: while no feed-in is reported the current power is held
(never increased). If feed-in stays absent for longer than the **switch-off delay**, one step of
the lowest-priority consumer is removed at a time.

Optional global inputs: **Enable** (false = everything safely off), **Feed-in reserve (dyn.)**
(overrides the configured value) and **Plant OK** (false = everything safely off). Unconnected
inputs count as "clear".

### Power budget

```text
budget = power currently used by the controlled consumers − grid power − feed-in reserve
```

This keeps power that was already allocated, even though it has reduced the previously measured
export. The "used power" is the consumer's measured power (input *Measured power*), otherwise the
value estimated from setpoint and maximum power.

Example: 2 000 W controlled load, meter −1 000 W, reserve 200 W → budget 2 800 W.

### Consumers and priority

The order of the list **is** the priority: top = highest. Sorting is by drag and drop only; the
position number is display-only and duplicate priorities are impossible. New consumers are
appended at the bottom. The budget is handed out top to bottom; when it shrinks, consumers are
reduced or switched off from the bottom up. Every consumer has a fixed internal ID, so reordering
never changes existing connections.

Common settings: name, *Active*, control type, rated/maximum power (fixed value or input), minimum
run time, minimum off time, and switch-on/off delay (empty = global value). A **minimum run time**
prevents an immediate load shed: the consumer keeps running until it expires, the priority order
stays intact.

**Percent** — setpoint 0–100 %. Settings: maximum power, minimum power, minimum/maximum setpoint,
step size and the behaviour below the minimum power (set to 0 % or hold at the minimum while the
switch-off delay runs). Example: 4 kW of 10 kW → 40 %.

**On/Off** — boolean output. The consumer switches on once the budget available to it reaches the
**switch-on threshold** (default: rated power) continuously for the switch-on delay, and off when it
stays below the **switch-off threshold** for the switch-off delay. Separate thresholds form a
hysteresis; a switch-off threshold below the rated power tolerates some grid import.

**Trigger** — a one-shot pulse (e.g. start a washing machine through an API). It fires when the
**minimum surplus** has been present continuously for the configured time and is then blocked for
the **lockout time**. **Re-arm**: after the lockout has expired, only after the threshold was
undershot in between (default), or via the **Reset** input. An optional **reserved power** holds
budget for a short while for the firing. A trigger never fires again on every control cycle.

### Outputs

Per consumer: one output (percentage / on-off / trigger pulse) and a status (`inactive`, `off`,
`on_delay`, `active`, `reduced`, `min_runtime`, `off_delay`, `locked`, `fired`, `safe`,
`safe_hold`, `invalid_input`). Diagnostics: grid power, surplus, power budget, allocated power, remaining budget,
block status (`ok`, `disabled`, `plant_not_ok`, `invalid_input`, `stale_input`) and warning. With
**Output only on change** (default) unchanged outputs are not sent again. The debug tab lists every output with its last emitted value; in the safe state the diagnostics stay visible (grid power when known; surplus, budget, allocated and remaining = 0).

### Error and restart behaviour

- Missing, non-numeric, `NaN` or stale meter values, as well as *Enable* = false or *Plant OK* =
  false, never activate anything. By default all percentages go to 0 %, all switching outputs to
  `false` and no trigger fires (alternative: hold the last outputs). The event is written to the log.
- **Stale values** are only checked when **Maximum age of meter values** is greater than 0 *and* the
  Read Object's *Changed* output is wired to the block (see Sensor Watchdog).
- After a restart the block always begins in the safe state; its state is not persisted, and a
  trigger never fires because of a restored old state.
