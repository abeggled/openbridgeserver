---
title: Logic Module
---

# Logic Module

The Logic Module is a visual graph editor for custom automations: function blocks ("Read
object", "Write object", logical operators, math, scheduling, text processing, and more)
are placed via drag & drop and connected with edges. Each **logic sheet** is an
independent graph with its own active/inactive state.

## Toolbar {#logic-toolbar}

- **Logic sheet selector** — switches between existing graphs; disabled graphs are marked
  accordingly.
- **+ New** / **Save** — creates a new logic sheet or saves changes to the current one.
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
- **Rename** / **Duplicate** — changes the logic sheet's name/description, or creates a
  full copy as a new logic sheet.
- **Export** / **Import** — downloads the current graph as a JSON file, or creates a new
  logic sheet from such a file — useful for backing up or transferring individual graphs
  between installations.
- **Delete** — deletes the logic sheet irreversibly.

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
