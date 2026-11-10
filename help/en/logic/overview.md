---
title: Logic Module
---

# Logic Module {#logic}

The Logic Module is a visual graph editor for custom automations: function blocks ("Read
object", "Write object", logical operators, math, scheduling, text processing, and more)
are placed via drag & drop and connected with edges. Each **logic sheet** is an
independent graph with its own active/inactive state.

## Toolbar {#logic-toolbar}

- **Logic sheet selector** — opens a picker for switching logic sheets and managing their
  hierarchy assignments (see [Open Logic](#logic-graph-picker)).
- **+ New** / **Save** — creates a new logic sheet (see [New logic sheet](#logic-new-sheet))
  or saves changes to the current one.
- **▶ Run** — checks permissions and runs the graph once, manually (only possible for
  enabled graphs).
- **Debug** — toggles debug mode: after each run, every block shows its last computed
  values directly in the configuration panel.
- **Grid** / **Snap** — "Grid" toggles the background grid on/off (purely visual,
  available to all users); "Snap" makes blocks snap to the grid while dragging (admins
  only, since it changes positions). The grid size can be set in pixels next to it.
- **Enable/Disable** — turns the entire graph active or inactive without deleting it; a
  disabled graph can still be edited but won't run automatically or manually.
- **Copy** / **Paste** — copies the currently selected blocks (including the connections
  between them) to the clipboard and pastes them back offset from the original.
  "Save" is still needed afterward to persist the change.
- **Rename** — changes the logic sheet's name/description.
- **Duplicate** — asks for the copy's name (prefilled with the current name + " (Copy)"),
  then creates it as a new, independent logic sheet.
- **Export** / **Import** — downloads the current graph as a JSON file, or creates a new
  logic sheet from such a file — useful for backing up or transferring individual graphs
  between installations.
- **Delete** — deletes the logic sheet irreversibly.

## Open Logic {#logic-graph-picker}

The picker offers the assigned hierarchies for click-through, level by level; logic sheets
with no assignment appear in their own "Unassigned logic sheets" folder. An additional
"List of all logic sheets" folder
instead lists every existing logic sheet flat and alphabetically, regardless of whether or
where it is assigned, so an overview is always available even without knowing the hierarchy
structure. Disabled graphs are marked accordingly, and the currently open logic sheet is
marked with a dot to the left of its name.

Every row offers:

- **Assign hierarchy** (see below) — adds another hierarchy position.
- **Remove from hierarchy** — only available while browsing a hierarchy, undoes only the
  position currently being browsed; if that was the last one, the sheet reappears under
  "Unassigned logic sheets".
- **Links** (see below) — only available in "List of all logic sheets".
- **Delete** — deletes the logic sheet completely and irreversibly, after a confirmation
  prompt, including all of its hierarchy assignments.

The "Edit logic hierarchy" button leads to hierarchy management (Settings → Hierarchy),
which manages only the tree structure itself (trees and nodes) — assigning individual logic
sheets happens entirely here in this picker.

### Assign hierarchy {#logic-graph-picker-assign}

Adds another hierarchy position to the logic sheet — additive, existing assignments at other
positions are left untouched (never replaces). The search field is the same element used for
filter sets in the Monitor area; it offers both a hierarchy's own top level (e.g. "Shading")
and its sub-nodes (e.g. "Shading › Ground floor") for selection.

### Links {#logic-graph-picker-links}

Only available in "List of all logic sheets" — there is no single "currently browsed" position there to
remove via "Remove from hierarchy". Opens a popup instead that lists every hierarchy
position the sheet is assigned to at a glance (as a full path, e.g. "Shading › Ground
floor"), each individually removable without first navigating to it. A sheet with no
assignment at all shows a corresponding hint here.

## New logic sheet {#logic-new-sheet}

The dialog for creating a new logic sheet asks for a name, an optional description, and an
optional "Hierarchy nodes" search field (the same element used for filter sets in the
Monitor area). It lets the new logic sheet be assigned to one or more hierarchy positions
right when it's created — either a hierarchy's own top level (e.g. "Shading") or one of its
sub-nodes (e.g. "Shading › Ground floor"). Left empty, the new logic sheet lands at the top
level under "Unassigned logic sheets" as usual — the assignment can always be changed later via the "Assign
hierarchy" button in the "Open Logic" picker.

## Canvas {#logic-canvas}

The **block palette** on the left (admins only) is organized by category (logic, object
access, math, text, time, and more) — a block is dragged from there onto the canvas. On
the canvas itself:

- Blocks can be moved, selected by clicking (multi-select with Shift/Ctrl), and connected
  via their connector points into edges.
- A yellow warning bar at the top appears when the graph has structural problems (e.g. a
  cycle or duplicate connector assignments) — these must be resolved before saving.
- Zoom controls are in the bottom left, a draggable overview map (minimap) of the whole
  canvas in the bottom right.
- With no logic sheet selected, the canvas stays empty with a hint to select or create
  one.

## Block configuration {#logic-node-config}

Clicking a block opens the configuration panel on the right: the block's name (freely
editable, at the top of the panel) and its block-specific settings (e.g. which object is
read/written, the formula for a math block, the schedule for a timer block). When debug
mode is active, a second tab appears with that block's last computed input/output
values — values can also be overridden there for testing without real input data. The
panel's width can be adjusted by dragging its left edge.
