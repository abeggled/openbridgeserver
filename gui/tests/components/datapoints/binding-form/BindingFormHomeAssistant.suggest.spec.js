import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  haProfiles: vi.fn(),
  haEntities: vi.fn(),
  haServices: vi.fn(),
}))
vi.mock('@/api/client', () => ({ adapterApi: api }))

import BindingFormHomeAssistant from '@/components/datapoints/binding-form/BindingFormHomeAssistant.vue'

const BASE = { entity_id: '', attribute: '', service_domain: '', service_name: '', service_data_key: '', profile: '' }
const SHED = { entity_id: 'lock.shed', name: 'Shed', state: 'locked', unit: null, attributes: { friendly_name: 'Shed', battery: 80 } }
const SERVICES = [
  { domain: 'lock', services: [{ service: 'open', name: 'Open', fields: [{ key: 'code', name: 'Code', required: false }] }] },
  { domain: 'script', services: [] },
]

async function mk(cfg = {}, selectedInstanceId = 'ha-1') {
  const props = { cfg: { ...BASE, ...cfg }, selectedInstanceId }
  const w = mount(BindingFormHomeAssistant, { props, attachTo: document.body })
  await flushPromises()
  return { w, cfg: props.cfg }
}

async function focus(w, field) {
  await w.find(`[data-testid="config-field-${field}"]`).trigger('focus')
  await flushPromises()
  return w.findAll(`[data-testid^="config-field-${field}-option-"]`).map(o => o.text())
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset()
  api.haProfiles.mockResolvedValue({ data: [] })
  api.haEntities.mockResolvedValue({ data: { items: [SHED], total: 1, facets: {} } })
  api.haServices.mockResolvedValue({ data: SERVICES })
})

describe('BindingFormHomeAssistant — searchable fields', () => {
  it('searches entities of the selected instance and fills the entity_id', async () => {
    const { w, cfg } = await mk()
    expect(w.text()).toContain('Tippen sucht')
    const options = await focus(w, 'entity_id')
    expect(api.haEntities).toHaveBeenCalledWith('ha-1', { q: '', limit: 30 })
    expect(options[0]).toContain('lock.shed')
    await w.find('[data-testid="config-field-entity_id-option-0"]').trigger('mousedown')
    expect(cfg.entity_id).toBe('lock.shed')
  })

  it('suggests the attributes of the chosen entity, reusing the search cache', async () => {
    const { w } = await mk({ entity_id: ' Lock.Shed ' })
    expect(await focus(w, 'attribute')).toEqual([expect.stringContaining('battery')])
    // exact lookup: a substring search could fill its page with similar ids first
    expect(api.haEntities).toHaveBeenCalledWith('ha-1', { entity_id: 'lock.shed', limit: 1 })
    await focus(w, 'attribute')
    expect(api.haEntities).toHaveBeenCalledTimes(1)
  })

  it('remembers an entity HA does not know for 30 s only', async () => {
    const now = vi.spyOn(Date, 'now').mockReturnValue(1_000_000)
    try {
      api.haEntities.mockResolvedValue({ data: { items: [], total: 0, facets: {} } })
      const { w } = await mk({ entity_id: 'lock.shed' })
      expect(await focus(w, 'attribute')).toEqual([])
      await focus(w, 'attribute')
      expect(api.haEntities).toHaveBeenCalledTimes(1)
      // later HA knows it (e.g. after a restart): asked again, then found
      now.mockReturnValue(1_030_000)
      api.haEntities.mockResolvedValue({ data: { items: [SHED], total: 1, facets: {} } })
      expect(await focus(w, 'attribute')).toEqual([expect.stringContaining('battery')])
      expect(api.haEntities).toHaveBeenCalledTimes(2)
    } finally {
      now.mockRestore()
    }
  })

  it('does not reuse attributes of another instance', async () => {
    const { w } = await mk({ entity_id: 'lock.shed' })
    await focus(w, 'attribute')
    api.haEntities.mockResolvedValue({ data: { items: [{ ...SHED, attributes: { other: 1 } }], total: 1, facets: {} } })
    await w.setProps({ selectedInstanceId: 'ha-2' })
    expect(await focus(w, 'attribute')).toEqual([expect.stringContaining('other')])
    expect(api.haEntities).toHaveBeenLastCalledWith('ha-2', { entity_id: 'lock.shed', limit: 1 })
  })

  it('files a search answer under the instance it asked, even after a switch', async () => {
    let answerA
    api.haEntities
      .mockImplementationOnce(() => new Promise((resolve) => { answerA = resolve }))  // ha-1 is slow
      .mockResolvedValue({ data: { items: [{ ...SHED, attributes: { only_on_b: 1 } }], total: 1, facets: {} } })
    const { w } = await mk({ entity_id: 'lock.shed' }, 'ha-1')
    await w.find('[data-testid="config-field-entity_id"]').trigger('focus')
    await flushPromises()
    await w.setProps({ selectedInstanceId: 'ha-2' })  // switched before ha-1 answers
    answerA({ data: { items: [SHED], total: 1, facets: {} } })
    await flushPromises()
    expect(await focus(w, 'attribute')).toEqual([expect.stringContaining('only_on_b')])
    expect(api.haEntities).toHaveBeenLastCalledWith('ha-2', { entity_id: 'lock.shed', limit: 1 })
  })

  it('offers no attributes without an entity', async () => {
    const { w } = await mk()
    expect(await focus(w, 'attribute')).toEqual([])
    const unset = await mk({ entity_id: null })
    expect(await focus(unset.w, 'attribute')).toEqual([])
    expect(api.haEntities).not.toHaveBeenCalled()
  })

  it('suggests domains, actions and data fields from one services load', async () => {
    const { w } = await mk({ entity_id: 'lock.shed', service_name: 'open' })
    expect(await focus(w, 'service_domain')).toEqual(['lock', 'script'])
    expect(await focus(w, 'service_name')).toEqual([expect.stringContaining('open')])
    expect(await focus(w, 'service_data_key')).toEqual([expect.stringContaining('code')])
    expect(api.haServices).toHaveBeenCalledTimes(1)
  })

  it('uses the explicit service domain', async () => {
    const { w } = await mk({ entity_id: 'lock.shed', service_domain: 'script' })
    expect(await focus(w, 'service_name')).toEqual([])
  })

  it('retries a failed services load', async () => {
    api.haServices.mockRejectedValueOnce(new Error('offline'))
    const { w } = await mk({ entity_id: 'lock.shed' })
    expect(await focus(w, 'service_name')).toEqual([])
    expect(await focus(w, 'service_name')).toEqual([expect.stringContaining('open')])
    expect(api.haServices).toHaveBeenCalledTimes(2)
  })

  it('stays free text without an instance', async () => {
    const { w, cfg } = await mk({}, '')
    expect(await focus(w, 'entity_id')).toEqual([])
    expect(await focus(w, 'service_domain')).toEqual([])
    expect(api.haEntities).not.toHaveBeenCalled()
    expect(api.haServices).not.toHaveBeenCalled()
    await w.find('[data-testid="config-field-entity_id"]').setValue('sensor.custom')
    expect(cfg.entity_id).toBe('sensor.custom')
  })
})
