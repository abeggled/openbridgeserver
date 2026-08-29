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
