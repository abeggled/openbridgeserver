// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import InfoConfig from './Config.vue'

function translate(key: string, params?: Record<string, unknown>) {
  const messages: Record<string, string> = {
    'widgets.common.label': 'Label',
    'widgets.info.unitOverride': 'Unit override',
    'widgets.info.decimals': 'Decimal places',
    'widgets.info.additionalValues': `Additional values (max. ${params?.max})`,
    'widgets.info.valueN': `Value ${params?.n}`,
    'widgets.info.mainLabelPlaceholder': 'e.g. flow temperature',
    'widgets.info.unitPlaceholder': 'e.g. deg C',
    'widgets.info.placeholderLabel': 'Extra label',
    'widgets.info.placeholderUnit': 'Extra unit',
    'widgets.info.placeholderDecimals': 'Extra decimals',
  }
  return messages[key] ?? key
}

function mountConfig() {
  return mount(InfoConfig, {
    props: {
      modelValue: {
        label: '',
        unit: '',
        decimals: 1,
        extra_datapoints: [{ id: 'dp-1', label: '', unit: '', decimals: 1 }],
      },
    },
    global: {
      mocks: {
        $t: translate,
      },
      stubs: {
        DataPointPicker: true,
      },
    },
  })
}

describe('Info widget config i18n', () => {
  it('renders translated labels instead of raw translation calls', () => {
    const wrapper = mountConfig()

    expect(wrapper.text()).toContain('Additional values (max. 6)')
    expect(wrapper.text()).not.toContain("$t('widgets.info.additionalValues'")

    const inputs = wrapper.findAll('input')
    expect(inputs[0].attributes('placeholder')).toBe('e.g. flow temperature')
    expect(inputs[1].attributes('placeholder')).toBe('e.g. deg C')
    expect(inputs[3].attributes('placeholder')).toBe('Extra label')
    expect(inputs[4].attributes('placeholder')).toBe('Extra unit')
    expect(inputs[5].attributes('placeholder')).toBe('Extra decimals')
  })
})
