import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormKnx from '@/components/datapoints/binding-form/BindingFormKnx.vue'

const GA_STUB = {
  template: '<input class="ga-combobox" :value="modelValue" />',
  props: ['modelValue', 'placeholder'],
  emits: ['select', 'update:modelValue'],
}

const GROUPED_DPTS = [
  { family: '1', label: 'DPT 1', dpts: [{ dpt_id: '1.001', name: 'Switch', unit: '' }] },
  { family: '9', label: 'DPT 9', dpts: [{ dpt_id: '9.001', name: 'Temperature', unit: '°C' }] },
]

function mk(overrides = {}) {
  return mount(BindingFormKnx, {
    props: {
      cfg:           { group_address: '', dpt_id: '', respond_to_read: false },
      form:          { direction: 'BOTH' },
      groupedDpts:   GROUPED_DPTS,
      dpPersistValue: true,
      ...overrides,
    },
    global: { stubs: { GaCombobox: GA_STUB } },
  })
}

describe('BindingFormKnx — v-model mutations', () => {
  it('mutates cfg.dpt_id when DPT select changes', async () => {
    const cfg = { group_address: '', dpt_id: '', respond_to_read: false }
    const w = mount(BindingFormKnx, {
      props: { cfg, form: { direction: 'BOTH' }, groupedDpts: GROUPED_DPTS, dpPersistValue: true },
      global: { stubs: { GaCombobox: GA_STUB } },
    })
    const dptSelect = w.find('select')
    await dptSelect.setValue('9.001')
    expect(cfg.dpt_id).toBe('9.001')
    w.unmount()
  })

  it('mutates cfg.respond_to_read when checkbox is toggled', async () => {
    const cfg = { group_address: '', dpt_id: '', respond_to_read: false }
    const w = mount(BindingFormKnx, {
      props: { cfg, form: { direction: 'SOURCE' }, groupedDpts: GROUPED_DPTS, dpPersistValue: true },
      global: { stubs: { GaCombobox: GA_STUB } },
    })
    await w.find('#respond_to_read').setChecked(true)
    expect(cfg.respond_to_read).toBe(true)
    w.unmount()
  })
})
