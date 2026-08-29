---
title: "Blocks: Logic"
---

# Blocks: Logic

Basic logical operators, signal control, and memory blocks in the Logic Module.

## AND {#logic-block-and}

Output is **true** when ALL inputs are true. The number of inputs can be set between 2 and 30.
Each individual input and the output can be inverted independently — clicking the port name
directly on the block in the canvas toggles negation (shown with a leading "¬").

## OR {#logic-block-or}

Output is **true** when AT LEAST ONE input is true. Inputs (2–30) and the output can be negated
individually, same as the AND block.

## XOR {#logic-block-xor}

Output is **true** when EXACTLY ONE input is true (with more than two inputs: an odd number of
true inputs). Inputs (2–30) and the output can be negated individually.

## NOT {#logic-block-not}

Inverts the input — a single input and output, no further configuration.

## Gate {#logic-block-gate}

Signal gate: lets the input through as long as "Enable" is true, blocks it otherwise.

- **Behavior (closed)** — determines what's output while the gate is closed: hold the last
  passed-through value (**retain**) or output a fixed **default value**.
- **Invert enable** — reverses the meaning of the enable input (gate open when false).
- **Restore state after restart** — with "retain", the held value would otherwise be lost on a
  server restart.

## Memory {#logic-block-memory}

Outputs the value stored during the previous logic run and stores the current input value for the
next run. This block is the **explicit tick boundary** for controlled feedback loops — without
it, a graph with feedback would loop infinitely within a single run. The **reset** input resets
the stored value to the configured initial value. "Restore state after restart" determines whether
the stored value survives a server restart.

## Change Filter {#logic-block-change-filter}

Outputs the input value unchanged, but only sets the **Changed** trigger output when it differs
from the last value received — repeated identical values don't trigger again (matches Edomi's
"SendByChange"). Useful for not re-triggering downstream actions (e.g. notifications) on every
identical update.

## Compare {#logic-block-compare}

Compares the input against a configured operand using a selectable operator (`>`, `<`, `=`, `>=`,
`<=`, `!=`) and outputs the boolean result.

## Hysteresis {#logic-block-hysteresis}

Switches the output on when the upper threshold (**threshold_on**) is exceeded, and off only once
the lower threshold (**threshold_off**) is undercut — prevents rapid switching ("chattering") for
values that hover around a single threshold, e.g. temperature control.

## Merge {#logic-block-merge}

Bundles several independent value sources (2–30 inputs) onto one shared output: whichever input
delivers a new value most recently gets passed through (matches Edomi's "terminal/Klemme"). Unlike
other logic blocks, this node deliberately does **not** produce a new output value of its own each
run — it only relays whatever arrived last.

## Decision {#logic-block-decision}

Checks an input value against several independent conditions; each condition has its own trigger
output that fires when it matches. Conditions are managed as a list via "+ Add" (operators like
`=`, `≠`, `>`, `<`, "between", text "contains"/"starts with"/"ends with"/regex, …) — no manual JSON
editing required. At least two conditions are required.

## Value Mapping {#logic-block-value-mapping}

Maps an input value to exactly one result value using an ordered rule list — the first matching
rule wins. The **output type** (BOOL/INT/FLOAT/STRING) determines how the result is interpreted.
Optionally enable a **default value** that's output when no rule matches.

## Constant {#logic-block-const-value}

Outputs a fixed configured value — number, bool, or text (**data type**). Useful as a threshold,
reference value, or constant that other blocks compare or calculate against.
