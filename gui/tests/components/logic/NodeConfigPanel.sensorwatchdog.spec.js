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

async function mountPanel(data = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'n1', type: 'sensor_watchdog', data },
      nodeTypes: [{ type: 'sensor_watchdog', label: 'sensor_watchdog', description: '' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

function lastInputs(wrapper) {
  return JSON.parse(wrapper.emitted('update').at(-1)[0].inputs)
}

const INPUTS = JSON.stringify([
  { label: 'A', timeout_s: 10, fault_value: 'OFFLINE_A' },
  { label: 'B', timeout_s: 20, fault_value: 'OFFLINE_B' },
  { label: 'C', timeout_s: 30, fault_value: 'OFFLINE_C' },
])

describe('NodeConfigPanel sensor_watchdog', () => {
  it('renders one default row when nothing is stored', async () => {
    const w = await mountPanel({})
    await flushPromises()

    expect(w.find('[data-testid="watchdog-input-0"]').exists()).toBe(true)
    expect(w.find('[data-testid="watchdog-input-1"]').exists()).toBe(false)
    expect(w.find('[data-testid="watchdog-input-label-0"]').element.value).toBe('')
    expect(w.find('[data-testid="watchdog-input-timeout-0"]').element.value).toBe('60')
    w.unmount()
  })

  it('falls back to the default row when the stored JSON is unparsable', async () => {
    const w = await mountPanel({ inputs: '{' })
    await flushPromises()

    expect(w.find('[data-testid="watchdog-input-0"]').exists()).toBe(true)
    expect(w.find('[data-testid="watchdog-input-1"]').exists()).toBe(false)
    w.unmount()
  })

  it('renders rows stored as an array without rewriting them', async () => {
    const w = await mountPanel({ inputs: JSON.parse(INPUTS) })
    await flushPromises()

    expect(w.find('[data-testid="watchdog-input-2"]').exists()).toBe(true)
    expect(w.find('[data-testid="watchdog-input-label-1"]').element.value).toBe('B')
    expect(w.find('[data-testid="watchdog-input-timeout-1"]').element.value).toBe('20')
    expect(w.find('[data-testid="watchdog-input-fault-value-1"]').element.value).toBe('OFFLINE_B')
    expect(w.emitted('update')).toBeUndefined()
    w.unmount()
  })

  it('parses rows stored as a JSON string the same as an array', async () => {
    const w = await mountPanel({ inputs: INPUTS })
    await flushPromises()

    expect(w.find('[data-testid="watchdog-input-label-2"]').element.value).toBe('C')
    w.unmount()
  })

  it('appends a default row', async () => {
    const w = await mountPanel({ inputs: INPUTS })
    await flushPromises()

    await w.find('[data-testid="watchdog-input-add"]').trigger('click')

    expect(lastInputs(w)).toHaveLength(4)
    expect(lastInputs(w)[3]).toEqual({ label: '', timeout_s: 60, fault_value: null })
    w.unmount()
  })

  it('stops adding rows at 10 and disables the add button', async () => {
    const ten = Array.from({ length: 10 }, (_, i) => ({ label: `S${i + 1}`, timeout_s: 10, fault_value: null }))
    const w = await mountPanel({ inputs: ten })
    await flushPromises()

    const addBtn = w.find('[data-testid="watchdog-input-add"]')
    expect(addBtn.attributes('disabled')).toBeDefined()

    await addBtn.trigger('click')
    expect(w.emitted('update')).toBeUndefined()
    w.unmount()
  })

  it('removes a row but never the last one', async () => {
    const w = await mountPanel({ inputs: INPUTS })
    await flushPromises()

    await w.find('[data-testid="watchdog-input-remove-1"]').trigger('click')
    expect(lastInputs(w).map(r => r.label)).toEqual(['A', 'C'])

    await w.find('[data-testid="watchdog-input-remove-1"]').trigger('click')
    expect(lastInputs(w).map(r => r.label)).toEqual(['A'])

    const remove = w.find('[data-testid="watchdog-input-remove-0"]')
    expect(remove.attributes('disabled')).toBeDefined()
    await remove.trigger('click')
    expect(lastInputs(w)).toHaveLength(1)
    w.unmount()
  })

  it('ignores a remove for an index that no longer exists', async () => {
    const w = await mountPanel({ inputs: INPUTS })
    await flushPromises()

    w.vm.removeWatchdogInput(9)

    expect(w.emitted('update')).toBeUndefined()
    w.unmount()
  })

  it('edits label, timeout and fault value of one row', async () => {
    const w = await mountPanel({ inputs: INPUTS })
    await flushPromises()

    const label = w.find('[data-testid="watchdog-input-label-0"]')
    label.element.value = 'Fensterkontakt'
    await label.trigger('input')
    expect(lastInputs(w)[0].label).toBe('Fensterkontakt')

    const timeout = w.find('[data-testid="watchdog-input-timeout-0"]')
    timeout.element.value = '45'
    await timeout.trigger('input')
    expect(lastInputs(w)[0].timeout_s).toBe(45)

    const faultValue = w.find('[data-testid="watchdog-input-fault-value-0"]')
    faultValue.element.value = 'ZU'
    await faultValue.trigger('input')
    expect(lastInputs(w)[0].fault_value).toBe('ZU')
    w.unmount()
  })

  it('ignores an edit for an index that no longer exists', async () => {
    const w = await mountPanel({ inputs: INPUTS })
    await flushPromises()

    w.vm.updateWatchdogInput(9, 'label', 'x')

    expect(w.emitted('update')).toBeUndefined()
    w.unmount()
  })
})
