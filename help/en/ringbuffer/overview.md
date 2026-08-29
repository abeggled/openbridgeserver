---
title: Monitor
---

# Monitor

The Monitor shows value changes live as they arrive in the system, and keeps a
configurably sized history of them in the ring buffer. Unlike the data point history (see
**History**), the Monitor isn't a recording tool for a single object — it's a searchable,
filterable live stream **across all objects**.

## Toolbar {#ringbuffer-toolbar}

- **⚙ Configure** (admins only) — opens the monitor configuration: storage model (memory
  or disk), maximum entries, maximum disk space, maximum retention by age, and — in
  segmented mode — the segment rotation thresholds. The monitor can also be fully disabled
  here (which deletes all existing entries) or re-enabled.
- **Segments** — only visible in segmented storage mode; opens an overview of the
  individual storage segments with status (Active/Closed/Legacy/Quarantined), size, time
  span, and integrity. A red dot on the button indicates at least one segment has a
  problem (e.g. corrupt or quarantined).
- **↻ Refresh** — reloads the table with the currently set filters.
- **⏸ Pause / ▶ Resume** — pauses queuing new live entries into the table (already loaded
  entries stay visible); resuming catches up on entries that arrived during the pause.
- **Status badge** — summarizes the WebSocket connection and pause state (Live / Paused /
  Offline).
- The numbers on the right (entries / capacity · storage model · disk usage and retention
  where applicable) show the current usage; the "ⓘ" icon next to them briefly explains the
  rotation behavior (the oldest entries get overwritten once the limit is reached).

### Filter sets and time filter

The topbar below the toolbar lets you create, edit, and pin **filter sets** as chips —
each set combines hierarchy nodes, individual objects, KNX devices, tags, adapters,
full-text search, and optionally a value filter (e.g. "temperature > 25"). Multiple pinned
sets combine with OR; within one set, criteria combine with AND (except
hierarchy/object, which combine with OR). Sets can be marked "shared" so other users can
pin them too. The **time filter** additionally restricts to a time range or a point in
time ± a span. "Export" downloads the currently filtered view as CSV/TSV with selectable
format options (separator, character encoding, extra columns).

## Live table {#ringbuffer-table}

Each row is a single value change: timestamp, object (linked to its detail page), new and
previous value, quality, and the triggering adapter. If a row matches at least one pinned
filter set, it's highlighted (matching that set's color); a title tooltip names the
matching sets. With no filter set, the table shows every value change in the system.
