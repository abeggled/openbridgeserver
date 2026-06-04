// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import StufenschalterConfig from './Config.vue'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (key === 'widgets.stufenschalter.defaultOffLabel') return 'Off'
      if (key === 'widgets.stufenschalter.defaultStepLabel') return `Step ${params?.n}`
      return key
    },
  }),
}))

function mountConfig() {
  return mount(StufenschalterConfig, {
    props: {
      modelValue: {
        label: '',
        steps: [
          { label: 'widgets.stufenschalter.defaultOffLabel', value: '0', icon: '', color: '#6b7280' },
          { label: 'widgets.stufenschalter.defaultStepLabel', value: '1', icon: '', color: '#3b82f6' },
          { label: 'widgets.stufenschalter.defaultStepLabel', value: '2', icon: '', color: '#10b981' },
        ],
      },
    },
    global: {
      mocks: {
        $t: (key: string, params?: Record<string, unknown>) => {
          if (key === 'widgets.stufenschalter.stepsCount') return `Steps (${params?.n}/${params?.max})`
          return key
        },
      },
      stubs: {
        IconPicker: true,
      },
    },
  })
}

describe('Stufenschalter config serialization', () => {
  it('displays localized defaults but keeps language-neutral sentinel labels when saving', async () => {
    const wrapper = mountConfig()

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()

    const textInputs = wrapper.findAll('input[type="text"]')
    expect((textInputs[1].element as HTMLInputElement).value).toBe('Off')
    expect((textInputs[3].element as HTMLInputElement).value).toBe('Step 1')
    expect((textInputs[5].element as HTMLInputElement).value).toBe('Step 2')

    await textInputs[0].setValue('FanSpeed')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toMatchObject({
      label: 'FanSpeed',
      steps: [
        { label: 'widgets.stufenschalter.defaultOffLabel', value: '0' },
        { label: 'widgets.stufenschalter.defaultStepLabel', value: '1' },
        { label: 'widgets.stufenschalter.defaultStepLabel', value: '2' },
      ],
    })
  })
})
