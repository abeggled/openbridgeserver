import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi:       { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:   { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
    authApi:     { login: vi.fn(), me: vi.fn() },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

async function mountIcal(data = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: {
        id: 'ical1',
        type: 'ical',
        data: { url: '', refresh_interval_min: 60, filters: '[]', ...data },
      },
      nodeTypes: [{ type: 'ical', label: 'iCalendar', description: 'iCal subscription' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

// ─── URL field ───────────────────────────────────────────────────────────────

describe('NodeConfigPanel ical — URL field', () => {
  it('shows iCal-URL label', async () => {
    const w = await mountIcal()
    await flushPromises()
    expect(w.text()).toContain('iCal-URL')
    w.unmount()
  })

  it('changing the URL emits update', async () => {
    const w = await mountIcal()
    await flushPromises()

    const urlInput = w.find('[data-testid="ical-url"]')
    await urlInput.setValue('https://calendar.example.com/cal.ics')
    await urlInput.trigger('change')
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates).toBeTruthy()
    expect(updates.at(-1)[0].url).toBe('https://calendar.example.com/cal.ics')
    w.unmount()
  })

  it('changing the refresh interval emits update', async () => {
    const w = await mountIcal()
    await flushPromises()

    const refreshInput = w.find('[data-testid="ical-refresh"]')
    await refreshInput.setValue('30')
    await refreshInput.trigger('change')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0].refresh_interval_min).toBe(30)
    w.unmount()
  })
})

// ─── Empty filter state ───────────────────────────────────────────────────────

describe('NodeConfigPanel ical — empty filters', () => {
  it('shows no-filters message when filter list is empty', async () => {
    const w = await mountIcal({ filters: '[]' })
    await flushPromises()
    expect(w.text()).toContain('Noch keine Filter')
    w.unmount()
  })

  it('shows the add-filter button', async () => {
    const w = await mountIcal()
    await flushPromises()
    const addBtn = w.find('[data-testid="ical-add-filter"]')
    expect(addBtn.exists()).toBe(true)
    expect(addBtn.text()).toContain('Filter hinzufügen')
    w.unmount()
  })
})

// ─── Add filter ───────────────────────────────────────────────────────────────

describe('NodeConfigPanel ical — add filter', () => {
  it('clicking add-filter adds a new filter row', async () => {
    const w = await mountIcal()
    await flushPromises()

    await w.find('[data-testid="ical-add-filter"]').trigger('click')
    await flushPromises()

    // Filter 1 should now appear
    expect(w.text()).toContain('Filter 1')
    expect(w.find('[data-testid="ical-filter-name-0"]').exists()).toBe(true)
    w.unmount()
  })

  it('adding a filter emits update with filters JSON containing one filter', async () => {
    const w = await mountIcal()
    await flushPromises()

    await w.find('[data-testid="ical-add-filter"]').trigger('click')
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates).toBeTruthy()
    const parsed = JSON.parse(updates.at(-1)[0].filters)
    expect(parsed).toHaveLength(1)
    expect(parsed[0].field_logic).toBe('or')
    w.unmount()
  })

  it('adding two filters shows Filter 1 and Filter 2', async () => {
    const w = await mountIcal()
    await flushPromises()

    await w.find('[data-testid="ical-add-filter"]').trigger('click')
    await flushPromises()
    await w.find('[data-testid="ical-add-filter"]').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('Filter 1')
    expect(w.text()).toContain('Filter 2')
    w.unmount()
  })
})

// ─── Update filter ────────────────────────────────────────────────────────────

describe('NodeConfigPanel ical — update filter', () => {
  it('typing in filter name emits update with new name', async () => {
    const w = await mountIcal()
    await flushPromises()

    await w.find('[data-testid="ical-add-filter"]').trigger('click')
    await flushPromises()

    const nameInput = w.find('[data-testid="ical-filter-name-0"]')
    await nameInput.setValue('Urlaub')
    await nameInput.trigger('input')
    // icalUpdateFilter is called @input which calls _icalSave → emitUpdate
    await nameInput.trigger('change')
    await flushPromises()

    const updates = w.emitted('update')
    const parsed = JSON.parse(updates.at(-1)[0].filters)
    expect(parsed[0].name).toBe('Urlaub')
    w.unmount()
  })

  it('clicking AND button sets field_logic to "and" and emits update', async () => {
    const w = await mountIcal()
    await flushPromises()

    await w.find('[data-testid="ical-add-filter"]').trigger('click')
    await flushPromises()

    await w.find('[data-testid="ical-filter-logic-and-0"]').trigger('click')
    await flushPromises()

    const parsed = JSON.parse(w.emitted('update').at(-1)[0].filters)
    expect(parsed[0].field_logic).toBe('and')
    w.unmount()
  })

  it('clicking OR button sets field_logic to "or" and emits update', async () => {
    // Start with AND logic
    const filters = JSON.stringify([{
      name: 'F1', field_logic: 'and',
      summary_pattern: '', location_pattern: '', description_pattern: '', case_sensitive: false,
    }])
    const w = await mountIcal({ filters })
    await flushPromises()

    await w.find('[data-testid="ical-filter-logic-or-0"]').trigger('click')
    await flushPromises()

    const parsed = JSON.parse(w.emitted('update').at(-1)[0].filters)
    expect(parsed[0].field_logic).toBe('or')
    w.unmount()
  })

  it('setting summary_pattern emits update with the pattern', async () => {
    const w = await mountIcal()
    await flushPromises()

    await w.find('[data-testid="ical-add-filter"]').trigger('click')
    await flushPromises()

    const summaryInput = w.find('[data-testid="ical-filter-summary_pattern-0"]')
    await summaryInput.setValue('Meeting.*')
    await summaryInput.trigger('input')
    await summaryInput.trigger('change')
    await flushPromises()

    const parsed = JSON.parse(w.emitted('update').at(-1)[0].filters)
    expect(parsed[0].summary_pattern).toBe('Meeting.*')
    w.unmount()
  })
})

// ─── Remove filter ────────────────────────────────────────────────────────────

describe('NodeConfigPanel ical — remove filter', () => {
  it('clicking remove button removes the filter and emits update', async () => {
    const w = await mountIcal()
    await flushPromises()

    await w.find('[data-testid="ical-add-filter"]').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('Filter 1')

    await w.find('[data-testid="ical-remove-filter-0"]').trigger('click')
    await flushPromises()

    expect(w.text()).toContain('Noch keine Filter')
    const updates = w.emitted('update')
    const parsed = JSON.parse(updates.at(-1)[0].filters)
    expect(parsed).toHaveLength(0)
    w.unmount()
  })

  it('removing the first filter when two exist keeps the second', async () => {
    const filters = JSON.stringify([
      { name: 'Alpha', field_logic: 'or', summary_pattern: '', location_pattern: '', description_pattern: '', case_sensitive: false },
      { name: 'Beta',  field_logic: 'or', summary_pattern: '', location_pattern: '', description_pattern: '', case_sensitive: false },
    ])
    const w = await mountIcal({ filters })
    await flushPromises()

    await w.find('[data-testid="ical-remove-filter-0"]').trigger('click')
    await flushPromises()

    const parsed = JSON.parse(w.emitted('update').at(-1)[0].filters)
    expect(parsed).toHaveLength(1)
    expect(parsed[0].name).toBe('Beta')
    w.unmount()
  })
})

// ─── Pre-existing filters ─────────────────────────────────────────────────────

describe('NodeConfigPanel ical — initial filters from data', () => {
  it('renders existing filters from node data', async () => {
    const filters = JSON.stringify([
      { name: 'Urlaub', field_logic: 'or', summary_pattern: 'Urlaub', location_pattern: '', description_pattern: '', case_sensitive: false },
    ])
    const w = await mountIcal({ filters })
    await flushPromises()

    expect(w.text()).toContain('Filter 1')
    const nameInput = w.find('[data-testid="ical-filter-name-0"]')
    expect(nameInput.element.value).toBe('Urlaub')
    w.unmount()
  })
})
