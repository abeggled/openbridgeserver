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
      node: { id: 'n1', type: 'hems_surplus', data },
      nodeTypes: [{ type: 'hems_surplus', label: 'hems_surplus', description: '' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

describe('NodeConfigPanel hems_surplus', () => {
  it('renders the surplus control editor instead of the generic form', async () => {
    const w = await mountPanel({})
    await flushPromises()
    expect(w.find('[data-testid="hems-config"]').exists()).toBe(true)
    expect(w.find('[data-testid="hems-grid-mode"]').exists()).toBe(true)
    w.unmount()
  })

  it('forwards an edit as an update of the block data', async () => {
    const w = await mountPanel({ interval_s: 30, consumers: '[]' })
    await flushPromises()
    await w.find('[data-testid="hems-consumer-add"]').trigger('click')
    const update = w.emitted('update').at(-1)[0]
    expect(update.interval_s).toBe(30)
    expect(JSON.parse(update.consumers)).toHaveLength(1)

    const input = w.find('[data-testid="hems-global-target_w"]')
    input.element.value = '300'
    await input.trigger('change')
    expect(w.emitted('update').at(-1)[0].target_w).toBe(300)
    expect(JSON.parse(w.emitted('update').at(-1)[0].consumers)).toHaveLength(1)
    w.unmount()
  })
})

describe('NodeConfigPanel hems_surplus debug tab', () => {
  it('shows the outputs under the consumer names instead of the technical ids', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    const w = mount(mod.default, {
      props: {
        node: { id: 'n1', type: 'hems_surplus', data: { consumers: [{ id: 'wuzo62', name: 'Warmwasser Boiler', mode: 'percent' }] } },
        nodeTypes: [{ type: 'hems_surplus', label: 'hems_surplus', description: '' }],
        nodeOutputs: {},
        debugMode: true,
        debugOutputs: { c_wuzo62: 0, c_wuzo62_status: 'off', status: 'ok' },
        debugMetadata: { timestamp: '2026-08-20T10:00:00Z', duration_ms: 2, used_overrides: false },
      },
      global: { plugins: [pinia] },
      attachTo: document.body,
    })
    await flushPromises()
    const text = w.text()
    expect(text).toContain('Warmwasser Boiler')
    expect(text).toContain('Warmwasser Boiler: Status')
    expect(text).not.toContain('c_wuzo62')
    w.unmount()
  })
})
