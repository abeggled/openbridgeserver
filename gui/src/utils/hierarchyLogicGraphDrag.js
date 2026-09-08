// Shared drag-and-drop MIME type for dragging a logic graph from the
// HierarchyManager palette onto a hierarchy node to link it there (#1217).
// A dedicated custom type (rather than 'text/plain') keeps the drop handler
// from mis-firing on unrelated drags (e.g. text selections) dropped onto a
// node row.
export const LOGIC_GRAPH_DRAG_MIME = 'application/x-obs-logic-graph-id'
