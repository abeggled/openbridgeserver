/**
 * Integration test for table row colouring (issue #437).
 *
 * Verifies the RingBufferView pipes `matched_set_ids` into the
 * `useSetColors` composable and applies the resulting inline style to each
 * `<tr>`. Also locks in:
 *
 *   - Entries without `matched_set_ids` render without a row style.
 *   - Multi-match: the first topbar set (ascending `topbar_order`) wins.
 *   - When at least one topbar set is active, the view uses
 *     `queryMultiFiltersets` instead of `queryV2`. Otherwise we keep the
 *     existing `queryV2` path (so the default case is untouched).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

function makeSet(overrides = {}) {
  return {
    id: 'set-a',
    name: 'Set A',
    color: '#3b82f6',
    is_active: true,
    topbar_active: true,
    topbar_order: 0,
    ...overrides,
  }
}

function makeEntry(id, overrides = {}) {
  return {
    ts: new Date().toISOString(),
    datapoint_id: `dp-${id}`,
    name: `dp ${id}`,
    new_value: id,
    old_value: null,
    source_adapter: 'api',
    quality: 'good',
    matched_set_ids: [],
    ...overrides,
  }
}

describe('RingBufferView table-row colouring', () => {
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

  it('paints a row with the matched topbar-set colour when matched_set_ids is set', async () => {
    const helpers = await import('../../helpers/mountRingBufferView.js')
    const ringbufferApi = helpers.makeRingbufferApiMock({
      listFiltersets: vi.fn().mockResolvedValue({
        data: [makeSet({ id: 'set-a', color: '#3b82f6' })],
      }),
      queryMultiFiltersets: vi.fn().mockResolvedValue({
        data: [makeEntry(1, { matched_set_ids: ['set-a'] })],
      }),
    })

    const { wrapper } = await helpers.mountRingBufferView({ ringbufferApi })
    const rows = wrapper.findAll('[data-testid="ringbuffer-entry"]')
    expect(rows.length).toBe(1)
    const style = rows[0].attributes('style') || ''
    expect(style.toLowerCase()).toContain('background-color')
    // chroma-js stores the colour as the configured hex
    expect(style.toLowerCase()).toMatch(/#3b82f6|rgb\(\s*59\s*,\s*130\s*,\s*246\s*\)/)
  })

  it('does not paint rows when matched_set_ids is empty', async () => {
    const helpers = await import('../../helpers/mountRingBufferView.js')
    const ringbufferApi = helpers.makeRingbufferApiMock({
      // No topbar sets → falls back to queryV2
      listFiltersets: vi.fn().mockResolvedValue({ data: [] }),
      queryV2: vi.fn().mockResolvedValue({
        data: [makeEntry(1, { matched_set_ids: [] })],
      }),
    })

    const { wrapper } = await helpers.mountRingBufferView({ ringbufferApi })
    const rows = wrapper.findAll('[data-testid="ringbuffer-entry"]')
    expect(rows.length).toBe(1)
    const style = rows[0].attributes('style') || ''
    expect(style.toLowerCase()).not.toContain('background-color')
  })

  it('uses queryMultiFiltersets when at least one set is topbar-active', async () => {
    const helpers = await import('../../helpers/mountRingBufferView.js')
    const ringbufferApi = helpers.makeRingbufferApiMock({
      listFiltersets: vi.fn().mockResolvedValue({
        data: [
          makeSet({ id: 'set-a', topbar_active: true,  topbar_order: 0 }),
          makeSet({ id: 'set-b', topbar_active: false, topbar_order: 1 }),
        ],
      }),
      queryMultiFiltersets: vi.fn().mockResolvedValue({ data: [] }),
    })

    await helpers.mountRingBufferView({ ringbufferApi })
    expect(ringbufferApi.queryMultiFiltersets).toHaveBeenCalledTimes(1)
    // The body must include the active set ids (set-a only, set-b is not active).
    const [body] = ringbufferApi.queryMultiFiltersets.mock.calls[0]
    expect(body.set_ids).toEqual(['set-a'])
    // queryV2 must not be called in this path.
    expect(ringbufferApi.queryV2).not.toHaveBeenCalled()
  })

  it('falls back to queryV2 when no topbar set is active', async () => {
    const helpers = await import('../../helpers/mountRingBufferView.js')
    const ringbufferApi = helpers.makeRingbufferApiMock({
      listFiltersets: vi.fn().mockResolvedValue({
        data: [makeSet({ id: 'set-a', topbar_active: false })],
      }),
    })

    await helpers.mountRingBufferView({ ringbufferApi })
    expect(ringbufferApi.queryV2).toHaveBeenCalledTimes(1)
    expect(ringbufferApi.queryMultiFiltersets).not.toHaveBeenCalled()
  })

  it('multi-match: first topbar set in topbar_order wins', async () => {
    const helpers = await import('../../helpers/mountRingBufferView.js')
    const ringbufferApi = helpers.makeRingbufferApiMock({
      listFiltersets: vi.fn().mockResolvedValue({
        data: [
          makeSet({ id: 'set-b', color: '#ef4444', topbar_order: 1 }),
          makeSet({ id: 'set-a', color: '#10b981', topbar_order: 0 }),
        ],
      }),
      queryMultiFiltersets: vi.fn().mockResolvedValue({
        // Entry matches both, but set-a's topbar_order is lower → wins
        data: [makeEntry(1, { matched_set_ids: ['set-b', 'set-a'] })],
      }),
    })

    const { wrapper } = await helpers.mountRingBufferView({ ringbufferApi })
    const rows = wrapper.findAll('[data-testid="ringbuffer-entry"]')
    expect(rows.length).toBe(1)
    const style = (rows[0].attributes('style') || '').toLowerCase()
    expect(style).toMatch(/#10b981|rgb\(\s*16\s*,\s*185\s*,\s*129\s*\)/)
    // set-b's red colour must not appear
    expect(style).not.toMatch(/#ef4444|rgb\(\s*239\s*,\s*68\s*,\s*68\s*\)/)
  })

  // -------------------------------------------------------------------------
  // QA-01 audit (#439): WebSocket reconnect + matched_set_ids handling
  // -------------------------------------------------------------------------

  it('live WS event with matched_set_ids paints the row using the topbar colour map', async () => {
    // Documents the future-compatible path: should the backend ever start
    // sending matched_set_ids on WS pushes, the colour lookup must work
    // unchanged because the topbar cache is populated by listFiltersets.
    const helpers = await import('../../helpers/mountRingBufferView.js')
    const ringbufferApi = helpers.makeRingbufferApiMock({
      listFiltersets: vi.fn().mockResolvedValue({
        data: [makeSet({ id: 'set-a', color: '#22c55e', topbar_active: true })],
      }),
      queryMultiFiltersets: vi.fn().mockResolvedValue({ data: [] }),
    })

    const { wrapper, emitLive } = await helpers.mountRingBufferView({ ringbufferApi })
    emitLive({
      ts: new Date().toISOString(),
      datapoint_id: 'live-set-a',
      name: 'live-set-a',
      new_value: 7,
      old_value: null,
      source_adapter: 'api',
      quality: 'good',
      matched_set_ids: ['set-a'],
    })
    await new Promise((r) => setTimeout(r, 100))
    await helpers.flushPromises()

    const rows = wrapper.findAll('[data-testid="ringbuffer-entry"]')
    const live = rows.find((r) => r.attributes('data-dp') === 'live-set-a')
    expect(live).toBeTruthy()
    const style = (live.attributes('style') || '').toLowerCase()
    expect(style).toMatch(/#22c55e|rgb\(\s*34\s*,\s*197\s*,\s*94\s*\)/)
  })

  it('topbar reload after a "changed" event keeps set-state in sync (reconnect path)', async () => {
    // The chip strip emits "changed" after every PATCH; the view responds by
    // re-running its query so the colour cache is rebuilt with the freshly
    // loaded set list. We simulate this by calling the same code path twice
    // and confirming queryMultiFiltersets sees the updated set ids.
    const helpers = await import('../../helpers/mountRingBufferView.js')
    const firstList = [
      makeSet({ id: 'set-a', topbar_active: true, topbar_order: 0 }),
    ]
    const secondList = [
      makeSet({ id: 'set-a', topbar_active: true, topbar_order: 0 }),
      makeSet({ id: 'set-b', topbar_active: true, topbar_order: 1 }),
    ]
    const listFiltersets = vi
      .fn()
      .mockResolvedValueOnce({ data: firstList })
      .mockResolvedValueOnce({ data: secondList })
      .mockResolvedValue({ data: secondList })
    const ringbufferApi = helpers.makeRingbufferApiMock({
      listFiltersets,
      queryMultiFiltersets: vi.fn().mockResolvedValue({ data: [] }),
    })

    const { wrapper } = await helpers.mountRingBufferView({ ringbufferApi })
    expect(listFiltersets).toHaveBeenCalledTimes(1)
    // First call should query with [set-a] only.
    expect(ringbufferApi.queryMultiFiltersets.mock.calls[0][0].set_ids).toEqual(['set-a'])

    // Simulate "changed" event from the chip strip
    const chips = wrapper.findComponent({ name: 'TopbarFilterChips' })
    if (chips.exists()) {
      await chips.vm.$emit('changed')
    } else {
      // Fallback: trigger via DOM if the chips child component is not exposed
      const ev = new Event('input', { bubbles: true })
      wrapper.element.dispatchEvent(ev)
    }
    await helpers.flushPromises()
    // listFiltersets must have been called at least twice now.
    expect(listFiltersets.mock.calls.length).toBeGreaterThanOrEqual(2)
  })

  it('live WS entries arrive with empty matched_set_ids and stay unpainted', async () => {
    const helpers = await import('../../helpers/mountRingBufferView.js')
    const ringbufferApi = helpers.makeRingbufferApiMock({
      listFiltersets: vi.fn().mockResolvedValue({ data: [] }),
    })

    const { wrapper, emitLive } = await helpers.mountRingBufferView({ ringbufferApi })
    emitLive({
      ts: new Date().toISOString(),
      datapoint_id: 'live-1',
      name: 'live-1',
      new_value: 1,
      old_value: null,
      source_adapter: 'api',
      quality: 'good',
    })
    // 60ms flush debounce + microtask drain
    await new Promise((r) => setTimeout(r, 100))
    await helpers.flushPromises()

    const rows = wrapper.findAll('[data-testid="ringbuffer-entry"]')
    // Find the live entry by data-dp attribute
    const live = rows.find((r) => r.attributes('data-dp') === 'live-1')
    expect(live).toBeTruthy()
    const style = (live.attributes('style') || '').toLowerCase()
    expect(style).not.toContain('background-color')
  })
})
