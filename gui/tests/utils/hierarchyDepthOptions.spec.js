import { describe, it, expect } from 'vitest'
import { buildDepthOptions } from '@/utils/hierarchyDepthOptions'

// Minimal t/t helpers that return a predictable marker string
function makeI18n(overrides = {}) {
  return {
    t: (key, params = {}) => {
      if (key in overrides) return overrides[key]
      // Interpolate {param} placeholders
      return Object.entries(params).reduce(
        (s, [k, v]) => s.replace(`{${k}}`, String(v)),
        key,
      )
    },
  }
}

const rootNodes = [
  { name: 'EG', children: [{ name: 'Wohnzimmer', children: [] }, { name: 'Küche', children: [] }] },
  { name: 'OG', children: [{ name: 'Schlafzimmer', children: [] }] },
]

describe('buildDepthOptions — non-edit mode', () => {
  it('returns maxDepth+1 options (0…maxDepth)', () => {
    const opts = buildDepthOptions({ isEdit: false })
    expect(opts).toHaveLength(5) // 0..4
  })

  it('all options are enabled', () => {
    const opts = buildDepthOptions({ isEdit: false })
    expect(opts.every(o => o.disabled === false)).toBe(true)
  })

  it('uses GENERIC_LABELS fallback when no t function given', () => {
    const opts = buildDepthOptions({ isEdit: false })
    expect(opts[0].label).toContain('Hierarchiename')
    expect(opts[1].label).toContain('Erste Ebene')
  })

  it('uses translated labels when t function given', () => {
    const { t } = makeI18n({ 'hierarchy.depthOptions.generic0': 'Root level' })
    const opts = buildDepthOptions({ isEdit: false, t })
    expect(opts[0].label).toBe('Root level')
  })

  it('respects custom maxDepth', () => {
    const opts = buildDepthOptions({ isEdit: false, maxDepth: 2 })
    expect(opts).toHaveLength(3) // 0, 1, 2
    expect(opts.map(o => o.value)).toEqual([0, 1, 2])
  })
})

describe('buildDepthOptions — edit mode', () => {
  const tree = { name: 'Gebäude' }

  it('level 0 uses tree name in label', () => {
    const opts = buildDepthOptions({ isEdit: true, tree, rootNodes, maxDepth: 2 })
    expect(opts[0].value).toBe(0)
    expect(opts[0].label).toContain('Gebäude')
    expect(opts[0].disabled).toBe(false)
  })

  it('level 0 falls back to LEVEL_NAMES when tree has no name', () => {
    const opts = buildDepthOptions({ isEdit: true, tree: {}, rootNodes, maxDepth: 1 })
    expect(opts[0].label).toContain('Hierarchiename')
  })

  it('level with names shows example from first name (one distinct)', () => {
    // depth 1 → collectNamesAtDepth(rootNodes, 0) → ['EG', 'OG'] (2 distinct)
    // depth 2 → collectNamesAtDepth(rootNodes, 1) → ['Wohnzimmer', 'Küche', 'Schlafzimmer'] (3 distinct)
    const singleRoots = [{ name: 'EG', children: [{ name: 'Wohnzimmer', children: [] }] }]
    const opts = buildDepthOptions({ isEdit: true, tree, rootNodes: singleRoots, maxDepth: 2 })
    // level 1: collectNamesAtDepth(singleRoots, 0) = ['EG'] → 1 distinct → "only" branch
    expect(opts[1].label).toContain('EG')
    expect(opts[1].label).toContain('nur')
    expect(opts[1].disabled).toBe(false)
  })

  it('level with multiple distinct names shows count', () => {
    const opts = buildDepthOptions({ isEdit: true, tree, rootNodes, maxDepth: 1 })
    // level 1: collectNamesAtDepth(rootNodes, 0) = ['EG', 'OG'] → 2 distinct
    expect(opts[1].label).toContain('EG')
    expect(opts[1].label).toContain('2')
    expect(opts[1].disabled).toBe(false)
  })

  it('level with no names is disabled', () => {
    const emptyRoots = [{ name: 'EG', children: [] }]
    const opts = buildDepthOptions({ isEdit: true, tree, rootNodes: emptyRoots, maxDepth: 2 })
    // level 2: collectNamesAtDepth(emptyRoots, 1) = [] → disabled
    expect(opts[2].disabled).toBe(true)
    expect(opts[2].value).toBe(2)
  })

  it('level beyond LEVEL_NAMES array uses fallback label', () => {
    const opts = buildDepthOptions({ isEdit: true, tree, rootNodes, maxDepth: 5 })
    // level 5 is beyond LEVEL_NAMES (length 5, indices 0-4) — uses fallback
    expect(opts[5].label).toContain('5')
  })
})
