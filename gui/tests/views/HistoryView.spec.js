import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { nextTick } from 'vue'

// Collects Chart constructor invocations across tests
let ChartMock
const chartCalls = []

// Module-level handle: lets tests trigger DpCombobox@select from outside
let _dpStubEmit = null

const DPC_STUB = {
  name: 'DpCombobox',
  template: '<div class="dp-combobox" />',
  emits: ['select', 'update:modelValue'],
  props: ['modelValue', 'displayName', 'placeholder'],
  setup(_, { emit }) {
    _dpStubEmit = (dp) => emit('select', dp)
  },
}

beforeEach(() => {
  vi.resetModules()
  chartCalls.length = 0
  _dpStubEmit = null

  ChartMock = vi.fn().mockImplementation(function () {
    chartCalls.push(this)
    this.destroy = vi.fn()
  })

  vi.doMock('chart.js', () => ({
    Chart: ChartMock,
    LineController: {},
    LineElement: {},
    PointElement: {},
    LinearScale: {},
    TimeScale: {},
    Tooltip: {},
    Legend: {},
    registerables: [],
  }))
  vi.doMock('chart.js/auto', () => ({}))
})

afterEach(() => {
  vi.doUnmock('chart.js')
  vi.doUnmock('chart.js/auto')
  vi.doUnmock('vue-router')
  vi.doUnmock('@/api/client')
})

const SAMPLE_POINT = { ts: '2024-01-15T12:00:00Z', v: 21.5, q: 'good', u: '°C', a: null }
const SAMPLE_DP = { name: 'Wohnzimmer Temp', unit: '°C' }

async function mountHistory({
  routeQuery = {},
  aggData = [],
  queryData = [],
  dpData = SAMPLE_DP,
  aggPending = null,
  queryPending = null,
  dpPending = null,
} = {}) {
  vi.doMock('vue-router', () => ({
    useRoute: () => ({ query: routeQuery }),
  }))

  const histAgg   = aggPending
    ? vi.fn().mockReturnValue(aggPending)
    : vi.fn().mockResolvedValue({ data: aggData })
  const histQuery = queryPending
    ? vi.fn().mockReturnValue(queryPending)
    : vi.fn().mockResolvedValue({ data: queryData })
  const dpGet     = dpPending
    ? vi.fn().mockReturnValue(dpPending)
    : vi.fn().mockResolvedValue({ data: dpData })

  vi.doMock('@/api/client', () => ({
    historyApi:  { aggregate: histAgg, query: histQuery },
    dpApi:       { get: dpGet },
    settingsApi: { get: vi.fn().mockResolvedValue({ data: {} }) },
    authApi:     { login: vi.fn(), me: vi.fn() },
    navLinksApi: { list: vi.fn().mockResolvedValue({ data: [] }) },
    searchApi:   { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
  }))

  const pinia = createPinia()
  setActivePinia(pinia)

  const { default: HistoryView } = await import('@/views/HistoryView.vue')
  const wrapper = mount(HistoryView, {
    global: {
      plugins: [pinia],
      stubs: {
        DpCombobox: DPC_STUB,
        Badge: {
          template: '<span :class="`badge-${variant}`"><slot /></span>',
          props: ['variant', 'size'],
        },
        Spinner: { template: '<span class="spinner" />' },
      },
    },
  })
  await flushPromises()
  return { wrapper, histAgg, histQuery, dpGet }
}

// ─── No dp selected ──────────────────────────────────────────────────────────

describe('HistoryView — no dp selected', () => {
  it('shows select-object hint', async () => {
    const { wrapper } = await mountHistory()
    expect(wrapper.text()).toContain('Objekt wählen und «Laden» klicken')
  })

  it('does not call dpApi.get without query param', async () => {
    const { dpGet } = await mountHistory()
    expect(dpGet).not.toHaveBeenCalled()
  })

  it('load button is disabled when no dp selected', async () => {
    const { wrapper } = await mountHistory()
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
  })

  it('chartTitle defaults to "Verlauf"', async () => {
    const { wrapper } = await mountHistory()
    expect(wrapper.text()).toContain('Verlauf')
  })
})

// ─── dp in query param ───────────────────────────────────────────────────────

describe('HistoryView — dp in query param', () => {
  it('calls dpApi.get with the id from the query string', async () => {
    const { dpGet } = await mountHistory({ routeQuery: { dp: 'uuid-123' } })
    expect(dpGet).toHaveBeenCalledWith('uuid-123')
  })

  it('calls historyApi.aggregate on mount (default mode is aggregate)', async () => {
    const { histAgg } = await mountHistory({
      routeQuery: { dp: 'uuid-123' },
      aggData: [SAMPLE_POINT],
    })
    expect(histAgg).toHaveBeenCalled()
  })

  it('shows dp name and aggregate mode in chartTitle after load', async () => {
    const { wrapper } = await mountHistory({
      routeQuery: { dp: 'uuid-123' },
      dpData:     { name: 'Außentemperatur', unit: '°C' },
    })
    expect(wrapper.text()).toContain('Außentemperatur (avg / 1h)')
  })

  it('shows no-data message when API returns empty array', async () => {
    const { wrapper } = await mountHistory({
      routeQuery: { dp: 'uuid-123' },
      aggData:    [],
    })
    expect(wrapper.text()).toContain('Keine Daten im gewählten Zeitraum')
  })

  it('renders canvas element when data points are returned', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'uuid-123' }, aggData: [SAMPLE_POINT] })
    expect(wrapper.find('canvas').exists()).toBe(true)
  })
})

// ─── Mode switching / load ───────────────────────────────────────────────────

describe('HistoryView — load modes', () => {
  it('hides aggregate controls when mode is switched to raw', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' } })

    // aggregate mode: mode + aggFn + aggInterval = 3 selects
    expect(wrapper.findAll('select').length).toBeGreaterThanOrEqual(3)

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await nextTick()

    expect(wrapper.findAll('select').length).toBe(1)
  })

  it('calls historyApi.query when mode=raw and load is clicked', async () => {
    const { wrapper, histQuery } = await mountHistory({ routeQuery: { dp: 'dp-1' } })

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(histQuery).toHaveBeenCalled()
  })

  it('shows raw data table after loading data in raw mode', async () => {
    const { wrapper, histQuery } = await mountHistory({ routeQuery: { dp: 'dp-1' } })
    histQuery.mockResolvedValue({ data: [SAMPLE_POINT] })

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Rohdaten')
  })

  it('renders a table row for each point in raw mode', async () => {
    const points = [SAMPLE_POINT, { ...SAMPLE_POINT, v: 19.0 }]
    const { wrapper, histQuery } = await mountHistory({ routeQuery: { dp: 'dp-1' } })
    histQuery.mockResolvedValue({ data: points })

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('tbody tr').length).toBe(2)
  })
})

// ─── DpCombobox interaction ──────────────────────────────────────────────────

describe('HistoryView — DpCombobox interaction', () => {
  it('updates chartTitle when a dp is selected', async () => {
    const { wrapper } = await mountHistory()
    expect(wrapper.text()).toContain('Verlauf')

    _dpStubEmit({ id: 'dp-1', name: 'Heizung', unit: 'kW' })
    await nextTick()

    expect(wrapper.text()).toContain('Heizung (avg / 1h)')
  })

  it('resets chartTitle to default when null is emitted', async () => {
    const { wrapper } = await mountHistory()
    _dpStubEmit({ id: 'dp-1', name: 'Heizung', unit: 'kW' })
    await nextTick()
    expect(wrapper.text()).toContain('Heizung')

    _dpStubEmit(null)
    await nextTick()
    expect(wrapper.text()).toContain('Verlauf')
  })

  it('shows select-object hint after dp is cleared', async () => {
    const { wrapper } = await mountHistory()
    _dpStubEmit({ id: 'dp-1', name: 'Test', unit: '' })
    await nextTick()

    _dpStubEmit(null)
    await nextTick()

    expect(wrapper.text()).toContain('Objekt wählen und «Laden» klicken')
  })
})

// ─── qualityLabel via raw table ──────────────────────────────────────────────

describe('HistoryView — qualityLabel', () => {
  it('maps good / bad / uncertain to German translations in raw table', async () => {
    const points = [
      { ts: 't1', v: 1, q: 'good',      u: '', a: null },
      { ts: 't2', v: 2, q: 'bad',       u: '', a: null },
      { ts: 't3', v: 3, q: 'uncertain', u: '', a: null },
    ]
    const { wrapper, histQuery } = await mountHistory({ routeQuery: { dp: 'dp-1' } })
    histQuery.mockResolvedValue({ data: points })

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    const text = wrapper.text()
    expect(text).toContain('Gut')
    expect(text).toContain('Schlecht')
    expect(text).toContain('Unbekannt')
  })
})

// ─── Chart rendering (regression for #1146) ──────────────────────────────────

describe('HistoryView — chart rendering', () => {
  it('constructs the Chart after a successful aggregate load', async () => {
    await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    expect(ChartMock).toHaveBeenCalledTimes(1)
  })

  it('constructs the Chart on the canvas that is mounted in the DOM', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    expect(ChartMock.mock.calls[0][0]).toBe(wrapper.find('canvas').element)
  })

  it('passes the loaded points as {x: unix-ms, y: value} pairs', async () => {
    await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    const [, config] = ChartMock.mock.calls[0]
    expect(config.data.datasets[0].data).toEqual([{ x: Date.parse('2024-01-15T12:00:00Z'), y: 21.5, u: '°C' }])
  })

  it('reads aggregate buckets from the bucket field', async () => {
    const bucket = { bucket: '2024-01-15T12:00:00', v: 7 }
    await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [bucket] })
    const [, config] = ChartMock.mock.calls[0]
    expect(config.data.datasets[0].data).toEqual([{ x: Date.parse('2024-01-15T12:00:00Z'), y: 7, u: null }])
  })

  it('does not construct a Chart when the load returns no points', async () => {
    await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [] })
    expect(ChartMock).not.toHaveBeenCalled()
  })

  it('constructs the Chart and renders the table in raw mode', async () => {
    const { wrapper, histQuery } = await mountHistory({ routeQuery: { dp: 'dp-1' } })
    histQuery.mockResolvedValue({ data: [SAMPLE_POINT] })

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(ChartMock).toHaveBeenCalledTimes(1)
    expect(wrapper.findAll('tbody tr').length).toBe(1)
  })

  it('redraws after switching the mode and reloading', async () => {
    const { wrapper, histQuery } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    histQuery.mockResolvedValue({ data: [SAMPLE_POINT, { ...SAMPLE_POINT, v: 19 }] })
    expect(ChartMock).toHaveBeenCalledTimes(1)

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(ChartMock).toHaveBeenCalledTimes(2)
    expect(ChartMock.mock.calls[1][1].data.datasets[0].data).toHaveLength(2)
  })

  it('destroys the previous chart instance before redrawing', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(chartCalls).toHaveLength(2)
    expect(chartCalls[0].destroy).toHaveBeenCalled()
  })

  it('redraws with the new label after switching the aggregation function and reloading', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })

    const [, fnSelect] = wrapper.findAll('select')
    await fnSelect.setValue('max')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(ChartMock).toHaveBeenCalledTimes(2)
    expect(ChartMock.mock.calls[1][1].data.datasets[0].label).toContain('(max / 1h)')
  })

  it('reloads and redraws with the new interval after switching it', async () => {
    const { wrapper, histAgg } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })

    const [, , intervalSelect] = wrapper.findAll('select')
    await intervalSelect.setValue('1d')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(histAgg).toHaveBeenLastCalledWith('dp-1', expect.objectContaining({ fn: 'avg', interval: '1d' }))
    expect(ChartMock).toHaveBeenCalledTimes(2)
  })

  it('reloads and redraws with the new time range', async () => {
    const { wrapper, histAgg } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })

    const [fromInput, toInput] = wrapper.findAll('input[type="datetime-local"]')
    await fromInput.setValue('2024-01-01T00:00')
    await toInput.setValue('2024-01-02T00:00')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(histAgg).toHaveBeenLastCalledWith('dp-1', expect.objectContaining({
      from: new Date('2024-01-01T00:00').toISOString(),
      to:   new Date('2024-01-02T00:00').toISOString(),
    }))
    expect(ChartMock).toHaveBeenCalledTimes(2)
  })

  // The drawn series belongs to the previously loaded object; it must not survive
  // a selection change, or the canvas gets redrawn with the old data under the
  // new object's name and unit.
  it('clears the chart when another data point is picked', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    expect(ChartMock).toHaveBeenCalledTimes(1)

    _dpStubEmit({ id: 'dp-2', name: 'Anderer', unit: 'kW' })
    await nextTick()

    expect(wrapper.find('canvas').exists()).toBe(false)
    expect(ChartMock).toHaveBeenCalledTimes(1)
    expect(chartCalls[0].destroy).toHaveBeenCalled()
  })

  it('does not redraw the old series after clearing and picking another data point', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })

    // Clearing unmounts the canvas; picking again would remount it, and a
    // surviving `points` array would be redrawn as if it belonged to dp-2.
    _dpStubEmit(null)
    await nextTick()
    _dpStubEmit({ id: 'dp-2', name: 'Leistung', unit: 'kW' })
    await nextTick()

    expect(wrapper.find('canvas').exists()).toBe(false)
    expect(ChartMock).toHaveBeenCalledTimes(1)
  })

  it('destroys the chart when a reload returns no points', async () => {
    const { wrapper, histAgg } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    histAgg.mockResolvedValue({ data: [] })

    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.find('canvas').exists()).toBe(false)
    expect(chartCalls).toHaveLength(1)
    expect(chartCalls[0].destroy).toHaveBeenCalled()
  })

  it('destroys the chart when the data point is cleared', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    expect(chartCalls).toHaveLength(1)

    _dpStubEmit(null)
    await nextTick()

    expect(wrapper.find('canvas').exists()).toBe(false)
    expect(chartCalls[0].destroy).toHaveBeenCalled()
  })

  it('destroys the chart when the view is unmounted', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })

    wrapper.unmount()

    expect(chartCalls[0].destroy).toHaveBeenCalled()
  })

  it('drops a point that carries no timestamp', async () => {
    await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [{ v: 5 }] })
    expect(ChartMock.mock.calls[0][1].data.datasets[0].data).toEqual([])
  })

  it('drops a point whose timestamp cannot be parsed', async () => {
    await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [{ ts: 'not-a-date', v: 5 }] })
    expect(ChartMock.mock.calls[0][1].data.datasets[0].data).toEqual([])
  })

  // The backend passes a malformed aggregate bucket through verbatim
  // (_format_utc_bucket). Plotting it at epoch would stretch the linear x-axis
  // from 1970 to now and squash the real series against the right edge.
  it('keeps the valid points when one aggregate bucket is malformed', async () => {
    await mountHistory({
      routeQuery: { dp: 'dp-1' },
      aggData:    [{ bucket: 'broken', v: 1 }, { bucket: '2024-01-15T12:00:00Z', v: 2 }],
    })
    expect(ChartMock.mock.calls[0][1].data.datasets[0].data)
      .toEqual([{ x: Date.parse('2024-01-15T12:00:00Z'), y: 2, u: null }])
  })

  it('still lists a dropped point in the raw table', async () => {
    const { wrapper, histQuery } = await mountHistory({ routeQuery: { dp: 'dp-1' } })
    histQuery.mockResolvedValue({ data: [{ ts: 'not-a-date', v: 5, q: 'good', u: '', a: null }] })

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(ChartMock.mock.calls[0][1].data.datasets[0].data).toEqual([])
    expect(wrapper.findAll('tbody tr').length).toBe(1)
  })

  it('uses the dark theme colours when the dark class is set', async () => {
    document.documentElement.classList.add('dark')
    try {
      await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    } finally {
      document.documentElement.classList.remove('dark')
    }
    const [, config] = ChartMock.mock.calls[0]
    expect(config.options.scales.x.ticks.color).toBe('#64748b')
    expect(config.options.plugins.tooltip.backgroundColor).toBe('#1e2435')
  })

  it('uses the light theme colours by default', async () => {
    await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    const [, config] = ChartMock.mock.calls[0]
    expect(config.options.scales.x.ticks.color).toBe('#94a3b8')
    expect(config.options.plugins.tooltip.backgroundColor).toBe('#ffffff')
  })

  it('drops point markers for large series', async () => {
    const many = Array.from({ length: 201 }, (_, i) => ({ ...SAMPLE_POINT, v: i }))
    await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: many })
    expect(ChartMock.mock.calls[0][1].data.datasets[0].pointRadius).toBe(0)
  })
})

// ─── Card describes the loaded series, not the pending selection ─────────────

describe('HistoryView — loaded series description', () => {
  // Picking another object only arms the next Load. Until then the drawn curve
  // still belongs to the previous object, so neither the header nor the tooltip
  // may adopt the new object's name or unit.
  it('describes the newly picked data point once the stale series is cleared', async () => {
    const { wrapper } = await mountHistory({
      routeQuery: { dp: 'dp-1' },
      aggData:    [SAMPLE_POINT],
      dpData:     { name: 'Temperatur', unit: '°C' },
    })
    expect(wrapper.text()).toContain('Temperatur (avg / 1h)')

    _dpStubEmit({ id: 'dp-2', name: 'Leistung', unit: 'kW' })
    await nextTick()

    expect(wrapper.text()).toContain('Leistung (avg / 1h)')
    expect(wrapper.text()).not.toContain('Temperatur (avg / 1h)')
  })

  it('keeps the loaded title after the aggregation function is changed', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })

    const [, fnSelect] = wrapper.findAll('select')
    await fnSelect.setValue('max')

    expect(wrapper.text()).toContain('Wohnzimmer Temp (avg / 1h)')
  })

  it('adopts the new description once the next load succeeds', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })

    const [, fnSelect] = wrapper.findAll('select')
    await fnSelect.setValue('max')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Wohnzimmer Temp (max / 1h)')
  })

  it('shows the pending selection while nothing is loaded', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [] })
    expect(wrapper.text()).toContain('Wohnzimmer Temp (avg / 1h)')
  })

  it('falls back to the pending selection when a reload returns no points', async () => {
    const { wrapper, histAgg } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    histAgg.mockResolvedValue({ data: [] })

    _dpStubEmit({ id: 'dp-2', name: 'Leistung', unit: 'kW' })
    await wrapper.find('button').trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Leistung (avg / 1h)')
  })

  it('resets the header to the default title when the data point is cleared', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })

    _dpStubEmit(null)
    await nextTick()

    expect(wrapper.text()).toContain('Verlauf')
    expect(wrapper.text()).not.toContain('Wohnzimmer Temp')
  })
})

// ─── In-flight selection changes ─────────────────────────────────────────────

describe('HistoryView — obsolete responses', () => {
  // The response carries the values of the object that was requested. If the
  // user has moved on by the time it lands, showing it would present one
  // object's history as another's.
  it('discards a response whose data point is no longer selected', async () => {
    let resolveAgg
    const pending = new Promise(resolve => { resolveAgg = resolve })
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggPending: pending })

    _dpStubEmit({ id: 'dp-2', name: 'Leistung', unit: 'kW' })
    await nextTick()

    resolveAgg({ data: [SAMPLE_POINT] })
    await flushPromises()

    expect(ChartMock).not.toHaveBeenCalled()
    expect(wrapper.find('canvas').exists()).toBe(false)
    expect(wrapper.findAll('tbody tr').length).toBe(0)
  })

  it('clears the spinner when an obsolete response is discarded', async () => {
    let resolveAgg
    const pending = new Promise(resolve => { resolveAgg = resolve })
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggPending: pending })

    _dpStubEmit({ id: 'dp-2', name: 'Leistung', unit: 'kW' })
    resolveAgg({ data: [SAMPLE_POINT] })
    await flushPromises()

    expect(wrapper.find('button').attributes('disabled')).toBeUndefined()
  })

  // A raw response must keep raw semantics — its per-point unit — even if the
  // Mode control has been flipped to Aggregate since the request went out.
  // ?dp=<id> resolves the object's name/unit on mount. If the user picks another
  // object first, that lookup must not stamp the old metadata onto the new
  // selection — the load itself already targets the new object.
  it('discards the initial metadata lookup when another data point is picked', async () => {
    let resolveDp
    const pending = new Promise(resolve => { resolveDp = resolve })
    const { wrapper, histAgg } = await mountHistory({
      routeQuery: { dp: 'dp-1' },
      aggData:    [{ bucket: '2024-01-15T12:00:00Z', v: 99 }],
      dpPending:  pending,
    })

    _dpStubEmit({ id: 'dp-2', name: 'Leistung', unit: 'kW' })
    await nextTick()

    resolveDp({ data: { name: 'Temperatur', unit: '°C' } })
    await flushPromises()

    expect(histAgg).toHaveBeenLastCalledWith('dp-2', expect.anything())
    const [, config] = ChartMock.mock.calls.at(-1)
    expect(config.data.datasets[0].label).toBe('Leistung (avg / 1h)')
    expect(config.options.plugins.tooltip.callbacks.label({ parsed: { y: 99 }, raw: { u: null } }))
      .toBe('99 kW')
    expect(wrapper.text()).toContain('Leistung (avg / 1h)')
  })

  // Load is clickable before the ?dp=<id> metadata lookup resolves, so that
  // click and the mount's own metadata-aware load overlap on the same object.
  // Whichever response lands last, the newest request must be the one that wins.
  it('lets the newest load win when two overlap on the same data point', async () => {
    let resolveDp, resolveFirst, resolveSecond
    const dpPending = new Promise(resolve => { resolveDp = resolve })
    const first     = new Promise(resolve => { resolveFirst = resolve })
    const second    = new Promise(resolve => { resolveSecond = resolve })

    const { wrapper, histAgg } = await mountHistory({ routeQuery: { dp: 'dp-1' }, dpPending })
    histAgg.mockImplementationOnce(() => first).mockImplementationOnce(() => second)

    // Click Load while the metadata is still pending → request 1, no name/unit.
    await wrapper.find('button').trigger('click')
    // Metadata arrives, the mount's load starts → request 2, with name/unit.
    resolveDp({ data: { name: 'Temperatur', unit: '°C' } })
    await flushPromises()

    // The newer response lands first, the stale one last.
    resolveSecond({ data: [{ bucket: '2024-01-15T12:00:00Z', v: 2 }] })
    await flushPromises()
    resolveFirst({ data: [{ bucket: '2024-01-15T12:00:00Z', v: 1 }] })
    await flushPromises()

    expect(histAgg).toHaveBeenCalledTimes(2)
    const [, config] = ChartMock.mock.calls.at(-1)
    expect(config.data.datasets[0].label).toBe('Temperatur (avg / 1h)')
    expect(config.data.datasets[0].data[0].y).toBe(2)
    expect(config.options.plugins.tooltip.callbacks.label({ parsed: { y: 2 }, raw: { u: null } }))
      .toBe('2 °C')
    expect(wrapper.text()).toContain('Temperatur (avg / 1h)')
  })

  it('leaves the spinner to the newest load when a superseded one finishes first', async () => {
    let resolveDp, resolveFirst
    const dpPending = new Promise(resolve => { resolveDp = resolve })
    const first     = new Promise(resolve => { resolveFirst = resolve })
    const second    = new Promise(() => {})   // still in flight

    const { wrapper, histAgg } = await mountHistory({ routeQuery: { dp: 'dp-1' }, dpPending })
    histAgg.mockImplementationOnce(() => first).mockImplementationOnce(() => second)

    await wrapper.find('button').trigger('click')
    resolveDp({ data: { name: 'Temperatur', unit: '°C' } })
    await flushPromises()

    resolveFirst({ data: [SAMPLE_POINT] })
    await flushPromises()

    // Request 2 is still running, so the view must still read as loading.
    expect(wrapper.find('button').attributes('disabled')).toBeDefined()
  })

  it('keeps the requested mode when it is switched mid-flight', async () => {
    let resolveQuery
    const pending = new Promise(resolve => { resolveQuery = resolve })
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, queryPending: pending })

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await modeSelect.setValue('aggregate')

    resolveQuery({ data: [{ ts: '2024-01-15T12:00:00Z', v: 21.5, u: 'bar', q: 'good' }] })
    await flushPromises()

    const [, config] = ChartMock.mock.calls.at(-1)
    expect(config.options.plugins.tooltip.callbacks.label({ parsed: { y: 21.5 }, raw: { u: 'bar' } }))
      .toBe('21.5 bar')
  })

  it('keeps the raw table for a raw response when the mode is switched mid-flight', async () => {
    let resolveQuery
    const pending = new Promise(resolve => { resolveQuery = resolve })
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, queryPending: pending })

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await modeSelect.setValue('aggregate')

    resolveQuery({ data: [SAMPLE_POINT] })
    await flushPromises()

    expect(wrapper.findAll('tbody tr').length).toBe(1)
  })

  it('hides the raw table when the mode is switched to raw without reloading', async () => {
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })

    // The loaded series is aggregate; the table must not present buckets as raw rows.
    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await nextTick()

    expect(wrapper.findAll('tbody tr').length).toBe(0)
  })

  it('keeps the requested description when the aggregation changes mid-flight', async () => {
    let resolveAgg
    const pending = new Promise(resolve => { resolveAgg = resolve })
    const { wrapper } = await mountHistory({ routeQuery: { dp: 'dp-1' }, aggPending: pending })

    // Same object, so the response is still wanted — but it was aggregated with
    // the function that was selected when the request went out.
    const [, fnSelect] = wrapper.findAll('select')
    await fnSelect.setValue('max')

    resolveAgg({ data: [SAMPLE_POINT] })
    await flushPromises()

    expect(ChartMock.mock.calls[0][1].data.datasets[0].label).toContain('(avg / 1h)')
    expect(wrapper.text()).toContain('Wohnzimmer Temp (avg / 1h)')
  })
})

// ─── Chart callbacks ─────────────────────────────────────────────────────────

describe('HistoryView — chart callbacks', () => {
  it('formats the x-axis ticks and the tooltip title as local date/time', async () => {
    await mountHistory({ routeQuery: { dp: 'dp-1' }, aggData: [SAMPLE_POINT] })
    const [, config] = ChartMock.mock.calls[0]
    const ms = Date.parse('2024-01-15T12:00:00Z')

    const tick = config.options.scales.x.ticks.callback(ms)
    expect(tick).toMatch(/\d/)
    expect(config.options.plugins.tooltip.callbacks.title([{ parsed: { x: ms } }])).toBe(tick)
  })

  it('appends the data point unit to the tooltip label in aggregate mode', async () => {
    await mountHistory({
      routeQuery: { dp: 'dp-1' },
      aggData:    [{ bucket: '2024-01-15T12:00:00Z', v: 21.5 }],
      dpData:     { name: 'Außentemperatur', unit: '°C' },
    })
    const [, config] = ChartMock.mock.calls[0]
    const label = config.options.plugins.tooltip.callbacks.label({ parsed: { y: 21.5 }, raw: { u: null } })
    expect(label).toBe('21.5 °C')
  })

  it('uses the per-point unit in the tooltip label in raw mode', async () => {
    const { wrapper, histQuery } = await mountHistory({ routeQuery: { dp: 'dp-1' }, dpData: { name: 'X', unit: 'kW' } })
    histQuery.mockResolvedValue({ data: [{ ...SAMPLE_POINT, u: 'bar' }] })

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    const [, config] = ChartMock.mock.calls[0]
    expect(config.options.plugins.tooltip.callbacks.label({ parsed: { y: 21.5 }, raw: { u: 'bar' } })).toBe('21.5 bar')
  })

  it('falls back to the data point unit in raw mode when the point carries none', async () => {
    const { wrapper, histQuery } = await mountHistory({ routeQuery: { dp: 'dp-1' }, dpData: { name: 'X', unit: 'kW' } })
    histQuery.mockResolvedValue({ data: [{ ts: '2024-01-15T12:00:00Z', v: 4 }] })

    const [modeSelect] = wrapper.findAll('select')
    await modeSelect.setValue('raw')
    await wrapper.find('button').trigger('click')
    await flushPromises()

    const [, config] = ChartMock.mock.calls[0]
    expect(config.options.plugins.tooltip.callbacks.label({ parsed: { y: 4 }, raw: { u: null } })).toBe('4 kW')
  })

  it('omits the unit in the tooltip label when none is known', async () => {
    await mountHistory({
      routeQuery: { dp: 'dp-1' },
      aggData:    [{ bucket: '2024-01-15T12:00:00Z', v: 3 }],
      dpData:     { name: 'Zähler' },
    })
    const [, config] = ChartMock.mock.calls[0]
    expect(config.options.plugins.tooltip.callbacks.label({ parsed: { y: 3 }, raw: { u: null } })).toBe('3')
  })
})
