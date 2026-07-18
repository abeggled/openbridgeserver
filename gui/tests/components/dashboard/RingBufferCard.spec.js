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
  vi.doUnmock('@/stores/auth')
})

function segmentedPayload({ segments = [], maxFileSizeBytes = 100 * 1024 * 1024, sizeBytes = 30 * 1024 * 1024, retentionSeconds = 3 * 24 * 3600, overBudget = false, prognosis, segmentMaxAge = 6 * 3600, maxAge = null, statsOverrides = {} } = {}) {
  return {
    enabled: true,
    total: 800,
    file_size_bytes: sizeBytes,
    max_file_size_bytes: maxFileSizeBytes,
    max_age: maxAge,
    segment_max_age: segmentMaxAge,
    // Volle Prognose (#919/#938): PrognosisBlock rendert Rate/Retention/Budget.
    // Default: nur der Retention-Horizont; einzelne Tests überschreiben prognosis.
    prognosis: prognosis ?? { estimated_retention_seconds: retentionSeconds },
    store: {
      common: { segment_count: segments.length, size_bytes: sizeBytes, oldest_ts: null, newest_ts: null },
      backend_extra: { segments, retention_over_budget: overBudget, retention_pressure_reason: null },
    },
    ...statsOverrides,
  }
}

const fullPrognosis = {
  bytes_per_hour: 50 * 1024 * 1024,
  rows_per_hour: 12000,
  avg_segment_seconds: 6 * 3600,
  estimated_retention_seconds: 5 * 24 * 3600,
  effective_segment_max_bytes: 16 * 1024 * 1024,
}

const healthySegments = [
  { segment_id: 2, status: 'active', integrity_status: 'ok', recovery_status: 'none' },
  { segment_id: 1, status: 'closed', integrity_status: 'ok', recovery_status: 'none' },
]

const problemSegments = [
  { segment_id: 2, status: 'active', integrity_status: 'ok', recovery_status: 'none' },
  { segment_id: 1, status: 'quarantined', integrity_status: 'corrupt', recovery_status: 'quarantined' },
]

async function mountCard(statsData, { isAdmin = true } = {}) {
  statsMock.mockResolvedValue({ data: statsData })
  vi.doMock('@/api/client', () => ({
    ringbufferApi: { stats: statsMock, config: vi.fn() },
  }))
  // Auth-Store mocken: nur isAdmin ist für das Gating der Konfig-Aktionen relevant (#938).
  vi.doMock('@/stores/auth', () => ({
    useAuthStore: () => ({ isAdmin }),
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
          template:
            '<div class="config-modal-stub" v-if="modelValue" data-testid="config-modal-open"><button data-testid="config-close-stub" @click="$emit(\'update:modelValue\', false)" /></div>',
          props: ['modelValue'],
          emits: ['update:modelValue'],
        },
        LegacyMigrationBanner: {
          template: '<button data-testid="banner-open-stub" @click="$emit(\'open\')" />',
          emits: ['open'],
        },
        LegacyMigrationWizard: {
          template:
            '<div v-if="modelValue" data-testid="wizard-open"><button data-testid="wizard-openconfig-stub" @click="$emit(\'open-config\')" /></div>',
          props: ['modelValue'],
          emits: ['update:modelValue', 'open-config'],
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
    // Volle Prognose (#919/#938): Durchsatz + Rotation + Historie + Budget.
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-rotation"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-history"]').text()).toContain('Tage')
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(true)
  })

  it('feeds effective row and derived-age limits into the dashboard prognosis', async () => {
    const wrapper = await mountCard(segmentedPayload({
      segments: healthySegments,
      segmentMaxAge: null,
      prognosis: { ...fullPrognosis, effective_segment_max_bytes: null },
      statsOverrides: {
        effective_segment_max_age: 12 * 3600,
        effective_segment_max_bytes: null,
        effective_segment_max_rows: 6000,
      },
    }))
    expect(wrapper.find('[data-testid="prognosis-rotation"]').text()).toContain('Zeilen')
    expect(wrapper.find('[data-testid="prognosis-budget"]').text()).toContain('12')
  })

  it('shows other retention limits instead of unlimited growth without a disk budget', async () => {
    const wrapper = await mountCard(segmentedPayload({
      segments: healthySegments,
      maxFileSizeBytes: null,
      prognosis: fullPrognosis,
      statsOverrides: { retention_unbounded: false },
    }))
    const history = wrapper.find('[data-testid="prognosis-history"]').text()
    expect(history).toContain('andere Gesamtgrenzen')
    expect(history).not.toContain('Historie wächst')
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

  it('shows a RED retention signal (Fall B) when the budget floor is breached (retention_over_budget)', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments, overBudget: true }))
    const sig = wrapper.find('[data-testid="rb-card-retention-signal"]')
    expect(sig.exists()).toBe(true)
    expect(sig.classes().join(' ')).toContain('bg-red-500/10')
    expect(sig.text()).toContain('Budget zu klein')
    // Ein voller Budget-Füllstand ist KEIN Segment-Problem.
    expect(wrapper.find('[data-testid="rb-card-problem"]').exists()).toBe(false)
  })

  it('shows NO retention signal in normal operation (budget filling is not an alarm)', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments }))
    expect(wrapper.find('[data-testid="rb-card-retention-signal"]').exists()).toBe(false)
  })

  it('shows an AMBER retention signal (Fall A) when estimated retention is below the max_age target', async () => {
    const wrapper = await mountCard(segmentedPayload({
      segments: healthySegments,
      prognosis: { ...fullPrognosis, estimated_retention_seconds: 12 * 3600 }, // ~12 h Ist
      maxAge: 5 * 24 * 3600, // Ziel 5 Tage
    }))
    const sig = wrapper.find('[data-testid="rb-card-retention-signal"]')
    expect(sig.exists()).toBe(true)
    expect(sig.classes().join(' ')).toContain('bg-amber-500/10')
    expect(sig.text()).toContain('Retention unter Ziel')
  })

  it('keeps the budget bar neutral (blue) even at high fill — full budget is normal', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments, sizeBytes: 98 * 1024 * 1024, maxFileSizeBytes: 100 * 1024 * 1024 }))
    const bar = wrapper.find('[data-testid="rb-card-budget-bar"] > div')
    expect(bar.classes().join(' ')).toContain('bg-blue-500')
    expect(bar.classes().join(' ')).not.toContain('bg-amber-500')
    // Super-kurzer Sägezahn-Hinweis unter der Leiste.
    const hint = wrapper.find('[data-testid="rb-card-budget-peak-hint"]')
    expect(hint.exists()).toBe(true)
    expect(hint.text()).toContain('133')
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

// ── Admin-Gating der Konfig-Aktionen (#938) ────────────────────────────────
// Die Dashboard-Route ist für alle authentifizierten Nutzer erreichbar, aber
// das Speichern der Config ist admin-only (Backend gibt sonst 403). Deshalb
// werden die Konfig-Einstiege wie in RingBufferView mit auth.isAdmin gegatet.
describe('RingBufferCard — admin gating of config actions', () => {
  it('non-admin: no configure button in the disabled state, rest stays', async () => {
    const wrapper = await mountCard({ enabled: false }, { isAdmin: false })
    expect(wrapper.find('[data-testid="rb-card-disabled"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="rb-card-configure-disabled"]').exists()).toBe(false)
  })

  it('admin: configure button present in the disabled state', async () => {
    const wrapper = await mountCard({ enabled: false }, { isAdmin: true })
    expect(wrapper.find('[data-testid="rb-card-configure-disabled"]').exists()).toBe(true)
  })

  it('non-admin: no configure button in the segmented state, stats/prognosis/details stay', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments, prognosis: fullPrognosis }), { isAdmin: false })
    expect(wrapper.find('[data-testid="rb-card-configure"]').exists()).toBe(false)
    // Read-only-Teile der Karte bleiben für alle sichtbar.
    expect(wrapper.find('[data-testid="rb-card-segments"]').text()).toBe('2')
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="rb-card-open-segments"]').exists()).toBe(true)
  })

  it('admin: configure button present in the segmented state', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments }), { isAdmin: true })
    expect(wrapper.find('[data-testid="rb-card-configure"]').exists()).toBe(true)
  })

  it('non-admin: no configure button in the legacy state, stats stay', async () => {
    const wrapper = await mountCard({ enabled: true, store: null, total: 10, file_size_bytes: 0 }, { isAdmin: false })
    expect(wrapper.find('[data-testid="rb-card-legacy"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="rb-card-configure"]').exists()).toBe(false)
  })
})

// ── Fehler / Laden ─────────────────────────────────────────────────────────
describe('RingBufferCard — error state', () => {
  it('shows the error message when stats() rejects', async () => {
    statsMock.mockRejectedValue(new Error('boom'))
    vi.doMock('@/api/client', () => ({ ringbufferApi: { stats: statsMock, config: vi.fn() } }))
    vi.doMock('@/stores/auth', () => ({ useAuthStore: () => ({ isAdmin: true }) }))
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

// ── Wizard ↔ Konfigurator-Wechselspiel ──────────────────────────────────────
describe('RingBufferCard — Wizard/Konfig-Wechselspiel', () => {
  it('schließt den Wizard, solange die Konfig offen ist, und öffnet ihn danach wieder', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments }))

    // Wizard über den Banner öffnen.
    await wrapper.find('[data-testid="banner-open-stub"]').trigger('click')
    expect(wrapper.find('[data-testid="wizard-open"]').exists()).toBe(true)

    // "Konfiguration öffnen" im Wizard: Konfig-Modal auf, Wizard ZU
    // (sonst läge das Konfig-Modal hinter dem Wizard).
    await wrapper.find('[data-testid="wizard-openconfig-stub"]').trigger('click')
    expect(wrapper.find('[data-testid="config-modal-open"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="wizard-open"]').exists()).toBe(false)

    // Konfig schließen: Wizard kommt automatisch zurück (lädt Status frisch).
    await wrapper.find('[data-testid="config-close-stub"]').trigger('click')
    expect(wrapper.find('[data-testid="config-modal-open"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="wizard-open"]').exists()).toBe(true)
  })

  it('normales Konfig-Öffnen (ohne Wizard) öffnet den Wizard NICHT beim Schließen', async () => {
    const wrapper = await mountCard(segmentedPayload({ segments: healthySegments }))
    await wrapper.find('[data-testid="rb-card-configure"]').trigger('click')
    await wrapper.find('[data-testid="config-close-stub"]').trigger('click')
    expect(wrapper.find('[data-testid="wizard-open"]').exists()).toBe(false)
  })
})
