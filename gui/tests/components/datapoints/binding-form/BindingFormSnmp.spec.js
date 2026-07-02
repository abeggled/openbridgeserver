import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormSnmp from '@/components/datapoints/binding-form/BindingFormSnmp.vue'

const BASE_CFG = {
  host: '', port: 161, oid: '', data_type: 'auto',
  poll_interval: 30, timeout: 5, retries: 1,
}

function mk(overrides = {}) {
  return mount(BindingFormSnmp, {
    props: {
      cfg:              { ...BASE_CFG },
      form:             { direction: 'BOTH' },
      selectedInstanceId: 1,
      snmpWalkRoot:     '',
      snmpWalkResults:  [],
      snmpWalkLoading:  false,
      snmpWalkError:    null,
      snmpWalkHasMore:  false,
      ...overrides,
    },
  })
}

describe('BindingFormSnmp — basic inputs', () => {
  it('renders host input with required attr', () => {
    const inputs = mk().findAll('input')
    const host = inputs.find(i => i.attributes('placeholder') && i.attributes('placeholder').includes('192'))
    expect(host?.attributes('required')).toBeDefined()
  })

  it('renders port number input', () => {
    const portInput = mk().findAll('input[type="number"]').find(i => i.element.value === '161')
    expect(portInput).toBeTruthy()
  })

  it('renders OID input with required attr', () => {
    const oidInput = mk().findAll('input').find(i => i.attributes('class')?.includes('font-mono'))
    expect(oidInput?.attributes('required')).toBeDefined()
  })

  it('renders data_type select with auto option', () => {
    const select = mk().find('select')
    expect(select.exists()).toBe(true)
    const options = select.findAll('option').map(o => o.element.value)
    expect(options).toContain('auto')
  })

  it('shows all 8 data type options', () => {
    const options = mk().find('select').findAll('option')
    expect(options.length).toBe(8)
  })
})

describe('BindingFormSnmp — SNMP walk', () => {
  it('walk button emits snmp-walk on click', async () => {
    const w = mk({ cfg: { ...BASE_CFG, host: '192.168.1.1' } })
    const walkBtn = w.findAll('button').find(b => b.text().includes('OID-Walk') || b.text().includes('Walk'))
    await walkBtn.trigger('click')
    expect(w.emitted('snmp-walk')).toBeTruthy()
  })

  it('walk button is disabled when no host', () => {
    const w = mk({ cfg: { ...BASE_CFG, host: '' } })
    const walkBtn = w.findAll('button')[0]
    expect(walkBtn.attributes('disabled')).toBeDefined()
  })

  it('walk button is disabled when no selectedInstanceId', () => {
    const w = mk({ selectedInstanceId: null, cfg: { ...BASE_CFG, host: '192.168.1.1' } })
    const walkBtn = w.findAll('button')[0]
    expect(walkBtn.attributes('disabled')).toBeDefined()
  })

  it('shows walk results when populated', () => {
    const w = mk({ snmpWalkResults: [{ oid: '1.3.6.1.2.1.1.1.0', type: 'OctetString', value: 'Linux' }] })
    expect(w.text()).toContain('1.3.6.1.2.1.1.1.0')
  })

  it('sets cfg.oid when a walk result is clicked', async () => {
    const cfg = { ...BASE_CFG, host: '192.168.1.1' }
    const w = mount(BindingFormSnmp, {
      props: { cfg, form: { direction: 'BOTH' }, selectedInstanceId: 1, snmpWalkRoot: '', snmpWalkResults: [{ oid: '1.3.6.1.2.1.1.1.0', type: 'OctetString', value: 'Linux' }], snmpWalkLoading: false, snmpWalkError: null, snmpWalkHasMore: false },
    })
    await w.findAll('button').find(b => b.text().includes('1.3.6.1.2.1.1.1.0')).trigger('click')
    expect(cfg.oid).toBe('1.3.6.1.2.1.1.1.0')
  })

  it('shows walk error when snmpWalkError is set', () => {
    const w = mk({ snmpWalkError: 'Timeout' })
    expect(w.text()).toContain('Timeout')
  })

  it('emits update:snmpWalkRoot on walk root input', async () => {
    const w = mk()
    const walkRootInput = w.findAll('input').find(i => i.attributes('class')?.includes('text-xs'))
    await walkRootInput.setValue('1.3.6.1')
    expect(w.emitted('update:snmpWalkRoot')).toBeTruthy()
  })
})

describe('BindingFormSnmp — direction-conditional UI', () => {
  it('shows poll_interval input for SOURCE direction', () => {
    const w = mk({ form: { direction: 'SOURCE' } })
    const allInputs = w.findAll('input[type="number"]')
    expect(allInputs.length).toBeGreaterThan(2)
  })

  it('hides poll_interval input for DEST direction', () => {
    const wDest = mk({ form: { direction: 'DEST' } })
    const wBoth = mk({ form: { direction: 'BOTH' } })
    expect(wDest.findAll('input[type="number"]').length).toBeLessThan(wBoth.findAll('input[type="number"]').length)
  })
})
