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
    adapterApi:  { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    messageArchivesApi: { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

const DEBUG_INPUT = {
  id: 'in1',
  label: '1',
  incoming: 23,
  effective: 23,
  overridden: false,
  capturedOverridden: false,
  locallyOverridden: false,
  overrideText: undefined,
}

async function mountPanel(props = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  const wrapper = mount(mod.default, {
    props: {
      node: { id: 'n1', type: 'and', data: { input_count: 2 } },
      nodeTypes: [{ type: 'and', label: 'AND', description: '', config_schema: { input_count: { type: 'integer' } } }],
      nodeOutputs: {},
      ...props,
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

const tab = (wrapper, id) => wrapper.find(`[data-testid="node-panel-tab-${id}"]`)

describe('NodeConfigPanel debug tabs (issue #1128)', () => {
  it('shows no tab bar and only the settings while debug mode is off', async () => {
    const w = await mountPanel()

    expect(w.find('[data-testid="node-panel-tabs"]').exists()).toBe(false)
    expect(w.find('[data-testid="debug-inspector"]').exists()).toBe(false)
    expect(w.text()).toContain('Anzahl Eingänge')
    w.unmount()
  })

  it('opens on the debug values and switches back to the settings without leaving debug mode', async () => {
    const w = await mountPanel({
      debugMode: true,
      debugInputs: [DEBUG_INPUT],
      debugOutputs: { out: true },
      debugMetadata: { timestamp: '2026-08-20T10:00:00Z', duration_ms: 2, used_overrides: false },
    })

    expect(w.find('[data-testid="node-panel-tabs"]').exists()).toBe(true)
    expect(tab(w, 'debug').classes()).toContain('tab-btn--active')
    expect(tab(w, 'debug').classes()).toContain('tab-btn--debug')
    expect(tab(w, 'settings').classes()).not.toContain('tab-btn--active')
    expect(w.find('[data-testid="debug-inspector"]').exists()).toBe(true)
    expect(w.text()).not.toContain('Anzahl Eingänge')

    await tab(w, 'settings').trigger('click')

    expect(w.find('[data-testid="debug-inspector"]').exists()).toBe(false)
    expect(w.text()).toContain('Anzahl Eingänge')
    expect(tab(w, 'settings').classes()).toContain('tab-btn--active')

    // The settings stay editable while debug mode is still on.
    const input = w.findAll('input').find(field => field.element.type === 'number')
    await input.setValue('3')
    await input.trigger('change')
    expect(w.emitted('update')[0][0]).toEqual({ input_count: 3 })

    // …and the debug values are one click away again.
    await tab(w, 'debug').trigger('click')
    expect(w.find('[data-testid="debug-inspector"]').exists()).toBe(true)
    expect(tab(w, 'debug').classes()).toContain('tab-btn--active')
    expect(tab(w, 'settings').classes()).not.toContain('tab-btn--active')
    w.unmount()
  })

  it('follows debug mode being switched on and off', async () => {
    const w = await mountPanel({ debugInputs: [DEBUG_INPUT] })

    await w.setProps({ debugMode: true })
    expect(tab(w, 'debug').classes()).toContain('tab-btn--active')
    expect(w.find('[data-testid="debug-inspector"]').exists()).toBe(true)

    await w.setProps({ debugMode: false })
    expect(w.find('[data-testid="node-panel-tabs"]').exists()).toBe(false)
    expect(w.find('[data-testid="debug-inspector"]').exists()).toBe(false)
    expect(w.text()).toContain('Anzahl Eingänge')

    // Re-entering debug mode returns to the debug values.
    await w.setProps({ debugMode: true })
    expect(tab(w, 'debug').classes()).toContain('tab-btn--active')
    w.unmount()
  })

  it('keeps the chosen tab when another block is selected', async () => {
    const w = await mountPanel({ debugMode: true, debugInputs: [DEBUG_INPUT] })

    await tab(w, 'settings').trigger('click')
    await w.setProps({ node: { id: 'n2', type: 'and', data: { input_count: 2 } } })
    await flushPromises()

    expect(tab(w, 'settings').classes()).toContain('tab-btn--active')
    expect(w.find('[data-testid="debug-inspector"]').exists()).toBe(false)
    w.unmount()
  })

  it('appends the debug values to the setting tabs a DataPoint block already has', async () => {
    const w = await mountPanel({
      node: { id: 'dp', type: 'datapoint_write', data: { datapoint_name: 'Licht', value_formula: 'x * 2' } },
      nodeTypes: [{ type: 'datapoint_write', label: 'Write Object', description: '' }],
      debugMode: true,
      debugInputs: [DEBUG_INPUT],
    })

    // One single tab bar — the debug values join the block's own tabs instead
    // of stacking a second bar on top of them.
    const bars = w.findAll('[data-testid="node-panel-tabs"]')
    expect(bars).toHaveLength(1)
    expect(bars[0].findAll('button').map(button => button.text())).toEqual([
      'Verbindung', 'Transformation•', 'Filter', 'Debug-Werte',
    ])
    expect(tab(w, 'debug').classes()).toContain('tab-btn--active')
    expect(w.find('[data-testid="debug-inspector"]').exists()).toBe(true)

    await tab(w, 'transform').trigger('click')
    expect(w.find('[data-testid="debug-inspector"]').exists()).toBe(false)
    expect(w.text()).toContain('Wert-Transformation')
    expect(tab(w, 'transform').classes()).toContain('tab-btn--active')
    expect(tab(w, 'connection').classes()).not.toContain('tab-btn--active')

    await tab(w, 'connection').trigger('click')
    expect(tab(w, 'connection').classes()).toContain('tab-btn--active')
    expect(tab(w, 'transform').classes()).not.toContain('tab-btn--active')
    w.unmount()
  })

  it('leaves the DataPoint tab bar unchanged while debug mode is off', async () => {
    const w = await mountPanel({
      node: { id: 'dp', type: 'datapoint_read', data: { datapoint_name: 'Licht' } },
      nodeTypes: [{ type: 'datapoint_read', label: 'Read Object', description: '' }],
    })

    const buttons = w.find('[data-testid="node-panel-tabs"]').findAll('button')
    expect(buttons.map(button => button.text())).toEqual(['Verbindung', 'Transformation', 'Filter'])
    expect(tab(w, 'debug').exists()).toBe(false)
    expect(tab(w, 'connection').classes()).toContain('tab-btn--active')
    w.unmount()
  })

  it('forwards override actions of the embedded debug pane', async () => {
    const w = await mountPanel({
      debugMode: true,
      debugInputs: [{ ...DEBUG_INPUT, overridden: true, locallyOverridden: true, overrideText: '42' }],
      hasDebugOverrides: true,
    })

    await w.find('[data-testid="debug-inspector"] textarea').setValue('7')
    await w.findAll('button').find(button => button.text() === 'Löschen').trigger('click')
    await w.findAll('button').find(button => button.text() === 'Alle Überschreibungen löschen').trigger('click')

    expect(w.emitted('set-override')[0]).toEqual(['in1', '7'])
    expect(w.emitted('clear-override')[0]).toEqual(['in1'])
    expect(w.emitted('clear-all')).toHaveLength(1)
    w.unmount()
  })
})
