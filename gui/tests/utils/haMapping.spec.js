import { describe, it, expect } from 'vitest'
import {
  advancedMappingText,
  attributeSuggestions,
  conventionKey,
  domainSuggestions,
  entityDomain,
  entitySuggestions,
  fieldSuggestions,
  serviceSuggestions,
  parseAdvancedMapping,
  profileLabelKey,
  profileTypeMismatch,
  profilesForEntity,
  reasonKey,
  resolveHaMapping,
  writeRows,
} from '@/utils/haMapping'

const CATALOG = [
  {
    id: 'lock.boolean_locked', domains: ['lock'], attribute: null,
    state_map: { locked: true, unlocked: false }, transitional_states: ['locking'], fault_states: ['jammed'],
    action: null, write_actions: { true: { domain: null, name: 'lock', value_path: null }, false: { domain: null, name: 'unlock', value_path: null } },
    convention: 'trueLocked',
  },
  {
    id: 'number.value', domains: ['number', 'input_number'], attribute: null, state_map: null,
    transitional_states: [], fault_states: [], action: { domain: null, name: 'set_value', value_path: 'value' }, write_actions: null, convention: null,
  },
  {
    id: 'sensor.value', domains: ['sensor'], attribute: null, state_map: null,
    transitional_states: [], fault_states: [], action: null, write_actions: null, convention: null,
  },
  {
    id: 'light.brightness', domains: ['light'], attribute: 'brightness', state_map: null,
    transitional_states: [], fault_states: [], action: { domain: 'light', name: 'turn_on', value_path: 'brightness' }, write_actions: null, convention: null,
  },
]

describe('haMapping keys', () => {
  it('builds locale keys', () => {
    expect(profileLabelKey('lock.boolean_locked')).toBe('adapters.homeassistant.profile.lock_boolean_locked')
    expect(conventionKey('trueOn')).toBe('adapters.homeassistant.convention.trueOn')
    expect(reasonKey('featureMissing')).toBe('adapters.homeassistant.reason.featureMissing')
  })

  it('extracts the domain and filters profiles', () => {
    expect(entityDomain('input_number.t')).toBe('input_number')
    expect(entityDomain(undefined)).toBe('')
    expect(entityDomain(' Light.Kitchen ')).toBe('light')
    expect(profilesForEntity(CATALOG, 'input_number.t').map(p => p.id)).toEqual(['number.value'])
    expect(profilesForEntity(null, 'lock.x')).toEqual([])
  })
})

describe('resolveHaMapping', () => {
  it('applies the profile state_map only to the value the profile reads', () => {
    expect(resolveHaMapping({ entity_id: 'lock.x', profile: 'lock.boolean_locked' }, CATALOG).read.state_map).not.toBeNull()
    expect(resolveHaMapping({ entity_id: 'lock.x', profile: 'lock.boolean_locked', attribute: 'battery' }, CATALOG).read.state_map).toBeNull()
    expect(resolveHaMapping({ entity_id: 'sensor.x', profile: 'sensor.value' }, CATALOG).read.state_map).toBeNull()
    expect(resolveHaMapping({ entity_id: 'lock.x', attribute: 'battery', state_map: { '1': true } }, CATALOG).read.state_map).toEqual({ '1': true })
  })

  it('applies the profile transitional and fault states only to the value the profile reads', () => {
    expect(resolveHaMapping({ entity_id: 'lock.x', profile: 'lock.boolean_locked', attribute: 'battery' }, CATALOG).read)
      .toMatchObject({ transitional_states: [], fault_states: [] })
    const bare = [{ id: 'x.raw', domains: ['x'], attribute: null }]
    expect(resolveHaMapping({ entity_id: 'x.a', profile: 'x.raw' }, bare).read).toMatchObject({ transitional_states: [], fault_states: [] })
  })

  it('treats empty maps as unset, like the backend', () => {
    const r = resolveHaMapping({ entity_id: 'lock.x', profile: 'lock.boolean_locked', state_map: {}, write_actions: {} }, CATALOG)
    expect(r.mode).toBe('profile')
    expect(r.read.state_map).not.toEqual({})
  })

  it('ignores whitespace-only legacy fields, as saving trims them', () => {
    expect(resolveHaMapping({ entity_id: 'lock.x', profile: 'lock.boolean_locked', service_name: '  ', service_domain: null }, CATALOG).mode).toBe('profile')
  })

  it('legacy fields win over everything', () => {
    for (const key of ['service_domain', 'service_name', 'service_data_key']) {
      const r = resolveHaMapping({ entity_id: 'lock.x', profile: 'lock.boolean_locked', [key]: 'x', action: { domain: 'a', name: 'b' } }, CATALOG)
      expect(r.mode).toBe('legacy')
      expect(r.write).toBeNull()
      expect(r.convention).toBe('trueLocked')
    }
  })

  it('write_actions beat action, action beats profile', () => {
    const wa = { true: { domain: 'script', name: 'turn_on' } }
    expect(resolveHaMapping({ entity_id: 'lock.x', profile: 'lock.boolean_locked', write_actions: wa, action: { domain: 'a', name: 'b' } }, CATALOG))
      .toMatchObject({ mode: 'write_actions', write: wa })
    expect(resolveHaMapping({ entity_id: 'lock.x', profile: 'lock.boolean_locked', write_actions: null, action: { domain: 'a', name: 'b' } }, CATALOG))
      .toMatchObject({ mode: 'action', write: { domain: 'a', name: 'b' } })
  })

  it('materializes profile actions with the entity domain', () => {
    const lock = resolveHaMapping({ entity_id: 'lock.x', profile: 'lock.boolean_locked' }, CATALOG)
    expect(lock.mode).toBe('profile')
    expect(lock.write.false).toEqual({ domain: 'lock', name: 'unlock', value_path: null })
    expect(lock.read).toEqual({ attribute: null, state_map: { locked: true, unlocked: false }, transitional_states: ['locking'], fault_states: ['jammed'] })
    const number = resolveHaMapping({ entity_id: 'input_number.x', profile: 'number.value' }, CATALOG)
    expect(number.write).toEqual({ domain: 'input_number', name: 'set_value', value_path: 'value' })
    const light = resolveHaMapping({ entity_id: 'light.x', profile: 'light.brightness' }, CATALOG)
    expect(light.write.domain).toBe('light')
    expect(light.read.attribute).toBe('brightness')
    expect(resolveHaMapping({ entity_id: 'sensor.x', profile: 'sensor.value' }, CATALOG)).toMatchObject({ mode: 'profile', write: null })
  })

  it('explicit inbound fields override the profile', () => {
    const r = resolveHaMapping({
      entity_id: 'lock.x', profile: 'lock.boolean_locked', attribute: 'a',
      state_map: { x: 1 }, transitional_states: ['t'], fault_states: ['f'],
    }, CATALOG)
    expect(r.read).toEqual({ attribute: 'a', state_map: { x: 1 }, transitional_states: ['t'], fault_states: ['f'] })
  })

  it('defaults without profile or catalog', () => {
    const r = resolveHaMapping({ entity_id: 'switch.x', profile: '' }, undefined)
    expect(r).toEqual({ mode: 'default', read: { attribute: null, state_map: null, transitional_states: [], fault_states: [] }, write: null, writeKind: 'none', convention: null })
  })
})

describe('writeRows', () => {
  it('handles empty, legacy, single and keyed writes', () => {
    expect(writeRows(null, 'single')).toEqual([])
    expect(writeRows({ mode: 'legacy' }, 'none')).toEqual([])
    expect(writeRows({ domain: 'cover', name: 'stop_cover' }, 'single')).toEqual([{ when: null, action: 'cover.stop_cover', valuePath: null }])
    expect(writeRows({ domain: 'cover', name: 'set_cover_position', value_path: 'position' }, 'single')[0].valuePath).toBe('position')
    expect(writeRows({ true: { domain: 'lock', name: 'lock' }, false: { domain: 'lock', name: 'unlock', value_path: 'x' } }, 'map')).toEqual([
      { when: 'true', action: 'lock.lock', valuePath: null },
      { when: 'false', action: 'lock.unlock', valuePath: 'x' },
    ])
  })

  it('keeps value keys called "name" or "mode" as map entries', () => {
    const write = { mode: { domain: 'select', name: 'a' }, name: { domain: 'select', name: 'b' } }
    expect(writeRows(write, 'map').map(r => [r.when, r.action])).toEqual([['mode', 'select.a'], ['name', 'select.b']])
  })

  it('resolves the kind with the mapping', () => {
    expect(resolveHaMapping({ entity_id: 'lock.x', profile: 'lock.boolean_locked' }, CATALOG).writeKind).toBe('map')
    expect(resolveHaMapping({ entity_id: 'input_number.x', profile: 'number.value' }, CATALOG).writeKind).toBe('single')
    expect(resolveHaMapping({ entity_id: 'x.y', action: { domain: 'a', name: 'b' } }, CATALOG).writeKind).toBe('single')
    expect(resolveHaMapping({ entity_id: 'x.y', write_actions: { mode: { domain: 'a', name: 'b' } } }, CATALOG).writeKind).toBe('map')
    expect(resolveHaMapping({ entity_id: 'sensor.x', profile: 'sensor.value' }, CATALOG).writeKind).toBe('none')
    expect(resolveHaMapping({ entity_id: 'switch.x' }, CATALOG).writeKind).toBe('none')
  })
})

describe('advanced mapping JSON', () => {
  it('serializes only set keys', () => {
    expect(advancedMappingText({ action: null, transitional_states: [], write_actions: {} })).toBe('')
    // a malformed stored value stays visible so it can be corrected
    expect(JSON.parse(advancedMappingText({ action: 'lock.lock' }))).toEqual({ action: 'lock.lock' })
    expect(JSON.parse(advancedMappingText({ state_map: { a: 1 }, fault_states: ['x'], profile: 'p' }))).toEqual({ state_map: { a: 1 }, fault_states: ['x'] })
  })

  it('parses valid input and resets missing keys', () => {
    expect(parseAdvancedMapping('  ')).toEqual({ ok: true, value: { action: null, write_actions: null, state_map: null, transitional_states: [], fault_states: [] } })
    expect(parseAdvancedMapping(undefined).ok).toBe(true)
    expect(parseAdvancedMapping('{"state_map": {"a": 1}}').value).toEqual({
      action: null, write_actions: null, state_map: { a: 1 }, transitional_states: [], fault_states: [],
    })
  })

  it.each(['{', '[]', 'null', '"x"', '{"template": "{{x}}"}'])('rejects %s', (text) => {
    expect(parseAdvancedMapping(text)).toEqual({ ok: false })
  })

  it('accepts every key in its own shape', () => {
    const text = JSON.stringify({
      action: { domain: 'lock', name: 'lock' },
      write_actions: { true: { domain: 'lock', name: 'lock' } },
      state_map: { locked: true },
      transitional_states: ['locking'],
      fault_states: ['jammed'],
    })
    expect(parseAdvancedMapping(text).ok).toBe(true)
    expect(parseAdvancedMapping('{"action": null, "write_actions": null, "state_map": null}').ok).toBe(true)
    expect(parseAdvancedMapping('{"action": {}, "write_actions": {}}').ok).toBe(true)  // {} = unset
  })

  // the form renders these values: a wrong type must be invalid input, not a render crash
  it.each([
    '{"transitional_states": "opening"}',
    '{"fault_states": [1]}',
    '{"write_actions": {"true": null}}',
    '{"write_actions": {"true": {"domain": "lock"}}}',
    '{"write_actions": []}',
    '{"action": "lock.lock"}',
    '{"action": {"domain": 1, "name": "lock"}}',
    '{"state_map": ["a"]}',
    '{"state_map": "a"}',
  ])('rejects the wrongly typed %s', (text) => {
    expect(parseAdvancedMapping(text)).toEqual({ ok: false })
  })
})

describe('suggestion lists', () => {
  const SERVICES = [
    { domain: 'cover', services: [] },
    {
      domain: 'lock',
      services: [
        { service: 'lock', name: 'Lock', fields: [{ key: 'code', name: 'Code', required: false }] },
        { service: 'open', name: null, fields: [{ key: 'code', name: null, required: true }, { key: 'force', name: 'Force', required: false }] },
      ],
    },
  ]

  it('maps entities', () => {
    expect(entitySuggestions([
      { entity_id: 'lock.shed', name: 'Shed', state: 'locked', unit: null },
      { entity_id: 'sensor.t', name: 'sensor.t', state: '21', unit: '°C' },
      { entity_id: 'x.y', name: '', state: null, unit: '' },
    ])).toEqual([
      { value: 'lock.shed', label: 'Shed', detail: 'locked' },
      { value: 'sensor.t', label: null, detail: '21 °C' },
      { value: 'x.y', label: null, detail: null },
    ])
    expect(entitySuggestions(undefined)).toEqual([])
  })

  it('lists attributes without friendly_name, filtered and sorted', () => {
    const entity = { attributes: { friendly_name: 'x', volume_level: 0.3, is_volume_muted: false } }
    expect(attributeSuggestions(entity, '').map(a => a.value)).toEqual(['is_volume_muted', 'volume_level'])
    expect(attributeSuggestions(entity, 'LEVEL')).toEqual([{ value: 'volume_level', label: null, detail: '0.3' }])
    expect(attributeSuggestions(undefined, '')).toEqual([])
    expect(attributeSuggestions(entity).length).toBe(2)
  })

  it('lists domains, services and fields', () => {
    expect(domainSuggestions(SERVICES, 'lo')).toEqual([{ value: 'lock', label: null, detail: null }])
    expect(domainSuggestions(null, '')).toEqual([])
    expect(serviceSuggestions(SERVICES, 'lock', 'Lock').map(s => s.value)).toEqual(['lock'])
    expect(serviceSuggestions(SERVICES, 'nope', '')).toEqual([])
    expect(serviceSuggestions(null, 'lock', '')).toEqual([])
    expect(fieldSuggestions(SERVICES, 'lock', 'open', '')).toEqual([
      { value: 'code', label: null, detail: '*' },
      { value: 'force', label: 'Force', detail: null },
    ])
    // unknown/empty action: the union of the domain's fields, deduplicated
    expect(fieldSuggestions(SERVICES, 'lock', '', 'co').map(f => f.value)).toEqual(['code'])
  })
})

describe('profileTypeMismatch', () => {
  const cat = [{ id: 'a', data_type: 'BOOLEAN' }, { id: 'b', data_type: 'FLOAT' }, { id: 'c', data_type: 'AUTO' }]
  it.each([
    ['a', 'BOOLEAN', null],
    ['b', 'INTEGER', null],
    ['b', 'float', null],
    ['c', 'STRING', null],
    ['x', 'STRING', null],
    ['a', 'DATE', null],
    ['a', undefined, null],
    ['a', 'INTEGER', { profile: 'BOOLEAN', dp: 'INTEGER' }],
    ['b', 'STRING', { profile: 'FLOAT', dp: 'STRING' }],
  ])('%s on %s', (id, dp, expected) => {
    expect(profileTypeMismatch(cat, id, dp)).toEqual(expected)
  })
  it('handles a missing catalog', () => {
    expect(profileTypeMismatch(null, 'a', 'INTEGER')).toBeNull()
  })
  it('only applies to the value the profile reads', () => {
    const lights = [{ id: 'l', data_type: 'INTEGER', attribute: 'brightness' }]
    expect(profileTypeMismatch(cat, 'a', 'INTEGER', 'battery')).toBeNull()
    expect(profileTypeMismatch(lights, 'l', 'BOOLEAN', 'brightness')).toEqual({ profile: 'INTEGER', dp: 'BOOLEAN' })
  })
})
