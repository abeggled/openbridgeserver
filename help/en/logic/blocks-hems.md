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

::: tip Own logic sheet
Like schedules and the Sensor Watchdog, the scheduler runs the whole logic sheet. Put the block on a
sheet of its own so other Write Objects are not written again every control interval (or enable
*Only on change* on them).
:::

### Measurement modes at the grid connection point

| Mode | Inputs | Meaning |
|---|---|---|
| Bidirectional via one input | Grid power | positive = grid import, negative = export |
| Separate inputs | Grid import, Grid export | both positive (0 to x W); internally `grid power = import − export`. Both values must be valid. |
| Feed-in power only | Feed-in power | `0 W` = no known feed-in, positive = current feed-in |

**Limitation of "Feed-in power only":** at `0 W` the block cannot tell an exact balance from grid
import. It therefore reacts conservatively: while no feed-in is reported the current power is held
(never increased). If feed-in stays absent for longer than the **switch-off delay**, one step of the
lowest-priority consumer is removed per control cycle from then on (at least 10 % for percentage
consumers) until feed-in is reported again. The switch-off delay that counts is the one of the
consumer concerned (its own or the global one); after it the consumer gives the step up at once,
only a minimum run time is still awaited. Power freed in the process goes to no other
consumer, and a positive feed-in reserve is deducted once, straight away on reaching 0 W, since 0 W feed-in certainly falls
short of it.

Optional global inputs: **Enable** (false = everything safely off), **Feed-in reserve (dyn.)**
(overrides the configured value) and **Plant OK** (false = everything safely off). Unconnected
inputs count as "clear"; a connected input without a known value (e.g. a Read Object not yet seeded
after a restart) counts as false. Text values are read as in every logic block: only `0`, `false`, `no`,
`off` and empty are false.

### Power budget

```text
budget = power currently used by the controlled consumers − grid power − feed-in reserve
```

This keeps power that was already allocated, even though it has reduced the previously measured
export. The "used power" is the consumer's measured power (input *Measured power*), otherwise the
value estimated from setpoint and maximum power. Only consumers the block itself switched on count:
an inactive consumer, or one the block switched off, that still draws power (manual operation, own
thermostat) is ordinary house load and is not handed to others. If a switched-on consumer draws less
than its share according to *Measured power* (thermostat off, car full), the rest goes to lower
priorities.

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
undershot in between (default), or via the **Reset** input. An optional **reserved power** is
deducted from the budget of lower priorities in the firing cycle and the next one, so the starting
load's power is not handed out already. The **pulse duration** ranges from 0.1 s to 1 h. A trigger
never fires again merely because the surplus persists, only once it has been re-armed; with "after
the lockout" and a lockout of 0 that is every control cycle while the surplus suffices.

### Outputs

Per consumer: one output (percentage / on-off / trigger pulse) and a status (`inactive`, `off`,
`on_delay`, `active`, `reduced`, `min_runtime`, `off_delay`, `locked`, `fired`, `safe`,
`safe_hold`, `invalid_input`). Diagnostics: grid power, surplus, power budget, allocated power, remaining budget,
block status (`ok`, `disabled`, `plant_not_ok`, `invalid_input`, `stale_input`) and warning. With
**Output only on change** (default) unchanged outputs are not sent again to directly wired Write
Objects: such a Write Object writes only when the value differs from the one it last actually wrote
— so a value its own throttle dropped is not lost either (a Write Object with its own trigger writes
on every firing instead). This holds for states; do not throttle a trigger output — a dropped pulse
cannot be made up for once it has ended. Logic blocks behind an
output (e.g. NOT, AND) always compute with the current value of an on/off, percentage, status or
diagnostic output — it is a state. A trigger output, by contrast, is an event: it exists only on
the run it was sent, so e.g. an API call on it is made exactly once per firing. Removing a consumer from the list sets its output to 0 % or `false` once, so an actuator
that is still wired does not stay on. The debug tab lists every output with its last emitted value; in the safe state the diagnostics stay visible (grid power when known; surplus, budget, allocated and remaining = 0).

### Error and restart behaviour

- If a value does not arrive (e.g. a meter read through an API Client on a run that did not trigger
  the API Client), the last value received still counts for the **maximum age of meter values**, or
  for two control intervals without one; after that it is missing.
- Missing, non-numeric, `NaN` or stale meter values, as well as *Enable* = false or *Plant OK* =
  false, never activate anything. By default all percentages go to 0 %, all switching outputs to
  `false` and no trigger fires (alternative: hold the last outputs — only an output that was already
  sent is held; a consumer without one, e.g. right after a restart, goes to the safe state). The
  event is written to the log.
- **Stale values** are only checked when **Maximum age of meter values** is greater than 0 *and* the
  Read Object's *Changed* output is wired to the block (see Sensor Watchdog). With separate inputs the
  meter counts as stale only when neither side has reported in time — a send-on-change export side
  that stays at 0 W is not enough. Likewise, with "Feed-in power only" a silent meter at 0 W does not
  count as stale (0 W is handled conservatively anyway). The block reacts as soon
  as the age is exceeded, not only at the next control cycle. A value restored after a restart counts
  as stale until the meter reports a new telegram.
- After a restart the block always begins in the safe state; its state is not persisted, and a
  trigger never fires because of a restored old state.
- Disabling the logic sheet or deleting the block ends the control, but — as with every logic
  block — its outputs are not reset. To shut down in a controlled way, first set *Enable* to false
  (safe state), then disable.
