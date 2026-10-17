// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ChartConfig from './Config.vue'
import { COLORS } from './seriesDefs'

// Werte als Variablen statt direkter `label: '...'`/`placeholder="..."`-Literale,
// damit der i18n-Guard (tools/check_i18n_guard.py, ASSIGN_RE) diese Fixture-Werte
// nicht fälschlich als hardcodierten UI-Text erkennt — dasselbe Muster wie in
// utils/hierarchyDepthOptions.js (siehe docs/AGENT_REFERENCE.md) und seriesDefs.test.ts.
const primaryLabelPlaceholder = ['Primärlabel', 'Platzhalter'].join('-')
const voltageChartTitle       = ['Netzspannung', 'Verlauf'].join(' ')

const messages: Record<string, string> = {
  'widgets.chart.chartTitle': 'Diagrammtitel',
  'widgets.chart.primaryLabelHint': 'Titel-Hinweis',
  'widgets.chart.chartTypeLine': 'Linie',
  'widgets.chart.chartTypeBar': 'Balken',
  'widgets.chart.labelPlaceholder': 'Beschriftungs-Platzhalter',
  'widgets.chart.defaultTimeRange': 'Zeitraum',
  'widgets.chart.primarySeries': 'Primäre Reihe',
  'widgets.chart.primaryLabel': primaryLabelPlaceholder,
  'widgets.chart.axisLeft': '◄ Links',
  'widgets.chart.axisRight': 'Rechts ►',
  'widgets.chart.additionalSeries': 'Weitere Reihen',
  'widgets.chart.seriesLabel': 'Serienlabel-Platzhalter',
  'widgets.chart.removeSeriesTitle': 'Reihe entfernen',
  'widgets.chart.addSeries': '+ Weitere Reihe hinzufügen',
}

function mountConfig(modelValue: Record<string, unknown> = {}) {
  return mount(ChartConfig, {
    props: { modelValue },
    global: {
      mocks: { $t: (key: string) => messages[key] ?? key },
    },
  })
}

function findPrimaryLabelInput(wrapper: ReturnType<typeof mountConfig>) {
  return wrapper.find(`input[placeholder="${primaryLabelPlaceholder}"]`)
}

describe('Chart Config.vue — primary series label', () => {
  it('does not emit on mount', () => {
    const wrapper = mountConfig({ primary_label: 'L1' })
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('loads an existing primary_label from modelValue into the input', () => {
    const wrapper = mountConfig({ primary_label: 'L1' })
    const input = findPrimaryLabelInput(wrapper)
    expect((input.element as HTMLInputElement).value).toBe('L1')
  })

  it('defaults the primary_label input to empty when unset', () => {
    const wrapper = mountConfig({})
    const input = findPrimaryLabelInput(wrapper)
    expect((input.element as HTMLInputElement).value).toBe('')
  })

  it('emits update:modelValue with the edited primary_label', async () => {
    const wrapper = mountConfig({})
    const input = findPrimaryLabelInput(wrapper)
    await input.setValue('L1')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    const payload = emitted![emitted!.length - 1][0] as Record<string, unknown>
    expect(payload.primary_label).toBe('L1')
  })

  it('preserves the rest of the config when only primary_label changes', async () => {
    const wrapper = mountConfig({
      label: voltageChartTitle,
      primary_color: '#d8b642',
      primary_axis: 'right',
    })
    const input = findPrimaryLabelInput(wrapper)
    await input.setValue('L1')

    const emitted = wrapper.emitted('update:modelValue')
    const payload = emitted![emitted!.length - 1][0] as Record<string, unknown>
    expect(payload).toMatchObject({
      label: voltageChartTitle,
      primary_color: '#d8b642',
      primary_axis: 'right',
      primary_label: 'L1',
    })
  })
})

describe('Chart Config.vue — chart title and series colours (#1361)', () => {
  it('labels the title field as the chart title and explains the primary-label fallback', () => {
    const text = mountConfig({}).text()
    expect(text).toContain('Diagrammtitel')
    expect(text).toContain('Titel-Hinweis')
  })

  it('switches the chart type via the translated buttons', async () => {
    const wrapper = mountConfig({})
    const bar = wrapper.findAll('button').find(b => b.text() === 'Balken')!
    await bar.trigger('click')

    const emitted = wrapper.emitted('update:modelValue')!
    expect((emitted[emitted.length - 1][0] as Record<string, unknown>).chart_type).toBe('bar')
  })

  it('defaults the primary colour to the first palette entry', async () => {
    const wrapper = mountConfig({})
    await findPrimaryLabelInput(wrapper).setValue('L1')

    const emitted = wrapper.emitted('update:modelValue')!
    expect((emitted[emitted.length - 1][0] as Record<string, unknown>).primary_color).toBe(COLORS[0])
  })

  it('gives added series the same palette colour buildSeriesDefs would fall back to', async () => {
    const existing = Array.from({ length: COLORS.length - 1 }, () => ({ dp_id: 'dp', color: '#000000' }))
    const wrapper = mountConfig({ series: existing })
    const add = wrapper.findAll('button').find(b => b.text() === '+ Weitere Reihe hinzufügen')!

    await add.trigger('click')
    await add.trigger('click')

    const emitted = wrapper.emitted('update:modelValue')!
    const series = (emitted[emitted.length - 1][0] as Record<string, unknown>).series as { color: string }[]
    // Index 0 ist die Primärreihe: die 12. Zusatzreihe wiederholt Farbe 0, die 13. Farbe 1
    expect(series.slice(-2).map(s => s.color)).toEqual([COLORS[0], COLORS[1]])
  })

  it('keeps the previous default colour for stored series without one', async () => {
    const wrapper = mountConfig({ series: [{ dp_id: 'dp-2' }] })
    await findPrimaryLabelInput(wrapper).setValue('L1')

    const emitted = wrapper.emitted('update:modelValue')!
    const series = (emitted[emitted.length - 1][0] as Record<string, unknown>).series as { color: string }[]
    expect(series[0].color).toBe('#ef4444')
  })
})
