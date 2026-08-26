---
title: Hierarchy
---

# Hierarchy

The Hierarchy tab lives in the Admin GUI under **Settings → Hierarchy**.

## Device structure {#settings-hierarchy}

Represents a tree-shaped structure (buildings, rooms, trades, topology, …) that data points
can be assigned to — it's used both for navigation/grouping in the GUI and as the basis for
scope assignment in the rights editor (see Settings → Users).

Multiple hierarchies can exist in parallel; each has its own mode:

- **Topology** — follows the KNX group address structure.
- **Building structure** — spatial layout, usually imported from an ETS project.
- **Trades → function** — grouped by trade.

**Import from ETS** — automatically generates a hierarchy from an ETS project's spatial or
functional structure (building/trades mode); data points can optionally be auto-linked to the
matching nodes via their group address. The same import is also available directly from the
KNX project import in the Data Management tab.

Nodes can be manually renamed, added, and deleted again (deleting a branch also removes all
its child nodes). The "display start level" determines from which level the shortened path is
shown in data point lists — the full path always remains visible as a tooltip.
