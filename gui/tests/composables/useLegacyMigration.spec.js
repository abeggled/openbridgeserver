/**
 * Tests for useLegacyMigration (#966) — geteilter Zustand des
 * Migrations-Assistenten (Banner, Wizard, Segment-Panel-Einstieg).
 *
 * Deckt ab: showBanner-/escalated-Ableitung, decide()/startMigration()-Aufrufe
 * inkl. Status-Übernahme sowie das 1-s-Polling während eines laufenden Jobs.
 * Der Composable hält Modul-Singleton-Zustand → vi.resetModules() pro Test.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

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
  vi.useRealTimers()
})

function legacyPayload(over = {}) {
  return {
    path: '/data/ringbuffer.db',
    status: 'legacy',
    size_bytes: 5 * 1024 * 1024,
    row_estimate: 1200,
    from_ts: '2025-06-01T00:00:00Z',
    to_ts: '2026-06-01T00:00:00Z',
    retention_protected: true,
    ...over,
  }
}

function statusPayload(over = {}) {
  return {
    decision: 'pending',
    retention_protected: true,
    legacy: legacyPayload(),
    disk_free_bytes: 500 * 1024 * 1024,
    budget_bytes: 100 * 1024 * 1024,
    over_budget: false,
    estimated_seconds_until_budget: null,
    job: { phase: 'idle', copied_rows: 0, total_rows: 0, copied_bytes: 0, dropped_rows: 0, error: null },
    ...over,
  }
}

async function loadComposable() {
  vi.doMock('@/api/client', () => ({
    ringbufferApi: { migrationStatus, migrationDecision, migrationStart },
  }))
  const { useLegacyMigration } = await import('@/composables/useLegacyMigration')
  return useLegacyMigration()
}

describe('useLegacyMigration — showBanner', () => {
  it('is true only while decision=pending and a legacy source exists', async () => {
    const api = await loadComposable()
    expect(api.showBanner.value).toBe(false)

    migrationStatus.mockResolvedValue({ data: statusPayload() })
    await api.refresh()
    expect(api.showBanner.value).toBe(true)

    migrationStatus.mockResolvedValue({ data: statusPayload({ decision: 'skipped' }) })
    await api.refresh()
    expect(api.showBanner.value).toBe(false)

    migrationStatus.mockResolvedValue({ data: statusPayload({ legacy: null }) })
    await api.refresh()
    expect(api.showBanner.value).toBe(false)
  })
})

describe('useLegacyMigration — escalated', () => {
  it('escalates on over_budget and on ETA below 7 days (0 inclusive)', async () => {
    const api = await loadComposable()
    expect(api.escalated.value).toBe(false)

    migrationStatus.mockResolvedValue({ data: statusPayload({ over_budget: true, estimated_seconds_until_budget: 0 }) })
    await api.refresh()
    expect(api.escalated.value).toBe(true)

    migrationStatus.mockResolvedValue({ data: statusPayload({ estimated_seconds_until_budget: 3 * 24 * 3600 }) })
    await api.refresh()
    expect(api.escalated.value).toBe(true)

    migrationStatus.mockResolvedValue({ data: statusPayload({ estimated_seconds_until_budget: 0 }) })
    await api.refresh()
    expect(api.escalated.value).toBe(true)
  })

  it('does not escalate with a far ETA or without any prognosis', async () => {
    const api = await loadComposable()

    migrationStatus.mockResolvedValue({ data: statusPayload({ estimated_seconds_until_budget: 30 * 24 * 3600 }) })
    await api.refresh()
    expect(api.escalated.value).toBe(false)

    migrationStatus.mockResolvedValue({ data: statusPayload({ estimated_seconds_until_budget: null }) })
    await api.refresh()
    expect(api.escalated.value).toBe(false)
  })
})

describe('useLegacyMigration — actions', () => {
  it('decide() posts the decision and applies the returned status', async () => {
    const api = await loadComposable()
    migrationStatus.mockResolvedValue({ data: statusPayload() })
    await api.refresh()
    expect(api.showBanner.value).toBe(true)

    migrationDecision.mockResolvedValue({ data: statusPayload({ decision: 'skipped' }) })
    await api.decide('skip')
    expect(migrationDecision).toHaveBeenCalledWith('skip')
    expect(api.decision.value).toBe('skipped')
    expect(api.showBanner.value).toBe(false)
  })

  it('startMigration() posts the start call and applies the returned status', async () => {
    const api = await loadComposable()
    migrationStart.mockResolvedValue({
      data: statusPayload({ job: { phase: 'copying', copied_rows: 10, total_rows: 100, copied_bytes: 0, dropped_rows: 0, error: null } }),
    })
    // done-Status für die Poll-Ticks, damit das Intervall wieder stoppt.
    migrationStatus.mockResolvedValue({
      data: statusPayload({ decision: 'migrated', job: { phase: 'done', copied_rows: 100, total_rows: 100, copied_bytes: 0, dropped_rows: 0, error: null } }),
    })
    await api.startMigration()
    expect(migrationStart).toHaveBeenCalledTimes(1)
    expect(api.jobRunning.value).toBe(true)
  })

  it('refresh() marks loadError and rethrows on failure', async () => {
    const api = await loadComposable()
    migrationStatus.mockRejectedValue(new Error('boom'))
    await expect(api.refresh()).rejects.toThrow('boom')
    expect(api.loadError.value).toBe(true)
  })
})

describe('useLegacyMigration — polling', () => {
  it('polls every second while the job runs and stops once it is done', async () => {
    vi.useFakeTimers()
    const api = await loadComposable()

    migrationStart.mockResolvedValue({
      data: statusPayload({ job: { phase: 'copying', copied_rows: 10, total_rows: 100, copied_bytes: 0, dropped_rows: 0, error: null } }),
    })
    migrationStatus.mockResolvedValue({
      data: statusPayload({ job: { phase: 'copying', copied_rows: 50, total_rows: 100, copied_bytes: 0, dropped_rows: 0, error: null } }),
    })
    await api.startMigration()
    expect(migrationStatus).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(1000)
    expect(migrationStatus).toHaveBeenCalledTimes(1)
    expect(api.job.value.copied_rows).toBe(50)

    // Job terminal → Polling stoppt (kein weiterer GET nach dem done-Tick).
    migrationStatus.mockResolvedValue({
      data: statusPayload({ decision: 'migrated', job: { phase: 'done', copied_rows: 100, total_rows: 100, copied_bytes: 0, dropped_rows: 0, error: null } }),
    })
    await vi.advanceTimersByTimeAsync(1000)
    expect(migrationStatus).toHaveBeenCalledTimes(2)
    expect(api.jobRunning.value).toBe(false)

    await vi.advanceTimersByTimeAsync(3000)
    expect(migrationStatus).toHaveBeenCalledTimes(2)
  })
})
