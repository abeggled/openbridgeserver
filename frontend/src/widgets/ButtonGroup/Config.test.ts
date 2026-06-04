// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import ButtonGroupConfig from './Config.vue'

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (key === 'widgets.buttongroup.defaultButtonWithNumber') return `Button ${params?.number}`
      return key
    },
  }),
}))

function mountConfig() {
  return mount(ButtonGroupConfig, {
    props: {
      modelValue: {
        label: '',
        columns: 99,
        showLabel: true,
        buttons: [
          {
            id: 'button-1',
            label: '',
            icon: '',
            color: '#3b82f6',
            value: 'true',
            resetEnabled: false,
            resetValue: 'false',
            resetDelayMs: 300,
          },
        ],
      },
    },
    global: {
      mocks: {
        $t: (key: string, params?: Record<string, unknown>) => {
          if (key === 'widgets.buttongroup.buttons') return `Buttons (${params?.count}/${params?.max})`
          return key
        },
      },
      stubs: {
        IconPicker: true,
      },
    },
  })
}

describe('ButtonGroup config serialization', () => {
  it('does not emit on mount and saves default labels as a language-neutral sentinel', async () => {
    const wrapper = mountConfig()

    expect(wrapper.emitted('update:modelValue')).toBeUndefined()

    const textInputs = wrapper.findAll('input[type="text"]')
    expect((textInputs[1].element as HTMLInputElement).value).toBe('Button 1')

    await textInputs[0].setValue('Scenes')

    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toHaveLength(1)
    expect(emitted![0][0]).toMatchObject({
      label: 'Scenes',
      columns: 4,
      buttons: [
        {
          id: 'button-1',
          label: 'widgets.buttongroup.defaultButton',
        },
      ],
    })
  })
})
