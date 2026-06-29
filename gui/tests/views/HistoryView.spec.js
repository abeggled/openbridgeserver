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
} = {}) {
  vi.doMock('vue-router', () => ({
    useRoute: () => ({ query: routeQuery }),
  }))

  const histAgg   = vi.fn().mockResolvedValue({ data: aggData })
  const histQuery = vi.fn().mockResolvedValue({ data: queryData })
  const dpGet     = vi.fn().mockResolvedValue({ data: dpData })

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
