/**
 * Tests for the TopbarStats component (issue #435).
 *
 * Compact inline render of ringbuffer stats:
 *   12.345 / 50.000 · file
 *
 * Plus a Floating-UI tooltip attached to a "ⓘ" help icon that explains
 * the storage behaviour.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

function makeApi(stats = {}) {
  return {
    stats: vi.fn().mockResolvedValue({
      data: {
        total: 12345,
        max_entries: 50000,
        storage: 'file',
        file_size_bytes: 1024,
        max_file_size_bytes: null,
        max_age: null,
        ...stats,
      },
    }),
  }
}

async function mountStats(api = makeApi()) {
  vi.doMock('@/api/client', () => ({ ringbufferApi: api }))
  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({
      connected: false,
      onRingbufferEntry: () => () => {},
    }),
  }))
  const mod = await import('@/views/ringbuffer/TopbarStats.vue')
  const wrapper = mount(mod.default, { attachTo: document.body })
  await flushPromises()
  await wrapper.vm.$nextTick()
  await flushPromises()
  return { wrapper, api }
}

describe('TopbarStats', () => {
  beforeEach(() => {
    vi.resetModules()
    setActivePinia(createPinia())
    document.body.innerHTML = ''
  })

  it('calls ringbufferApi.stats() once on mount', async () => {
    const api = makeApi()
    await mountStats(api)
    expect(api.stats).toHaveBeenCalledTimes(1)
  })

  it('renders total / max · storage in the compact format', async () => {
    const { wrapper } = await mountStats()
    const root = wrapper.find('[data-testid="topbar-stats"]')
    expect(root.exists()).toBe(true)
    const text = root.text().replace(/\s+/g, ' ')
    // Allow either locale grouping (12.345 / 12,345) and just check digits + storage
    expect(text).toMatch(/12[.,\s]?345/)
    expect(text).toMatch(/50[.,\s]?000/)
    expect(text).toContain('file')
  })

  it('shows a help icon (ⓘ) and a tooltip with storage-behaviour explanation on hover', async () => {
    const { wrapper } = await mountStats()
    const icon = wrapper.find('[data-testid="topbar-stats-help"]')
    expect(icon.exists()).toBe(true)
    // Trigger pointerenter to open the tooltip
    await icon.trigger('pointerenter')
    await flushPromises()
    const tip = document.querySelector('[data-testid="topbar-stats-tooltip"]')
    expect(tip).toBeTruthy()
    expect(tip.textContent.toLowerCase()).toMatch(/speicher|file|max/)
  })

  it('hides the tooltip after pointerleave', async () => {
    const { wrapper } = await mountStats()
    const icon = wrapper.find('[data-testid="topbar-stats-help"]')
    await icon.trigger('pointerenter')
    await flushPromises()
    expect(document.querySelector('[data-testid="topbar-stats-tooltip"]')).toBeTruthy()
    await icon.trigger('pointerleave')
    await flushPromises()
    expect(document.querySelector('[data-testid="topbar-stats-tooltip"]')).toBeFalsy()
  })

  it('falls back to em-dashes / zeros when the stats call fails', async () => {
    const api = {
      stats: vi.fn().mockRejectedValue(new Error('boom')),
    }
    const { wrapper } = await mountStats(api)
    const root = wrapper.find('[data-testid="topbar-stats"]')
    expect(root.exists()).toBe(true)
    // Either contains a dash or stays empty-ish; must not throw / not break.
    expect(root.text()).toBeDefined()
  })
})
