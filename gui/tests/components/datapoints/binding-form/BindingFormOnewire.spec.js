import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormOnewire from '@/components/datapoints/binding-form/BindingFormOnewire.vue'

function mk(cfg = {}) {
  return mount(BindingFormOnewire, { props: { cfg: { sensor_id: '', sensor_type: '', ...cfg } } })
}

describe('BindingFormOnewire', () => {
  it('renders two input fields', () => {
    expect(mk().findAll('input').length).toBe(2)
  })

  it('first input is bound to cfg.sensor_id', async () => {
    const cfg = { sensor_id: 'DS18B20', sensor_type: '' }
    const w = mount(BindingFormOnewire, { props: { cfg } })
    expect(w.findAll('input')[0].element.value).toBe('DS18B20')
  })

  it('second input is bound to cfg.sensor_type', async () => {
    const cfg = { sensor_id: '', sensor_type: 'temperature' }
    const w = mount(BindingFormOnewire, { props: { cfg } })
    expect(w.findAll('input')[1].element.value).toBe('temperature')
  })

  it('updating sensor_id input mutates cfg', async () => {
    const cfg = { sensor_id: '', sensor_type: '' }
    const w = mount(BindingFormOnewire, { props: { cfg } })
    await w.findAll('input')[0].setValue('28-ff1234')
    expect(cfg.sensor_id).toBe('28-ff1234')
  })

  it('first input has required attribute', () => {
    const w = mk()
    expect(w.findAll('input')[0].attributes('required')).toBeDefined()
  })
})
