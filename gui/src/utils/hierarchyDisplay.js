export function normalizeHierarchyDisplayDepth(displayDepth) {
  const depth = Number(displayDepth)
  if (!Number.isFinite(depth) || depth <= 0) return 0
  return Math.trunc(depth)
}

export function hierarchyDisplayPath({
  treeName,
  path,
  displayDepth,
  treeHasDisplayDepth = false,
  hideShallow = false,
}) {
  const nodePath = Array.isArray(path) ? path : []
  const depth = normalizeHierarchyDisplayDepth(displayDepth)

  if (depth <= 0) {
    return treeName ? [treeName, ...nodePath] : nodePath.slice()
  }

  const startIndex = depth - 1
  if (nodePath.length > startIndex) return nodePath.slice(startIndex)
  if (hideShallow && treeHasDisplayDepth) return []
  return treeName ? [treeName, ...nodePath] : nodePath.slice()
}

export function hierarchyDisplayIndent(path, displayDepth) {
  const depth = normalizeHierarchyDisplayDepth(displayDepth)
  const nodePath = Array.isArray(path) ? path : []
  const firstVisibleNodeLevel = depth > 0 ? depth : 1
  return Math.max(0, nodePath.length - firstVisibleNodeLevel)
}

export function hierarchyDisplayLabel(options) {
  return hierarchyDisplayPath(options).join(' › ')
}

/**
 * `HierarchyCombobox`/`Combobox` (multi mode) emit an array of composite
 * `"<tree_id>:<node_id>"` strings, not the full item objects — split one
 * back into its parts. Returns `null` for a malformed id (no `:`, or an
 * empty tree_id) rather than throwing, so a caller can skip it defensively.
 */
export function parseHierarchyCompositeId(compositeId) {
  const idx = String(compositeId).indexOf(':')
  if (idx <= 0) return null
  return { tree_id: compositeId.slice(0, idx), node_id: compositeId.slice(idx + 1) }
}
