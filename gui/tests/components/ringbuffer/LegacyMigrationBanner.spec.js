/**
 * Tests for LegacyMigrationBanner (#966) — Hinweis-Balken des
 * Migrations-Assistenten.
 *
 * Deckt ab: Rendern nur bei decision=pending + Legacy + Admin, „Später"-Klick
 * postet die skip-Entscheidung, Eskalations-Variante (amber) sowie das
 * „Assistent öffnen"-Event.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'

const migrationStatus = vi.fn()
const migrationDecision = vi.fn()
const migrationStart = vi.fn()

beforeEach(() => {
  vi.resetModules()
  migrationStatus.mockReset()
  migrationDecision.mockReset()
  migrationStart.mockReset()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/stores/auth')
})

function statusPayload(over = {}) {
  return {
    decision: 'pending',
    retention_protected: true,
    legacy: { path: '/data/ringbuffer.db', status: 'legacy', size_bytes: 5 * 1024 * 1024, row_estimate: 1200, from_ts: '2025-06-01T00:00:00Z', to_ts: '2026-06-01T00:00:00Z', retention_protected: true },
    disk_free_bytes: 500 * 1024 * 1024,
    budget_bytes: 100 * 1024 * 1024,
    over_budget: false,
    estimated_seconds_until_budget: null,
    job: { phase: 'idle', copied_rows: 0, total_rows: 0, copied_bytes: 0, dropped_rows: 0, error: null },
    ...over,
  }
}

async function mountBanner(statusData, { isAdmin = true, props = {} } = {}) {
  migrationStatus.mockResolvedValue({ data: statusData })
  vi.doMock('@/api/client', () => ({
    ringbufferApi: { migrationStatus, migrationDecision, migrationStart },
  }))
  vi.doMock('@/stores/auth', () => ({
    useAuthStore: () => ({ isAdmin }),
  }))
  const { default: LegacyMigrationBanner } = await import('@/components/ringbuffer/LegacyMigrationBanner.vue')
  const wrapper = mount(LegacyMigrationBanner, { props })
  await flushPromises()
  return wrapper
}

describe('LegacyMigrationBanner — Sichtbarkeit', () => {
  it('renders for admins while decision=pending and legacy exists', async () => {
    const wrapper = await mountBanner(statusPayload())
    expect(wrapper.find('[data-testid="legacy-migration-banner"]').exists()).toBe(true)
  })

  it('does not render (and does not fetch) for non-admins', async () => {
    const wrapper = await mountBanner(statusPayload(), { isAdmin: false })
    expect(wrapper.find('[data-testid="legacy-migration-banner"]').exists()).toBe(false)
    expect(migrationStatus).not.toHaveBeenCalled()
  })

  it('does not render once a decision was made (skipped)', async () => {
    const wrapper = await mountBanner(statusPayload({ decision: 'skipped' }))
    expect(wrapper.find('[data-testid="legacy-migration-banner"]').exists()).toBe(false)
  })

  it('does not render without a legacy source', async () => {
    const wrapper = await mountBanner(statusPayload({ legacy: null }))
    expect(wrapper.find('[data-testid="legacy-migration-banner"]').exists()).toBe(false)
  })

  it.each(['migrated', 'discarded'])(
    'stays visible for a stale %s marker while the legacy source still exists',
    async (decision) => {
      const wrapper = await mountBanner(statusPayload({ decision }))
      expect(wrapper.find('[data-testid="legacy-migration-banner"]').exists()).toBe(true)
    }
  )
})

describe('LegacyMigrationBanner — Aktionen', () => {
  it('emits "open" when the assistant button is clicked', async () => {
    const wrapper = await mountBanner(statusPayload())
    await wrapper.find('[data-testid="legacy-banner-open"]').trigger('click')
    expect(wrapper.emitted('open')).toHaveLength(1)
  })

  it('posts decision=skip on "Später" and hides the banner afterwards', async () => {
    const wrapper = await mountBanner(statusPayload())
    migrationDecision.mockResolvedValue({ data: statusPayload({ decision: 'skipped' }) })
    await wrapper.find('[data-testid="legacy-banner-later"]').trigger('click')
    await flushPromises()
    expect(migrationDecision).toHaveBeenCalledWith('skip')
    expect(wrapper.find('[data-testid="legacy-migration-banner"]').exists()).toBe(false)
  })
})

describe('LegacyMigrationBanner — Eskalation', () => {
  it('uses the neutral variant without budget pressure', async () => {
    const wrapper = await mountBanner(statusPayload())
    const banner = wrapper.find('[data-testid="legacy-migration-banner"]')
    expect(banner.attributes('data-escalated')).toBe('false')
    expect(banner.classes().join(' ')).toContain('border-blue-500/30')
  })

  it('switches to the warn variant when over budget', async () => {
    const wrapper = await mountBanner(statusPayload({ over_budget: true, estimated_seconds_until_budget: 0 }))
    const banner = wrapper.find('[data-testid="legacy-migration-banner"]')
    expect(banner.attributes('data-escalated')).toBe('true')
    expect(banner.classes().join(' ')).toContain('border-amber-500/40')
  })

  it('escalates when the prognosis falls below seven days', async () => {
    const wrapper = await mountBanner(statusPayload({ estimated_seconds_until_budget: 2 * 24 * 3600 }))
    expect(wrapper.find('[data-testid="legacy-migration-banner"]').attributes('data-escalated')).toBe('true')
  })
})

describe('LegacyMigrationBanner — Admin-State-Race (#968)', () => {
  it('refreshes when isAdmin becomes true after mount (async loadMe)', async () => {
    // App.vue lädt auth.loadMe() async NACH dem Mount der Kind-Komponenten – isAdmin
    // ist beim Mount noch false. Ein reines onMounted-if-isAdmin würde den Refresh
    // verpassen; der Watcher muss ihn nachholen, sobald isAdmin true wird.
    const authState = reactive({ isAdmin: false })
    migrationStatus.mockResolvedValue({ data: statusPayload() })
    vi.doMock('@/api/client', () => ({
      ringbufferApi: { migrationStatus, migrationDecision, migrationStart },
    }))
    vi.doMock('@/stores/auth', () => ({ useAuthStore: () => authState }))
    const { default: LegacyMigrationBanner } = await import('@/components/ringbuffer/LegacyMigrationBanner.vue')
    const wrapper = mount(LegacyMigrationBanner)
    await flushPromises()

    // Beim Mount noch kein Admin → kein Fetch, kein Banner.
    expect(migrationStatus).not.toHaveBeenCalled()
    expect(wrapper.find('[data-testid="legacy-migration-banner"]').exists()).toBe(false)

    // loadMe() ist durch: isAdmin wird true → Watcher holt den Refresh nach.
    authState.isAdmin = true
    await flushPromises()
    expect(migrationStatus).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="legacy-migration-banner"]').exists()).toBe(true)
  })
})

describe('LegacyMigrationBanner — Kompakt-Variante', () => {
  it('hides the body copy in compact mode but keeps the actions', async () => {
    const wrapper = await mountBanner(statusPayload(), { props: { compact: true } })
    const banner = wrapper.find('[data-testid="legacy-migration-banner"]')
    expect(banner.exists()).toBe(true)
    expect(banner.findAll('p')).toHaveLength(1) // nur der Titel
    expect(wrapper.find('[data-testid="legacy-banner-open"]').exists()).toBe(true)
  })
})
