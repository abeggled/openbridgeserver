import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormKnx from '@/components/datapoints/binding-form/BindingFormKnx.vue'

const GA_STUB = {
  template: '<input class="ga-combobox" :value="modelValue" @change="$emit(\'select\', $event.target.value)" />',
  props: ['modelValue', 'placeholder'],
  emits: ['select', 'update:modelValue'],
}

const GROUPED_DPTS = [
  {
    family: '1',
    label:  'DPT 1 — Boolean',
    dpts:   [{ dpt_id: '1.001', name: 'Switch', unit: '' }],
  },
  {
    family: '9',
    label:  'DPT 9 — Float',
    dpts:   [{ dpt_id: '9.001', name: 'Temperature', unit: '°C' }],
  },
]

function mk(propsOverride = {}) {
  return mount(BindingFormKnx, {
    props: {
      cfg:          { group_address: '', dpt_id: '', respond_to_read: false },
      form:         { direction: 'BOTH' },
      groupedDpts:  GROUPED_DPTS,
      dpPersistValue: true,
      ...propsOverride,
    },
    global: { stubs: { GaCombobox: GA_STUB } },
  })
}

describe('BindingFormKnx', () => {
  it('renders the GaCombobox for group address', () => {
    expect(mk().find('.ga-combobox').exists()).toBe(true)
  })

  it('renders the DPT select with optgroups', () => {
    const w = mk()
    expect(w.findAll('optgroup').length).toBe(2)
    expect(w.find('optgroup[label="DPT 1 — Boolean"]').exists()).toBe(true)
  })

  it('renders DPT options inside optgroup', () => {
    const w = mk()
    expect(w.text()).toContain('1.001')
    expect(w.text()).toContain('Switch')
  })

  it('renders DPT option with unit in brackets when unit is set', () => {
    const w = mk()
    expect(w.text()).toContain('[°C]')
  })

  it('shows respond_to_read checkbox for direction=BOTH', () => {
    const w = mk({ form: { direction: 'BOTH' } })
    expect(w.find('#respond_to_read').exists()).toBe(true)
  })

  it('shows respond_to_read checkbox for direction=SOURCE', () => {
    const w = mk({ form: { direction: 'SOURCE' } })
    expect(w.find('#respond_to_read').exists()).toBe(true)
  })

  it('hides respond_to_read checkbox for direction=TARGET', () => {
    const w = mk({ form: { direction: 'TARGET' } })
    expect(w.find('#respond_to_read').exists()).toBe(false)
  })

  it('respond_to_read checkbox is disabled when dpPersistValue=false', () => {
    const w = mk({ dpPersistValue: false })
    expect(w.find('#respond_to_read').attributes('disabled')).toBeDefined()
  })

  it('respond_to_read checkbox is enabled when dpPersistValue=true', () => {
    const w = mk({ dpPersistValue: true })
    expect(w.find('#respond_to_read').attributes('disabled')).toBeUndefined()
  })

  it('emits ga-select when GaCombobox fires select event', async () => {
    const w = mk()
    await w.find('.ga-combobox').setValue('1/0/1')
    await w.find('.ga-combobox').trigger('change')
    expect(w.emitted('ga-select')).toBeTruthy()
  })

  describe('DPT main type without a subtype (#1260)', () => {
    const WITH_MAIN_TYPE = [
      {
        family: 'DPT14',
        label:  'DPT 14.x — 32-Bit IEEE Float',
        dpts:   [
          { dpt_id: 'DPT14', name: '32-Bit Float (no subtype)', unit: '' },
          { dpt_id: 'DPT14.056', name: 'Power', unit: 'W' },
        ],
      },
    ]

    it('offers the main type as an option labelled as having no subtype, next to the subtypes', async () => {
      const w = mk({ groupedDpts: WITH_MAIN_TYPE })
      const option = w.find('option[value="DPT14"]')
      expect(option.text()).toBe('DPT 14 (ohne Subtyp)')
      expect(w.find('option[value="DPT14.056"]').text()).toBe('DPT14.056 — Power [W]')
      w.vm.$i18n.locale = 'en'
      await w.vm.$nextTick()
      expect(option.text()).toBe('DPT 14 (no subtype)')
      w.vm.$i18n.locale = 'de'
    })

    it('selecting the main type writes it into the binding config and shows it as selected', async () => {
      const cfg = { group_address: '', dpt_id: '', respond_to_read: false }
      const w = mk({ cfg, groupedDpts: WITH_MAIN_TYPE })
      await w.find('select').setValue('DPT14')
      expect(cfg.dpt_id).toBe('DPT14')
      const select = w.find('select').element
      expect(select.options[select.selectedIndex].text.trim()).toBe('DPT 14 (ohne Subtyp)')
    })
  })
})
