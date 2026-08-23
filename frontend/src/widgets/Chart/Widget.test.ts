// @vitest-environment jsdom
/**
 * Chart widget number formatting (issue #1073).
 *
 * Chart.js formats its axis ticks with `Intl.NumberFormat` under
 * `options.locale`; left unset it silently follows the browser language instead
 * of the configured regional format. The tooltip values go through the same
 * formatter as every other widget.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { nextTick } from 'vue'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useFormatStore } from '@/stores/format'

const chartConfigs: any[] = []

vi.mock('chart.js', () => {
  class ChartMock {
    static register = vi.fn()
    options: unknown
    constructor(_canvas: unknown, config: any) {
      chartConfigs.push(config)
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

async function mountChart() {
  const mod = await import('./Widget.vue')
  wrapper = mount(mod.default, {
    props: {
      config: { label: 'Verlauf' },
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
