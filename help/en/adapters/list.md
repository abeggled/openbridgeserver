---
title: Adapter Instances
---

# Adapter Instances

Adapters connect external systems (KNX, Modbus, MQTT, 1-Wire, Home Assistant, ioBroker,
SNMP, scheduling, presence simulation, and more) to OBS as **instances**. Each instance has
a type, its own configuration, and any number of bindings to data points.

## Instance list {#adapters-list}

Each card shows an adapter instance with:

- **Status dot** — summarizes the connection state by color:

  | Color | Meaning |
  |---|---|
  | gray | instance inactive/stopped |
  | green | running and connected |
  | yellow, pulsing | running but not (yet) connected |
  | yellow | warning (degraded operation) |
  | red | error |

- **Type badge** — the adapter type (e.g. KNX, MODBUS_TCP).
- **Status badge** — text form of the status dot (Connected / Running / Degraded /
  Inactive / Error).
- **Bindings** — number of data point bindings this instance has.

On warning or error, a detail message with the exact cause appears as well. Clicking the
arrow on the right expands the instance to show its configuration and actions (see below).

## Create a new instance {#adapters-create}

"+ New instance" opens a form: first choose the **adapter type** and **name**, then the
type-specific configuration mask appears (e.g. host/port for KNX or Modbus TCP, broker
address for MQTT). Bindings to data points can only be created once the instance exists.

## Instance actions {#adapters-instance-actions}

When an instance is expanded:

- **Test connection** — checks the currently entered configuration without saving.
- **Save** — applies changes and reconnects the adapter.
- **Reconnect** — disconnects and reconnects using the existing configuration, without
  changing it.
- **Import** (ioBroker only) — imports ioBroker states as new OBS objects with a binding.
- **Manage objects** (presence simulation only) — selects simulated Boolean/Integer objects
  and manages their bindings.
- **Migrate bindings** — moves all of this instance's bindings to another instance of the
  same adapter type; bindings already present at the target are skipped.
- **Delete instance** — deletes the instance irreversibly, including all of its bindings.

"Enabled" turns the instance off entirely without deleting it — a disabled instance keeps
its configuration and bindings but does not connect.

## Scheduling {#adapters-zeitschaltuhr}

The scheduling adapter is a pure **source**: it writes to objects at defined points in time
but never reads from them. One binding is exactly **one schedule point** — for several
switching times on the same object, create several bindings.

| Schedule type | Switches |
|---|---|
| Daily | every day, or on selected weekdays |
| Annual | in selected months (no selection = all), optionally on a fixed day of the month |
| Holiday | on selected holidays (no selection = all holidays) |
| Metadata | not a schedule point — publishes the holiday/vacation status automatically |

The switching time is either a fixed time of day or tied to the position of the sun
(sunrise, sunset, solar noon, or a solar altitude angle), each with an offset in minutes.
A schedule point can also repeat on a cadence — hourly at the given minute, or every minute.
Holidays and vacations can be ignored, skipped, switched exclusively, or treated like a
Sunday, per schedule point.

### Output value {#adapters-zeitschaltuhr-value}

The **output value** is parsed against the type of the bound object — so a schedule can
drive any object type, not just on/off:

| Object type | Input control | Accepted values |
|---|---|---|
| Yes/No | On/Off select | `1`/`0`, `true`/`false`, `on`/`off`, `ein`/`aus` |
| Whole number | Number field | whole number, e.g. `50` |
| Decimal number | Number field with unit | decimal number, e.g. `21.5` |
| Text | Text field | taken literally — including `1`, `0`, `on` or `ein` |
| Date | Date picker | ISO 8601, e.g. `2026-12-24` |
| Time | Time picker | ISO 8601, e.g. `08:00:00` |
| Timestamp | Date/time picker | ISO 8601, e.g. `2026-12-24T08:00:00` |
| Unknown | Text field | heuristic: yes/no literal → whole number → decimal number → text |

The input control therefore follows the object: a blind position object of type decimal
number gets a number field with its unit, a yes/no object gets an On/Off select.

If the value does not fit the object type, the error appears directly below the field and
**saving is rejected**. The mistake surfaces while the schedule point is being created
rather than hours later when it fires. If a value that was already stored still cannot be
converted at switching time — because the object type was changed afterwards, say — OBS logs
a warning, records a `type_mismatch` diagnostic on the object, and skips the switch.

Metadata bindings have no output value: they publish the holiday/vacation status themselves.
