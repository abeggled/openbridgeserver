/**
 * Tests for SegmentStatsPanel.vue (issue #938).
 *
 * The panel renders the segmented RingBuffer store (`{common, backend_extra}`)
 * as a read-only overview: totals, a per-segment table with status badges, and
 * operational notices (retention pressure, network drive, checkpoint busy).
 * It is only ever mounted when `store != null`, so the panel itself assumes a
 * non-null store; the null/legacy fallback is exercised in RingBufferView.
 */
import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import { createTestI18n } from '../../helpers/createTestI18n'

// Stub useTz so the panel does not pull the settings Pinia store (localStorage).
vi.mock('@/composables/useTz', () => ({
  useTz: () => ({ fmtDateTime: (iso) => String(iso ?? ''), fmtDate: (iso) => String(iso ?? '') }),
}))

import SegmentStatsPanel from '@/views/ringbuffer/SegmentStatsPanel.vue'

const BadgeStub = defineComponent({
  name: 'Badge',
  props: ['variant', 'size', 'dot'],
  setup(props, { slots }) {
    return () => h('span', { 'data-variant': props.variant }, slots.default ? slots.default() : [])
  },
})

function makeStore(overrides = {}) {
  return {
    common: {
      total: 1500,
      oldest_ts: '2026-06-01T00:00:00.000Z',
      newest_ts: '2026-06-02T00:00:00.000Z',
      segment_count: 3,
      size_bytes: 5 * 1024 * 1024,
      ...(overrides.common ?? {}),
    },
    backend_extra: {
      active_segment_id: 42,
      closed_segment_count: 2,
      wal_size_bytes: 128 * 1024,
      shm_size_bytes: 32 * 1024,
      last_checkpoint_at: '2026-06-02T00:00:00.000Z',
      last_checkpoint_mode: 'TRUNCATE',
      last_checkpoint_result: 'ok',
      wal_checkpoint_busy: 0,
      checkpoint_pending: 0,
      retention_over_budget: false,
      retention_pressure_reason: null,
      storage_on_network_drive: false,
      segments: [
        { segment_id: 42, status: 'active', row_count: 500, size_bytes: 1024 * 1024, from_ts: '2026-06-02T00:00:00.000Z', to_ts: null, integrity_status: 'ok', recovery_status: 'none', quarantine_reason: null },
        { segment_id: 41, status: 'closed', row_count: 700, size_bytes: 2 * 1024 * 1024, from_ts: '2026-06-01T12:00:00.000Z', to_ts: '2026-06-02T00:00:00.000Z', integrity_status: 'ok', recovery_status: 'none', quarantine_reason: null },
        { segment_id: 1, status: 'legacy', row_count: 300, size_bytes: 2 * 1024 * 1024, from_ts: '2026-06-01T00:00:00.000Z', to_ts: '2026-06-01T12:00:00.000Z', integrity_status: 'ok', recovery_status: 'none', quarantine_reason: null },
      ],
      ...(overrides.backend_extra ?? {}),
    },
  }
}

function mountPanel(store) {
  return mount(SegmentStatsPanel, {
    props: { store },
    global: {
      plugins: [createTestI18n()],
      stubs: { Badge: BadgeStub },
    },
  })
}

describe('SegmentStatsPanel', () => {
  it('renders the overview from store.common', () => {
    const wrapper = mountPanel(makeStore())
    expect(wrapper.find('[data-testid="segment-count"]').text()).toBe('3')
    expect(wrapper.find('[data-testid="segment-active"]').text()).toBe('#42')
    expect(wrapper.find('[data-testid="segment-total-size"]').text()).toContain('MB')
    expect(wrapper.find('[data-testid="segment-oldest"]').text()).not.toBe('—')
    expect(wrapper.find('[data-testid="segment-newest"]').text()).not.toBe('—')
  })

  it('renders one row per segment with a status badge', () => {
    const wrapper = mountPanel(makeStore())
    const rows = wrapper.findAll('[data-testid="segment-row"]')
    expect(rows).toHaveLength(3)
    const variants = wrapper.findAll('[data-variant]').map((n) => n.attributes('data-variant'))
    expect(variants).toContain('success') // active
    expect(variants).toContain('muted') // closed
    expect(variants).toContain('info') // legacy
  })

  it('marks legacy segments as read-only pre-upgrade data', () => {
    const wrapper = mountPanel(makeStore())
    const note = wrapper.find('[data-testid="segment-legacy-note"]')
    expect(note.exists()).toBe(true)
    expect(note.text()).toContain('Nur-Lese-Altdaten')
  })

  it('shows the quarantine reason for quarantined segments', () => {
    const store = makeStore({
      backend_extra: {
        segments: [
          { segment_id: 7, status: 'quarantined', row_count: 0, size_bytes: 0, from_ts: null, to_ts: null, integrity_status: 'corrupt', recovery_status: 'quarantined', quarantine_reason: 'checksum mismatch' },
        ],
      },
    })
    const wrapper = mountPanel(store)
    const reason = wrapper.find('[data-testid="segment-quarantine-reason"]')
    expect(reason.exists()).toBe(true)
    expect(reason.text()).toBe('checksum mismatch')
    expect(wrapper.find('[data-variant="danger"]').exists()).toBe(true)
  })

  it('renders a retention-over-budget notice with the pressure reason', () => {
    const store = makeStore({
      backend_extra: { retention_over_budget: true, retention_pressure_reason: 'max_age exceeded' },
    })
    const wrapper = mountPanel(store)
    const notice = wrapper.find('[data-testid="segment-notice-retention"]')
    expect(notice.exists()).toBe(true)
    expect(notice.text()).toContain('max_age exceeded')
  })

  it('warns when storage is on a network drive', () => {
    const store = makeStore({ backend_extra: { storage_on_network_drive: true } })
    const wrapper = mountPanel(store)
    const notice = wrapper.find('[data-testid="segment-notice-network"]')
    expect(notice.exists()).toBe(true)
    expect(notice.text()).toContain('Netzlaufwerk')
  })

  it('surfaces WAL size and a pending-checkpoint counter', () => {
    const store = makeStore({ backend_extra: { checkpoint_pending: 2 } })
    const wrapper = mountPanel(store)
    expect(wrapper.find('[data-testid="segment-wal-size"]').text()).toContain('KB')
    expect(wrapper.find('[data-testid="segment-checkpoint-pending"]').text()).toContain('2')
  })

  it('omits notices when the store is healthy', () => {
    const wrapper = mountPanel(makeStore())
    expect(wrapper.find('[data-testid="segment-notices"]').exists()).toBe(false)
  })
})
