import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const api = vi.hoisted(() => ({
  haEntities: vi.fn(),
  haImportPreview: vi.fn(),
  haImport: vi.fn(),
}))
vi.mock('@/api/client', () => ({ adapterApi: api }))

import HomeAssistantImport from '@/components/adapters/HomeAssistantImport.vue'

const LOCK = {
  entity_id: 'lock.shed', name: 'Shed', domain: 'lock', state: 'locked', unit: null, available: true,
  attributes: { friendly_name: 'Shed', supported_features: 1, battery: 80 }, area: 'Garden', device: 'Nuki', platform: 'nuki', device_class: null,
  profiles: [{ id: 'lock.boolean_locked', supported: true, reason: null }, { id: 'lock.open', supported: false, reason: 'featureMissing' }],
  default_profile: 'lock.boolean_locked',
}
const CLIMATE = {
  entity_id: 'climate.living', name: 'Living', domain: 'climate', state: 'heat', unit: '°C', available: false,
  attributes: {}, area: null, device: null, platform: null, device_class: null, profiles: [], default_profile: null,
}

function page(items = [LOCK, CLIMATE], extra = {}) {
  return { data: { total: items.length, items, facets: { domain: ['climate', 'lock'], area: ['Garden'], platform: ['nuki'], device: ['Nuki'] }, metadata_available: true, ...extra } }
}

const NEW_LOCK = {
  entity_id: 'lock.shed', attribute: null, profile: 'lock.boolean_locked', name: 'Shed', data_type: 'BOOLEAN', unit: null,
  direction: 'BOTH', tags: ['homeassistant', 'lock'], config: {}, convention: 'trueLocked', status: 'new', reason: null,
  mapping: {
    mode: 'profile',
    read: { attribute: null, state_map: { locked: true }, transitional_states: [], fault_states: [] },
    write: { true: { domain: 'lock', name: 'lock' }, false: { domain: 'lock', name: 'unlock' } },
  },
}
const ATTR = {
  ...NEW_LOCK, attribute: 'battery', profile: null, name: 'Shed (battery)', data_type: 'INTEGER', unit: '%', direction: 'SOURCE', convention: null,
  mapping: { mode: 'default', read: { attribute: 'battery', state_map: null, transitional_states: [], fault_states: [] }, write: { mode: 'default' } },
}
const UNSUPPORTED = { ...ATTR, entity_id: 'climate.living', status: 'unsupported', reason: 'writeNeedsProfile', mapping: null, tags: [] }

async function mk() {
  const w = mount(HomeAssistantImport, { props: { instance: { id: 'ha-1', name: 'HA' } } })
  await flushPromises()
  return w
}

async function loaded(items) {
  api.haEntities.mockResolvedValue(page(items))
  const w = await mk()
  await w.find('[data-testid="ha-load"]').trigger('click')
  await flushPromises()
  return w
}

beforeEach(() => {
  for (const fn of Object.values(api)) fn.mockReset()
})

describe('HomeAssistantImport', () => {
  it('loads entities with filters and shows hits', async () => {
    const w = await loaded()
    expect(api.haEntities).toHaveBeenCalledWith('ha-1', { q: '', domain: '', area: '', platform: '', device: '', availability: '', offset: 0, limit: 50 })
    expect(w.find('[data-testid="ha-hits"]').exists()).toBe(true)
    expect(w.find('[data-testid="ha-no-metadata"]').exists()).toBe(false)
    expect(w.find('[data-testid="ha-entity-lock.shed"]').text()).toContain('Garden · Nuki · nuki')
    await w.find('[data-testid="ha-filter-domain"]').setValue('lock')
    await flushPromises()
    expect(api.haEntities).toHaveBeenLastCalledWith('ha-1', expect.objectContaining({ domain: 'lock', offset: 0 }))
    await w.find('[data-testid="ha-filter-availability"]').setValue('available')
    await w.find('[data-testid="ha-filter-q"]').setValue('shed')
    await w.find('[data-testid="ha-filter-q"]').trigger('keyup.enter')
    await flushPromises()
    expect(api.haEntities).toHaveBeenLastCalledWith('ha-1', expect.objectContaining({ q: 'shed', availability: 'available' }))
  })

  it('reports a missing registry and an empty result', async () => {
    api.haEntities.mockResolvedValue(page([], { metadata_available: false }))
    const w = await mk()
    await w.find('[data-testid="ha-load"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="ha-no-metadata"]').exists()).toBe(true)
    expect(w.find('[data-testid="ha-empty"]').exists()).toBe(true)
  })

  it.each([
    [{ response: { data: { detail: 'Home-Assistant-Instanz ist nicht verbunden' } } }, 'nicht verbunden'],
    [new Error('net'), 'Entities konnten nicht geladen werden'],
  ])('shows load errors', async (error, text) => {
    api.haEntities.mockRejectedValue(error)
    const w = await mk()
    await w.find('[data-testid="ha-load"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="ha-error"]').text()).toContain(text)
    expect(w.find('[data-testid="ha-empty"]').exists()).toBe(false)
  })

  it('pages through results', async () => {
    const many = Array.from({ length: 50 }, (_, i) => ({ ...CLIMATE, entity_id: `sensor.s${i}` }))
    api.haEntities.mockResolvedValue({ data: { ...page(many).data, total: 120 } })
    const w = await mk()
    await w.find('[data-testid="ha-load"]').trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="ha-prev"]').attributes('disabled')).toBeDefined()
    await w.find('[data-testid="ha-next"]').trigger('click')
    await flushPromises()
    expect(api.haEntities).toHaveBeenLastCalledWith('ha-1', expect.objectContaining({ offset: 50 }))
    await w.find('[data-testid="ha-prev"]').trigger('click')
    await flushPromises()
    expect(api.haEntities).toHaveBeenLastCalledWith('ha-1', expect.objectContaining({ offset: 0 }))
  })

  it('selects default, plain state, profiles and attributes, then previews and imports', async () => {
    const w = await loaded()
    expect(w.find('[data-testid="ha-preview"]').attributes('disabled')).toBeDefined()
    await w.find('[data-testid="ha-select-lock.shed"]').setValue(true)
    await w.find('[data-testid="ha-select-climate.living"]').setValue(true)
    await w.find('[data-testid="ha-expand-lock.shed"]').trigger('click')
    expect(w.find('[data-testid="ha-profile-lock.shed-lock.open"]').attributes('disabled')).toBeDefined()
    expect(w.find('[data-testid="ha-entity-lock.shed"]').text()).toContain('Entity unterstützt diese Funktion nicht')
    expect(w.find('[data-testid="ha-attr-lock.shed-supported_features"]').exists()).toBe(false)
    await w.find('[data-testid="ha-attr-lock.shed-battery"]').setValue(true)
    await w.find('[data-testid="ha-plain-lock.shed"]').setValue(true)
    await w.find('[data-testid="ha-plain-lock.shed"]').setValue(false)
    expect(w.find('[data-testid="ha-profile-lock.shed-lock.boolean_locked"]').element.checked).toBe(true)
    await w.find('[data-testid="ha-expand-lock.shed"]').trigger('click')
    expect(w.find('[data-testid="ha-attr-lock.shed-battery"]').exists()).toBe(false)

    await w.find('[data-testid="ha-tags"]').setValue(' garden, ,ha ')
    api.haImportPreview.mockResolvedValue({ data: { preview: [NEW_LOCK, ATTR, UNSUPPORTED] } })
    await w.find('[data-testid="ha-preview"]').trigger('click')
    await flushPromises()
    expect(api.haImportPreview).toHaveBeenCalledWith('ha-1', {
      selections: [
        { entity_id: 'lock.shed', attribute: null, profile: 'lock.boolean_locked' },
        { entity_id: 'climate.living', attribute: null, profile: null },
        { entity_id: 'lock.shed', attribute: 'battery', profile: 'auto' },
      ],
      tags: ['garden', 'ha'],
      persist_value: true,
      record_history: true,
    })
    const plan = w.find('[data-testid="ha-plan"]').text()
    expect(plan).toContain('lock.lock')
    expect(plan).toContain('true = verriegelt')
    expect(plan).toContain('Schreiben benötigt ein Profil')
    expect(w.find('[data-testid="ha-plan-1"]').text()).toContain('battery')

    api.haImport.mockResolvedValue({ data: { created_datapoints: 2, created_bindings: 2, skipped_existing: 0, skipped_conflicts: 0, skipped_unsupported: 1, errors: ['x: boom'], preview: [] } })
    api.haImportPreview.mockResolvedValue({ data: { preview: [{ ...NEW_LOCK, status: 'exists', reason: 'bindingExists' }] } })
    await w.find('[data-testid="ha-import-btn"]').trigger('click')
    await flushPromises()
    expect(api.haImport).toHaveBeenCalledTimes(1)
    expect(w.find('[data-testid="ha-result"]').text()).toContain('x: boom')
    expect(w.find('[data-testid="ha-import-btn"]').attributes('disabled')).toBeDefined()

    // changing the selection invalidates the plan
    await w.find('[data-testid="ha-select-climate.living"]').setValue(false)
    expect(w.find('[data-testid="ha-plan"]').exists()).toBe(false)
  })

  it.each([
    ['haImportPreview', 'ha-preview', 'Vorschau konnte nicht erstellt werden'],
    ['haImport', 'ha-import-btn', 'Import fehlgeschlagen'],
  ])('shows %s failures', async (fn, button, key) => {
    const w = await loaded()
    await w.find('[data-testid="ha-select-lock.shed"]').setValue(true)
    api.haImportPreview.mockResolvedValue({ data: { preview: [NEW_LOCK] } })
    await w.find('[data-testid="ha-preview"]').trigger('click')
    await flushPromises()
    api[fn].mockRejectedValue(new Error('x'))
    await w.find(`[data-testid="${button}"]`).trigger('click')
    await flushPromises()
    expect(w.find('[data-testid="ha-error"]').text()).toContain(key)
  })

  it('covers profile toggles, options, single actions and informational reasons', async () => {
    const bare = { ...CLIMATE, entity_id: 'scene.x', attributes: undefined, profiles: [{ id: 'scene.activate', supported: true, reason: null }], default_profile: 'scene.activate' }
    const w = await loaded([LOCK, bare])
    await w.find('[data-testid="ha-expand-scene.x"]').trigger('click')
    expect(w.find('[data-testid="ha-entity-scene.x"]').text()).not.toContain('Attribute')
    await w.find('[data-testid="ha-expand-lock.shed"]').trigger('click')
    const profile = w.find('[data-testid="ha-profile-lock.shed-lock.boolean_locked"]')
    await profile.setValue(true)
    await profile.setValue(false)
    expect(w.find('[data-testid="ha-select-lock.shed"]').element.checked).toBe(false)
    await profile.setValue(true)
    const [persist, history] = w.findAll('input[type="checkbox"]').slice(-2)
    await persist.setValue(false)
    await history.setValue(false)
    const position = {
      ...NEW_LOCK, reason: 'ambiguousReadOnly',
      mapping: { mode: 'profile', read: { attribute: 'current_position', state_map: null, transitional_states: [], fault_states: [] }, write: { domain: 'cover', name: 'set_cover_position', value_path: 'position' } },
    }
    api.haImportPreview.mockResolvedValue({ data: { preview: [position] } })
    await w.find('[data-testid="ha-preview"]').trigger('click')
    await flushPromises()
    expect(api.haImportPreview.mock.calls[0][1]).toMatchObject({ persist_value: false, record_history: false })
    const row = w.find('[data-testid="ha-plan-0"]').text()
    expect(row).toContain('cover.set_cover_position')
    expect(row).toContain('position')
    expect(row).toContain('Mehrdeutige Domain')
  })

  it('loads on open and searches live while typing', async () => {
    api.haEntities.mockResolvedValue(page())
    const w = await mk()
    expect(api.haEntities).toHaveBeenCalledTimes(1)
    vi.useFakeTimers()
    try {
      const q = w.find('[data-testid="ha-filter-q"]')
      await q.setValue('sh')
      await q.setValue('shed')
      vi.advanceTimersByTime(299)
      expect(api.haEntities).toHaveBeenCalledTimes(1)
      vi.advanceTimersByTime(1)
      expect(api.haEntities).toHaveBeenCalledTimes(2)
      expect(api.haEntities).toHaveBeenLastCalledWith('ha-1', expect.objectContaining({ q: 'shed' }))
      await q.setValue('x')
      w.unmount()
      vi.advanceTimersByTime(500)
      expect(api.haEntities).toHaveBeenCalledTimes(2)
    } finally {
      vi.useRealTimers()
    }
  })

  it('ignores responses and errors of superseded searches', async () => {
    let resolveOld
    let rejectOld
    api.haEntities.mockResolvedValueOnce(page())  // initial load
    const w = await mk()
    api.haEntities
      .mockImplementationOnce(() => new Promise(r => { resolveOld = r }))
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectOld = reject }))
      .mockResolvedValueOnce(page([CLIMATE]))
    w.vm.load(0)
    w.vm.load(0)
    await w.vm.load(0)
    await flushPromises()
    resolveOld(page([LOCK]))
    rejectOld(new Error('late'))
    await flushPromises()
    expect(w.find('[data-testid="ha-entity-climate.living"]').exists()).toBe(true)
    expect(w.find('[data-testid="ha-entity-lock.shed"]').exists()).toBe(false)
    expect(w.find('[data-testid="ha-error"]').exists()).toBe(false)
    expect(w.find('[data-testid="ha-load"]').attributes('disabled')).toBeUndefined()
  })

  it('exposes load for the parent', async () => {
    api.haEntities.mockResolvedValue(page())
    const w = await mk()
    await w.vm.load(0)
    expect(api.haEntities).toHaveBeenCalled()
  })
})
