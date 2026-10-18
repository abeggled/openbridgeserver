---
title: "Blocks: Timer"
---

# Blocks: Timer

Time-triggered blocks, delays, counters, and sequences.

## Trigger {#logic-block-timer-cron}

Fires automatically on a cron schedule (minute hour day month weekday). Configuration offers
three interlinked levels:

- **Preset schedules** — common patterns like "Every 5 minutes", "Daily at 07:00", or "Weekdays
  (Mon–Fri) at 06:00" via dropdown.
- **Customize schedule** — a visual editor with one input field per cron field (minute, hour, day,
  month, weekday; `0`=Sunday). Supports `*` (every), `*/5` (every 5), ranges (`1-5`), and lists
  (`1,3`).
- **Expression** — the raw cron expression, directly editable, with a link to crontab.guru for
  looking up more complex patterns.

All three levels stay in sync — a change at any level updates the others.

The schedule is evaluated in the configured application timezone (**Settings → General**), not
UTC — a "Daily at 07:00" trigger fires at 7am local time and follows daylight-saving transitions.

## Date/Time {#logic-block-datetime}

Outputs the current date and time in the configured application timezone (**Date**, **Time**,
**Custom** outputs). The custom format uses the same formatting tokens as **Settings → General**
(`d`/`dd`, `EE`/`EEE`/`EEEE`, `M`/`MM`/`MMM`/`MMMM`, `yy`/`yyyy`, `H`/`HH`, `m`/`mm`, `s`/`ss`).

## Delay {#logic-block-timer-delay}

Delays a trigger signal by a configured number of seconds before it appears at the output. When
the delay has elapsed the sheet runs again and the output triggers the downstream blocks once —
like the **Trigger** block at its scheduled time.

The output is a pulse: it sends only in that run and nothing in any other run of the sheet — not
even through downstream logic such as **NOT**. A connected Write Object therefore writes only when
the delay fires.

- **Retrigger:** A new trigger while a delay is pending starts the delay over. The output therefore
  triggers once, one delay after the last trigger.
- **Triggering:** The block starts on a rising edge at its input, on every single pulse (for example
  from a **Change Filter** or the **Trigger** block) and on every true telegram a directly connected
  **Read Object** receives again — even when the input does not fall back to false in between. With
  a condition in between (such as a comparison), only its rising edge counts.
- **Slow blocks:** The delay counts from the run that saw the trigger. A slow block elsewhere on the
  sheet (e.g. **API Client**) does not lengthen it.
- **Save and restart:** A pending delay is kept. It still fires at the planned time after the sheet
  is saved or the server restarts; if that time has already passed, at once.
- **Input level:** The block remembers whether its input was last true — also across a save and a
  restart. An input that stays true is therefore no new rising edge. When a save changes what feeds
  the input (a connection, a block before it or that block's settings), the input is evaluated
  again: if it is false now, its next rise counts; if it is still true, the block does not fire
  again — changing the threshold of a comparison that is still met does not restart the delay. If a
  newly connected input has no value yet, the block starts as if new. Renaming a block changes
  nothing. After a restart, the block likewise checks whether its input has become false meanwhile.
- **Cancellation:** Disabling or deleting the sheet discards a pending delay. If the input is still
  active after the sheet is enabled again, the delay starts over on the sheet's next run.
- **Feedback:** The output may lead back to the block's own input — also through other blocks —
  without a **Memory** in between. That gives, for example, a signal that repeats every N seconds.
- **Chains:** When the delay has elapsed a new logic cascade begins, as with the **Trigger** block.
  A loop through data points — also back into the same sheet, such as a blinker or a periodic
  signal — therefore keeps running. With a delay under one second the run belongs to the cascade of
  the value that triggered it instead, and such a loop is stopped after at most ten steps like any
  other.

## Pulse {#logic-block-timer-pulse}

Outputs a pulse on a trigger signal, held for a configured duration (seconds): the output becomes
true at once and false again when the duration has elapsed. The sheet runs again on both changes so
the downstream blocks receive the new state. Connect the output directly to the **Value** of a
Write Object without a trigger of its own: the data point then follows the pulse. With **Output only on
change** it writes only on a change, not on every run of the sheet. A Write Object
that fires through its own trigger in the same run that starts the pulse still writes the state
from before the pulse.

- **Retrigger:** A new trigger during the pulse extends it — the output stays true until the
  duration has elapsed from the last trigger (retriggerable, e.g. for staircase lighting).
- **Slow blocks:** The duration counts from the run that writes the start of the pulse. If a slow
  block in that run (e.g. **API Client**) holds up its writes, the data point still stays true for
  the full duration. The start itself is written together with that run's other writes, so only
  after the slow block.
- **Chains:** The end of the pulse begins a new logic cascade, like an elapsed **Delay** (not with a
  duration under one second).
- **Triggering:** as for **Delay** — a rising edge, a single pulse or a repeated true telegram at the
  input. A motion detector that sends "1" cyclically while someone is present therefore keeps the
  pulse on.
- **Input level:** as for **Delay** — an input that stays true does not extend the pulse after a
  save or a restart.
- **Save and restart:** A running pulse is kept in the block's state. It still ends at the planned
  time after the sheet is saved or the server restarts; if that time has already passed, at once.
- **Disable:** Nothing runs in a disabled sheet. If the pulse has run out meanwhile, the output
  falls to false when the sheet is enabled again.
- **Delete:** Deleting the sheet or the Pulse block ends the pulse too; a data point it wrote keeps
  its last value.

## Operating Hours {#logic-block-operating-hours}

Counts operating hours while the **Active** input is true. The **Reset** input resets the counter
to zero. "Restore state after restart" determines whether the counter value survives a server
restart.

## Sequence {#logic-block-value-sequence}

Writes a series of values with configurable pauses in between — e.g. for blink or process control
sequences. Each **step** defines a target object (empty = a pure pause, no write), the value to
write, and the wait time (ms) until the next step; steps can be reordered, duplicated, and removed
via arrow buttons — "Blink preset" sets up a ready-made on/off sequence.

- **Run mode** — Once, a fixed number of repetitions, or as long as the **Condition** input is
  true.
- **On new trigger** — what happens if triggered again while a sequence is already running:
  Ignore, Restart (from the beginning), or Queue (append after the current one finishes).
- **Cancel when condition becomes false** — only for "as long as condition is true": cancels a
  running sequence immediately once the condition is no longer met.

## Sensor Watchdog {#logic-block-timer-sensor-watchdog}

Monitors up to 10 inputs for missing new telegrams. Each input has its own **Timeout** (seconds),
**Fault Value**, optional display name, and optional **Repeat** (seconds). While an input keeps
receiving telegrams regularly, its value is passed through unchanged to the corresponding output;
once no new telegram has arrived for longer than the configured timeout, that output switches to
the Fault Value instead — until a telegram arrives again.

**Important when wiring this up:** both **Value** and **Changed** from the upstream Read Object
block must be connected for each input. Only a genuine new telegram (Changed = true) resets the
timeout — a value re-sent unchanged still counts as a sign of life (important for e.g. contact
sensors whose value can stay the same for a long time while still actively sending). A mere
re-evaluation of the graph (triggered by an unrelated event, or by this block's own periodic
scheduler) does **not** count as a sign of life. If only **Value** is connected, that input reports
one fault after the first timeout and never recovers afterwards — this is not a bug, it is the
intended fail-stale behavior for a missing Changed connection.

The block runs its own internal periodic scheduler and detects an elapsed timeout even when
nothing else happens anywhere else in the graph — unlike a hand-built replacement out of
Delay/Pulse blocks, which only react to a new trigger signal and cannot "wake themselves up".

The moment an input newly transitions into the fault state, **Fault Text** outputs a message like
"No data from &lt;Name&gt;" and **Fault Trigger** fires a pulse — e.g. to drive a notification. With
**Repeat** left at 0 (default) this happens only once, on the initial fault; with a value greater
than 0, the trigger additionally fires again at that interval for as long as the input keeps being
stale — e.g. check every 10s, but only re-notify hourly. When an input recovers (a new telegram
arrives), no further trigger fires, and the repeat interval starts over the next time it goes
stale.
