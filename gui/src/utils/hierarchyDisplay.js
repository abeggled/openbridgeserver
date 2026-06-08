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
