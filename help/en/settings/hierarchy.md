---
title: Hierarchy
---

# Hierarchy

The Hierarchy tab lives in the Admin GUI under **Settings → Hierarchy**.

## Hierarchies {#settings-hierarchy}

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

## Assigning Logic graphs

The same hierarchies can also be used to group Logic graphs — a graph can be sorted into
several independent hierarchies at once (e.g. a technical grouping by
shading/lighting/sockets alongside a topological one by room). This assignment is purely
organizational and has no effect on permissions — Logic graphs keep their own, independent
permission model.

In the Logic editor, the "Open graph" button leads to a picker that offers the hierarchies for
click-through, level by level. Graphs with no assignment at all appear there in their own
"Unassigned" folder at the top level.

To organize graphs, this Settings page offers a logic-graph palette: dragging a graph from
there onto a node links it there — existing links in other folders or hierarchies are left
untouched. Dropping a graph directly on a hierarchy's own title (instead of one of its
sub-folders) links it straight to that hierarchy's top level, with no need to create a
sub-folder first.

Alternatively, the assignment can also be made right when creating a new graph: the "New
logic sheet" dialog in the Logic editor has an optional "Hierarchy nodes" search field (the
same element used for filter sets in the Monitor area) to assign one or more nodes directly.
Left empty, the new graph lands at the top level under "Not assigned" as usual.
