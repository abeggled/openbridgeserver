---
title: Logs
---

# Logs

Shows the OBS backend's application logs live — technical diagnostic messages from the
server process itself, not to be confused with **Message Archives** (structured event
messages for operational purposes) or **History** (data point value trends).

## Log level {#logs-level}

The level selector in the top right changes the currently effective log level of the
running backend process (DEBUG/INFO/WARNING/ERROR) — only effective for admins; for
non-admins the server silently rejects the change. A lower level (e.g. DEBUG) produces
significantly more messages but isn't persistent — after a server restart, the
configured default level applies again. "Refresh" reloads the list once; the status badge
shows whether new entries are also arriving live over the WebSocket connection.

## Filters and table {#logs-table}

- **Search field** — searches logger name and message text (client-side only, applies to
  the already loaded entries).
- **Level filter** — restricts to a single level (applied server-side, triggers a
  reload).
- **Count** — how many of the most recent entries to load (100/200/500).

New entries arriving live over WebSocket are inserted at the top of the table and respect
the currently set filters; the total stays capped at the chosen count.
