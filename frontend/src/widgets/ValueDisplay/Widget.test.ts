// @vitest-environment jsdom
/**
 * Acceptance test for issue #1073.
 *
 * A ValueDisplay configured with three decimals must render the raw value 1.05
 * as `1,050` under a German regional format and as `1.050` under an English one
 * — the underlying datapoint value stays the locale-neutral number 1.05.
 */
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { DataPointValue } from '@/types'
import { useFormatStore } from '@/stores/format'
import ValueDisplayWidget from './Widget.vue'

vi.mock('@/api/client', () => ({
  history: { query: vi.fn().mockResolvedValue([]) },
}))

vi.mock('@/composables/useIcons', () => ({
  useIcons: () => ({
    getSvg: vi.fn().mockResolvedValue(''),
    isSvgIcon: vi.fn().mockReturnValue(false),
    svgIconName: vi.fn(),
  }),
}))

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: () => ({
    onMessage: () => () => {},
    onValue: () => () => {},
    connect: vi.fn(),
  }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

let wrapper: VueWrapper | null = null

function dataPointValue(value: unknown): DataPointValue {
  return { id: 'dp-1', v: value, u: 'h', t: '2026-06-04T00:00:00Z', q: 'good' }
}

function mountWidget(rawValue: number, decimals: number) {
  wrapper = mount(ValueDisplayWidget, {
    props: {
      config: {
        rules: [
          {
            fn: 'default',
            threshold: '',
            icon: '',
            color: '#000000',
            output_type: 'value',
            calculation: '',
            prefix: '',
            text: '',
            decimals,
            postfix: '',
          },
        ],
      },
      datapointId: 'dp-1',
      value: dataPointValue(rawValue),
      editorMode: false,
    },
  })
  return wrapper
}

describe('ValueDisplay Widget.vue — regional number format (#1073)', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
  })

  afterEach(() => {
    wrapper?.unmount()
    wrapper = null
  })

  it('renders 1.05 with three decimals as 1,050 in a German regional format', () => {
    useFormatStore().regionFormatSetting = 'de-DE'

    const value = mountWidget(1.05, 3).find('[data-testid="widget-value"]')

    expect(value.text()).toBe('1,050')
  })

  it('renders the same value as 1.050 in an English regional format', () => {
    useFormatStore().regionFormatSetting = 'en-GB'

    const value = mountWidget(1.05, 3).find('[data-testid="widget-value"]')

    expect(value.text()).toBe('1.050')
  })

  it('uses the Swiss regional format while the UI language stays German', () => {
    const store = useFormatStore()
    store.language = 'de'
    store.regionFormatSetting = 'de-CH'

    const value = mountWidget(1234.5, 2).find('[data-testid="widget-value"]')

    expect(value.text()).toBe("1'234.50")
    expect(store.language).toBe('de')
  })

  it('leaves the raw datapoint value untouched', () => {
    useFormatStore().regionFormatSetting = 'de-DE'

    const rawValue = dataPointValue(1.05)
    wrapper = mount(ValueDisplayWidget, {
      props: { config: {}, datapointId: 'dp-1', value: rawValue, editorMode: false },
    })

    expect(rawValue.v).toBe(1.05)
    expect(wrapper.find('[data-testid="widget-value"]').text()).toBe('1,1')
  })

  it('still renders a page whose persisted decimals value is malformed (#1073)', () => {
    // WidgetInstance.config is an arbitrary dictionary, so an import or API
    // write can persist junk here; the page must not fail to render.
    useFormatStore().regionFormatSetting = 'de-DE'

    expect(() => mountWidget(1.05, 'bad' as unknown as number)).not.toThrow()
    expect(wrapper!.find('[data-testid="widget-value"]').text()).toBe('1')
  })

  it('falls back to one decimal when no rule matches the value type', () => {
    useFormatStore().regionFormatSetting = 'de-DE'

    wrapper = mount(ValueDisplayWidget, {
      props: {
        config: {},
        datapointId: 'dp-1',
        value: dataPointValue(21.55),
        editorMode: false,
      },
    })

    expect(wrapper.find('[data-testid="widget-value"]').text()).toBe('21,6')
  })
})
