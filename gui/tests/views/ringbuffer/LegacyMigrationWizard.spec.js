/**
 * Tests for LegacyMigrationWizard (#966) — Modal des Migrations-Assistenten.
 *
 * Deckt ab: Ist-Analyse inkl. Alt-Default-Budget-Hinweis + open-config-Event,
 * die drei Options-Karten, Bestätigungspflicht beim Verwerfen, Fortschritt bei
 * laufendem Migrationsjob, Fehlertext bei phase=failed und Abschluss-Ansicht.
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

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
  vi.doUnmock('@/components/ui/Modal.vue')
})

function job(over = {}) {
  return { phase: 'idle', copied_rows: 0, total_rows: 0, copied_bytes: 0, dropped_rows: 0, error: null, ...over }
}

function statusPayload(over = {}) {
  return {
    decision: 'pending',
    retention_protected: true,
    legacy: {
      path: '/data/ringbuffer.db',
      status: 'legacy',
      size_bytes: 512 * 1024 * 1024,
      row_estimate: 123456,
      from_ts: '2025-06-01T00:00:00Z',
      to_ts: '2026-06-01T00:00:00Z',
      retention_protected: true,
    },
    disk_free_bytes: 5 * 1024 * 1024 * 1024,
    budget_bytes: 100 * 1024 * 1024,
    over_budget: false,
    estimated_seconds_until_budget: null,
    job: job(),
    ...over,
  }
}

async function mountWizard(statusData) {
  migrationStatus.mockResolvedValue({ data: statusData })
  vi.doMock('@/api/client', () => ({
    ringbufferApi: { migrationStatus, migrationDecision, migrationStart },
  }))
  // Modal stubben, damit der Slot ohne Teleport/Transition rendert.
  vi.doMock('@/components/ui/Modal.vue', () => ({
    default: {
      name: 'Modal',
      props: ['modelValue', 'title', 'maxWidth'],
      emits: ['update:modelValue'],
      template: '<div v-if="modelValue" data-testid="modal-stub"><slot /><slot name="footer" /></div>',
    },
  }))
  const { default: LegacyMigrationWizard } = await import('@/views/ringbuffer/LegacyMigrationWizard.vue')
  const wrapper = mount(LegacyMigrationWizard, { props: { modelValue: false } })
  // Öffnen wie im echten Ablauf: v-model-Wechsel triggert den Status-Refresh.
  await wrapper.setProps({ modelValue: true })
  await flushPromises()
  return wrapper
}

describe('LegacyMigrationWizard — Ist-Analyse + Optionen', () => {
  it('shows the analysis and all three option cards', async () => {
    const wrapper = await mountWizard(statusPayload())
    expect(wrapper.find('[data-testid="wizard-analysis"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="wizard-legacy-size"]').text()).toContain('MiB')
    expect(wrapper.find('[data-testid="wizard-timespan"]').text()).toContain('–')
    expect(wrapper.find('[data-testid="wizard-option-migrate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="wizard-option-keep"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="wizard-option-discard"]').exists()).toBe(true)
    // 100-MiB-Budget → kein Alt-Default-Hinweis.
    // 512-MiB-Legacy bei 100-MiB-Budget: verlustfreie Uebernahme unmoeglich -> Hinweis.
    expect(wrapper.find('[data-testid="wizard-budget-hint"]').exists()).toBe(true)
  })

  it('hides the budget hint when the budget covers a lossless takeover (>= 2x legacy)', async () => {
    const wrapper = await mountWizard(statusPayload({ budget_bytes: 2 * 512 * 1024 * 1024 }))
    expect(wrapper.find('[data-testid="wizard-budget-hint"]').exists()).toBe(false)
  })

  it('flags the legacy 10 MiB default budget and emits open-config', async () => {
    const wrapper = await mountWizard(statusPayload({ budget_bytes: 10 * 1024 * 1024 }))
    expect(wrapper.find('[data-testid="wizard-budget-hint"]').exists()).toBe(true)
    await wrapper.find('[data-testid="wizard-open-config"]').trigger('click')
    expect(wrapper.emitted('open-config')).toHaveLength(1)
  })

  it('shows the disk verdict as negative when free space is below the budget', async () => {
    const wrapper = await mountWizard(statusPayload({ disk_free_bytes: 50 * 1024 * 1024 }))
    const verdict = wrapper.find('[data-testid="wizard-disk-verdict"]')
    expect(verdict.exists()).toBe(true)
    expect(verdict.classes().join(' ')).toContain('text-red-600')
    // Roter Disk-Check blockiert den Start.
    expect(wrapper.find('[data-testid="wizard-migrate-start"]').attributes('disabled')).toBeDefined()
  })

  it('checks free space against the estimated copy size, not the whole budget (#968)', async () => {
    // Großes Retention-Budget (5 GiB), aber der Job kopiert nur ~200 MiB. Freier
    // Platz (1 GiB) liegt UNTER dem Budget, aber deutlich ÜBER dem Copy-Bedarf –
    // die Migration darf NICHT blockiert werden.
    const wrapper = await mountWizard(
      statusPayload({
        budget_bytes: 5 * 1024 * 1024 * 1024,
        estimated_copy_bytes: 200 * 1024 * 1024,
        disk_free_bytes: 1024 * 1024 * 1024,
      })
    )
    const verdict = wrapper.find('[data-testid="wizard-disk-verdict"]')
    expect(verdict.exists()).toBe(true)
    expect(verdict.classes().join(' ')).toContain('text-green-600')
    expect(wrapper.find('[data-testid="wizard-migrate-start"]').attributes('disabled')).toBeUndefined()
  })
})

describe('LegacyMigrationWizard — Migrieren', () => {
  it('starts the migration job via the API', async () => {
    const wrapper = await mountWizard(statusPayload())
    migrationStart.mockResolvedValue({
      data: statusPayload({ job: job({ phase: 'copying', copied_rows: 1, total_rows: 100 }) }),
    })
    await wrapper.find('[data-testid="wizard-migrate-start"]').trigger('click')
    await flushPromises()
    expect(migrationStart).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="wizard-migrate-progress"]').exists()).toBe(true)
    // Danach terminalen Status liefern, damit das Job-Polling wieder stoppt.
    migrationStatus.mockResolvedValue({ data: statusPayload({ decision: 'migrated', job: job({ phase: 'done' }) }) })
  })

  it('shows progress while the job is copying and disables the start button', async () => {
    const wrapper = await mountWizard(
      statusPayload({ job: job({ phase: 'copying', copied_rows: 50, total_rows: 100 }) })
    )
    const progress = wrapper.find('[data-testid="wizard-migrate-progress"]')
    expect(progress.exists()).toBe(true)
    expect(wrapper.find('[data-testid="wizard-migrate-rows"]').text()).toContain('50')
    expect(wrapper.find('[data-testid="wizard-migrate-rows"]').text()).toContain('100')
    expect(wrapper.find('[data-testid="wizard-migrate-start"]').attributes('disabled')).toBeDefined()
    // Polling wieder abstellen: nächster Tick liefert einen terminalen Status.
    migrationStatus.mockResolvedValue({ data: statusPayload({ decision: 'migrated', job: job({ phase: 'done' }) }) })
  })

  it('shows the error text when the job failed', async () => {
    const wrapper = await mountWizard(
      statusPayload({ job: job({ phase: 'failed', error: 'disk full' }) })
    )
    const error = wrapper.find('[data-testid="wizard-migrate-error"]')
    expect(error.exists()).toBe(true)
    expect(error.text()).toContain('disk full')
    // failed ist terminal → Start wieder möglich.
    expect(wrapper.find('[data-testid="wizard-migrate-start"]').attributes('disabled')).toBeUndefined()
  })

  it('shows the finished view when the decision is terminal (migrated)', async () => {
    const wrapper = await mountWizard(statusPayload({ decision: 'migrated', legacy: null, job: job({ phase: 'done' }) }))
    expect(wrapper.find('[data-testid="wizard-finished"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="wizard-options"]').exists()).toBe(false)
  })
})

describe('LegacyMigrationWizard — Behalten', () => {
  it('shows the prognosis in days and posts decision=keep', async () => {
    const wrapper = await mountWizard(statusPayload({ estimated_seconds_until_budget: 12 * 24 * 3600 }))
    expect(wrapper.find('[data-testid="wizard-keep-eta"]').text()).toContain('12')
    migrationDecision.mockResolvedValue({ data: statusPayload({ decision: 'keep' }) })
    await wrapper.find('[data-testid="wizard-keep"]').trigger('click')
    await flushPromises()
    expect(migrationDecision).toHaveBeenCalledWith('keep')
    expect(wrapper.find('[data-testid="wizard-finished"]').exists()).toBe(true)
  })
})

describe('LegacyMigrationWizard — Verwerfen', () => {
  it('requires confirmation before posting decision=discard', async () => {
    const wrapper = await mountWizard(statusPayload())
    migrationDecision.mockResolvedValue({ data: statusPayload({ decision: 'discarded', legacy: null }) })

    // Klick auf Verwerfen öffnet NUR den Bestätigungsdialog, kein API-Call.
    await wrapper.find('[data-testid="wizard-discard"]').trigger('click')
    expect(migrationDecision).not.toHaveBeenCalled()
    const confirmBtn = wrapper.find('[data-testid="btn-confirm"]')
    expect(confirmBtn.exists()).toBe(true)

    await confirmBtn.trigger('click')
    await flushPromises()
    expect(migrationDecision).toHaveBeenCalledWith('discard')
    expect(wrapper.find('[data-testid="wizard-finished"]').exists()).toBe(true)
  })

  it('shows the space to be freed on the discard card', async () => {
    const wrapper = await mountWizard(statusPayload())
    expect(wrapper.find('[data-testid="wizard-discard-freed"]').text()).toContain('512')
  })
})
