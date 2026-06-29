import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormSnmp from '@/components/datapoints/binding-form/BindingFormSnmp.vue'

const BASE_CFG = { host: '192.168.1.1', port: 161, oid: '', data_type: 'auto', poll_interval: 30, timeout: 5, retries: 1 }

function mk(overrides = {}) {
  return mount(BindingFormSnmp, {
    props: {
      cfg: { ...BASE_CFG },
      form: { direction: 'SOURCE' },
      selectedInstanceId: 1,
      snmpWalkRoot: '',
      snmpWalkResults: [],
      snmpWalkLoading: false,
      snmpWalkError: null,
      snmpWalkHasMore: false,
      ...overrides,
    },
  })
}

describe('BindingFormSnmp — v-model mutations (additional fields)', () => {
  it('mutates cfg.data_type when data type select changes', async () => {
    const cfg = { ...BASE_CFG }
    const w = mount(BindingFormSnmp, {
      props: { cfg, form: { direction: 'SOURCE' }, selectedInstanceId: 1, snmpWalkRoot: '', snmpWalkResults: [], snmpWalkLoading: false, snmpWalkError: null, snmpWalkHasMore: false },
    })
    await w.find('select').setValue('int')
    expect(cfg.data_type).toBe('int')
    w.unmount()
  })

  it('mutates cfg.poll_interval via number input (SOURCE direction)', async () => {
    const cfg = { ...BASE_CFG }
    const w = mount(BindingFormSnmp, {
      props: { cfg, form: { direction: 'SOURCE' }, selectedInstanceId: 1, snmpWalkRoot: '', snmpWalkResults: [], snmpWalkLoading: false, snmpWalkError: null, snmpWalkHasMore: false },
    })
    // poll_interval input: number input with min=1
    const pollInput = w.findAll('input[type="number"]').find(i => i.attributes('min') === '1' && i.attributes('step') === '1')
    await pollInput.setValue('60')
    expect(cfg.poll_interval).toBe(60)
    w.unmount()
  })

  it('mutates cfg.timeout via number input', async () => {
    const cfg = { ...BASE_CFG }
    const w = mount(BindingFormSnmp, {
      props: { cfg, form: { direction: 'SOURCE' }, selectedInstanceId: 1, snmpWalkRoot: '', snmpWalkResults: [], snmpWalkLoading: false, snmpWalkError: null, snmpWalkHasMore: false },
    })
    const timeoutInput = w.findAll('input[type="number"]').find(i => i.attributes('step') === '0.5')
    await timeoutInput.setValue('10')
    expect(cfg.timeout).toBe(10)
    w.unmount()
  })

  it('mutates cfg.retries via number input', async () => {
    const cfg = { ...BASE_CFG }
    const w = mount(BindingFormSnmp, {
      props: { cfg, form: { direction: 'SOURCE' }, selectedInstanceId: 1, snmpWalkRoot: '', snmpWalkResults: [], snmpWalkLoading: false, snmpWalkError: null, snmpWalkHasMore: false },
    })
    const retriesInput = w.findAll('input[type="number"]').find(i => i.attributes('max') === '5')
    await retriesInput.setValue('3')
    expect(cfg.retries).toBe(3)
    w.unmount()
  })
})
