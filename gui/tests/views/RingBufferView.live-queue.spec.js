/**
 * Characterization tests for the live WebSocket queue in RingBufferView.
 *
 * Exercises (via the wsStore.onRingbufferEntry mock):
 *   - pause/resume toggling of `paused`
 *   - `enqueueLive` accumulating into liveQueue while paused
 *   - `flushLiveQueue` batching by LIVE_BATCH_SIZE (200) once resumed
 *   - the "Pausiert (N wartend)" badge reflecting queue size
 *
 * Locks the current 200-entry batch size and 60ms flush interval as the
 * #392/#426 baseline.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

function makeEntry(id, overrides = {}) {
  return {
    id,
    ts: new Date(Date.now() + id).toISOString(),
    datapoint_id: `dp-${id}`,
    name: `dp ${id}`,
    topic: `dp/${id}`,
    old_value: null,
    new_value: id,
    source_adapter: 'api',
    quality: 'good',
    metadata_version: 1,
    metadata: {},
    ...overrides,
  }
}

describe('RingBufferView live WebSocket queue', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  afterEach(() => {
    vi.doUnmock('@/api/client')
    vi.doUnmock('@/stores/websocket')
    vi.doUnmock('@/composables/useTz')
    vi.doUnmock('@/components/ui/Badge.vue')
    vi.doUnmock('@/components/ui/Spinner.vue')
    vi.doUnmock('@/components/ui/Modal.vue')
  })

  it('registers a ringbuffer handler on mount', async () => {
    const { mountRingBufferView } = await import('../helpers/mountRingBufferView.js')
    const { hasLiveHandler } = await mountRingBufferView({ wsConnected: true })
    expect(hasLiveHandler()).toBe(true)
  })

  it('Pause keeps live entries in liveQueue and bumps queuedCount', async () => {
    const { mountRingBufferView, flushPromises } = await import('../helpers/mountRingBufferView.js')
    const { wrapper, emitLive } = await mountRingBufferView({ wsConnected: true })

    await wrapper.find('[data-testid="btn-live-pause"]').trigger('click')
    await flushPromises()

    // After pause, the resume button is rendered.
    expect(wrapper.find('[data-testid="btn-live-resume"]').exists()).toBe(true)

    emitLive(makeEntry(1))
    emitLive(makeEntry(2))
    emitLive(makeEntry(3))
    await flushPromises()

    // Queue is reflected in the live-mode badge.
    const badge = wrapper.find('[data-testid="status-badge"]')
    expect(badge.text()).toBe('Pausiert (3 wartend)')

    // While paused, the table stays empty — no flush happens.
    expect(wrapper.find('[data-testid="ringbuffer-empty"]').exists()).toBe(true)
  })

  it('Resume drains the queue and prepends entries to the table', async () => {
    const { mountRingBufferView, flushPromises } = await import('../helpers/mountRingBufferView.js')
    const { wrapper, emitLive } = await mountRingBufferView({ wsConnected: true })

    await wrapper.find('[data-testid="btn-live-pause"]').trigger('click')
    await flushPromises()

    emitLive(makeEntry(1))
    emitLive(makeEntry(2))
    emitLive(makeEntry(3))
    await flushPromises()

    await wrapper.find('[data-testid="btn-live-resume"]').trigger('click')
    // Flush interval is 60 ms — wait for it.
    await new Promise((r) => setTimeout(r, 100))
    await flushPromises()

    const rows = wrapper.findAll('[data-testid="ringbuffer-entry"]')
    expect(rows.length).toBe(3)
    // Live badge resets.
    expect(wrapper.find('[data-testid="status-badge"]').text()).toBe('Live')
  })

  it('LIVE_BATCH_SIZE = 200 — a 250-entry burst flushes in two batches', async () => {
    const { mountRingBufferView, flushPromises } = await import('../helpers/mountRingBufferView.js')
    const { wrapper, emitLive } = await mountRingBufferView({ wsConnected: true })

    await wrapper.find('[data-testid="btn-live-pause"]').trigger('click')
    await flushPromises()

    for (let i = 1; i <= 250; i++) emitLive(makeEntry(i))
    await flushPromises()
    expect(wrapper.find('[data-testid="status-badge"]').text()).toBe('Pausiert (250 wartend)')

    await wrapper.find('[data-testid="btn-live-resume"]').trigger('click')

    // First flush: 200 entries, queue still has 50.
    await new Promise((r) => setTimeout(r, 100))
    await flushPromises()
    expect(wrapper.findAll('[data-testid="ringbuffer-entry"]').length).toBe(200)

    // Second flush within the next interval clears the remainder.
    await new Promise((r) => setTimeout(r, 100))
    await flushPromises()
    expect(wrapper.findAll('[data-testid="ringbuffer-entry"]').length).toBe(250)
  })

})
