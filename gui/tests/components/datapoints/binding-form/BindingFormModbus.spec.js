import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormModbus from '@/components/datapoints/binding-form/BindingFormModbus.vue'

function mk(cfg = {}) {
  return mount(BindingFormModbus, {
    props: {
      cfg: {
        address: 0, register_type: 'holding', data_format: 'uint16',
        unit_id: null, count: null, scale_factor: null,
        poll_interval: null, byte_order: 'big', word_order: 'big',
        ...cfg,
      },
    },
  })
}

describe('BindingFormModbus', () => {
  it('renders the address number input', () => {
    expect(mk().find('input[type="number"]').exists()).toBe(true)
  })

  it('renders the register type select with 4 options', () => {
    const opts = mk().findAll('select')[0].findAll('option')
    expect(opts.length).toBe(4)
  })

  it('renders the data format select with optgroups', () => {
    const w = mk()
    expect(w.findAll('optgroup').length).toBeGreaterThan(0)
    expect(w.text()).toContain('UINT16')
  })

  it('address input has required attribute', () => {
    expect(mk().find('input[type="number"]').attributes('required')).toBeDefined()
  })

  it('address input is bound to cfg.address', () => {
    const cfg = { address: 42, register_type: 'holding', data_format: 'uint16' }
    const w = mount(BindingFormModbus, { props: { cfg: { ...cfg, unit_id: null, count: null, scale_factor: null, poll_interval: null, byte_order: 'big', word_order: 'big' } } })
    expect(w.find('input[type="number"]').element.value).toBe('42')
  })

  it('renders byte order and word order selects', () => {
    const selects = mk().findAll('select')
    // register_type, data_format, byte_order, word_order = at least 4
    expect(selects.length).toBeGreaterThanOrEqual(4)
  })
})
