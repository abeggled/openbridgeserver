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

Delays a trigger signal by a configured number of seconds before it appears at the output.

## Pulse {#logic-block-timer-pulse}

Outputs a pulse on a trigger signal, held for a configured duration (seconds).

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

Monitors up to 10 inputs for missing new values. Each input has its own **Timeout** (seconds),
**Fault Value**, and optional display name. While an input keeps receiving values regularly, it is
passed through unchanged to its corresponding output; once no new value has arrived for longer
than the configured timeout, that output switches to the Fault Value instead — until a value
arrives again.

The block runs its own internal periodic scheduler and detects an elapsed timeout even when
nothing else happens anywhere else in the graph — unlike a hand-built replacement out of
Delay/Pulse blocks, which only react to a new trigger signal and cannot "wake themselves up".

The moment an input newly transitions into the fault state, **Fault Text** outputs a message like
"No data from &lt;Name&gt;" and **Fault Trigger** fires a one-shot pulse — e.g. to drive a
notification. When an input recovers (a new value arrives), no new trigger fires.
