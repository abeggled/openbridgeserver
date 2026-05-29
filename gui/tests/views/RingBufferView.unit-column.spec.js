/**
 * Tests for the DP-unit display in the Monitor (RingBufferView) value columns.
 *
 * Issue #434 — the "Wert" and "Vorheriger Wert" columns must show the
 * DataPoint's unit (e.g. "°C", "W") next to the value, analogous to
 * HistoryView.vue:80. The unit comes from the entry payload as `e.unit`
 * — the backend hydrates it from the DataPoint registry for both REST
 * responses and live WebSocket pushes.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

function makeEntry(overrides = {}) {
  return {
    id: 1,
    ts: '2026-05-11T12:34:56Z',
    datapoint_id: 'dp-1',
    name: 'Temp Sensor',
    topic: 'dp/dp-1/value',
    old_value: 21.5,
    new_value: 22.3,
    source_adapter: 'api',
    quality: 'good',
    metadata_version: 1,
    metadata: {},
    ...overrides,
  }
}

describe('RingBufferView unit column display (#434)', () => {
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

  it('renders unit span next to new_value and old_value when entry has unit', async () => {
    const { mountRingBufferView, makeRingbufferApiMock, flushPromises } = await import(
      '../helpers/mountRingBufferView.js'
    )

    const ringbufferApi = makeRingbufferApiMock({
      queryV2: vi.fn().mockResolvedValue({
        data: [makeEntry({ unit: '°C' })],
      }),
    })

    const { wrapper } = await mountRingBufferView({ ringbufferApi })
    await flushPromises()

    const row = wrapper.find('[data-testid="ringbuffer-entry"]')
    expect(row.exists()).toBe(true)

    // The value cell contains the value AND a unit span.
    const html = row.html()
    expect(html).toContain('22.3')
    expect(html).toContain('21.5')
    // The unit text appears (at least once for new_value, once for old_value).
    const occurrences = (html.match(/°C/g) || []).length
    expect(occurrences).toBeGreaterThanOrEqual(2)
  })

  it('does not render a unit span when entry has no unit', async () => {
    const { mountRingBufferView, makeRingbufferApiMock, flushPromises } = await import(
      '../helpers/mountRingBufferView.js'
    )

    const ringbufferApi = makeRingbufferApiMock({
      queryV2: vi.fn().mockResolvedValue({
        data: [makeEntry({ unit: null })],
      }),
    })

    const { wrapper } = await mountRingBufferView({ ringbufferApi })
    await flushPromises()

    const row = wrapper.find('[data-testid="ringbuffer-entry"]')
    expect(row.exists()).toBe(true)

    // No unit span — and notably no stray whitespace/separator artifacts.
    const html = row.html()
    // Sanity: value is still rendered.
    expect(html).toContain('22.3')
    // The tell-tale "ml-1" unit-span class must not appear in the row.
    expect(html).not.toMatch(/class="[^"]*ml-1[^"]*"/)
  })

  it('renders unit for live WebSocket pushes too', async () => {
    const { mountRingBufferView, flushPromises } = await import(
      '../helpers/mountRingBufferView.js'
    )

    const { wrapper, emitLive } = await mountRingBufferView()

    emitLive(makeEntry({ id: 42, datapoint_id: 'dp-live', name: 'Live DP', unit: 'W', new_value: 1234, old_value: 1000 }))
    // Live flush interval is 60 ms.
    await new Promise((r) => setTimeout(r, 100))
    await flushPromises()

    const rows = wrapper.findAll('[data-testid="ringbuffer-entry"]')
    expect(rows.length).toBe(1)
    const html = rows[0].html()
    expect(html).toContain('1234')
    expect(html).toContain('1000')
    const occurrences = (html.match(/\bW\b/g) || []).length
    expect(occurrences).toBeGreaterThanOrEqual(2)
  })

  it('renders unit even when old_value is null (only new_value side)', async () => {
    const { mountRingBufferView, makeRingbufferApiMock, flushPromises } = await import(
      '../helpers/mountRingBufferView.js'
    )

    const ringbufferApi = makeRingbufferApiMock({
      queryV2: vi.fn().mockResolvedValue({
        data: [makeEntry({ unit: 'kWh', old_value: null, new_value: 7.5 })],
      }),
    })

    const { wrapper } = await mountRingBufferView({ ringbufferApi })
    await flushPromises()

    const row = wrapper.find('[data-testid="ringbuffer-entry"]')
    const html = row.html()
    expect(html).toContain('7.5')
    // old_value is rendered as "-" placeholder — no unit next to it.
    // new_value still has the unit.
    expect(html).toMatch(/kWh/)
  })
})
