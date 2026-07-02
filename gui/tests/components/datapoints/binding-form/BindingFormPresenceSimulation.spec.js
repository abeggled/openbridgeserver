import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormPresenceSimulation from '@/components/datapoints/binding-form/BindingFormPresenceSimulation.vue'

function mk(propsOverride = {}) {
  return mount(BindingFormPresenceSimulation, {
    props: {
      cfg:             { offset_override: null, on_presence_override: null, on_presence_value: '' },
      anwOffsetSelect: '',
      ...propsOverride,
    },
  })
}

describe('BindingFormPresenceSimulation', () => {
  it('renders the offset select', () => {
    expect(mk().find('select').exists()).toBe(true)
  })

  it('hides custom-days number input when anwOffsetSelect is not "custom"', () => {
    const w = mk({ anwOffsetSelect: '' })
    expect(w.find('input[type="number"]').exists()).toBe(false)
  })

  it('shows custom-days number input when anwOffsetSelect="custom"', () => {
    const w = mk({ anwOffsetSelect: 'custom' })
    expect(w.find('input[type="number"]').exists()).toBe(true)
  })

  it('emits update:anwOffsetSelect and anw-offset-select-change on select change', async () => {
    const w = mk()
    await w.find('select').setValue('7')
    expect(w.emitted('update:anwOffsetSelect')).toEqual([['7']])
    expect(w.emitted('anw-offset-select-change')).toBeTruthy()
  })

  it('renders on_presence_override select', () => {
    expect(mk().findAll('select').length).toBe(2)
  })

  it('hides on_presence_value text input when on_presence_override is not "setzen"', () => {
    const cfg = { offset_override: null, on_presence_override: 'behalten', on_presence_value: '' }
    const w = mk({ cfg })
    // There should be no text input (only number input for custom, which is also hidden here)
    expect(w.find('input[type="text"]').exists()).toBe(false)
  })

  it('shows on_presence_value text input when on_presence_override="setzen"', () => {
    const cfg = { offset_override: null, on_presence_override: 'setzen', on_presence_value: '' }
    const w = mk({ cfg })
    expect(w.find('input[type="text"]').exists()).toBe(true)
  })

  it('emits anw-offset-custom-input when custom number input fires input', async () => {
    const cfg = { offset_override: null, on_presence_override: null, on_presence_value: '' }
    const w = mk({ cfg, anwOffsetSelect: 'custom' })
    await w.find('input[type="number"]').trigger('input')
    expect(w.emitted('anw-offset-custom-input')).toBeTruthy()
  })
})
