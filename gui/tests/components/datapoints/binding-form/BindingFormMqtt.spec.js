import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormMqtt from '@/components/datapoints/binding-form/BindingFormMqtt.vue'

const BASE_CFG = {
  topic: '', publish_topic: '', retain: false, payload_template: '',
  source_data_type: '', json_path: '', xml_path: '',
}
const SOURCE_TYPES = [
  { value: 'auto', label: 'Auto' },
  { value: 'number', label: 'Number' },
]

function mk(overrides = {}) {
  return mount(BindingFormMqtt, {
    props: {
      cfg:               { ...BASE_CFG },
      form:              { direction: 'BOTH', adapter_instance_id: 1 },
      mqttSourceTypes:   SOURCE_TYPES,
      mqttTypeCompat:    null,
      dpDataType:        'float',
      mqttBrowseTopics:  [],
      mqttBrowseLoading: false,
      mqttBrowseError:   null,
      mqttJsonSample:    '',
      mqttJsonKeys:      [],
      mqttJsonParseError: null,
      mqttXmlSample:     '',
      mqttXmlElements:   [],
      mqttXmlParseError: null,
      mqttSampleLoading: false,
      ...overrides,
    },
  })
}

describe('BindingFormMqtt — topic', () => {
  it('renders the topic input', () => {
    expect(mk().find('[data-testid="input-mqtt-topic"]').exists()).toBe(true)
  })

  it('topic input has required attribute', () => {
    expect(mk().find('[data-testid="input-mqtt-topic"]').attributes('required')).toBeDefined()
  })

  it('browse button emits mqtt-browse on click', async () => {
    const w = mk({ form: { direction: 'BOTH', adapter_instance_id: 1 } })
    await w.findAll('button')[0].trigger('click')
    expect(w.emitted('mqtt-browse')).toBeTruthy()
  })

  it('browse button is disabled when no adapter_instance_id', () => {
    const w = mk({ form: { direction: 'BOTH', adapter_instance_id: null } })
    expect(w.findAll('button')[0].attributes('disabled')).toBeDefined()
  })

  it('renders browse topic results when mqttBrowseTopics is populated', () => {
    const w = mk({ mqttBrowseTopics: ['home/sensor/temp', 'home/sensor/hum'] })
    expect(w.text()).toContain('home/sensor/temp')
  })

  it('emits select-mqtt-topic when a browse result is clicked', async () => {
    const w = mk({ mqttBrowseTopics: ['home/sensor/temp'] })
    const btn = w.findAll('button').find(b => b.text().includes('home/sensor/temp'))
    await btn.trigger('click')
    expect(w.emitted('select-mqtt-topic')).toEqual([['home/sensor/temp']])
  })

  it('shows browse error when mqttBrowseError is set', () => {
    const w = mk({ mqttBrowseError: 'Connection refused' })
    expect(w.text()).toContain('Connection refused')
  })
})

describe('BindingFormMqtt — direction-conditional UI', () => {
  it('shows publish_topic input for BOTH direction', () => {
    const w = mk({ form: { direction: 'BOTH', adapter_instance_id: 1 } })
    expect(w.find('input[placeholder*="publish"]').exists() || w.text().includes('Publish')).toBe(true)
  })

  it('hides publish_topic input for SOURCE direction', () => {
    const w = mk({ form: { direction: 'SOURCE', adapter_instance_id: 1 } })
    // In SOURCE mode the publish topic field (which uses v-if="form.direction === 'BOTH'") is hidden
    expect(w.findAll('input').length).toBeLessThan(mk().findAll('input').length)
  })

  it('shows retain checkbox for DEST direction', () => {
    const w = mk({ form: { direction: 'DEST', adapter_instance_id: 1 } })
    expect(w.find('#mqtt_retain').exists()).toBe(true)
  })

  it('hides retain checkbox for SOURCE direction', () => {
    const w = mk({ form: { direction: 'SOURCE', adapter_instance_id: 1 } })
    expect(w.find('#mqtt_retain').exists()).toBe(false)
  })

  it('shows source_data_type select for SOURCE direction', () => {
    const w = mk({ form: { direction: 'SOURCE', adapter_instance_id: 1 } })
    expect(w.find('[data-testid="select-source-data-type"]').exists()).toBe(true)
  })

  it('hides source_data_type select for DEST direction', () => {
    const w = mk({ form: { direction: 'DEST', adapter_instance_id: 1 } })
    expect(w.find('[data-testid="select-source-data-type"]').exists()).toBe(false)
  })
})
