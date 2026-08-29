---
title: Message Archives
---

# Message Archives

Message archives store structured event messages (system, security, notifications,
automations, adapter diagnostics, and more) separately from the running OBS database —
each archive is its own database file with its own retention limit. This view is only
fully usable by admins.

## Archive list {#messagearchives-list}

- **Integrity Check** — checks the database file(s) for structural consistency.
- **New Archive** — creates another archive with its own ID, color, and retention limits.

The list on the left shows all existing archives with color, name, and current entry
count; clicking one selects that archive for the detail panel and message table on the
right.

## Archive details {#messagearchives-detail}

Selected or newly created archive:

- **Edit** — name, description, default message type (the type pre-filled for new
  entries when a sending component doesn't specify its own), color, and the retention
  limits (maximum entry count and/or maximum age in days). The archive ID can only be set
  when creating the archive, not changed afterward.
- **Export JSONL** / **Export CSV** — downloads all of the archive's entries in one of
  the two formats.
- **Clear** — deletes all of the archive's entries irreversibly; the archive itself
  remains.
- **Delete** — deletes the entire archive, including all entries, irreversibly.

## Messages {#messagearchives-entries}

The table shows the entries of the selected archive. Filters (full-text search over
title/text, severity, status, type) can be combined; "Refresh" reloads with the current
filters. Each row shows time, title and message text, type, severity, status, and the
reporting source.
