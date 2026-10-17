// @vitest-environment jsdom
/**
 * Issue #1073 — the widgets named in the report (Info, HorizontalBar,
 * Energiefluss) render numbers in the configured regional format instead of a
 * hard-coded decimal point. The datapoint values themselves stay neutral.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DataPointValue } from '@/types'
import { useFormatStore } from '@/stores/format'
import InfoWidget from './Info/Widget.vue'
import HorizontalBarWidget from './HorizontalBar/Widget.vue'
import EnergieflussWidget from './Energiefluss/Widget.vue'

const storeValues = new Map<string, DataPointValue>()

vi.mock('@/stores/datapoints', () => ({
  useDatapointsStore: () => ({
    getValue: (id: string) => storeValues.get(id) ?? null,
    subscribe: vi.fn(),
  }),
}))

vi.mock('@/composables/useIcons', () => ({
  useIcons: () => ({
    getSvg: vi.fn().mockResolvedValue(''),
    isSvgIcon: vi.fn().mockReturnValue(false),
    svgIconName: vi.fn(),
  }),
}))

let wrapper: VueWrapper | null = null

function dpValue(value: unknown, unit: string | null = null): DataPointValue {
  return { id: 'dp-1', v: value, u: unit, t: '2026-06-04T00:00:00Z', q: 'good' }
}

beforeEach(() => {
  setActivePinia(createPinia())
  storeValues.clear()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
})

describe('Info widget (#1073)', () => {
  it.each([
    ['de-DE', '1,050', '1.050'],
    ['de-CH', '1.050', '1,050'],
    ['en-US', '1.050', '1,050'],
  ])('renders 1.05 with three decimals for %s', (region, expected, unexpected) => {
    useFormatStore().regionFormatSetting = region

    wrapper = mount(InfoWidget, {
      props: {
        config: { decimals: 3 },
        datapointId: 'dp-1',
        value: dpValue(1.05, 'h'),
        editorMode: false,
      },
    })

    expect(wrapper.text()).toContain(expected)
    expect(wrapper.text()).not.toContain(unexpected)
  })

  it('leaves non-numeric values untouched', () => {
    useFormatStore().regionFormatSetting = 'de-DE'

    wrapper = mount(InfoWidget, {
      props: {
        config: { decimals: 3 },
        datapointId: 'dp-1',
        value: dpValue('n/a'),
        editorMode: false,
      },
    })

    expect(wrapper.text()).toContain('n/a')
  })
})

describe('HorizontalBar widget (#1073)', () => {
  it.each([
    ['de-DE', '1.234,50'],
    ['de-CH', "1'234.50"],
  ])('renders a grouped bar value for %s', (region, expected) => {
    useFormatStore().regionFormatSetting = region
    storeValues.set('dp-bar', dpValue(1234.5, 'W'))

    wrapper = mount(HorizontalBarWidget, {
      props: {
        config: {
          bars: [{ label: 'Bar', dp_id: 'dp-bar', min: 0, max: 2000, decimals: 2, prefix: '', postfix: '' }],
        },
        datapointId: null,
        value: null,
        editorMode: false,
      },
    })

    expect(wrapper.find('[data-testid="widget-value"]').text()).toContain(expected)
  })
})

describe('Energiefluss widget (#1073)', () => {
  it.each([
    ['de-DE', '1,5'],
    ['en-US', '1.5'],
  ])('renders the kW conversion for %s', (region, expected) => {
    useFormatStore().regionFormatSetting = region
    storeValues.set('dp-house', dpValue(1500, 'W'))

    wrapper = mount(EnergieflussWidget, {
      props: {
        config: {
          house_dp: 'dp-house',
          house_unit: 'W',
          house_decimals: 1,
          entities: [
            { id: 'dp-house', label: 'Netz', icon: '', color: '#3b82f6', direction: 'to_house', unit: 'W', decimals: 1, invert: false },
          ],
        },
        datapointId: null,
        value: null,
        editorMode: false,
      },
    })

    expect(wrapper.find('[data-testid="ef-house-value"]').text()).toContain(expected)
  })
})
