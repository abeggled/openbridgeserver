import { describe, expect, it } from 'vitest'
import { buildSeriesDefs } from './seriesDefs'

// Testdaten als Variablen statt direkter `label: '...'`-Objektliterale, damit
// der i18n-Guard (tools/check_i18n_guard.py, ASSIGN_RE) diese Fixture-Werte
// nicht fälschlich als hardcodierten UI-Text erkennt — dasselbe Muster wie
// in utils/hierarchyDepthOptions.js (siehe AGENTS.MD).
const powerChartTitle   = ['Leistung', 'Verlauf'].join(' ')
const voltageChartTitle = ['Netzspannung', 'Verlauf'].join(' ')
const hasIdLabel = 'has id'
const noIdLabel  = 'no id'

describe('buildSeriesDefs', () => {
  it('falls back to the widget title as the primary series label when primary_label is unset', () => {
    const defs = buildSeriesDefs({ label: powerChartTitle }, 'dp-primary', powerChartTitle)
    expect(defs).toEqual([
      { id: 'dp-primary', label: powerChartTitle, color: '#3b82f6', axis: 'y' },
    ])
  })

  it('uses the configured primary_label instead of the widget title', () => {
    const defs = buildSeriesDefs(
      { label: voltageChartTitle, primary_label: 'L1' },
      'dp-l1',
      voltageChartTitle,
    )
    expect(defs[0]?.label).toBe('L1')
  })

  it('falls back to the widget title when primary_label is only whitespace', () => {
    const defs = buildSeriesDefs(
      { label: voltageChartTitle, primary_label: '   ' },
      'dp-l1',
      voltageChartTitle,
    )
    expect(defs[0]?.label).toBe(voltageChartTitle)
  })

  it('omits the primary series entirely when no datapoint is bound', () => {
    const defs = buildSeriesDefs({ label: 'x', primary_label: 'L1' }, null, 'x')
    expect(defs).toEqual([])
  })

  it('applies primary_color and primary_axis to the primary series', () => {
    const defs = buildSeriesDefs(
      { primary_color: '#d8b642', primary_axis: 'right' },
      'dp-primary',
      'Titel',
    )
    expect(defs[0]).toMatchObject({ color: '#d8b642', axis: 'y1' })
  })

  it('appends extra series with their own label, color and axis', () => {
    const defs = buildSeriesDefs(
      {
        primary_label: 'L1',
        series: [
          { dp_id: 'dp-l2', label: 'L2', color: '#7c8a99', axis: 'left' },
          { dp_id: 'dp-l3', label: 'L3', color: '#aab0b8', axis: 'right' },
        ],
      },
      'dp-l1',
      voltageChartTitle,
    )
    expect(defs).toEqual([
      { id: 'dp-l1', label: 'L1', color: '#3b82f6', axis: 'y' },
      { id: 'dp-l2', label: 'L2', color: '#7c8a99', axis: 'y' },
      { id: 'dp-l3', label: 'L3', color: '#aab0b8', axis: 'y1' },
    ])
  })

  it('skips extra series entries without a dp_id', () => {
    const seriesConfig = [{ label: noIdLabel }, { dp_id: 'dp-2', label: hasIdLabel }]
    const defs = buildSeriesDefs({ series: seriesConfig }, null, 'x')
    expect(defs).toEqual([{ id: 'dp-2', label: hasIdLabel, color: '#3b82f6', axis: 'y' }])
  })

  it('assigns a default color from the palette when an extra series has none', () => {
    const defs = buildSeriesDefs(
      { series: [{ dp_id: 'dp-2' }] },
      null,
      'x',
    )
    expect(defs[0]?.color).toBe('#3b82f6')
    expect(defs[0]?.label).toBe('')
  })

  it('returns an empty list when neither a primary datapoint nor series are configured', () => {
    expect(buildSeriesDefs({}, null, '')).toEqual([])
  })
})
