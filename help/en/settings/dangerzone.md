---
title: Danger Zone
---

# Danger Zone

The Danger Zone lives in the Admin GUI under **Settings → Danger Zone**.

## Irreversible delete actions {#settings-dangerzone}

Every action on this page is **irreversible** and requires an explicit confirmation before it
runs:

- **Delete all bindings** — deletes all bindings; data points and adapter instances
  themselves are kept.
- **Delete all data points** — deletes all data points, including their bindings.
- **Delete all logic graphs** — deletes all logic graphs and stops the logic engine.
- **Delete all adapters** — stops and deletes all adapter instances, including their
  bindings.
- **Delete KNX group addresses** — deletes all imported KNX group addresses (only active when
  some exist).
- **Reset to factory settings** — deletes everything at once: data points, bindings,
  adapters, KNX group addresses, logic graphs, icons, and the FontAwesome API key. **User
  accounts are the sole exception and are kept.**

For a complete restore after a factory reset from a backup, see Data Management → Restore
autobackup.
