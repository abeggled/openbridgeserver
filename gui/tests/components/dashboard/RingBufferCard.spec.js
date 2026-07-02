/**
 * RingBufferCard (#919/#938) — Dashboard-Karte für RingBuffer/Retention.
 *
 * Deckt alle Zustände ab: deaktiviert / segmentiert (mit + ohne Problem,
 * unbegrenztes Budget) / Legacy, plus Modal-Öffnen (Segment-Details + Config)
 * und den Deaktiviert-Konfigurieren-Button.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const statsMock = vi.fn()

beforeEach(() => {
  vi.resetModules()
  statsMock.mockReset()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

function segmentedPayload({ segments = [], maxFileSizeBytes = 100 * 1024 * 1024, sizeBytes = 30 * 1024 * 1024, retentionSeconds = 3 * 24 * 3600, overBudget = false, prognosis, segmentMaxAge = 6 * 3600 } = {}) {
  return {
    enabled: true,
    total: 800,
    file_size_bytes: sizeBytes,
    max_file_size_bytes: maxFileSizeBytes,
    max_age: null,
    segment_max_age: segmentMaxAge,
    // Volle Prognose (#919/#938): PrognosisBlock rendert Rate/Retention/Budget.
    // Default: nur der Retention-Horizont; einzelne Tests überschreiben prognosis.
    prognosis: prognosis ?? { estimated_retention_seconds: retentionSeconds },
    store: {
      common: { segment_count: segments.length, size_bytes: sizeBytes, oldest_ts: null, newest_ts: null },
      backend_extra: { segments, retention_over_budget: overBudget, retention_pressure_reason: null },
    },
  }
}

const fullPrognosis = {
  bytes_per_hour: 50 * 1024 * 1024,
  rows_per_hour: 12000,
  avg_segment_seconds: 6 * 3600,
  estimated_retention_seconds: 5 * 24 * 3600,
  recommended_budget_for_segment_age_bytes: 2 * 1024 * 1024 * 1024,
}

const healthySegments = [
  { segment_id: 2, status: 'active', integrity_status: 'ok', recovery_status: 'none' },
  { segment_id: 1, status: 'closed', integrity_status: 'ok', recovery_status: 'none' },
]

const problemSegments = [
  { segment_id: 2, status: 'active', integrity_status: 'ok', recovery_status: 'none' },
  { segment_id: 1, status: 'quarantined', integrity_status: 'corrupt', recovery_status: 'quarantined' },
]

async function mountCard(statsData) {
  statsMock.mockResolvedValue({ data: statsData })
  vi.doMock('@/api/client', () => ({
    ringbufferApi: { stats: statsMock, config: vi.fn() },
  }))
  const { default: RingBufferCard } = await import('@/components/dashboard/RingBufferCard.vue')
  const wrapper = mount(RingBufferCard, {
    global: {
      stubs: {
        RouterLink: { template: '<a href="#"><slot /></a>' },
        Spinner: { template: '<span class="spinner" />' },
        Modal: {
          template: '<div class="modal-stub" v-if="modelValue"><slot /></div>',
          props: ['modelValue', 'title', 'maxWidth'],
        },
        SegmentStatsPanel: { template: '<div data-testid="segment-stats-panel-stub" />', props: ['store'] },
        MonitorConfigModal: {
          template: '<div class="config-modal-stub" v-if="modelValue" data-testid="config-modal-open" />',
          props: ['modelValue'],
        },
      },
    },
  })
  await flushPromises()
  return wrapper
}

// ── Zustand: Monitor deaktiviert ───────────────────────────────────────────
describe('RingBufferCard — disabled state', () => {
  it('shows the disabled block and no retention numbers', async () => {
    const wrapper = await mountCard({ enabled: false })
    expect(wrapper.find('[data-testid="rb-card-disabled"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="rb-card-budget"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="rb-card-segments"]').exists()).toBe(false)
  })

  it('opens the config modal via the Konfigurieren button', async () => {
    const wrapper = await mountCard({ enabled: false })
    expect(wrapper.find('[data-testid="config-modal-open"]').exists()).toBe(false)
    await wrapper.find('[data-testid="rb-card-configure-disabled"]').trigger('click')
    expect(wrapper.find('[data-testid="config-modal-open"]').exists()).toBe(true)
  })
})

// ── Zustand: segmentiert ───────────────────────────────────────────────────
describe('RingBufferCard — segmented state', () => {
  it('renders a budget bar with used / max text', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments }))
    expect(wrapper.find('[data-testid="rb-card-budget-bar"]').exists()).toBe(true)
    const text = wrapper.find('[data-testid="rb-card-budget-text"]').text()
    expect(text).toContain('MiB')
    expect(text).toContain('/')
  })

  it('shows unlimited instead of a bar when max_file_size_bytes is null', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments, maxFileSizeBytes: null }))
    expect(wrapper.find('[data-testid="rb-card-budget-bar"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="rb-card-budget-unlimited"]').text()).toContain('unbegrenzt')
  })

  it('shows segment count and the full prognosis block', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments, prognosis: fullPrognosis }))
    expect(wrapper.find('[data-testid="rb-card-segments"]').text()).toBe('2')
    // Volle Prognose (#919/#938) statt nur Retention-Horizont: Rate + Retention + Budget.
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-retention"]').text()).toContain('Tage')
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(true)
  })

  it('shows the warming-up hint when prognosis rate fields are unavailable', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments, prognosis: { estimated_retention_seconds: null } }))
    expect(wrapper.find('[data-testid="prognosis-warming"]').text()).toContain('läuft sich noch ein')
  })

  it('omits the prognosis budget line when segment_max_age is missing', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments, prognosis: fullPrognosis, segmentMaxAge: null }))
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(false)
  })

  it('does NOT show the problem banner when all segments are healthy', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments }))
    expect(wrapper.find('[data-testid="rb-card-problem"]').exists()).toBe(false)
  })

  it('shows the problem banner with the canonical summary wording (same as the dialog)', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: problemSegments }))
    const banner = wrapper.find('[data-testid="rb-card-problem"]')
    expect(banner.exists()).toBe(true)
    // Kanonische Formulierung wie im Segment-Dialog (#919/#938), nicht mehr „betroffen".
    expect(banner.text()).toContain('Probleme:')
    expect(banner.text()).toContain('1 beschädigt')
    expect(banner.text()).toContain('1 isoliert')
    expect(banner.text()).not.toContain('betroffen')
  })

  it('shows only a soft info hint for retention_over_budget (no alarm)', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments, overBudget: true }))
    expect(wrapper.find('[data-testid="rb-card-problem"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="rb-card-over-budget"]').exists()).toBe(true)
  })

  it('opens the segment details modal hosting SegmentStatsPanel', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments }))
    expect(wrapper.find('[data-testid="segment-stats-panel-stub"]').exists()).toBe(false)
    await wrapper.find('[data-testid="rb-card-open-segments"]').trigger('click')
    expect(wrapper.find('[data-testid="segment-stats-panel-stub"]').exists()).toBe(true)
  })

  it('opens the config modal from the segmented state', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments }))
    await wrapper.find('[data-testid="rb-card-configure"]').trigger('click')
    expect(wrapper.find('[data-testid="config-modal-open"]').exists()).toBe(true)
  })
})

// ── Zustand: Legacy ────────────────────────────────────────────────────────
describe('RingBufferCard — legacy state', () => {
  it('shows entries and file size, no segment section', async () => {
    const wrapper = await mountCard({ enabled: true, store: null, total: 1234, file_size_bytes: 5 * 1024 * 1024 })
    expect(wrapper.find('[data-testid="rb-card-legacy"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="rb-card-legacy-total"]').text()).toContain('1')
    expect(wrapper.find('[data-testid="rb-card-legacy-size"]').text()).toContain('MiB')
    expect(wrapper.find('[data-testid="rb-card-open-segments"]').exists()).toBe(false)
  })

  it('opens the config modal from the legacy state', async () => {
    const wrapper = await mountCard({ enabled: true, store: null, total: 10, file_size_bytes: 0 })
    await wrapper.find('[data-testid="rb-card-configure"]').trigger('click')
    expect(wrapper.find('[data-testid="config-modal-open"]').exists()).toBe(true)
  })
})

// ── Fehler / Laden ─────────────────────────────────────────────────────────
describe('RingBufferCard — error state', () => {
  it('shows the error message when stats() rejects', async () => {
    statsMock.mockRejectedValue(new Error('boom'))
    vi.doMock('@/api/client', () => ({ ringbufferApi: { stats: statsMock, config: vi.fn() } }))
    const { default: RingBufferCard } = await import('@/components/dashboard/RingBufferCard.vue')
    const wrapper = mount(RingBufferCard, {
      global: {
        stubs: {
          RouterLink: { template: '<a href="#"><slot /></a>' },
          Spinner: { template: '<span class="spinner" />' },
          Modal: { template: '<div v-if="modelValue"><slot /></div>', props: ['modelValue', 'title', 'maxWidth'] },
          SegmentStatsPanel: { template: '<div />', props: ['store'] },
          MonitorConfigModal: { template: '<div v-if="modelValue" />', props: ['modelValue'] },
        },
      },
    })
    await flushPromises()
    expect(wrapper.find('[data-testid="rb-card-error"]').exists()).toBe(true)
  })
})
