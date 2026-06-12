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
 *   tags       — list of tag strings, OR over entry.metadata.datapoint.tags
 *   q          — substring match over name | datapoint_id | source_adapter
 *   value_filter — operator over new_value
 *   hierarchy_nodes — node refs matched against entry.metadata.hierarchy_nodes
 *                     including ancestor_node_ids for descendants.
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
    expect(isEmptyFilter({ devices: ['1.1.10'] })).toBe(false)
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

  it('matches tags from the REST-compatible metadata.datapoint shape', () => {
    expect(matchEntry(makeEntry({ metadata: { datapoint: { tags: ['Heizung', 'Küche'] } } }), { tags: ['küche'] })).toBe(true)
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

  it('does not treat backend-valid but JavaScript-invalid regex syntax as a positive match', () => {
    const filter = { value_filter: { operator: 'regex', pattern: '(?P<name>temp)' } }
    expect(matchEntry(makeEntry({ new_value: 'temp' }), filter)).toBe(false)
    expect(matchEntry(makeEntry({ source_adapter: 'mqtt', new_value: 'temp' }), { ...filter, adapters: ['knx'] })).toBe(false)
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
  it('matches include_descendants filters via ancestor_node_ids', () => {
    expect(matchEntry(makeEntry({
      metadata: {
        hierarchy_nodes: [
          { tree_id: 't', node_id: 'leaf', ancestor_node_ids: ['leaf', 'floor-1', 'root'] },
        ],
      },
    }), { hierarchy_nodes: [{ tree_id: 't', node_id: 'floor-1', include_descendants: true }] })).toBe(true)
  })

  it('matches non-descendant filters only against the direct node', () => {
    const entry = makeEntry({
      metadata: {
        hierarchy_nodes: [
          { tree_id: 't', node_id: 'leaf', ancestor_node_ids: ['leaf', 'floor-1', 'root'] },
        ],
      },
    })
    expect(matchEntry(entry, { hierarchy_nodes: [{ tree_id: 't', node_id: 'leaf', include_descendants: false }] })).toBe(true)
    expect(matchEntry(entry, { hierarchy_nodes: [{ tree_id: 't', node_id: 'floor-1', include_descendants: false }] })).toBe(false)
  })

  it('does not match the wrong hierarchy node', () => {
    expect(matchEntry(makeEntry({
      metadata: {
        hierarchy_nodes: [
          { tree_id: 't', node_id: 'leaf', ancestor_node_ids: ['leaf', 'floor-1', 'root'] },
        ],
      },
    }), { hierarchy_nodes: [{ tree_id: 't', node_id: 'floor-2', include_descendants: true }] })).toBe(false)
  })

  it('AND-combines hierarchy_nodes with other populated criteria', () => {
    const filter = {
      hierarchy_nodes: [{ tree_id: 't', node_id: 'floor-1', include_descendants: true }],
      adapters: ['knx'],
    }
    const entry = makeEntry({
      source_adapter: 'knx',
      metadata: {
        hierarchy_nodes: [
          { tree_id: 't', node_id: 'leaf', ancestor_node_ids: ['leaf', 'floor-1', 'root'] },
        ],
      },
    })
    expect(matchEntry(entry, filter)).toBe(true)
    expect(matchEntry({ ...entry, source_adapter: 'mqtt' }, filter)).toBe(false)
  })

  it('OR-combines hierarchy_nodes with explicit datapoints before other criteria', () => {
    const filter = {
      hierarchy_nodes: [{ tree_id: 't', node_id: 'floor-1', include_descendants: true }],
      datapoints: ['dp-explicit'],
      adapters: ['knx'],
    }
    const hierarchyEntry = makeEntry({
      datapoint_id: 'dp-from-hierarchy',
      source_adapter: 'knx',
      metadata: {
        hierarchy_nodes: [
          { tree_id: 't', node_id: 'leaf', ancestor_node_ids: ['leaf', 'floor-1', 'root'] },
        ],
      },
    })
    const explicitEntry = makeEntry({
      datapoint_id: 'dp-explicit',
      source_adapter: 'knx',
      metadata: { hierarchy_nodes: [] },
    })
    expect(matchEntry(hierarchyEntry, filter)).toBe(true)
    expect(matchEntry(explicitEntry, filter)).toBe(true)
    expect(matchEntry({ ...explicitEntry, source_adapter: 'mqtt' }, filter)).toBe(false)
    expect(matchEntry(makeEntry({ datapoint_id: 'dp-other', source_adapter: 'knx' }), filter)).toBe(false)
  })

  it('returns false without hierarchy metadata', () => {
    expect(matchEntry(makeEntry({ source_adapter: 'knx' }), {
      hierarchy_nodes: [{ tree_id: 't', node_id: 'n', include_descendants: true }],
      adapters: ['knx'],
    })).toBe(false)
  })
})

describe('matchEntry — device-only filters', () => {
  it('returns false when only devices is populated (no client-evaluable constraint)', () => {
    expect(matchEntry(makeEntry(), { devices: ['1.1.10'] })).toBe(false)
  })

  it('returns false when devices is set alongside a client-evaluable passing criterion', () => {
    expect(matchEntry(makeEntry({ source_adapter: 'knx' }), {
      devices: ['1.1.10'],
      adapters: ['knx'],
    })).toBe(false)
  })
})
