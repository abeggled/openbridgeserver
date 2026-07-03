/**
 * Tests for MonitorConfigModal.vue (issue #438) — QA-01 coverage audit (#439).
 *
 * The modal is the Ringbuffer config UI. It only fetches /stats once it is
 * opened (deferred-load pattern from #438) and re-hydrates the form on each
 * open. On submit it serialises the form back into the flat
 * `ringbufferApi.config()` payload and shows a success/error banner.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { defineComponent, h } from 'vue'

beforeEach(() => {
  vi.resetModules()
  document.body.innerHTML = ''
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/components/ui/ConfirmDialog.vue')
  vi.doUnmock('@/components/ui/Modal.vue')
  vi.doUnmock('@/components/ui/Spinner.vue')
})

function makeApi(overrides = {}) {
  return {
    stats: vi.fn().mockResolvedValue({
      data: {
        total: 1234,
        enabled: true,
        max_entries: 50000,
        max_file_size_bytes: 2 * 1024 * 1024 * 1024, // 2 GB
        max_age: 30 * 24 * 60 * 60, // 30 days
        effective_retention_seconds: 30 * 24 * 60 * 60,
        file_size_bytes: 1024 * 1024 * 500, // 500 MB
      },
    }),
    config: vi.fn().mockResolvedValue({
      data: {
        total: 1234,
        enabled: true,
        max_entries: 50000,
        max_file_size_bytes: 2 * 1024 * 1024 * 1024,
        max_age: 30 * 24 * 60 * 60,
        effective_retention_seconds: 30 * 24 * 60 * 60,
        file_size_bytes: 1024 * 1024 * 500,
      },
    }),
    ...overrides,
  }
}

async function mountModal({ initialOpen = true, api } = {}) {
  api = api ?? makeApi()
  vi.doMock('@/api/client', () => ({ ringbufferApi: api }))

  // Modal stub renders slot only when modelValue=true.
  vi.doMock('@/components/ui/Modal.vue', () => ({
    default: defineComponent({
      name: 'Modal',
      props: ['modelValue', 'title', 'maxWidth'],
      emits: ['update:modelValue'],
      setup(props, { slots }) {
        return () =>
          props.modelValue
            ? h('div', { 'data-testid': 'config-modal' }, [
                slots.default ? slots.default() : null,
                slots.footer ? slots.footer() : null,
              ])
            : null
      },
    }),
  }))

  vi.doMock('@/components/ui/Spinner.vue', () => ({
    default: defineComponent({
      name: 'Spinner',
      props: ['size', 'color'],
      setup() {
        return () => h('span', { 'data-testid': 'spinner' })
      },
    }),
  }))

  const mod = await import('@/views/ringbuffer/MonitorConfigModal.vue')
  const MonitorConfigModal = mod.default
  const wrapper = mount(MonitorConfigModal, {
    props: { modelValue: initialOpen },
    attachTo: document.body,
  })
  await flushPromises()
  // The first watch fires when modelValue transitions to true. The initial
  // mount with modelValue=true does not fire the watcher (watch tracks
  // changes, not the initial state) — toggle to make it observable.
  if (initialOpen) {
    await wrapper.setProps({ modelValue: false })
    await flushPromises()
    await wrapper.setProps({ modelValue: true })
    await flushPromises()
  }
  return { wrapper, api }
}

describe('MonitorConfigModal QA-01 coverage (#439)', () => {
  it('fetches /stats only after the modal opens and hydrates the form', async () => {
    const { wrapper, api } = await mountModal()
    expect(api.stats).toHaveBeenCalled()

    // Stats display
    expect(wrapper.find('[data-testid="rb-config-stats-total"]').text()).toContain('1234')
    expect(wrapper.find('[data-testid="rb-config-stats-file-size"]').text()).toContain('500')
    // 30 days is exactly 1 month per the modal's formatRetention helper.
    expect(wrapper.find('[data-testid="rb-config-stats-retention"]').text()).toMatch(/30d/)

    // Form hydration: 2 GB → unit=gb, value=2; 30 days → unit=days, value=30.
    expect(wrapper.find('[data-testid="rb-config-max-entries"]').element.value).toBe('50000')
    expect(wrapper.find('[data-testid="rb-config-max-size-value"]').element.value).toBe('2')
    expect(wrapper.find('[data-testid="rb-config-max-size-unit"]').element.value).toBe('gb')
    // 30 days = 1 month per the picker, so the form picks unit=months.
    expect(wrapper.find('[data-testid="rb-config-retention-value"]').element.value).toBe('1')
    expect(wrapper.find('[data-testid="rb-config-retention-unit"]').element.value).toBe('months')
  })

  it('documents the effective-storage sawtooth (budget is a target, ~133% peak)', async () => {
    const { wrapper } = await mountModal()
    const note = wrapper.find('[data-testid="rb-config-effective-storage-note"]')
    expect(note.exists()).toBe(true)
    expect(note.text()).toContain('133')
    expect(note.text()).toContain('Retention-Ziel')
  })

  it('hydrates with sane defaults when stats reflects an empty system', async () => {
    // After ringbuffer-config persistence: an unconfigured system reports
    // max_entries=null, max_file_size_bytes=10 MiB (sane default), max_age=null.
    // The form mirrors that state: entries+age toggles off, size toggle on
    // showing the 10 MB cap; the disabled inputs hold suggestion values.
    const api = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: {
          total: 0,
          max_entries: null,
          max_file_size_bytes: 10 * 1024 * 1024,
          max_age: null,
          file_size_bytes: 0,
        },
      }),
    })
    const { wrapper } = await mountModal({ api })

    const entriesCheck = wrapper.find('#max-entries-enabled').element
    const sizeCheck = wrapper.find('#max-size-enabled').element
    const retCheck = wrapper.find('#retention-enabled').element
    expect(entriesCheck.checked).toBe(false)
    expect(sizeCheck.checked).toBe(true)
    expect(retCheck.checked).toBe(false)

    expect(wrapper.find('[data-testid="rb-config-max-entries"]').element.value).toBe('50000')
    expect(wrapper.find('[data-testid="rb-config-max-size-value"]').element.value).toBe('10')
    expect(wrapper.find('[data-testid="rb-config-max-size-unit"]').element.value).toBe('mb')
    expect(wrapper.find('[data-testid="rb-config-retention-value"]').element.value).toBe('30')
  })

  it('formats retention values that match months / years cleanly', async () => {
    const monthsApi = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: {
          total: 0,
          max_entries: 10000,
          max_file_size_bytes: null,
          max_age: 6 * 30 * 24 * 60 * 60, // 6 months
          file_size_bytes: 0,
        },
      }),
    })
    const { wrapper } = await mountModal({ api: monthsApi })
    expect(wrapper.find('[data-testid="rb-config-retention-value"]').element.value).toBe('6')
    expect(wrapper.find('[data-testid="rb-config-retention-unit"]').element.value).toBe('months')
  })

  it('falls back silently when /stats rejects — the form still renders with initial defaults', async () => {
    const failApi = makeApi({
      stats: vi.fn().mockRejectedValue(new Error('boom')),
    })
    const { wrapper, api } = await mountModal({ api: failApi })
    expect(api.stats).toHaveBeenCalled()
    // Form is mounted with initial form defaults (matches the server-side
    // defaults: entries unlimited, 10 MiB size cap, no age cap).
    expect(wrapper.find('[data-testid="rb-config-max-entries"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="rb-config-max-entries"]').element.value).toBe('50000')
    expect(wrapper.find('#max-entries-enabled').element.checked).toBe(false)
    expect(wrapper.find('#max-size-enabled').element.checked).toBe(true)
    expect(wrapper.find('[data-testid="rb-config-max-size-value"]').element.value).toBe('10')
    expect(wrapper.find('[data-testid="rb-config-max-size-unit"]').element.value).toBe('mb')
  })

  it('submitting the form posts a flat payload and shows a success banner', async () => {
    const { wrapper, api } = await mountModal()
    // Tweak the max-entries to verify the value flows into the payload
    await wrapper.find('[data-testid="rb-config-max-entries"]').setValue(75000)
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(api.config).toHaveBeenCalledTimes(1)
    const payload = api.config.mock.calls[0][0]
    expect(payload.max_entries).toBe(75000)
    expect(payload.enabled).toBe(true)
    expect(payload.storage).toBe('file')
    // Success banner
    expect(wrapper.text()).toContain('Monitor-Konfiguration gespeichert')
  })

  it('rejects an invalid max-entries value with an inline error', async () => {
    const { wrapper, api } = await mountModal()
    await wrapper.find('[data-testid="rb-config-max-entries"]').setValue(10) // < 100
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(api.config).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('mindestens 100')
  })

  it('rejects a zero max-size value with an inline error', async () => {
    const { wrapper, api } = await mountModal()
    // Enable the size cap and set value to 0
    await wrapper.find('#max-size-enabled').setValue(true)
    await wrapper.find('[data-testid="rb-config-max-size-value"]').setValue('0')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(api.config).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Speicherplatz muss grösser')
  })

  it('shows a server-side error from the config call', async () => {
    const api = makeApi({
      config: vi.fn().mockRejectedValue({ response: { data: { detail: 'server says no' } } }),
    })
    const { wrapper } = await mountModal({ api })
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('server says no')
  })

  it('warns before disabling and only posts after confirmation', async () => {
    const api = makeApi({
      config: vi.fn().mockResolvedValue({
        data: {
          enabled: false,
          total: 0,
          max_entries: 50000,
          max_file_size_bytes: 2 * 1024 * 1024 * 1024,
          max_age: 30 * 24 * 60 * 60,
          effective_retention_seconds: null,
          file_size_bytes: 0,
        },
      }),
    })
    const { wrapper } = await mountModal({ api })

    await wrapper.find('[data-testid="rb-config-enabled"]').setValue(false)
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(api.config).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('alle bisherigen Monitor-Einträge werden gelöscht')

    await wrapper.find('[data-testid="btn-confirm"]').trigger('click')
    await flushPromises()

    expect(api.config).toHaveBeenCalledTimes(1)
    expect(api.config.mock.calls[0][0]).toEqual({ enabled: false, storage: 'file' })
    expect(wrapper.find('[data-testid="rb-config-stats-enabled"]').text()).toContain('Deaktiviert')
  })
})

describe('MonitorConfigModal segment rotation (#938)', () => {
  it('renders the segment rotation field with the 6h default and no activation toggle', async () => {
    const { wrapper } = await mountModal()
    // Segmentation is automatic — there must be no "segmented" activation toggle.
    expect(wrapper.find('[data-testid="rb-config-segmented"]').exists()).toBe(false)
    const ageField = wrapper.find('[data-testid="rb-config-segment-max-age"]')
    expect(ageField.exists()).toBe(true)
    expect(ageField.element.value).toBe('6')
  })

  it('posts segment_max_age (in seconds) on submit', async () => {
    const { wrapper, api } = await mountModal()
    await wrapper.find('[data-testid="rb-config-segment-max-age"]').setValue('8')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(api.config).toHaveBeenCalledTimes(1)
    const payload = api.config.mock.calls[0][0]
    expect(payload.segment_max_age).toBe(8 * 60 * 60)
    // Optional advanced fields are sent as explicit null (= auto) when empty, so
    // the backend does not keep a previously persisted value (#938, Codex #951).
    expect('segment_max_bytes' in payload).toBe(true)
    expect(payload.segment_max_bytes).toBeNull()
    expect('segment_max_rows' in payload).toBe(true)
    expect(payload.segment_max_rows).toBeNull()
  })

  it('includes the optional advanced segment thresholds when filled', async () => {
    const { wrapper, api } = await mountModal()
    await wrapper.find('[data-testid="rb-config-segment-max-bytes"]').setValue('4')
    await wrapper.find('[data-testid="rb-config-segment-max-bytes-unit"]').setValue('mb')
    await wrapper.find('[data-testid="rb-config-segment-max-rows"]').setValue('1000')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    const payload = api.config.mock.calls[0][0]
    expect(payload.segment_max_bytes).toBe(4 * 1024 * 1024)
    expect(payload.segment_max_rows).toBe(1000)
  })

  it('sends explicit null when a hydrated segment threshold is cleared (reset to auto, #938 Codex #951)', async () => {
    const api = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: {
          total: 1,
          enabled: true,
          max_entries: null,
          max_file_size_bytes: 500 * 1024 * 1024,
          max_age: null,
          file_size_bytes: 0,
          segment_max_age: 43200,
          segment_max_bytes: 256 * 1024 * 1024, // hydratisiert → Feld zeigt 256
          segment_max_rows: 1000, // hydratisiert → Feld zeigt 1000
        },
      }),
    })
    const { wrapper } = await mountModal({ api })
    // Beide Felder wurden aus den Stats vorbefüllt.
    expect(wrapper.find('[data-testid="rb-config-segment-max-bytes"]').element.value).toBe('256')
    expect(wrapper.find('[data-testid="rb-config-segment-max-rows"]').element.value).toBe('1000')
    // Nutzer leert sie wieder (= auf auto zurückstellen).
    await wrapper.find('[data-testid="rb-config-segment-max-bytes"]').setValue('')
    await wrapper.find('[data-testid="rb-config-segment-max-rows"]').setValue('')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(api.config).toHaveBeenCalledTimes(1)
    const payload = api.config.mock.calls[0][0]
    // Explizites null muss im Payload landen, nicht fehlen – sonst behält das
    // Backend den zuvor persistierten Wert.
    expect('segment_max_bytes' in payload).toBe(true)
    expect(payload.segment_max_bytes).toBeNull()
    expect('segment_max_rows' in payload).toBe(true)
    expect(payload.segment_max_rows).toBeNull()
  })

  it('rejects a segment age below 1 hour with an inline error', async () => {
    const { wrapper, api } = await mountModal()
    await wrapper.find('[data-testid="rb-config-segment-max-age"]').setValue('0')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(api.config).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Segment-Alter')
  })

  it('renders a friendly message for the 3-segment-rule 422, not raw backend text', async () => {
    // Echter Ratio-Fehlertext aus obs/ringbuffer/store/config.py (_check_ratio).
    const api = makeApi({
      config: vi.fn().mockRejectedValue({
        response: {
          data: {
            detail:
              'max_age (100) must be >= 3 * segment_max_age (60) = 180; segmentation is too coarse for segment-granular retention',
          },
        },
      }),
    })
    const { wrapper } = await mountModal({ api })
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('zu grob für die gewählte Aufbewahrung')
    expect(wrapper.text()).not.toContain('too coarse')
  })

  it('shows the explicit segment bounds error, not the ratio message (#938, Codex #951)', async () => {
    // Echter Grenz-Fehlertext aus validate_explicit_segment_bounds: das ``detail``
    // nennt die konkrete 300-s-Untergrenze und darf NICHT auf die (unrelated)
    // 3-Segment-Ratio-Meldung umgebogen werden.
    const api = makeApi({
      config: vi.fn().mockRejectedValue({
        response: {
          data: { detail: 'segment_max_age (60 s) must be between 300 s (5 min) and 2592000 s (30 d)' },
        },
      }),
    })
    const { wrapper } = await mountModal({ api })
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('must be between 300 s')
    expect(wrapper.text()).not.toContain('zu grob für die gewählte Aufbewahrung')
  })

  it('flattens a FastAPI validation error list into a readable line', async () => {
    const api = makeApi({
      config: vi.fn().mockRejectedValue({
        response: { data: { detail: [{ loc: ['body', 'x'], msg: 'field required' }] } },
      }),
    })
    const { wrapper } = await mountModal({ api })
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(wrapper.text()).toContain('field required')
  })

  it('hydrates the segment config from persisted stats (#919/#938)', async () => {
    const api = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: {
          total: 1,
          enabled: true,
          max_entries: null,
          max_file_size_bytes: 500 * 1024 * 1024,
          max_age: null,
          file_size_bytes: 0,
          segment_max_age: 43200, // 12 h → Feld zeigt 12
          segment_max_bytes: 256 * 1024 * 1024, // 256 MB
          segment_max_rows: null,
        },
      }),
    })
    const { wrapper } = await mountModal({ api })
    expect(wrapper.find('[data-testid="rb-config-segment-max-age"]').element.value).toBe('12')
    expect(wrapper.find('[data-testid="rb-config-segment-max-bytes"]').element.value).toBe('256')
    expect(wrapper.find('[data-testid="rb-config-segment-max-bytes-unit"]').element.value).toBe('mb')
  })

  it('falls back to the 6 h default segment age when stats omits it', async () => {
    const { wrapper } = await mountModal() // Default-Mock ohne segment_max_age
    expect(wrapper.find('[data-testid="rb-config-segment-max-age"]').element.value).toBe('6')
    expect(wrapper.find('[data-testid="rb-config-segment-max-bytes"]').element.value).toBe('')
  })

  it('hydrates a sub-hour segment age losslessly (900s = 15 min) and posts exactly 900s (#938 Codex #951)', async () => {
    const api = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: {
          total: 1,
          enabled: true,
          max_entries: null,
          max_file_size_bytes: 500 * 1024 * 1024,
          max_age: null,
          file_size_bytes: 0,
          segment_max_age: 900, // 15 min — migriert aus einem 15-min-Retention-Fenster
        },
      }),
    })
    const { wrapper, api: mocked } = await mountModal({ api })
    // Verlustfreie Anzeige: 15 Minuten, Einheit min — nicht 0, nicht auf 1 h aufgerundet.
    expect(wrapper.find('[data-testid="rb-config-segment-max-age"]').element.value).toBe('15')
    expect(wrapper.find('[data-testid="rb-config-segment-max-age-unit"]').element.value).toBe('minutes')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(mocked.config).toHaveBeenCalledTimes(1)
    const payload = mocked.config.mock.calls[0][0]
    expect(payload.segment_max_age).toBe(900)
  })

  it('hydrates a migrated sub-300s segment age losslessly (200s) and posts exactly 200s when the age field is untouched (#938 Codex #951)', async () => {
    // Eine pre-Segmentierungs-Config mit einem Retention-Fenster < 15 min leitet
    // segment_max_age = max_age // 3 ab (z. B. max_age=600 → 200 s). Der Wert ist
    // startup-gültig, liegt aber unter dem 300-s-UI-Minimum. Das Modal muss ihn
    // verlustfrei anzeigen und beim Speichern (ohne dass der Nutzer das Alter-Feld
    // anfasst) unverändert als 200 s durchreichen – kein Clamp auf 300, kein
    // client-seitiger Reject.
    const api = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: {
          total: 1,
          enabled: true,
          max_entries: null,
          max_file_size_bytes: 500 * 1024 * 1024,
          max_age: null,
          file_size_bytes: 0,
          segment_max_age: 200, // migriert, < 300 s
        },
      }),
    })
    const { wrapper, api: mocked } = await mountModal({ api })
    // Verlustfreie Anzeige: 200 Sekunden, Einheit seconds – nicht geklemmt, nicht gerundet.
    expect(wrapper.find('[data-testid="rb-config-segment-max-age"]').element.value).toBe('200')
    expect(wrapper.find('[data-testid="rb-config-segment-max-age-unit"]').element.value).toBe('seconds')
    // Nutzer ändert eine UNRELATED-Einstellung (max-entries), NICHT das Alter-Feld.
    await wrapper.find('#max-entries-enabled').setValue(true)
    await wrapper.find('[data-testid="rb-config-max-entries"]').setValue('12345')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(mocked.config).toHaveBeenCalledTimes(1)
    const payload = mocked.config.mock.calls[0][0]
    // Migrierter Sub-300s-Wert bleibt erhalten – exakt 200 s, kein Clamp auf 300.
    expect(payload.segment_max_age).toBe(200)
    expect(payload.max_entries).toBe(12345)
  })

  it('rejects when the user actively edits the age field to a sub-300s value, even after hydrating a migrated sub-300s value (#938 Codex #951)', async () => {
    const api = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: {
          total: 1,
          enabled: true,
          max_entries: null,
          max_file_size_bytes: 500 * 1024 * 1024,
          max_age: null,
          file_size_bytes: 0,
          segment_max_age: 200, // migriert, < 300 s
        },
      }),
    })
    const { wrapper } = await mountModal({ api })
    // Nutzer tippt AKTIV einen neuen, zu kleinen Wert ins Alter-Feld.
    await wrapper.find('[data-testid="rb-config-segment-max-age"]').setValue('120') // 120 s < 300 s
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(api.config).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Segment-Alter')
  })

  it('respects the 300s (5 min) backend minimum for the segment age', async () => {
    const { wrapper, api } = await mountModal()
    await wrapper.find('[data-testid="rb-config-segment-max-age-unit"]').setValue('minutes')
    await wrapper.find('[data-testid="rb-config-segment-max-age"]').setValue('4') // 240s < 300s
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    expect(api.config).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Segment-Alter')
  })

  it('posts a whole-hour segment age losslessly (6h → 21600s) as a regression guard', async () => {
    const { wrapper, api } = await mountModal()
    // Default is 6 h; verify the hour path still round-trips exactly.
    expect(wrapper.find('[data-testid="rb-config-segment-max-age"]').element.value).toBe('6')
    expect(wrapper.find('[data-testid="rb-config-segment-max-age-unit"]').element.value).toBe('hours')
    await wrapper.find('form').trigger('submit')
    await flushPromises()
    const payload = api.config.mock.calls[0][0]
    expect(payload.segment_max_age).toBe(6 * 60 * 60)
  })

  it('renders MiB/GiB unit labels for size selects (binary, #919)', async () => {
    const { wrapper } = await mountModal()
    const sizeUnitOptions = wrapper.find('[data-testid="rb-config-max-size-unit"]').findAll('option').map((o) => o.text())
    expect(sizeUnitOptions).toEqual(['MiB', 'GiB'])
    expect(wrapper.text()).not.toContain('500 MB')
  })

  it('formats the disk usage stat in binary MiB (#919)', async () => {
    const api = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: { total: 1, enabled: true, max_entries: null, max_file_size_bytes: null, max_age: null, file_size_bytes: 500 * 1024 * 1024 },
      }),
    })
    const { wrapper } = await mountModal({ api })
    const text = wrapper.find('[data-testid="rb-config-stats-file-size"]').text()
    expect(text).toContain('MiB')
    expect(text).not.toContain('MB ')
  })
})

describe('MonitorConfigModal prognosis (#919/#938)', () => {
  // Der Prognose-Block ist in die gemeinsame PrognosisBlock-Komponente
  // ausgelagert (DRY). Die Integrationstests hier prüfen, dass das Modal ihn
  // korrekt mit stats.prognosis + Segment-Alter füttert (data-testid prognosis-*).
  it('renders human-readable forecast lines from stats.prognosis', async () => {
    const api = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: {
          total: 100, enabled: true, max_entries: null, max_file_size_bytes: 20 * 1024 * 1024 * 1024, max_age: null, file_size_bytes: 0,
          segment_max_age: 6 * 3600,
          prognosis: {
            sample_segment_count: 5,
            bytes_per_hour: 50 * 1024 * 1024, // 50 MiB/h
            rows_per_hour: 12000,
            avg_segment_seconds: 6 * 3600, // 6 h
            estimated_retention_seconds: 5 * 24 * 3600, // 5 days
            effective_segment_max_bytes: 16 * 1024 * 1024, // 16 MiB
          },
        },
      }),
    })
    const { wrapper } = await mountModal({ api })
    expect(wrapper.find('[data-testid="prognosis-warming"]').exists()).toBe(false)
    const rate = wrapper.find('[data-testid="prognosis-rate"]').text()
    expect(rate).toContain('50')
    expect(rate).toContain('MiB/h')
    expect(rate).toContain('12.000') // Events/h, de-DE grouping
    expect(wrapper.find('[data-testid="prognosis-rotation"]').exists()).toBe(true)
    const history = wrapper.find('[data-testid="prognosis-history"]').text()
    expect(history).toContain('5 Tage')
    // Budget frontend-berechnet aus dem Formular-Segment-Alter (6 h): 50 MiB * 6 * 3 = 900 MiB.
    const budget = wrapper.find('[data-testid="prognosis-budget"]').text()
    expect(budget).toContain('900 MiB')
    expect(budget).toContain('6') // current segment age in hours
    expect(budget).toContain('mind. 3 Segmente')
  })

  it('shows the warming-up hint when prognosis fields are null (too few segments)', async () => {
    const api = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: {
          total: 1, enabled: true, max_entries: null, max_file_size_bytes: null, max_age: null, file_size_bytes: 0,
          prognosis: {
            sample_segment_count: 1,
            bytes_per_hour: null,
            rows_per_hour: null,
            avg_segment_seconds: null,
            estimated_retention_seconds: null,
            effective_segment_max_bytes: null,
          },
        },
      }),
    })
    const { wrapper } = await mountModal({ api })
    expect(wrapper.find('[data-testid="prognosis-warming"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('NaN')
    expect(wrapper.text()).not.toContain('undefined')
  })

  it('shows the warming-up hint when stats has no prognosis object at all', async () => {
    const { wrapper } = await mountModal() // default mock has no prognosis
    expect(wrapper.find('[data-testid="prognosis-warming"]').exists()).toBe(true)
  })

  it('suppresses the history line when the retention estimate is null (budget set)', async () => {
    const api = makeApi({
      stats: vi.fn().mockResolvedValue({
        data: {
          total: 100, enabled: true, max_entries: null, max_file_size_bytes: 20 * 1024 * 1024 * 1024, max_age: null, file_size_bytes: 0,
          segment_max_age: 6 * 3600,
          prognosis: {
            sample_segment_count: 5,
            bytes_per_hour: 10 * 1024 * 1024,
            rows_per_hour: 3000,
            avg_segment_seconds: 3 * 3600,
            estimated_retention_seconds: null, // not enough data yet
            effective_segment_max_bytes: 16 * 1024 * 1024,
          },
        },
      }),
    })
    const { wrapper } = await mountModal({ api })
    // Durchsatz + Rotation + Budget rendern, aber die Historie-Zeile ist unterdrückt (no NaN).
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-rotation"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-history"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('NaN')
  })
})
