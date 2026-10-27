import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const haProfiles = vi.fn()
vi.mock('@/api/client', () => ({ adapterApi: { haProfiles: (...a) => haProfiles(...a) } }))

import BindingFormHomeAssistant from '@/components/datapoints/binding-form/BindingFormHomeAssistant.vue'

const CATALOG = [
  {
    id: 'lock.boolean_locked', domains: ['lock'], data_type: 'BOOLEAN', attribute: null,
    state_map: { locked: true, unlocked: false }, transitional_states: ['locking'], fault_states: ['jammed'],
    action: null, write_actions: { true: { domain: null, name: 'lock', value_path: null }, false: { domain: null, name: 'unlock', value_path: null } },
    convention: 'trueLocked',
  },
  {
    id: 'cover.position', domains: ['cover'], attribute: 'current_position', state_map: null,
    transitional_states: [], fault_states: [], action: { domain: null, name: 'set_cover_position', value_path: 'position' }, write_actions: null, convention: null,
  },
  {
    id: 'sensor.value', domains: ['sensor'], attribute: null, state_map: null,
    transitional_states: [], fault_states: [], action: null, write_actions: null, convention: null,
  },
]

const BASE = { entity_id: '', attribute: '', service_domain: '', service_name: '', service_data_key: '', profile: '' }

async function mk(cfg = {}) {
  const props = { cfg: { ...BASE, ...cfg } }
  const w = mount(BindingFormHomeAssistant, { props })
  await flushPromises()
  return { w, cfg: props.cfg }
}

beforeEach(() => {
  haProfiles.mockReset()
  haProfiles.mockResolvedValue({ data: CATALOG })
})

describe('BindingFormHomeAssistant — profiles (#1392)', () => {
  it('offers the profiles of the entity domain and shows the resolved lock mapping', async () => {
    const { w, cfg } = await mk({ entity_id: 'lock.shed' })
    const options = w.findAll('[data-testid="config-field-profile"] option').map(o => o.attributes('value'))
    expect(options).toEqual(['', 'lock.boolean_locked'])
    await w.find('[data-testid="config-field-profile"]').setValue('lock.boolean_locked')
    expect(cfg.profile).toBe('lock.boolean_locked')
    expect(w.find('[data-testid="ha-convention"]').exists()).toBe(true)
    const rows = w.findAll('[data-testid="ha-write-row"]')
    expect(rows).toHaveLength(2)
    expect(rows[0].text()).toContain('lock.lock')
    expect(w.text()).toContain('locking')
    expect(w.text()).toContain('jammed')
    expect(w.text()).toContain('"locked":true')
  })

  it('warns when the profile type does not fit the DataPoint', async () => {
    const props = { cfg: { ...BASE, entity_id: 'lock.shed', profile: 'lock.boolean_locked' }, dpDataType: 'FLOAT' }
    const w = mount(BindingFormHomeAssistant, { props })
    await flushPromises()
    expect(w.find('[data-testid="ha-type-mismatch"]').text()).toContain('BOOLEAN')
    await w.setProps({ dpDataType: 'BOOLEAN' })
    expect(w.find('[data-testid="ha-type-mismatch"]').exists()).toBe(false)
  })

  it('shows the value path of a single action', async () => {
    const { w } = await mk({ entity_id: 'cover.roof', profile: 'cover.position' })
    const row = w.find('[data-testid="ha-write-row"]')
    expect(row.text()).toContain('cover.set_cover_position')
    expect(row.text()).toContain('position')
    expect(w.find('[data-testid="ha-convention"]').exists()).toBe(false)
  })

  it('read-only profile shows no action', async () => {
    const { w } = await mk({ entity_id: 'sensor.t', profile: 'sensor.value' })
    expect(w.findAll('[data-testid="ha-write-row"]')).toHaveLength(0)
    expect(w.find('[data-testid="ha-write-legacy"]').exists()).toBe(false)
  })

  it('warns when legacy fields override a profile', async () => {
    const { w } = await mk({ entity_id: 'lock.shed', profile: 'lock.boolean_locked', service_name: 'lock' })
    expect(w.find('[data-testid="ha-legacy-overrides"]').exists()).toBe(true)
    expect(w.find('[data-testid="ha-write-legacy"]').exists()).toBe(true)
  })

  it('legacy fields without profile show no override warning', async () => {
    const { w } = await mk({ entity_id: 'lock.shed', service_name: 'lock' })
    expect(w.find('[data-testid="ha-legacy-overrides"]').exists()).toBe(false)
  })

  it('keeps a stored profile that does not fit the current entity selectable', async () => {
    const { w } = await mk({ entity_id: 'switch.x', profile: 'lock.boolean_locked' })
    const options = w.findAll('[data-testid="config-field-profile"] option').map(o => o.attributes('value'))
    expect(options).toEqual(['', 'lock.boolean_locked'])
  })

  it('works without a profile catalog', async () => {
    haProfiles.mockRejectedValue(new Error('offline'))
    const { w } = await mk({ entity_id: 'lock.shed' })
    expect(w.findAll('[data-testid="config-field-profile"] option')).toHaveLength(1)
    expect(w.find('[data-testid="ha-write-legacy"]').exists()).toBe(true)
  })

  it('drops a profile that does not fit a newly chosen entity domain', async () => {
    const { w, cfg } = await mk({ entity_id: 'lock.shed', profile: 'lock.boolean_locked' })
    await w.find('[data-testid="config-field-entity_id"]').setValue('lock.door')
    expect(cfg.profile).toBe('lock.boolean_locked')  // same domain: kept
    await w.find('[data-testid="config-field-entity_id"]').setValue('cover.roof')
    expect(cfg.profile).toBe('')
    cfg.profile = 'unknown.profile'
    await w.find('[data-testid="config-field-entity_id"]').setValue('switch.x')
    expect(cfg.profile).toBe('unknown.profile')  // not in the catalog: left to the backend
  })

  it('offers the read type as a dropdown', async () => {
    const { w, cfg } = await mk({ entity_id: 'sensor.t', read_as: '' })
    const options = w.findAll('[data-testid="config-field-read_as"] option').map(o => o.attributes('value'))
    expect(options).toEqual(['', 'auto', 'boolean', 'number', 'string'])
    await w.find('[data-testid="config-field-read_as"]').setValue('number')
    expect(cfg.read_as).toBe('number')
  })

  it('applies valid advanced JSON on blur and rejects invalid input', async () => {
    const { w, cfg } = await mk({ entity_id: 'lock.shed', state_map: { locked: 1 } })
    const area = w.find('[data-testid="config-field-ha-advanced"]')
    expect(JSON.parse(area.element.value)).toEqual({ state_map: { locked: 1 } })
    await area.setValue('{"write_actions": {"true": {"domain": "lock", "name": "open"}}}')
    await area.trigger('blur')
    expect(cfg.write_actions).toEqual({ true: { domain: 'lock', name: 'open' } })
    expect(cfg.state_map).toBeNull()
    expect(w.find('[data-testid="ha-advanced-invalid"]').exists()).toBe(false)
    expect(w.find('[data-testid="ha-write-row"]').text()).toContain('lock.open')
    await area.setValue('{oops')
    await area.trigger('blur')
    expect(w.find('[data-testid="ha-advanced-invalid"]').exists()).toBe(true)
    expect(w.emitted('advanced-validity')).toEqual([[true], [false]])
    expect(cfg.write_actions).toEqual({ true: { domain: 'lock', name: 'open' } })
  })
})
