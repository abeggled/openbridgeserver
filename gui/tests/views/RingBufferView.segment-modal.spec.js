/**
 * RingBufferView — segment stats moved into a modal + toolbar button badge (#938).
 *
 * After user feedback the SegmentStatsPanel is no longer rendered inline. A
 * toolbar button ("Segmente") opens a modal that hosts the panel, and the
 * button carries a warn badge when any segment reports a problem
 * (integrity_status='corrupt' or recovery_status in {quarantined, pending,
 * dirty_wal}).
 *
 * The shared mount helper stubs Modal so it always renders its slot; we assert
 * on the button, the badge, and the panel's presence rather than on open/close
 * transitions.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

function segmentedStatsPayload(segments) {
  return {
    total: 800,
    oldest_ts: '2026-06-01T00:00:00.000Z',
    newest_ts: '2026-06-02T00:00:00.000Z',
    storage: 'file',
    enabled: true,
    max_entries: 10000,
    max_file_size_bytes: null,
    max_age: null,
    file_size_bytes: 3 * 1024 * 1024,
    last_recovery_at: null,
    last_recovery_file_count: 0,
    store: {
      common: { total: 800, oldest_ts: '2026-06-01T00:00:00.000Z', newest_ts: '2026-06-02T00:00:00.000Z', segment_count: segments.length, size_bytes: 3 * 1024 * 1024 },
      backend_extra: {
        active_segment_id: segments[0]?.segment_id ?? null,
        wal_size_bytes: 4096,
        last_checkpoint_at: null,
        wal_checkpoint_busy: 0,
        checkpoint_pending: 0,
        retention_over_budget: false,
        retention_pressure_reason: null,
        storage_on_network_drive: false,
        segments,
      },
    },
  }
}

const healthySegments = [
  { segment_id: 5, status: 'active', row_count: 500, size_bytes: 1024 * 1024, from_ts: '2026-06-02T00:00:00.000Z', to_ts: null, integrity_status: 'ok', recovery_status: 'none', quarantine_reason: null },
  { segment_id: 4, status: 'closed', row_count: 300, size_bytes: 2 * 1024 * 1024, from_ts: '2026-06-01T00:00:00.000Z', to_ts: '2026-06-02T00:00:00.000Z', integrity_status: 'ok', recovery_status: 'none', quarantine_reason: null },
]

const problemSegments = [
  { segment_id: 5, status: 'active', row_count: 500, size_bytes: 1024 * 1024, from_ts: '2026-06-02T00:00:00.000Z', to_ts: null, integrity_status: 'ok', recovery_status: 'none', quarantine_reason: null },
  { segment_id: 3, status: 'quarantined', row_count: 0, size_bytes: 0, from_ts: null, to_ts: null, integrity_status: 'corrupt', recovery_status: 'quarantined', quarantine_reason: 'checksum mismatch' },
]

describe('RingBufferView segment modal + button badge (#938)', () => {
  beforeEach(() => {
    vi.resetModules()
  })
  afterEach(() => {
    vi.doUnmock('@/api/client')
    vi.doUnmock('@/stores/websocket')
    vi.doUnmock('@/composables/useTz')
  })

  it('shows the "Segmente" button and hosts the panel in a modal (not inline)', async () => {
    const { mountRingBufferView, makeRingbufferApiMock } = await import('../helpers/mountRingBufferView.js')
    const ringbufferApi = makeRingbufferApiMock({
      stats: vi.fn().mockResolvedValue({ data: segmentedStatsPayload(healthySegments) }),
    })
    const { wrapper } = await mountRingBufferView({ ringbufferApi })

    // Toolbar button exists.
    const btn = wrapper.find('[data-testid="btn-open-segments"]')
    expect(btn.exists()).toBe(true)
    // Panel is present (inside the stubbed modal which always renders its slot).
    expect(wrapper.find('[data-testid="segment-stats-panel"]').exists()).toBe(true)
  })

  it('shows no warn badge when all segments are healthy', async () => {
    const { mountRingBufferView, makeRingbufferApiMock } = await import('../helpers/mountRingBufferView.js')
    const ringbufferApi = makeRingbufferApiMock({
      stats: vi.fn().mockResolvedValue({ data: segmentedStatsPayload(healthySegments) }),
    })
    const { wrapper } = await mountRingBufferView({ ringbufferApi })
    expect(wrapper.find('[data-testid="btn-segments-badge"]').exists()).toBe(false)
  })

  it('shows a warn badge when a segment is corrupt/quarantined', async () => {
    const { mountRingBufferView, makeRingbufferApiMock } = await import('../helpers/mountRingBufferView.js')
    const ringbufferApi = makeRingbufferApiMock({
      stats: vi.fn().mockResolvedValue({ data: segmentedStatsPayload(problemSegments) }),
    })
    const { wrapper } = await mountRingBufferView({ ringbufferApi })
    expect(wrapper.find('[data-testid="btn-segments-badge"]').exists()).toBe(true)
  })

  it('hides the segment button in legacy mode (store is null)', async () => {
    const { mountRingBufferView, makeRingbufferApiMock } = await import('../helpers/mountRingBufferView.js')
    const legacy = { ...segmentedStatsPayload(healthySegments), store: null }
    const ringbufferApi = makeRingbufferApiMock({
      stats: vi.fn().mockResolvedValue({ data: legacy }),
    })
    const { wrapper } = await mountRingBufferView({ ringbufferApi })
    expect(wrapper.find('[data-testid="btn-open-segments"]').exists()).toBe(false)
  })
})
