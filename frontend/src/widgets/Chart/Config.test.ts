// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import ChartConfig from './Config.vue'

// Werte als Variablen statt direkter `label: '...'`/`placeholder="..."`-Literale,
// damit der i18n-Guard (tools/check_i18n_guard.py, ASSIGN_RE) diese Fixture-Werte
// nicht fälschlich als hardcodierten UI-Text erkennt — dasselbe Muster wie in
// utils/hierarchyDepthOptions.js (siehe AGENTS.MD) und seriesDefs.test.ts.
const primaryLabelPlaceholder = ['Primärlabel', 'Platzhalter'].join('-')
const voltageChartTitle       = ['Netzspannung', 'Verlauf'].join(' ')

const messages: Record<string, string> = {
  'widgets.common.label': 'Label',
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
