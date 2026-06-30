import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

let anwesenheitDpMock  = vi.fn()
let syncBindingsMock   = vi.fn()

beforeEach(() => {
  vi.resetModules()
  anwesenheitDpMock  = vi.fn().mockResolvedValue({ data: [] })
  syncBindingsMock   = vi.fn().mockResolvedValue({ data: { created: 0, removed: 0, errors: [] } })
  vi.doMock('@/api/client', () => ({
    adapterApi: {
      anwesenheitDatapoints:   anwesenheitDpMock,
      anwesenheitSyncBindings: syncBindingsMock,
    },
  }))
})

async function mountSelector(instanceId = 'inst-1') {
  const { default: AnwesenheitDatapointSelector } = await import('@/components/adapters/AnwesenheitDatapointSelector.vue')
  return mount(AnwesenheitDatapointSelector, {
    props: { instanceId },
  })
}

const ITEMS = [
  { id: 'dp-1', name: 'Licht Wohnzimmer', data_type: 'BOOLEAN', has_binding: true  },
  { id: 'dp-2', name: 'Temperatur Bad',    data_type: 'FLOAT',   has_binding: false },
  { id: 'dp-3', name: 'Rollladen EG',     data_type: 'INTEGER',  has_binding: true  },
]

describe('AnwesenheitDatapointSelector — mount & load', () => {
  it('calls anwesenheitDatapoints on mount', async () => {
    await mountSelector()
    await flushPromises()
    expect(anwesenheitDpMock).toHaveBeenCalledWith('inst-1')
  })

  it('shows no-objects message when items is empty', async () => {
    const w = await mountSelector()
    await flushPromises()
    expect(w.text()).toContain('Keine') // German "no objects" — case sensitive
  })

  it('shows item list when data loaded', async () => {
    anwesenheitDpMock.mockResolvedValue({ data: ITEMS })
    const w = await mountSelector()
    await flushPromises()
    expect(w.text()).toContain('Licht Wohnzimmer')
    expect(w.text()).toContain('Temperatur Bad')
  })

  it('pre-selects items that have_binding=true', async () => {
    anwesenheitDpMock.mockResolvedValue({ data: ITEMS })
    const w = await mountSelector()
    await flushPromises()
    const checkboxes = w.findAll('input[type="checkbox"]').slice(1) // skip "select all"
    expect(checkboxes[0].element.checked).toBe(true)  // dp-1
    expect(checkboxes[1].element.checked).toBe(false) // dp-2
    expect(checkboxes[2].element.checked).toBe(true)  // dp-3
  })

  it('shows error message on API failure', async () => {
    anwesenheitDpMock.mockRejectedValue({ response: { data: { detail: 'Verbindung fehlgeschlagen' } } })
    const w = await mountSelector()
    await flushPromises()
    expect(w.text()).toContain('Verbindung fehlgeschlagen')
  })
})

describe('AnwesenheitDatapointSelector — selection', () => {
  it('toggling a checkbox adds/removes from selection', async () => {
    anwesenheitDpMock.mockResolvedValue({ data: ITEMS })
    const w = await mountSelector()
    await flushPromises()
    const checkboxes = w.findAll('input[type="checkbox"]').slice(1) // skip "select all"
    // dp-2 is unselected — check it
    await checkboxes[1].setChecked(true)
    await checkboxes[1].trigger('change')
    // save button should now be enabled (isDirty)
    const saveBtn = w.findAll('button').find(b => b.text().includes('Speichern') || b.text().includes('speichern') || b.text().includes('Bindungen'))
    expect(saveBtn?.attributes('disabled')).toBeUndefined()
  })

  it('select-all checkbox selects all filtered items', async () => {
    anwesenheitDpMock.mockResolvedValue({ data: ITEMS })
    const w = await mountSelector()
    await flushPromises()
    const selectAll = w.findAll('input[type="checkbox"]')[0]
    await selectAll.setChecked(true)
    await selectAll.trigger('change')
    const itemCheckboxes = w.findAll('input[type="checkbox"]').slice(1)
    expect(itemCheckboxes.every(cb => cb.element.checked)).toBe(true)
  })
})

describe('AnwesenheitDatapointSelector — search filter', () => {
  it('search input filters visible items', async () => {
    anwesenheitDpMock.mockResolvedValue({ data: ITEMS })
    const w = await mountSelector()
    await flushPromises()
    const searchInput = w.find('input[type="text"]')
    await searchInput.setValue('Licht')
    await searchInput.trigger('input')
    const labels = w.findAll('label').filter(l => l.find('input[type="checkbox"]').exists())
    // Only the "select all" label and 1 item label should remain
    expect(labels.length).toBe(2) // select-all + 1 item
  })
})

describe('AnwesenheitDatapointSelector — save', () => {
  it('calls syncBindings with selected ids on save', async () => {
    anwesenheitDpMock.mockResolvedValue({ data: ITEMS })
    const w = await mountSelector()
    await flushPromises()
    // Change selection to make it dirty (deselect dp-3)
    const checkboxes = w.findAll('input[type="checkbox"]').slice(1)
    await checkboxes[2].setChecked(false)
    await checkboxes[2].trigger('change')
    const saveBtn = w.findAll('button').find(b => !b.text().includes('Neu laden') && !b.text().includes('Aktualisieren') && b.attributes('disabled') === undefined && b.text().trim() !== '')
    await saveBtn.trigger('click')
    await flushPromises()
    expect(syncBindingsMock).toHaveBeenCalledWith('inst-1', expect.any(Array))
  })

  it('refresh button calls load again', async () => {
    const w = await mountSelector()
    await flushPromises()
    expect(anwesenheitDpMock).toHaveBeenCalledTimes(1)
    const refreshBtn = w.findAll('button').find(b => b.attributes('disabled') !== 'true' && b.attributes('disabled') === undefined)
    await refreshBtn.trigger('click')
    await flushPromises()
    expect(anwesenheitDpMock).toHaveBeenCalledTimes(2)
  })
})
