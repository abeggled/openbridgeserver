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

function mountConfig(modelValue: Record<string, unknown> = {
  label: '',
  steps: [
    { label: 'widgets.stufenschalter.defaultOffLabel', value: '0', icon: '', color: '#6b7280' },
    { label: 'widgets.stufenschalter.defaultStepLabel', value: '1', icon: '', color: '#3b82f6' },
    { label: 'widgets.stufenschalter.defaultStepLabel', value: '2', icon: '', color: '#10b981' },
  ],
}) {
  return mount(StufenschalterConfig, {
    props: {
      modelValue,
    },
    global: {
      mocks: {
        $t: (key: string, params?: Record<string, unknown>) => {
          if (key === 'widgets.stufenschalter.stepsCount') return `Steps (${params?.n}/${params?.max})`
          if (key === 'widgets.stufenschalter.optionsCount') return `Options (${params?.n}/${params?.max})`
          if (key === 'widgets.stufenschalter.modeSequence') return 'Sequence'
          if (key === 'widgets.stufenschalter.modeSelectSave') return 'Selection with save'
          if (key === 'widgets.stufenschalter.modeSelectDirect') return 'Direct selection'
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
      mode: 'sequence',
      options: [
        { label: 'widgets.stufenschalter.defaultOffLabel', value: '0' },
        { label: 'widgets.stufenschalter.defaultStepLabel', value: '1' },
        { label: 'widgets.stufenschalter.defaultStepLabel', value: '2' },
      ],
    })
  })

  it('preserves legacy steps when editor defaults inject options', async () => {
    const wrapper = mountConfig({
      label: '',
      mode: 'sequence',
      options: [
        { label: 'widgets.stufenschalter.defaultOffLabel', value: '0', icon: '', color: '#6b7280' },
        { label: 'widgets.stufenschalter.defaultStepLabel', value: '1', icon: '', color: '#3b82f6' },
        { label: 'widgets.stufenschalter.defaultStepLabel', value: '2', icon: '', color: '#10b981' },
      ],
      steps: [
        { label: 'Low', value: '10', icon: '', color: '#6b7280' },
        { label: 'High', value: '20', icon: '', color: '#3b82f6' },
      ],
    })

    const textInputs = wrapper.findAll('input[type="text"]')
    expect((textInputs[1].element as HTMLInputElement).value).toBe('Low')
    expect((textInputs[3].element as HTMLInputElement).value).toBe('High')

    await textInputs[0].setValue('FanSpeed')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted![0][0]).toMatchObject({
      label: 'FanSpeed',
      mode: 'sequence',
      options: [
        { label: 'Low', value: '10' },
        { label: 'High', value: '20' },
      ],
    })
  })
})
