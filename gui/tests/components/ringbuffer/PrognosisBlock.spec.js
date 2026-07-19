/**
 * Tests for PrognosisBlock.vue (#919/#938) — gemeinsame RingBuffer-Prognose.
 *
 * Die Komponente rendert bis zu vier abgestimmte Zeilen aus ``prognosis``
 * (stats.prognosis) plus Budget-Kontext (``maxFileSizeBytes``,
 * ``segmentAgeHours``):
 *   1. Durchsatz  2. Rotation (mit/ohne Größen-Cap-Zusatz)
 *   3. Historie   4. Budget-Empfehlung (frontend-berechnet)
 * None-robust: einzelne fehlende Felder blenden nur ihre eigene Zeile aus,
 * ohne NaN/undefined zu rendern.
 */
import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import de from '@/locales/de.json'
import en from '@/locales/en.json'
import PrognosisBlock from '@/components/ringbuffer/PrognosisBlock.vue'

function mountBlock(props = {}) {
  const i18n = createI18n({ legacy: false, locale: 'de', fallbackLocale: 'en', messages: { de, en } })
  return mount(PrognosisBlock, { props, global: { plugins: [i18n] } })
}

const GIB = 1024 * 1024 * 1024

// Rotation = min(segment_max_age, Größen-Füllzeit). Bei 50 MiB/h und Cap 400 MiB
// ist die Füllzeit 8 h > segmentAgeHours (6 h) → ZEIT greift zuerst (zeitgetrieben,
// kein Größen-Cap-Zusatz).
const fullPrognosis = {
  sample_segment_count: 5,
  bytes_per_hour: 50 * 1024 * 1024, // 50 MiB/h
  rows_per_hour: 12000,
  avg_segment_seconds: 6 * 3600, // gemessen; für die Rotations-Zeile nicht mehr genutzt
  estimated_retention_seconds: 5 * 24 * 3600, // 5 days
  effective_segment_max_bytes: 400 * 1024 * 1024, // Cap 400 MiB → Füllzeit 8 h
}

describe('PrognosisBlock — fully populated', () => {
  it('renders throughput, rotation, history and budget lines', () => {
    const wrapper = mountBlock({ prognosis: fullPrognosis, segmentAgeHours: 6, maxFileSizeBytes: 20 * GIB })
    expect(wrapper.find('[data-testid="prognosis-warming"]').exists()).toBe(false)

    // 1. Durchsatz
    const rate = wrapper.find('[data-testid="prognosis-rate"]').text()
    expect(rate).toContain('50')
    expect(rate).toContain('MiB/h')
    expect(rate).toContain('3,3') // Events/s, de-DE decimal separator

    // 2. Rotation (Zeit greift zuerst → nur der erste Teil, kein Cap-Zusatz)
    const rotation = wrapper.find('[data-testid="prognosis-rotation"]').text()
    expect(rotation).toContain('~alle 6,0 h')
    expect(rotation).not.toContain('Größen-Cap')

    // 3. Historie
    const history = wrapper.find('[data-testid="prognosis-history"]').text()
    expect(history).toContain('5 Tage')
    expect(history).toContain('GiB')

    // 4. Budget-Empfehlung
    const budget = wrapper.find('[data-testid="prognosis-budget"]').text()
    expect(budget).toContain('6') // segment age in hours
    expect(budget).toContain('mind. 3 Segmente')

    expect(wrapper.text()).not.toContain('NaN')
    expect(wrapper.text()).not.toContain('undefined')
  })

  it('formats MiB throughput binary (50 MiB/h, not 52.4 MB)', () => {
    const wrapper = mountBlock({ prognosis: fullPrognosis, segmentAgeHours: 6, maxFileSizeBytes: 20 * GIB })
    expect(wrapper.find('[data-testid="prognosis-rate"]').text()).toContain('50,0 MiB/h')
  })
})

describe('PrognosisBlock — rotation size cap', () => {
  it('appends the size-cap clause when the cap kicks in before the configured time', () => {
    // Cap 50 MiB / 50 MiB/h = 1 h Füllzeit < segmentAgeHours (6 h) → größengetrieben.
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, bytes_per_hour: 50 * 1024 * 1024, effective_segment_max_bytes: 50 * 1024 * 1024 },
      segmentAgeHours: 6,
      maxFileSizeBytes: 20 * GIB,
    })
    const rotation = wrapper.find('[data-testid="prognosis-rotation"]').text()
    expect(rotation).toContain('~alle 1,0 h') // Füllzeit, nicht das 6-h-Alter
    expect(rotation).toContain('Größen-Cap')
    expect(rotation).toContain('50') // cap in MiB
    expect(rotation).toContain('6,0 h') // configured age
    expect(rotation).not.toContain('NaN')
  })

  it('omits the size-cap clause when time kicks in first (fill time >= age)', () => {
    // fullPrognosis: Cap 400 MiB / 50 MiB/h = 8 h Füllzeit >= 6 h Alter → zeitgetrieben.
    const wrapper = mountBlock({ prognosis: fullPrognosis, segmentAgeHours: 6, maxFileSizeBytes: 20 * GIB })
    const rotation = wrapper.find('[data-testid="prognosis-rotation"]').text()
    expect(rotation).toContain('~alle 6,0 h') // das eingestellte Alter
    expect(rotation).not.toContain('Größen-Cap')
  })
})

describe('PrognosisBlock — frontend budget calculation', () => {
  it('derives the budget from the full 30-day retention instead of only three daily segments', () => {
    // 50 MiB/h * 30 d * 24 h = 36,000 MiB = 35.2 GiB.
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, bytes_per_hour: 50 * 1024 * 1024 },
      segmentAgeHours: 24,
      retentionAgeSeconds: 30 * 24 * 3600,
      maxFileSizeBytes: 20 * GIB,
    })
    const budget = wrapper.find('[data-testid="prognosis-budget"]').text()
    expect(budget).toContain('30 Tage Retention')
    expect(budget).toContain('35,2 GiB')
    expect(budget).not.toContain('mind. 3 Segmente')
  })

  it('uses the retention target even when no segment age is available', () => {
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, bytes_per_hour: 50 * 1024 * 1024 },
      segmentAgeHours: null,
      retentionAgeSeconds: 30 * 24 * 3600,
    })
    const budget = wrapper.find('[data-testid="prognosis-budget"]').text()
    expect(budget).toContain('30 Tage Retention')
    expect(budget).toContain('35,2 GiB')
  })

  it('keeps the three-segment floor when it exceeds the configured retention target', () => {
    // 48 h Retention is smaller than the 72 h floor for three 24 h segments.
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, bytes_per_hour: 50 * 1024 * 1024 },
      segmentAgeHours: 24,
      retentionAgeSeconds: 48 * 3600,
    })
    const budget = wrapper.find('[data-testid="prognosis-budget"]').text()
    expect(budget).toContain('3,5 GiB')
    expect(budget).toContain('mind. 3 Segmente')
  })

  it('computes the recommended budget from bytes_per_hour * age * 3', () => {
    // 50 MiB/h * 4 h * 3 = 600 MiB → formatBytesBinary → "600 MiB".
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, bytes_per_hour: 50 * 1024 * 1024 },
      segmentAgeHours: 4,
      maxFileSizeBytes: 20 * GIB,
    })
    const budget = wrapper.find('[data-testid="prognosis-budget"]').text()
    expect(budget).toContain('4') // age matches the label
    expect(budget).toContain('600 MiB')
    expect(budget).toContain('mind. 3 Segmente')
  })

  it('omits the budget line when segmentAgeHours is null', () => {
    const wrapper = mountBlock({ prognosis: fullPrognosis, segmentAgeHours: null, maxFileSizeBytes: 20 * GIB })
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(false)
  })

  it('omits the budget line when bytes_per_hour is missing', () => {
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, bytes_per_hour: null },
      segmentAgeHours: 4,
      maxFileSizeBytes: 20 * GIB,
    })
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(false)
  })
})

describe('PrognosisBlock — unlimited budget', () => {
  it('renders the unlimited history hint when maxFileSizeBytes is null', () => {
    const wrapper = mountBlock({ prognosis: fullPrognosis, segmentAgeHours: 6, maxFileSizeBytes: null })
    const history = wrapper.find('[data-testid="prognosis-history"]').text()
    expect(history).toContain('unbegrenzt')
    expect(history).not.toContain('NaN')
  })

  it('projects 30-day and annual disk growth when total retention is explicitly unbounded', () => {
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, bytes_per_hour: 18.5 * 1024 * 1024 },
      segmentAgeHours: 24,
      segmentMaxBytes: null,
      segmentMaxRows: null,
      maxFileSizeBytes: null,
      retentionUnbounded: true,
    })
    const growth = wrapper.find('[data-testid="prognosis-unbounded-growth"]')
    expect(growth.exists()).toBe(true)
    expect(growth.text()).toContain('13 GiB')
    expect(growth.text()).toContain('158 GiB')
  })
})

describe('PrognosisBlock — winning rotation trigger', () => {
  it('names age when age is the only effective trigger', () => {
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, effective_segment_max_bytes: null },
      segmentAgeHours: 24,
      segmentMaxBytes: null,
      segmentMaxRows: null,
      maxFileSizeBytes: null,
    })
    expect(wrapper.find('[data-testid="prognosis-rotation"]').text()).toContain('Alter')
  })

  it('names rows when the row threshold is reached before age and size', () => {
    const wrapper = mountBlock({
      prognosis: fullPrognosis,
      segmentAgeHours: 6,
      segmentMaxBytes: 400 * 1024 * 1024,
      segmentMaxRows: 6000,
      maxFileSizeBytes: 20 * GIB,
    })
    const rotation = wrapper.find('[data-testid="prognosis-rotation"]').text()
    expect(rotation).toContain('0,5 h')
    expect(rotation).toContain('Zeilen')
  })

  it('uses age deterministically when all rotation triggers tie', () => {
    const wrapper = mountBlock({
      prognosis: fullPrognosis,
      segmentAgeHours: 6,
      segmentMaxBytes: 300 * 1024 * 1024, // 50 MiB/h → 6 h
      segmentMaxRows: 72000, // 12,000 rows/h → 6 h
      maxFileSizeBytes: 20 * GIB,
    })
    const rotation = wrapper.find('[data-testid="prognosis-rotation"]').text()
    expect(rotation).toContain('6,0 h')
    expect(rotation).toContain('Alter')
  })

  it('ignores a row threshold when no row rate is available', () => {
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, rows_per_hour: null },
      segmentAgeHours: 6,
      segmentMaxBytes: 400 * 1024 * 1024,
      segmentMaxRows: 1,
      maxFileSizeBytes: 20 * GIB,
    })
    const rotation = wrapper.find('[data-testid="prognosis-rotation"]').text()
    expect(rotation).toContain('6,0 h')
    expect(rotation).toContain('Alter')
    expect(rotation).not.toContain('Zeilen')
  })
})

describe('PrognosisBlock — warming up', () => {
  it('shows a five-second live countdown and then labels the rate provisional', async () => {
    const wrapper = mountBlock({
      prognosis: {
        source: 'active',
        provisional: true,
        observed_seconds: 3,
        ready_after_seconds: 2,
        bytes_per_hour: null,
        rows_per_hour: null,
      },
      segmentAgeHours: 24,
    })
    expect(wrapper.find('[data-testid="prognosis-warming"]').text()).toContain('2')

    await wrapper.setProps({
      prognosis: {
        source: 'active',
        provisional: true,
        observed_seconds: 5,
        ready_after_seconds: 0,
        observed_rows: 10,
        bytes_per_hour: 18.5 * 1024 * 1024,
        rows_per_hour: 7200,
      },
    })
    expect(wrapper.find('[data-testid="prognosis-provisional"]').text()).toContain('5')
    expect(wrapper.find('[data-testid="prognosis-rate"]').text()).toContain('2,0 Events/s')
    expect(wrapper.find('[data-testid="prognosis-rate"]').text()).toContain('18,5 MiB/h')
  })

  it('shows an idle message after warmup instead of a zero projection', () => {
    const wrapper = mountBlock({
      prognosis: {
        source: 'active',
        provisional: true,
        observed_seconds: 6,
        ready_after_seconds: 0,
        idle_seconds: 6,
        bytes_per_hour: null,
        rows_per_hour: null,
      },
    })
    expect(wrapper.find('[data-testid="prognosis-idle"]').text()).toContain('6 Sekunden keine neuen Events')
    expect(wrapper.find('[data-testid="prognosis-warming"]').exists()).toBe(false)
  })

  it('shows the event rate while storage growth is not measurable yet', () => {
    const wrapper = mountBlock({
      prognosis: {
        source: 'active',
        provisional: true,
        observed_seconds: 7,
        ready_after_seconds: 0,
        rows_per_hour: 3600,
        bytes_per_hour: null,
      },
    })
    const rate = wrapper.find('[data-testid="prognosis-rate"]').text()
    expect(rate).toContain('1,0 Events/s')
    expect(rate).toContain('Speicherwachstum wird noch gemessen')
  })

  it('shows the warming hint when all rate fields are null (too few segments)', () => {
    const wrapper = mountBlock({
      prognosis: {
        sample_segment_count: 1,
        bytes_per_hour: null,
        rows_per_hour: null,
        avg_segment_seconds: null,
        estimated_retention_seconds: null,
        effective_segment_max_bytes: null,
      },
      segmentAgeHours: 6,
      maxFileSizeBytes: 20 * GIB,
    })
    expect(wrapper.find('[data-testid="prognosis-warming"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('NaN')
    expect(wrapper.text()).not.toContain('undefined')
  })

  it('shows the warming hint when prognosis is null entirely', () => {
    const wrapper = mountBlock({ prognosis: null, segmentAgeHours: 6, maxFileSizeBytes: 20 * GIB })
    expect(wrapper.find('[data-testid="prognosis-warming"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(false)
  })
})

describe('PrognosisBlock — individual null fields blank only their line', () => {
  it('omits the rotation line when neither age nor size cap is known', () => {
    // Kein segment_max_age UND kein effektiver Cap → weder Zeit noch Größe bekannt.
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, effective_segment_max_bytes: null },
      segmentAgeHours: null,
      maxFileSizeBytes: 20 * GIB,
    })
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-rotation"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('NaN')
  })

  it('omits only the history line body when estimated_retention_seconds is null (budget set)', () => {
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, estimated_retention_seconds: null },
      segmentAgeHours: 6,
      maxFileSizeBytes: 20 * GIB,
    })
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-history"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(true)
  })

  it('renders a retention horizon in hours below 48h', () => {
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, estimated_retention_seconds: 12 * 3600 },
      segmentAgeHours: 6,
      maxFileSizeBytes: 20 * GIB,
    })
    expect(wrapper.find('[data-testid="prognosis-history"]').text()).toContain('12 h')
  })
})
