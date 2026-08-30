---
title: Data Points
---

# Data Points {#datapoints}

A data point is open bridge server's central data unit — every sensor or actuator value,
every quantity managed by an adapter or by Logic, is represented as a data point. This list
shows all data points currently created in the system.

## Data point list {#datapoints-list}

The header shows the total number of data points. "New" lets an admin create a new,
initially bindingless data point — bindings to adapters are set up separately, from the
respective adapter instance.

## Search and filters {#datapoints-filters}

- **Search field** — searches name, UUID, and configuration.
- **Type** — restricts to a single data type (e.g. FLOAT, BOOL, STRING).
- **Adapter** — multi-select by adapter **type** (e.g. KNX, Modbus); shows data points bound
  to any instance of the selected types.
- **Tag** — multi-select over the tags currently in use across the system.
- **Quality** — filters by the last reported quality status (**Good** / **Unknown** /
  **Bad**).
- **Hierarchy nodes** — filters to one or more nodes/branches of the data point hierarchy;
  the search also finds nodes outside the currently selected trees.

All filters combine (logical AND) and update the list automatically. "Reset all filters"
appears once at least one filter is active, and clears search, type, adapter, tag, quality,
and hierarchy selection in one step.

## Table {#datapoints-table}

- **Name** — links to the data point's detail page; any hierarchy paths the data point is
  assigned to appear below it. Clicking a path segment filters the list to that node or
  branch directly.
- **Type** and **Tags** — tags are clickable and set the tag filter.
- **Value** — the last known value, updated live over the WebSocket connection.
- **Quality** — a badge with the last reported status; an additional "!" badge appears when
  a type mismatch between the adapter and the data point's type is detected.
- **Actions** (fully visible to admins only) — open details, edit, duplicate (copies all
  properties and adapter bindings, but not the current value or history), and delete
  (also removes all of the data point's bindings).

The list loads further entries automatically as you scroll.

## Data point detail {#datapoints-detail}

Clicking the name in the table opens a data point's detail page. It summarises everything the
system knows about that one data point, and is also where its bindings are maintained:

- **Current value** — the last known value, updated live, with timestamp, MQTT topic, and
  (if set) MQTT alias. Below it, **Write value** sets a value directly — via two buttons for
  BOOLEAN, via an input field otherwise. The area stays disabled while no writable binding
  exists.
- **Properties** — name, data type, unit, tags, plus the **Persist value** (value survives a
  restart) and **Record history** (values are recorded long-term, see **Settings → History
  DB**) switches. "Edit" opens the same dialog as in the data point list; "History →" jumps
  to History with this data point pre-filtered.
- **Hierarchy assignments** — the hierarchy nodes the data point is assigned to; assignments
  can be added and removed right here.
- **Adapter bindings** — all bindings to adapter instances, each with its direction (read,
  write, read/write) and protocol-specific details (for KNX, e.g. group address and status
  GA). New bindings are created here, existing ones edited, disabled, or deleted.
- **Logic bindings** — the logic sheets that read or write this data point, with a jump into
  the sheet. Informational only: the usage itself is edited in the Logic module.
