/**
 * useClientSideMatch — pure-function matcher for a RingBuffer entry against
 * a FilterCriteria object. Used to gate and colour live WebSocket entries
 * since the WS push does not include `matched_set_ids` like the REST
 * multi-query response does.
 *
 * Field semantics (mirroring `FilterCriteria` from obs/api/v1/ringbuffer.py):
 *   - datapoints[]  — OR over entry.datapoint_id
 *   - adapters[]    — OR over entry.source_adapter
 *   - tags[]        — OR over entry.metadata.tags
 *   - q             — substring (case-insensitive) over name | datapoint_id | source_adapter
 *   - value_filter  — operator + value/lower/upper/pattern over entry.new_value
 *   - hierarchy_nodes — pass-through (the frontend has no hierarchy resolver;
 *                       a set with only hierarchy filters matches every entry
 *                       on the client). The REST OR-union remains authoritative.
 *
 * Multiple criteria within a single FilterCriteria are AND-combined.
 */

function _arrayIncludes(list, value) {
  return Array.isArray(list) && list.length > 0 && list.includes(value)
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
      return false
    }
  }
  // Unknown operator → don't drop the entry, defer to server.
  return true
}

/**
 * Returns true iff at least one *client-evaluable* criterion is populated AND
 * every populated criterion accepts the entry.
 *
 * Empty / null / undefined criteria match NOTHING (Phase-2 UX feedback).
 *
 * Hierarchy-only filters also match NOTHING on the client: the frontend has
 * no hierarchy resolver, and silently accepting every entry would colour
 * unrelated rows (e.g. a Wetterstation push would inherit a hierarchy set's
 * colour). The REST OR-union already does the right thing using the server's
 * recursive node-DP resolution, so the next refresh shows real matches; live
 * pushes for hierarchy-only sets stay uncoloured until then.
 */
export function matchEntry(entry, criteria) {
  if (!criteria || typeof criteria !== 'object') return false
  if (isEmptyFilter(criteria)) return false
  if (!entry) return false

  const hasNonHierarchyConstraint =
    (Array.isArray(criteria.datapoints) && criteria.datapoints.length > 0) ||
    (Array.isArray(criteria.adapters) && criteria.adapters.length > 0) ||
    (Array.isArray(criteria.tags) && criteria.tags.length > 0) ||
    (typeof criteria.q === 'string' && criteria.q.trim().length > 0) ||
    (criteria.value_filter && criteria.value_filter.operator)
  if (!hasNonHierarchyConstraint) return false

  // datapoints
  if (Array.isArray(criteria.datapoints) && criteria.datapoints.length > 0) {
    if (!criteria.datapoints.includes(entry.datapoint_id)) return false
  }

  // adapters
  if (Array.isArray(criteria.adapters) && criteria.adapters.length > 0) {
    if (!criteria.adapters.includes(entry.source_adapter)) return false
  }

  // tags
  if (Array.isArray(criteria.tags) && criteria.tags.length > 0) {
    const entryTags = entry.metadata && Array.isArray(entry.metadata.tags) ? entry.metadata.tags : []
    const hasAny = criteria.tags.some((t) => entryTags.includes(t))
    if (!hasAny) return false
    // Avoid lint complaints about unused helper above; _arrayIncludes is exported-like
    // (kept for clarity of intent in future overloads).
    void _arrayIncludes
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

  // hierarchy_nodes is pass-through on the client side.
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
