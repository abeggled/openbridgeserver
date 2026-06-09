/**
 * useClientSideMatch — pure-function matcher for a RingBuffer entry against
 * a FilterCriteria object. Used to gate and colour live WebSocket entries
 * since the WS push does not include `matched_set_ids` like the REST
 * multi-query response does.
 *
 * Field semantics (mirroring `FilterCriteria` from obs/api/v1/ringbuffer.py):
 *   - datapoints[]  — OR over entry.datapoint_id
 *   - devices[]     — server-resolved KNX physical addresses; pass-through
 *   - adapters[]    — OR over entry.source_adapter
 *   - tags[]        — OR over entry.metadata.datapoint.tags
 *   - q             — substring (case-insensitive) over name | datapoint_id | source_adapter
 *   - value_filter  — operator + value/lower/upper/pattern over entry.new_value
 *   - hierarchy_nodes — OR over entry.metadata.hierarchy_nodes for the same
 *                       tree, respecting include_descendants.
 *
 * Multiple criteria within a single FilterCriteria are AND-combined.
 */

function _normalizedStrings(list) {
  if (!Array.isArray(list)) return []
  return list
    .map((value) => String(value ?? '').trim().toLowerCase())
    .filter(Boolean)
}

function _entryTags(entry) {
  const metadata = entry?.metadata
  if (!metadata || typeof metadata !== 'object') return []
  const datapoint = metadata.datapoint
  if (datapoint && typeof datapoint === 'object' && Array.isArray(datapoint.tags)) {
    return _normalizedStrings(datapoint.tags)
  }
  // Legacy test fixtures and pre-metadata live payloads used metadata.tags.
  return _normalizedStrings(metadata.tags)
}

function _entryHierarchyNodes(entry) {
  const metadata = entry?.metadata
  if (!metadata || typeof metadata !== 'object') return []
  if (!Array.isArray(metadata.hierarchy_nodes)) return []
  return metadata.hierarchy_nodes
    .filter((node) => node && typeof node === 'object')
    .map((node) => ({
      treeId: String(node.tree_id ?? '').trim(),
      nodeId: String(node.node_id ?? '').trim(),
      ancestorNodeIds: Array.isArray(node.ancestor_node_ids)
        ? node.ancestor_node_ids.map((id) => String(id ?? '').trim()).filter(Boolean)
        : [],
    }))
    .filter((node) => node.treeId && node.nodeId)
}

function _matchHierarchy(entry, hierarchyNodes) {
  const entryNodes = _entryHierarchyNodes(entry)
  if (entryNodes.length === 0) return false

  return hierarchyNodes.some((requested) => {
    const requestedTreeId = String(requested?.tree_id ?? '').trim()
    const requestedNodeId = String(requested?.node_id ?? '').trim()
    if (!requestedTreeId || !requestedNodeId) return false
    const includeDescendants = requested?.include_descendants !== false
    return entryNodes.some((entryNode) => {
      if (entryNode.treeId !== requestedTreeId) return false
      if (!includeDescendants) return entryNode.nodeId === requestedNodeId
      return entryNode.ancestorNodeIds.includes(requestedNodeId)
    })
  })
}

/**
 * Returns true if the given FilterCriteria has no populated field — all lists
 * empty (or absent), `q` null/whitespace, `value_filter` null/missing.
 */
export function isEmptyFilter(criteria) {
  if (!criteria || typeof criteria !== 'object') return true
  const hasList = (key) => Array.isArray(criteria[key]) && criteria[key].length > 0
  if (hasList('hierarchy_nodes')) return false
  if (hasList('datapoints')) return false
  if (hasList('devices')) return false
  if (hasList('tags')) return false
  if (hasList('adapters')) return false
  if (typeof criteria.q === 'string' && criteria.q.trim().length > 0) return false
  if (criteria.value_filter && criteria.value_filter.operator) return false
  return true
}

function _matchValueFilter(entryValue, vf) {
  if (!vf || !vf.operator) return true
  const op = vf.operator
  const v = vf.value
  if (op === 'eq') return entryValue === v
  if (op === 'ne') return entryValue !== v
  if (op === 'gt') return Number(entryValue) > Number(v)
  if (op === 'gte') return Number(entryValue) >= Number(v)
  if (op === 'lt') return Number(entryValue) < Number(v)
  if (op === 'lte') return Number(entryValue) <= Number(v)
  if (op === 'between') {
    const n = Number(entryValue)
    const lo = Number(vf.lower)
    const hi = Number(vf.upper)
    if (Number.isFinite(lo) && n < lo) return false
    if (Number.isFinite(hi) && n > hi) return false
    return true
  }
  if (op === 'contains') return String(entryValue ?? '').includes(String(v ?? ''))
  if (op === 'regex') {
    if (!vf.pattern) return true
    try {
      const re = new RegExp(vf.pattern, vf.ignore_case ? 'i' : '')
      return re.test(String(entryValue ?? ''))
    } catch {
      // The backend accepts Python regex syntax that JavaScript cannot compile.
      // Do not count an unevaluable criterion as a positive live match.
      return false
    }
  }
  // Unknown operator → don't drop the entry, defer to server.
  return true
}

/**
 * Returns true iff at least one criterion is populated AND every populated
 * criterion accepts the entry.
 *
 * Empty / null / undefined criteria match NOTHING (Phase-2 UX feedback).
 *
 * Device-constrained filters match NOTHING on the client: the frontend has no
 * resolver for that server-side mapping, even when other client-evaluable
 * constraints pass. The REST OR-union remains authoritative; live pushes for
 * device-constrained sets stay uncoloured until the next refresh.
 */
export function matchEntry(entry, criteria) {
  if (!criteria || typeof criteria !== 'object') return false
  if (isEmptyFilter(criteria)) return false
  if (!entry) return false

  const hasHierarchyConstraint = Array.isArray(criteria.hierarchy_nodes) && criteria.hierarchy_nodes.length > 0
  const hasDatapointConstraint = Array.isArray(criteria.datapoints) && criteria.datapoints.length > 0
  const hasDeviceConstraint = Array.isArray(criteria.devices) && criteria.devices.length > 0
  const hasClientConstraint =
    hasDatapointConstraint ||
    (Array.isArray(criteria.adapters) && criteria.adapters.length > 0) ||
    (Array.isArray(criteria.tags) && criteria.tags.length > 0) ||
    (typeof criteria.q === 'string' && criteria.q.trim().length > 0) ||
    (criteria.value_filter && criteria.value_filter.operator)
  if (!hasHierarchyConstraint && !hasClientConstraint) return false
  if (hasDeviceConstraint) return false

  // Server-side hierarchy resolution OR-unions resolved hierarchy datapoints
  // with explicit datapoints before applying the remaining AND constraints.
  if (hasHierarchyConstraint || hasDatapointConstraint) {
    const matchesHierarchy = hasHierarchyConstraint && _matchHierarchy(entry, criteria.hierarchy_nodes)
    const matchesDatapoint = hasDatapointConstraint && criteria.datapoints.includes(entry.datapoint_id)
    if (!matchesHierarchy && !matchesDatapoint) return false
  }

  // adapters
  if (Array.isArray(criteria.adapters) && criteria.adapters.length > 0) {
    if (!criteria.adapters.includes(entry.source_adapter)) return false
  }

  // tags
  if (Array.isArray(criteria.tags) && criteria.tags.length > 0) {
    const entryTags = _entryTags(entry)
    const requestedTags = _normalizedStrings(criteria.tags)
    const hasAny = requestedTags.some((t) => entryTags.includes(t))
    if (!hasAny) return false
  }

  // q (case-insensitive substring over name | datapoint_id | source_adapter)
  if (typeof criteria.q === 'string' && criteria.q.trim().length > 0) {
    const needle = criteria.q.trim().toLowerCase()
    const hay = [entry.name, entry.datapoint_id, entry.source_adapter]
      .filter(Boolean)
      .map((s) => String(s).toLowerCase())
      .join('  ')
    if (!hay.includes(needle)) return false
  }

  // value_filter
  if (criteria.value_filter && criteria.value_filter.operator) {
    if (!_matchValueFilter(entry.new_value, criteria.value_filter)) return false
  }
  return true
}

/**
 * Compute which of the given active topbar sets match the entry.
 * Returns an array of set ids in input order.
 *
 * `sets` is an iterable of objects shaped like `{ id, filter }` where
 * `filter` is a FilterCriteria. Order is preserved so callers can derive
 * the first-match-wins colour pick from useSetColors.
 */
export function matchedSetIds(entry, sets) {
  const out = []
  if (!sets || !entry) return out
  for (const set of sets) {
    if (matchEntry(entry, set.filter)) out.push(set.id)
  }
  return out
}
