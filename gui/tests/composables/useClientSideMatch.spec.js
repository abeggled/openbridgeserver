/**
 * useClientSideMatch — client-side filter matcher used to colour and gate
 * live WebSocket entries (#36 follow-up).
 *
 * Background: the backend WS push does not include `matched_set_ids` — only
 * the REST multi-query response does. Live entries therefore arrived in the
 * table un-matched, so the row paint that the initial load applied to the
 * 500 rows of the OR-union disappeared as live updates pushed them off the
 * top of the table. Expected behaviour (user feedback): live entries are
 * matched against the active topbar sets on the client side; entries that
 * do not match any active set are not shown when filters are active.
 *
 * The matcher mirrors the simple, server-equivalent fields of FilterCriteria:
 *   datapoints — list of datapoint UUIDs, OR
 *   adapters   — list of adapter type strings, OR
 *   tags       — list of tag strings, OR over entry.metadata.tags
 *   q          — substring match over name | datapoint_id | source_adapter
 *   value_filter — operator over new_value
 *
 * hierarchy_nodes is intentionally pass-through: the frontend does not have
 * a hierarchy resolver, and live-entry filtering is best-effort. A set with
 * only hierarchy filters therefore matches every entry on the client (the
 * REST OR-union will still be correct on the next refresh).
 */
import { describe, it, expect } from 'vitest'
import { matchEntry, isEmptyFilter } from '@/composables/useClientSideMatch'

function makeEntry(overrides = {}) {
  return {
    id: 1,
    ts: '2026-05-12T20:00:00Z',
    datapoint_id: 'dp-1',
    name: 'Wohnzimmer Temperatur',
    new_value: 22.5,
    old_value: 22.4,
    source_adapter: 'knx',
    unit: '°C',
    quality: 'good',
    metadata: { tags: ['heizung', 'wohnen'] },
    ...overrides,
  }
}

describe('matchEntry — empty criteria (matches nothing — #36 semantics)', () => {
  // The user's expectation: an active topbar set with no filter criteria
  // should NOT colour every row — it should colour nothing, so the user
  // notices they still need to configure a filter. Empty/null criteria
  // therefore match no entry.

  it('returns false for an empty FilterCriteria object', () => {
    expect(matchEntry(makeEntry(), {})).toBe(false)
  })

  it('returns false when FilterCriteria is null/undefined', () => {
    expect(matchEntry(makeEntry(), null)).toBe(false)
    expect(matchEntry(makeEntry(), undefined)).toBe(false)
  })

  it('returns false when every field is empty/null (the V30-migrated shape)', () => {
    const empty = { hierarchy_nodes: [], datapoints: [], tags: [], adapters: [], q: null, value_filter: null }
    expect(matchEntry(makeEntry(), empty)).toBe(false)
  })
})

describe('isEmptyFilter helper', () => {
  it('treats null/undefined/{}/all-empty as empty', () => {
    expect(isEmptyFilter(null)).toBe(true)
    expect(isEmptyFilter(undefined)).toBe(true)
    expect(isEmptyFilter({})).toBe(true)
    expect(isEmptyFilter({ hierarchy_nodes: [], datapoints: [], tags: [], adapters: [], q: null, value_filter: null })).toBe(true)
    expect(isEmptyFilter({ q: '' })).toBe(true)
    expect(isEmptyFilter({ q: '   ' })).toBe(true)
  })

  it('returns false when any criterion is populated', () => {
    expect(isEmptyFilter({ datapoints: ['dp-1'] })).toBe(false)
    expect(isEmptyFilter({ adapters: ['knx'] })).toBe(false)
    expect(isEmptyFilter({ tags: ['heizung'] })).toBe(false)
    expect(isEmptyFilter({ q: 'temp' })).toBe(false)
    expect(isEmptyFilter({ value_filter: { operator: 'eq', value: 1 } })).toBe(false)
    expect(isEmptyFilter({ hierarchy_nodes: [{ tree_id: 't', node_id: 'n', include_descendants: true }] })).toBe(false)
  })
})

describe('matchEntry — datapoints (OR)', () => {
  it('matches when entry.datapoint_id is in the list', () => {
    expect(matchEntry(makeEntry({ datapoint_id: 'dp-7' }), { datapoints: ['dp-1', 'dp-7'] })).toBe(true)
  })

  it('does not match when entry.datapoint_id is not in the list', () => {
    expect(matchEntry(makeEntry({ datapoint_id: 'dp-99' }), { datapoints: ['dp-1', 'dp-7'] })).toBe(false)
  })

  it('does NOT match when datapoints list is empty AND no other criterion is set (empty filter semantics)', () => {
    expect(matchEntry(makeEntry(), { datapoints: [] })).toBe(false)
  })

  it('matches when datapoints list is empty BUT another criterion is set and passes', () => {
    expect(matchEntry(makeEntry({ source_adapter: 'knx' }), { datapoints: [], adapters: ['knx'] })).toBe(true)
  })
})

describe('matchEntry — adapters (OR)', () => {
  it('matches when entry.source_adapter is in the list', () => {
    expect(matchEntry(makeEntry({ source_adapter: 'mqtt' }), { adapters: ['knx', 'mqtt'] })).toBe(true)
  })

  it('does not match when entry.source_adapter is not in the list', () => {
    expect(matchEntry(makeEntry({ source_adapter: 'modbus' }), { adapters: ['knx', 'mqtt'] })).toBe(false)
  })
})

describe('matchEntry — tags (OR)', () => {
  it('matches when entry has at least one of the requested tags', () => {
    expect(matchEntry(makeEntry({ metadata: { tags: ['heizung', 'küche'] } }), { tags: ['küche'] })).toBe(true)
  })

  it('does not match when entry has none of the requested tags', () => {
    expect(matchEntry(makeEntry({ metadata: { tags: ['licht'] } }), { tags: ['heizung'] })).toBe(false)
  })

  it('handles missing metadata.tags gracefully', () => {
    expect(matchEntry(makeEntry({ metadata: {} }), { tags: ['heizung'] })).toBe(false)
    expect(matchEntry(makeEntry({ metadata: undefined }), { tags: ['heizung'] })).toBe(false)
  })
})

describe('matchEntry — q (substring)', () => {
  it('matches q against the entry name', () => {
    expect(matchEntry(makeEntry({ name: 'Wohnzimmer Temp' }), { q: 'wohnzimmer' })).toBe(true)
  })

  it('matches q against the datapoint_id', () => {
    expect(matchEntry(makeEntry({ datapoint_id: 'abc-def' }), { q: 'def' })).toBe(true)
  })

  it('matches q against the source_adapter', () => {
    expect(matchEntry(makeEntry({ source_adapter: 'knx' }), { q: 'kn' })).toBe(true)
  })

  it('does not match when q is in no searchable field', () => {
    expect(matchEntry(makeEntry({ name: 'X', datapoint_id: 'y', source_adapter: 'z' }), { q: 'nope' })).toBe(false)
  })
})

describe('matchEntry — value_filter', () => {
  it('numeric > works on new_value', () => {
    expect(matchEntry(makeEntry({ new_value: 25 }), { value_filter: { operator: 'gt', value: 20 } })).toBe(true)
    expect(matchEntry(makeEntry({ new_value: 15 }), { value_filter: { operator: 'gt', value: 20 } })).toBe(false)
  })

  it('numeric eq', () => {
    expect(matchEntry(makeEntry({ new_value: 22 }), { value_filter: { operator: 'eq', value: 22 } })).toBe(true)
  })

  it('regex on string new_value', () => {
    expect(matchEntry(makeEntry({ new_value: 'OK-200' }), { value_filter: { operator: 'regex', pattern: '^OK-' } })).toBe(true)
    expect(matchEntry(makeEntry({ new_value: 'ERR-500' }), { value_filter: { operator: 'regex', pattern: '^OK-' } })).toBe(false)
  })

  it('between numeric inclusive on lower/upper', () => {
    expect(matchEntry(makeEntry({ new_value: 50 }), { value_filter: { operator: 'between', lower: 0, upper: 100 } })).toBe(true)
    expect(matchEntry(makeEntry({ new_value: 150 }), { value_filter: { operator: 'between', lower: 0, upper: 100 } })).toBe(false)
  })
})

describe('matchEntry — AND across criteria', () => {
  it('all populated criteria must match', () => {
    const filter = {
      adapters: ['knx'],
      tags: ['heizung'],
      value_filter: { operator: 'gt', value: 20 },
    }
    expect(matchEntry(makeEntry({ source_adapter: 'knx', metadata: { tags: ['heizung'] }, new_value: 25 }), filter)).toBe(true)
    // Same entry but wrong adapter → no match
    expect(matchEntry(makeEntry({ source_adapter: 'mqtt', metadata: { tags: ['heizung'] }, new_value: 25 }), filter)).toBe(false)
    // Same entry but wrong tag → no match
    expect(matchEntry(makeEntry({ source_adapter: 'knx', metadata: { tags: ['licht'] }, new_value: 25 }), filter)).toBe(false)
    // Same entry but value too low → no match
    expect(matchEntry(makeEntry({ source_adapter: 'knx', metadata: { tags: ['heizung'] }, new_value: 5 }), filter)).toBe(false)
  })
})

describe('matchEntry — hierarchy-only filters', () => {
  it('returns false when only hierarchy_nodes is populated (no client-evaluable constraint)', () => {
    // Hierarchy is server-resolved; the frontend cannot tell whether an
    // entry belongs to a node, so a hierarchy-only filter does not match
    // on the client (live pushes stay uncoloured until next REST refresh).
    expect(matchEntry(makeEntry(), { hierarchy_nodes: [{ tree_id: 't', node_id: 'n', include_descendants: true }] })).toBe(false)
  })

  it('matches when hierarchy_nodes is set alongside a client-evaluable criterion that accepts the entry', () => {
    expect(matchEntry(makeEntry({ source_adapter: 'knx' }), {
      hierarchy_nodes: [{ tree_id: 't', node_id: 'n', include_descendants: true }],
      adapters: ['knx'],
    })).toBe(true)
  })
})
