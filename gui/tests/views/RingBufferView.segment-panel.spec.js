/**
 * RingBufferView ↔ SegmentStatsPanel wiring (issue #938).
 *
 * The panel is driven by `stats.store`: it renders in the segmented mode and
 * stays hidden in the (rare) legacy mode where `store` is null. RingBufferView
 * receives stats through the TopbarStats `@stats` emit and the /stats fetch in
 * loadRecoveryNotice.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { ref } from 'vue'

function segmentedStatsPayload() {
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
      common: { total: 800, oldest_ts: '2026-06-01T00:00:00.000Z', newest_ts: '2026-06-02T00:00:00.000Z', segment_count: 2, size_bytes: 3 * 1024 * 1024 },
      backend_extra: {
        active_segment_id: 5,
        closed_segment_count: 1,
        wal_size_bytes: 4096,
        shm_size_bytes: 0,
        last_checkpoint_at: null,
        last_checkpoint_mode: null,
        last_checkpoint_result: null,
        wal_checkpoint_busy: 0,
        checkpoint_pending: 0,
        retention_over_budget: false,
        retention_pressure_reason: null,
        storage_on_network_drive: false,
        segments: [
          { segment_id: 5, status: 'active', row_count: 500, size_bytes: 1024 * 1024, from_ts: '2026-06-02T00:00:00.000Z', to_ts: null, integrity_status: 'ok', recovery_status: 'none', quarantine_reason: null },
          { segment_id: 4, status: 'closed', row_count: 300, size_bytes: 2 * 1024 * 1024, from_ts: '2026-06-01T00:00:00.000Z', to_ts: '2026-06-02T00:00:00.000Z', integrity_status: 'ok', recovery_status: 'none', quarantine_reason: null },
        ],
      },
    },
  }
}

describe('RingBufferView segment panel wiring', () => {
  beforeEach(() => {
    vi.resetModules()
  })
  afterEach(() => {
    vi.doUnmock('@/api/client')
    vi.doUnmock('@/stores/websocket')
    vi.doUnmock('@/composables/useTz')
    vi.doUnmock('@/composables/useLegacyMigration')
  })

  it('renders the segment panel when stats.store is present', async () => {
    const { mountRingBufferView, makeRingbufferApiMock } = await import('../helpers/mountRingBufferView.js')
    const ringbufferApi = makeRingbufferApiMock({
      stats: vi.fn().mockResolvedValue({ data: segmentedStatsPayload() }),
    })
    const { wrapper } = await mountRingBufferView({ ringbufferApi })

    const panel = wrapper.find('[data-testid="segment-stats-panel"]')
    expect(panel.exists()).toBe(true)
    expect(wrapper.find('[data-testid="segment-count"]').text()).toBe('2')
    expect(wrapper.findAll('[data-testid="segment-row"]')).toHaveLength(2)
  })

  it('hides the segment panel in legacy mode (store is null)', async () => {
    const { mountRingBufferView, makeRingbufferApiMock } = await import('../helpers/mountRingBufferView.js')
    const legacy = { ...segmentedStatsPayload(), store: null }
    const ringbufferApi = makeRingbufferApiMock({
      stats: vi.fn().mockResolvedValue({ data: legacy }),
    })
    const { wrapper } = await mountRingBufferView({ ringbufferApi })

    expect(wrapper.find('[data-testid="segment-stats-panel"]').exists()).toBe(false)
  })

  it('reloads segment stats immediately when migration reaches done', async () => {
    const completionRevision = ref(0)
    vi.doMock('@/composables/useLegacyMigration', () => ({
      useLegacyMigration: () => ({
        completionRevision,
        refresh: vi.fn().mockResolvedValue(null),
        showBanner: ref(false),
        escalated: ref(false),
        status: ref(null),
        decision: ref(null),
        legacy: ref(null),
        job: ref(null),
        jobRunning: ref(false),
        decide: vi.fn(),
        startMigration: vi.fn(),
      }),
    }))

    const before = segmentedStatsPayload()
    before.store.backend_extra.segments = [
      {
        segment_id: 2,
        status: 'legacy',
        row_count: 0,
        size_bytes: 10 * 1024 * 1024,
        from_ts: null,
        to_ts: null,
        integrity_status: 'ok',
        recovery_status: 'none',
        quarantine_reason: null,
      },
    ]
    before.store.common.segment_count = 1

    const after = segmentedStatsPayload()
    after.store.backend_extra.segments = [
      {
        segment_id: 4,
        status: 'closed',
        row_count: 75534,
        size_bytes: 109 * 1024 * 1024,
        from_ts: '2026-07-03T11:23:31.344Z',
        to_ts: '2026-07-03T14:06:16.699Z',
        integrity_status: 'ok',
        recovery_status: 'none',
        quarantine_reason: null,
      },
    ]
    after.store.common.segment_count = 1

    const { mountRingBufferView, makeRingbufferApiMock, flushPromises } = await import('../helpers/mountRingBufferView.js')
    const ringbufferApi = makeRingbufferApiMock({
      stats: vi.fn().mockResolvedValue({ data: before }),
    })
    const topbarReload = vi.fn().mockResolvedValue(after)
    const { wrapper } = await mountRingBufferView({ ringbufferApi, topbarReload })

    expect(wrapper.find('[data-testid="segment-row"]').text()).toContain('0')

    completionRevision.value += 1
    await flushPromises()

    expect(topbarReload).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="segment-row"]').text()).toContain('75.534')
    expect(wrapper.find('[data-testid="segment-row"]').text()).not.toContain('Legacy')
  })
})
