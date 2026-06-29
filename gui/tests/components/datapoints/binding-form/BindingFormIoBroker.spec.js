import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormIoBroker from '@/components/datapoints/binding-form/BindingFormIoBroker.vue'

const BASE_CFG = {
  state_id: '', command_state_id: '', ack: false,
  source_data_type: '', json_key: '',
}

function mk(overrides = {}) {
  return mount(BindingFormIoBroker, {
    props: {
      cfg:                  { ...BASE_CFG },
      form:                 { direction: 'BOTH' },
      selectedInstanceId:   1,
      iobrokerStates:       [],
      iobrokerBrowseLoading: false,
      iobrokerBrowseError:  null,
      showAdvancedTabs:     false,
      ...overrides,
    },
  })
}

describe('BindingFormIoBroker — state_id field', () => {
  it('renders state_id input with required attr', () => {
    const input = mk().find('[data-testid="config-field-state_id"]')
    expect(input.exists()).toBe(true)
    expect(input.attributes('required')).toBeDefined()
  })

  it('input is bound to cfg.state_id', () => {
    const cfg = { ...BASE_CFG, state_id: 'system.adapter.info.0' }
    const w = mount(BindingFormIoBroker, {
      props: { cfg, form: { direction: 'BOTH' }, selectedInstanceId: 1, iobrokerStates: [], iobrokerBrowseLoading: false, iobrokerBrowseError: null, showAdvancedTabs: false },
    })
    expect(w.find('[data-testid="config-field-state_id"]').element.value).toBe('system.adapter.info.0')
  })

  it('emits iobroker-state-input on input event', async () => {
    const w = mk()
    await w.find('[data-testid="config-field-state_id"]').trigger('input')
    expect(w.emitted('iobroker-state-input')).toBeTruthy()
  })
})

describe('BindingFormIoBroker — browse button', () => {
  it('browse button is disabled when no selectedInstanceId', () => {
    const w = mk({ selectedInstanceId: null })
    const btn = w.findAll('button').find(b => b.text().includes('Durchsuchen'))
    expect(btn?.attributes('disabled')).toBeDefined()
  })

  it('browse button emits browse-iobroker-states on click', async () => {
    const w = mk()
    const btn = w.findAll('button').find(b => b.text().includes('Durchsuchen'))
    await btn.trigger('click')
    expect(w.emitted('browse-iobroker-states')).toBeTruthy()
  })

  it('shows browse results when iobrokerStates populated', () => {
    const w = mk({ iobrokerStates: [{ id: 'lights.0.power', type: 'boolean', write: true, name: 'Power', role: 'switch', value: true }] })
    expect(w.text()).toContain('lights.0.power')
  })

  it('emits select-iobroker-state when a result is clicked', async () => {
    const state = { id: 'temp.0.value', type: 'number', write: false, name: 'Temp', role: 'sensor', value: 22 }
    const w = mk({ iobrokerStates: [state] })
    const btn = w.findAll('button').find(b => b.text().includes('temp.0.value'))
    await btn.trigger('click')
    expect(w.emitted('select-iobroker-state')[0][0].id).toBe('temp.0.value')
  })

  it('shows browse error when iobrokerBrowseError is set', () => {
    const w = mk({ iobrokerBrowseError: 'Verbindung fehlgeschlagen' })
    expect(w.text()).toContain('Verbindung fehlgeschlagen')
  })
})

describe('BindingFormIoBroker — direction-conditional UI', () => {
  it('shows source_data_type select for BOTH direction', () => {
    expect(mk({ form: { direction: 'BOTH' } }).find('select').exists()).toBe(true)
  })

  it('shows source_data_type select for SOURCE direction', () => {
    const w = mk({ form: { direction: 'SOURCE' } })
    expect(w.find('select').exists()).toBe(true)
  })

  it('shows ack checkbox for DEST direction', () => {
    const w = mk({ form: { direction: 'DEST' } })
    expect(w.find('input[type="checkbox"]').exists()).toBe(true)
  })

  it('shows json_key input when source_data_type is json', () => {
    const cfg = { ...BASE_CFG, source_data_type: 'json' }
    const w = mount(BindingFormIoBroker, {
      props: { cfg, form: { direction: 'BOTH' }, selectedInstanceId: 1, iobrokerStates: [], iobrokerBrowseLoading: false, iobrokerBrowseError: null, showAdvancedTabs: false },
    })
    const inputs = w.findAll('input[type="text"], input:not([type])').filter(i => !i.attributes('data-testid'))
    expect(inputs.length).toBeGreaterThan(0)
  })
})

describe('BindingFormIoBroker — advanced toggle', () => {
  it('emits toggle-advanced-tabs on button click', async () => {
    const w = mk()
    const btn = w.findAll('button').find(b => b.text().includes('Optionen') || b.text().includes('Erweiterte'))
    await btn.trigger('click')
    expect(w.emitted('toggle-advanced-tabs')).toBeTruthy()
  })
})
