---
title: Data Points
---

# Data Points

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

## Create / edit form {#datapoints-form}

Opens from "New" on this page, or "Edit" on a data point's detail page or table row. Both
modes share the same fields; only the title differs.

- **Name** — required, freely chosen.
- **Data type** — the value type (e.g. FLOAT, BOOL, STRING); determines how written values
  are validated and displayed.
- **Unit** — optional, picked from a categorized list (temperature, electricity, …) or
  entered as free text via "Other".
- **Tags** — optional, comma-separated; used for filtering and hierarchy views.
- **MQTT alias** — an optional second MQTT topic the value is also published under, in
  addition to the data point's own topic.
- **Persist value** — keep the last value so it's immediately available again after a
  restart, instead of starting empty.
- **Record history** — store value changes in the history database so they appear on the
  **History** page.
- **Allow external write** — lets any MQTT client that can reach the broker set this value,
  not just OBS's own adapters and Logic.
