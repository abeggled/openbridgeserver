---
title: "Blocks: Math"
---

# Blocks: Math

Calculation, statistics, and counter blocks.

## Formula {#logic-block-math-formula}

Evaluates an expression using the variables `a` (= IN 1) and `b` (= IN 2), e.g. `a + b` or
`(a - b) * 2`. An additional **output transformation** can be defined — a second formula using the
variable `x` (= the main formula's result), e.g. for a unit conversion after the actual
calculation. Both formulas offer presets for multiplying/dividing plus `abs`, `round`, `min`,
`max`, `sqrt`, `floor`, `ceil`, and every `math.*` function; empty = no transformation.

## Scale {#logic-block-math-map}

Linearly scales a value from an input range (**min/max**) to an output range (**min/max**) — e.g.
converting a raw sensor value of 0–1023 to 0–100%.

## Clamp {#logic-block-clamp}

Limits the input value to a range [**min**, **max**]. Values outside are set to the respective
limit (not cut off or discarded).

## Random Value {#logic-block-random-value}

Outputs a random value between **minimum** and **maximum** on every **trigger** signal. **Data
type** "int" produces a whole number, "float" a decimal number with configurable **decimal
places**.

## Statistics {#logic-block-statistics}

Continuously computes minimum, maximum, average, and count across every value received since the
last reset. The **reset** input resets all four outputs.

## Average {#logic-block-avg-multi}

Computes the current average of 2–20 inputs, plus **moving averages** over fixed time windows
(1 minute, 1 hour, 1 day, 7/14/30/180/365 days) — each as its own output. Every newly received
value is stored with a timestamp; "Restore state after restart" determines whether this time
series survives a server restart.

## Min/Max Tracker {#logic-block-min-max-tracker}

Tracks a value's minimum and maximum across several time periods simultaneously (daily, weekly,
monthly, yearly, plus absolute since the beginning) — each period as its own output pair. The
period-bound values reset automatically at the respective period boundary (day/week/month/year
change); only "absolute" accumulates indefinitely. An initial value can optionally be set for each
period.

## Consumption Counter {#logic-block-consumption-counter}

Computes per-period consumption values — daily, weekly, monthly, yearly — from a continuously
increasing meter reading (e.g. a total electricity meter value), plus the respective **previous
period's** value for comparison (previous day, previous week, previous month, previous year). An
initial value can optionally be set for the meter reading and each period, e.g. when first setting
this up with an already-running meter.

## Summer/Winter (DIN) {#logic-block-heating-circuit}

Summer/winter switchover for heating control per DIN (Mannheim method), based on outside
temperature at the input. Three fixed measurement times per day (07:00, 14:00, 21:00) produce a
weighted **daily average** (`(T1 + T2 + 2×T3) / 4`); a moving **monthly average** over the last 31
daily averages smooths further. The **heating mode** output switches ON when the daily average
falls below the **threshold temperature**, and only OFF once threshold + **hysteresis** is
exceeded (prevents frequent switching). Missing measurement times are backfilled from history on
startup where possible; state survives a restart.
