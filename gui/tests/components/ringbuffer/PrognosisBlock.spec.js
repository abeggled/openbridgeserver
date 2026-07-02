/**
 * Tests for PrognosisBlock.vue (#919/#938) — gemeinsame RingBuffer-Prognose.
 *
 * Die Komponente rendert die komplette, menschlich formulierte Prognose aus
 * ``prognosis`` (stats.prognosis) plus optionaler Budget-Zeile aus
 * ``segmentAgeHours``. None-robust: einzelne fehlende Felder blenden nur ihre
 * eigene Zeile aus, ohne NaN/undefined zu rendern.
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

const fullPrognosis = {
  sample_segment_count: 5,
  bytes_per_hour: 50 * 1024 * 1024, // 50 MiB/h
  rows_per_hour: 12000,
  avg_segment_seconds: 6 * 3600, // 6 h
  estimated_retention_seconds: 5 * 24 * 3600, // 5 days
  recommended_budget_for_segment_age_bytes: 2 * 1024 * 1024 * 1024, // 2 GiB
}

describe('PrognosisBlock — fully populated', () => {
  it('renders rate, retention and budget lines', () => {
    const wrapper = mountBlock({ prognosis: fullPrognosis, segmentAgeHours: 6 })
    expect(wrapper.find('[data-testid="prognosis-warming"]').exists()).toBe(false)

    const rate = wrapper.find('[data-testid="prognosis-rate"]').text()
    expect(rate).toContain('50')
    expect(rate).toContain('MiB/h')
    expect(rate).toContain('12.000') // Events/h, de-DE grouping

    const retention = wrapper.find('[data-testid="prognosis-retention"]').text()
    expect(retention).toContain('5 Tage')

    const budget = wrapper.find('[data-testid="prognosis-budget"]').text()
    expect(budget).toContain('GiB')
    expect(budget).toContain('6') // segment age in hours

    expect(wrapper.text()).not.toContain('NaN')
    expect(wrapper.text()).not.toContain('undefined')
  })

  it('formats MiB throughput binary (50 MiB/h, not 52.4 MB)', () => {
    const wrapper = mountBlock({ prognosis: fullPrognosis, segmentAgeHours: 6 })
    const rate = wrapper.find('[data-testid="prognosis-rate"]').text()
    expect(rate).toContain('50,0 MiB/h')
  })
})

describe('PrognosisBlock — segmentAgeHours=null', () => {
  it('omits the budget line when no segment age is known', () => {
    const wrapper = mountBlock({ prognosis: fullPrognosis, segmentAgeHours: null })
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-retention"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(false)
  })
})

describe('PrognosisBlock — warming up', () => {
  it('shows the warming hint when rate fields are null (too few segments)', () => {
    const wrapper = mountBlock({
      prognosis: {
        sample_segment_count: 1,
        bytes_per_hour: null,
        rows_per_hour: null,
        avg_segment_seconds: null,
        estimated_retention_seconds: null,
        recommended_budget_for_segment_age_bytes: null,
      },
      segmentAgeHours: 6,
    })
    expect(wrapper.find('[data-testid="prognosis-warming"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('NaN')
    expect(wrapper.text()).not.toContain('undefined')
  })

  it('shows the warming hint when prognosis is null entirely', () => {
    const wrapper = mountBlock({ prognosis: null, segmentAgeHours: 6 })
    expect(wrapper.find('[data-testid="prognosis-warming"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(false)
  })
})

describe('PrognosisBlock — individual null fields blank only their line', () => {
  it('omits retention + budget when only those fields are null', () => {
    const wrapper = mountBlock({
      prognosis: {
        bytes_per_hour: 10 * 1024 * 1024,
        rows_per_hour: 3000,
        avg_segment_seconds: 3 * 3600,
        estimated_retention_seconds: null,
        recommended_budget_for_segment_age_bytes: null,
      },
      segmentAgeHours: 3,
    })
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-retention"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(false)
    expect(wrapper.text()).not.toContain('NaN')
  })

  it('omits only the budget line when the recommended budget is null', () => {
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, recommended_budget_for_segment_age_bytes: null },
      segmentAgeHours: 6,
    })
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-retention"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(false)
  })

  it('omits only the retention line when estimated_retention_seconds is null', () => {
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, estimated_retention_seconds: null },
      segmentAgeHours: 6,
    })
    expect(wrapper.find('[data-testid="prognosis-rate"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="prognosis-retention"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="prognosis-budget"]').exists()).toBe(true)
  })

  it('renders a retention horizon in hours below 48h', () => {
    const wrapper = mountBlock({
      prognosis: { ...fullPrognosis, estimated_retention_seconds: 12 * 3600 },
      segmentAgeHours: 6,
    })
    expect(wrapper.find('[data-testid="prognosis-retention"]').text()).toContain('12 h')
  })
})
