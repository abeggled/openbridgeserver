import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormModbus from '@/components/datapoints/binding-form/BindingFormModbus.vue'

function mk(cfg = {}) {
  return mount(BindingFormModbus, {
    props: {
      cfg: {
        address: 0, register_type: 'holding', data_format: 'uint16',
        unit_id: 1, count: 1, scale_factor: 1.0,
        poll_interval: 1.0, byte_order: 'big', word_order: 'big',
        ...cfg,
      },
    },
  })
}

describe('BindingFormModbus — v-model mutations (optional fields)', () => {
  it('mutates cfg.count via input', async () => {
    const cfg = { address: 0, register_type: 'holding', data_format: 'uint16', unit_id: 1, count: 1, scale_factor: 1.0, poll_interval: 1.0, byte_order: 'big', word_order: 'big' }
    const w = mount(BindingFormModbus, { props: { cfg } })
    const inputs = w.findAll('input[type="number"]')
    // count is the 5th number input (address, then optional grid: unit_id, count, scale_factor, poll_interval)
    const countInput = inputs.find(i => i.element.value === '1' && i.attributes('max') === '125')
    await countInput.setValue('4')
    expect(cfg.count).toBe(4)
    w.unmount()
  })

  it('mutates cfg.scale_factor via input', async () => {
    const cfg = { address: 0, register_type: 'holding', data_format: 'uint16', unit_id: 1, count: 1, scale_factor: 1.0, poll_interval: 1.0, byte_order: 'big', word_order: 'big' }
    const w = mount(BindingFormModbus, { props: { cfg } })
    const scaleInput = w.findAll('input[type="number"]').find(i => i.attributes('step') === 'any')
    await scaleInput.setValue('0.1')
    expect(cfg.scale_factor).toBe(0.1)
    w.unmount()
  })

  it('mutates cfg.poll_interval via input', async () => {
    const cfg = { address: 0, register_type: 'holding', data_format: 'uint16', unit_id: 1, count: 1, scale_factor: 1.0, poll_interval: 1.0, byte_order: 'big', word_order: 'big' }
    const w = mount(BindingFormModbus, { props: { cfg } })
    const pollInput = w.findAll('input[type="number"]').find(i => i.attributes('step') === '0.1')
    await pollInput.setValue('5.0')
    expect(cfg.poll_interval).toBe(5)
    w.unmount()
  })

  it('mutates cfg.byte_order via select', async () => {
    const cfg = { address: 0, register_type: 'holding', data_format: 'uint16', unit_id: 1, count: 1, scale_factor: 1.0, poll_interval: 1.0, byte_order: 'big', word_order: 'big' }
    const w = mount(BindingFormModbus, { props: { cfg } })
    // byte_order is the 3rd select (register_type, data_format, byte_order, word_order)
    const selects = w.findAll('select')
    const byteOrderSelect = selects[2]
    await byteOrderSelect.setValue('little')
    expect(cfg.byte_order).toBe('little')
    w.unmount()
  })

  it('mutates cfg.word_order via select', async () => {
    const cfg = { address: 0, register_type: 'holding', data_format: 'uint16', unit_id: 1, count: 1, scale_factor: 1.0, poll_interval: 1.0, byte_order: 'big', word_order: 'big' }
    const w = mount(BindingFormModbus, { props: { cfg } })
    const selects = w.findAll('select')
    const wordOrderSelect = selects[3]
    await wordOrderSelect.setValue('little')
    expect(cfg.word_order).toBe('little')
    w.unmount()
  })
})
