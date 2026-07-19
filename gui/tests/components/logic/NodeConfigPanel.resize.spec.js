import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  localStorage.clear()
  vi.doMock('@/api/client', () => ({
    dpApi:       { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:   { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
    authApi:     { login: vi.fn(), me: vi.fn() },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

async function mountPanel() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'c1', type: 'timer_cron', data: { cron: '0 7 * * *' } },
      nodeTypes: [{ type: 'timer_cron', label: 'Timer/Cron', description: 'Zeitgesteuert' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

describe('NodeConfigPanel — resizable width (issue #1034)', () => {
  it('renders at the default width with a resize handle on the left edge', async () => {
    const w = await mountPanel()
    await flushPromises()

    const root = w.element
    expect(root.style.width).toBe('288px')
    expect(w.find('[title]').exists()).toBe(true)
    w.unmount()
  })

  it('widens the panel when the resize handle is dragged left and persists the width', async () => {
    const w = await mountPanel()
    await flushPromises()

    const handle = w.find('[title]')
    await handle.trigger('pointerdown', { clientX: 500 })

    document.dispatchEvent(new MouseEvent('pointermove', { clientX: 400, bubbles: true }))
    await flushPromises()
    expect(w.element.style.width).toBe('388px')

    document.dispatchEvent(new MouseEvent('pointerup', { clientX: 400, bubbles: true }))
    await flushPromises()

    expect(localStorage.getItem('obs.logic.nodeConfigPanelWidth')).toBe('388')
    w.unmount()
  })

  it('restores a previously persisted width on next mount', async () => {
    localStorage.setItem('obs.logic.nodeConfigPanelWidth', '420')
    const w = await mountPanel()
    await flushPromises()

    expect(w.element.style.width).toBe('420px')
    w.unmount()
  })
})
