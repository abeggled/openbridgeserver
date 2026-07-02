import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let dpList, dpSearch

beforeEach(() => {
  vi.resetModules()
  dpList   = vi.fn().mockResolvedValue({ data: { items: [] } })
  dpSearch = vi.fn().mockResolvedValue({ data: { items: [] } })
  vi.doMock('@/api/client', () => ({
    dpApi:      { list: dpList },
    searchApi:  { search: dpSearch },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
    authApi:     { login: vi.fn(), me: vi.fn() },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

async function mountPanel(type, data) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'n1', type, data },
      nodeTypes: [{ type, label: type, description: '' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

function clickTab(wrapper, label) {
  const btn = wrapper.findAll('button').find(b => b.text().includes(label))
  return btn.trigger('click')
}

// ─── Connection tab ─────────────────────────────────────────────────────────

describe('NodeConfigPanel datapoint_read — connection tab', () => {
  it('calls dpApi.list on mount (empty search)', async () => {
    const w = await mountPanel('datapoint_read', { datapoint_name: '' })
    await flushPromises()
    expect(dpList).toHaveBeenCalledTimes(1)
    w.unmount()
  })

  it('shows dp search results and clicking one emits update with datapoint_id', async () => {
    dpList.mockResolvedValue({ data: { items: [
      { id: 'dp-1', name: 'Licht Wohnzimmer', data_type: 'BOOLEAN' },
    ] } })
    const w = await mountPanel('datapoint_read', { datapoint_name: '' })
    await flushPromises()

    const resultBtn = w.findAll('button').find(b => b.text().includes('Licht Wohnzimmer'))
    expect(resultBtn).toBeTruthy()
    await resultBtn.trigger('click')
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates).toBeTruthy()
    expect(updates.at(-1)[0].datapoint_id).toBe('dp-1')
    expect(updates.at(-1)[0].datapoint_name).toBe('Licht Wohnzimmer')
    w.unmount()
  })

  it('calls searchApi.search when search input has text', async () => {
    const w = await mountPanel('datapoint_read', { datapoint_name: '' })
    await flushPromises()

    const searchInput = w.find('input[type="text"]')
    await searchInput.setValue('Temp')
    await searchInput.trigger('input')
    await flushPromises()

    expect(dpSearch).toHaveBeenCalledWith({ q: 'Temp', size: 50 })
    w.unmount()
  })

  it('shows selected dp name after selecting', async () => {
    dpList.mockResolvedValue({ data: { items: [{ id: 'dp-2', name: 'Temperatur', data_type: 'FLOAT' }] } })
    const w = await mountPanel('datapoint_read', { datapoint_name: '' })
    await flushPromises()

    await w.findAll('button').find(b => b.text().includes('Temperatur')).trigger('click')
    await flushPromises()

    expect(w.text()).toContain('✓ Temperatur')
    w.unmount()
  })

  it('uses dpSearch.value from node.data.datapoint_name on init', async () => {
    const w = await mountPanel('datapoint_read', { datapoint_name: 'Existierender DP' })
    await flushPromises()

    const input = w.find('input[type="text"]')
    expect(input.element.value).toBe('Existierender DP')
    w.unmount()
  })
})

// ─── Transform tab ───────────────────────────────────────────────────────────

describe('NodeConfigPanel datapoint_read — transform tab', () => {
  it('switches to Transformation tab', async () => {
    const w = await mountPanel('datapoint_read', {})
    await flushPromises()
    await clickTab(w, 'Transformation')
    // The transform section is now shown (v-show)
    const formulaInput = w.find('input[placeholder="x * 100"]')
    expect(formulaInput.exists()).toBe(true)
    w.unmount()
  })

  it('selecting a formula preset emits update with value_formula', async () => {
    const w = await mountPanel('datapoint_read', {})
    await flushPromises()
    await clickTab(w, 'Transformation')

    // Find the formula preset select (first select in transform section)
    const selects = w.findAll('select')
    const presetSelect = selects.find(s => {
      const opts = s.findAll('option')
      return opts.some(o => o.element.value === 'x * 1000')
    })
    expect(presetSelect).toBeTruthy()
    await presetSelect.setValue('x * 1000')
    await presetSelect.trigger('change')
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates).toBeTruthy()
    expect(updates.at(-1)[0].value_formula).toBe('x * 1000')
    w.unmount()
  })

  it('selecting value_map preset emits update with value_map', async () => {
    const w = await mountPanel('datapoint_read', {})
    await flushPromises()
    await clickTab(w, 'Transformation')

    const vmSelect = w.find('[data-testid="value-map-preset"]')
    await vmSelect.setValue('num_invert')
    await vmSelect.trigger('change')
    await flushPromises()

    const updates = w.emitted('update')
    const last = updates.at(-1)[0]
    expect(last.value_map).toEqual({ '0': '1', '1': '0' })
    w.unmount()
  })

  it('selecting custom value_map preset shows textarea', async () => {
    const w = await mountPanel('datapoint_read', {})
    await flushPromises()
    await clickTab(w, 'Transformation')

    const vmSelect = w.find('[data-testid="value-map-preset"]')
    await vmSelect.setValue('custom')
    await vmSelect.trigger('change')
    await flushPromises()

    expect(w.find('[data-testid="value-map-custom"]').exists()).toBe(true)
    w.unmount()
  })

  it('entering invalid JSON in custom value_map shows error', async () => {
    const w = await mountPanel('datapoint_read', {})
    await flushPromises()
    await clickTab(w, 'Transformation')

    const vmSelect = w.find('[data-testid="value-map-preset"]')
    await vmSelect.setValue('custom')
    await vmSelect.trigger('change')
    await flushPromises()

    const ta = w.find('[data-testid="value-map-custom"]')
    await ta.setValue('{ not json }')
    await ta.trigger('change')
    await flushPromises()

    expect(w.text()).toContain('Ungültiges JSON')
    w.unmount()
  })

  it('restores value_map preset from node.data when matched', async () => {
    const w = await mountPanel('datapoint_read', {
      value_map: { '0': '1', '1': '0' },
    })
    await flushPromises()
    await clickTab(w, 'Transformation')

    const vmSelect = w.find('[data-testid="value-map-preset"]')
    expect(vmSelect.element.value).toBe('num_invert')
    w.unmount()
  })
})

// ─── Filter tab ───────────────────────────────────────────────────────────────

describe('NodeConfigPanel datapoint_read — filter tab', () => {
  it('switches to Filter tab', async () => {
    const w = await mountPanel('datapoint_read', {})
    await flushPromises()
    await clickTab(w, 'Filter')
    // Throttle input should be visible
    const throttleInput = w.find('input[type="number"]')
    expect(throttleInput.exists()).toBe(true)
    w.unmount()
  })

  it('changing throttle_value emits update', async () => {
    const w = await mountPanel('datapoint_read', {})
    await flushPromises()
    await clickTab(w, 'Filter')

    const throttle = w.find('input[type="number"]')
    await throttle.setValue('500')
    await throttle.trigger('change')
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates).toBeTruthy()
    expect(String(updates.at(-1)[0].throttle_value)).toBe('500')
    w.unmount()
  })

  it('toggle trigger_on_change checkbox emits update', async () => {
    const w = await mountPanel('datapoint_read', {})
    await flushPromises()
    await clickTab(w, 'Filter')

    const checkbox = w.find('input[type="checkbox"]')
    await checkbox.setChecked(true)
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates).toBeTruthy()
    expect(updates.at(-1)[0].trigger_on_change).toBe(true)
    w.unmount()
  })
})

// ─── datapoint_write tabs ─────────────────────────────────────────────────────

describe('NodeConfigPanel datapoint_write — filter tab uses only_on_change', () => {
  it('switching to filter tab shows "nur bei Änderung schreiben" text', async () => {
    const w = await mountPanel('datapoint_write', {})
    await flushPromises()
    await clickTab(w, 'Filter')
    // For write node, label mentions writing not triggering
    expect(w.text()).toMatch(/Schreib|schreib/i)
    w.unmount()
  })
})

// ─── searchDps error path (line 1646) ────────────────────────────────────────

describe('NodeConfigPanel datapoint_read — searchDps error handling', () => {
  it('clears dpResults when dpApi.list rejects', async () => {
    dpList.mockRejectedValue(new Error('network'))
    const w = await mountPanel('datapoint_read', { datapoint_name: '' })
    await flushPromises()
    // dpResults should be empty [] — no error shown, just graceful fallback
    const resultBtns = w.findAll('.flex-col.flex-1 button')
    expect(resultBtns).toHaveLength(0)
    w.unmount()
  })
})

// ─── close button ──────────────────────────────────────────────────────────────

describe('NodeConfigPanel — close button', () => {
  it('clicking the close button emits "close"', async () => {
    const w = await mountPanel('datapoint_read', {})
    await flushPromises()
    // Close button is the first button in the header (SVG icon)
    const closeBtn = w.find('button')
    await closeBtn.trigger('click')
    expect(w.emitted('close')).toBeTruthy()
    w.unmount()
  })
})
