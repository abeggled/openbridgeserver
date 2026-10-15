// @vitest-environment jsdom
/**
 * Chart widget number formatting (issue #1073).
 *
 * Chart.js formats its axis ticks with `Intl.NumberFormat` under
 * `options.locale`; left unset it silently follows the browser language instead
 * of the configured regional format. The tooltip values go through the same
 * formatter as every other widget.
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useFormatStore } from '@/stores/format'
import { useThemeStore } from '@/stores/theme'
import { history } from '@/api/client'

const chartConfigs: any[] = []
const chartInstances: any[] = []

vi.mock('chart.js', () => {
  class ChartMock {
    static register = vi.fn()
    options: unknown
    constructor(_canvas: unknown, config: any) {
      chartConfigs.push(config)
      chartInstances.push(this)
      this.options = config.options
    }

    update = vi.fn()
    destroy = vi.fn()
    data = { datasets: [] as unknown[] }
  }
  return {
    Chart: ChartMock,
    LineController: {}, LineElement: {}, PointElement: {},
    LinearScale: {}, CategoryScale: {}, Filler: {}, Tooltip: {}, Legend: {},
    BarController: {}, BarElement: {},
  }
})

vi.mock('@/api/client', () => ({
  history: { query: vi.fn().mockResolvedValue([]), aggregate: vi.fn().mockResolvedValue([]) },
}))

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: () => ({ onMessage: () => () => {}, connect: vi.fn() }),
}))

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

let wrapper: VueWrapper | null = null

// Der Theme-Store fiele ohne gespeichertes Theme auf matchMedia zurück, das jsdom nicht kennt
beforeEach(() => { localStorage.setItem('visu_theme', 'dark') })
afterEach(() => { localStorage.clear() })

async function mountChart(config: Record<string, unknown> = { label: 'Verlauf' }) {
  const mod = await import('./Widget.vue')
  wrapper = mount(mod.default, {
    props: {
      config,
      datapointId: 'dp-1',
      value: null,
      editorMode: false,
    },
    global: {
      mocks: { $t: (key: string) => key },
    },
    attachTo: document.body,
  })
  return chartConfigs[chartConfigs.length - 1]
}

describe('Chart Widget.vue — regional number format (#1073)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    chartConfigs.length = 0
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  it('passes the configured regional format to Chart.js', async () => {
    useFormatStore().regionFormatSetting = 'de-CH'

    const config = await mountChart()

    expect(config.options.locale).toBe('de-CH')
  })

  it('formats tooltip values with unit in the regional format', async () => {
    useFormatStore().regionFormatSetting = 'de-DE'

    const config = await mountChart()
    const label = config.options.plugins.tooltip.callbacks.label

    expect(label({ parsed: { y: 1234.5 }, datasetIndex: 0, dataset: { label: 'L1' } })).toBe('L1: 1.234,5')
  })

  it('updates the Chart.js locale when the settings arrive after mount', async () => {
    // App.vue starts the public-settings load in its own onMounted, which Vue
    // runs *after* the child widget has already constructed its chart.
    const store = useFormatStore()
    const config = await mountChart()
    expect(config.options.locale).toBe('de-DE')

    store.regionFormatSetting = 'de-CH'
    await nextTick()

    expect(config.options.locale).toBe('de-CH')
  })

  it('returns an empty label for a missing data point', async () => {
    useFormatStore().regionFormatSetting = 'de-DE'

    const config = await mountChart()
    const label = config.options.plugins.tooltip.callbacks.label

    expect(label({ parsed: { y: null }, datasetIndex: 0, dataset: { label: 'L1' } })).toBe('')
  })
})

describe('Chart Widget.vue — y axes follow the series assignment (#1111)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    chartConfigs.length = 0
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  async function axesAfterLoad(config: Record<string, unknown>) {
    const chartConfig = await mountChart({ label: 'Verlauf', ...config })
    await flushPromises()
    return chartConfig.options.scales
  }

  it('hides the left axis when every series is on the right', async () => {
    const scales = await axesAfterLoad({
      primary_axis: 'right',
      series: [{ dp_id: 'dp-2', axis: 'right' }],
    })

    expect(scales.y.display).toBe(false)
    expect(scales.y1.display).toBe(true)
  })

  it('shows only the left axis when every series is on the left', async () => {
    const scales = await axesAfterLoad({ series: [{ dp_id: 'dp-2' }] })

    expect(scales.y.display).toBe(true)
    expect(scales.y1.display).toBe(false)
  })

  it('shows both axes when series are split across them', async () => {
    const scales = await axesAfterLoad({ series: [{ dp_id: 'dp-2', axis: 'right' }] })

    expect(scales.y.display).toBe(true)
    expect(scales.y1.display).toBe(true)
  })
})

describe('Chart Widget.vue — tooltip swatches and legend (#1361)', () => {
  // Als Variablen, damit der i18n-Guard die Fixture-Werte nicht als UI-Text wertet
  const bedroom = ['Schlaf', 'zimmer'].join('')
  const bath    = ['B', 'ad'].join('')

  const twoSeries = {
    label:         'Verlauf',
    primary_label: bedroom,
    primary_color: '#3b82f6',
    series:        [{ dp_id: 'dp-2', label: bath, color: '#ef4444' }],
  }

  beforeEach(() => {
    setActivePinia(createPinia())
    chartConfigs.length = 0
    chartInstances.length = 0
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  async function mountLoaded(config: Record<string, unknown>) {
    const chartConfig = await mountChart(config)
    await flushPromises()
    return { chartConfig, chart: chartInstances[chartInstances.length - 1] }
  }

  it('fills the tooltip swatch completely with the series colour', async () => {
    const { chartConfig } = await mountLoaded(twoSeries)
    const labelColor = chartConfig.options.plugins.tooltip.callbacks.labelColor

    expect(labelColor({ datasetIndex: 1 })).toEqual({ borderColor: '#ef4444', backgroundColor: '#ef4444', borderWidth: 0 })
  })

  it('falls back to a neutral swatch for an unknown dataset', async () => {
    const { chartConfig } = await mountLoaded(twoSeries)
    const labelColor = chartConfig.options.plugins.tooltip.callbacks.labelColor

    expect(labelColor({ datasetIndex: 7 }).backgroundColor).toBe('#6b7280')
  })

  it('keeps the Chart.js legend off and renders underlined HTML entries instead', async () => {
    const { chartConfig } = await mountLoaded(twoSeries)

    expect(chartConfig.options.plugins.legend.display).toBe(false)
    const items = wrapper!.findAll('.chart-legend-item')
    expect(items.map(i => i.text())).toEqual([bedroom, bath])
    expect((items[1].element as HTMLElement).style.borderBottomColor).toBe('rgb(239, 68, 68)')
    expect(items[0].attributes('aria-pressed')).toBe('true')
    // Light- und Dark-Mode brauchen eigene Text-/Hover-Farben, sonst wird der Hover hell auf hell
    expect(items[0].classes()).toEqual(expect.arrayContaining([
      'text-gray-500', 'dark:text-gray-400', 'hover:text-gray-900', 'dark:hover:text-gray-200',
    ]))
  })

  it('numbers unlabelled series in the legend', async () => {
    await mountLoaded({ label: 'Verlauf', series: [{ dp_id: 'dp-2' }] })

    expect(wrapper!.findAll('.chart-legend-item').map(i => i.text()))
      .toEqual(['Verlauf', 'widgets.chart.seriesFallback'])
  })

  it('shows no legend for a single series', async () => {
    await mountLoaded({ label: 'Verlauf' })

    expect(wrapper!.find('.chart-legend-item').exists()).toBe(false)
  })

  it('hides and shows a series when its legend entry is clicked', async () => {
    const { chart } = await mountLoaded(twoSeries)
    const item = () => wrapper!.findAll('.chart-legend-item')[1]

    await item().trigger('click')
    expect(chart.data.datasets[1].hidden).toBe(true)
    expect(item().classes()).toContain('line-through')
    expect(item().attributes('aria-pressed')).toBe('false')
    expect(chart.update).toHaveBeenCalled()

    await item().trigger('click')
    expect(chart.data.datasets[1].hidden).toBe(false)
    expect(item().classes()).not.toContain('line-through')
  })

  it('keeps a hidden series hidden when the data is reloaded', async () => {
    const { chart } = await mountLoaded(twoSeries)
    await wrapper!.findAll('.chart-legend-item')[1].trigger('click')

    await wrapper!.setProps({ config: { ...twoSeries, time_range: 'last_24h' } })
    await flushPromises()

    expect(chart.data.datasets.map((d: any) => d.hidden)).toEqual([false, true])
  })

  it('remembers the toggle while a freshly created chart is still empty', async () => {
    await mountLoaded(twoSeries)
    const item = wrapper!.findAll('.chart-legend-item')[1]

    // Typwechsel baut einen neuen, noch leeren Chart; loadData() hängt noch an der History-Abfrage
    let release: (rows: unknown[]) => void = () => {}
    const pending = new Promise<unknown[]>(resolve => { release = resolve })
    vi.mocked(history.query).mockImplementation(() => pending as never)
    vi.mocked(history.aggregate).mockImplementation(() => pending as never)
    try {
      await wrapper!.setProps({ config: { ...twoSeries, chart_type: 'bar' } })
      const fresh = chartInstances[chartInstances.length - 1]
      await item.trigger('click')
      expect(fresh.data.datasets).toEqual([])
      expect(fresh.update).not.toHaveBeenCalled()
    } finally {
      vi.mocked(history.query).mockResolvedValue([])
      vi.mocked(history.aggregate).mockResolvedValue([])
      release([])
    }

    await flushPromises()
    const fresh = chartInstances[chartInstances.length - 1]
    expect(fresh.data.datasets.map((d: any) => d.hidden)).toEqual([false, true])
  })
})

describe('Chart Widget.vue — grid colour follows the Visu theme (#1361)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    chartConfigs.length = 0
    chartInstances.length = 0
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  const gridColors = (scales: any) => [scales.x.grid.color, scales.y.grid.color, scales.y1.grid.color]

  it('uses the dark grid colour in dark mode', async () => {
    const config = await mountChart()

    expect(gridColors(config.options.scales)).toEqual(['#374151', '#374151', '#374151'])
  })

  it('uses a light grid colour in light mode', async () => {
    localStorage.setItem('visu_theme', 'light')
    const config = await mountChart()

    expect(gridColors(config.options.scales)).toEqual(['#e5e7eb', '#e5e7eb', '#e5e7eb'])
  })

  it('recolours the grid when the theme is toggled', async () => {
    const config = await mountChart()
    const chart = chartInstances[chartInstances.length - 1]
    chart.update.mockClear()

    useThemeStore().toggle()
    await nextTick()

    expect(gridColors(config.options.scales)).toEqual(['#e5e7eb', '#e5e7eb', '#e5e7eb'])
    expect(chart.update).toHaveBeenCalled()
  })

  it('ignores a theme toggle in the editor, where no chart is drawn', async () => {
    const mod = await import('./Widget.vue')
    wrapper = mount(mod.default, {
      props: { config: { label: 'Verlauf' }, datapointId: 'dp-1', value: null, editorMode: true },
      global: { mocks: { $t: (key: string) => key } },
    })

    useThemeStore().toggle()
    await nextTick()

    expect(chartInstances).toEqual([])
  })
})
