import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormHomeAssistant from '@/components/datapoints/binding-form/BindingFormHomeAssistant.vue'

function mk(cfg = {}) {
  return mount(BindingFormHomeAssistant, {
    props: {
      cfg: {
        entity_id: '', attribute: '', service_domain: '',
        service_name: '', service_data_key: '',
        ...cfg,
      },
    },
  })
}

describe('BindingFormHomeAssistant', () => {
  it('renders entity_id input with required attribute', () => {
    const input = mk().find('[data-testid="config-field-entity_id"]')
    expect(input.exists()).toBe(true)
    expect(input.attributes('required')).toBeDefined()
  })

  it('entity_id input is bound to cfg.entity_id', () => {
    const cfg = { entity_id: 'sensor.temperature', attribute: '' }
    const w = mount(BindingFormHomeAssistant, { props: { cfg: { ...cfg, service_domain: '', service_name: '', service_data_key: '' } } })
    expect(w.find('[data-testid="config-field-entity_id"]').element.value).toBe('sensor.temperature')
  })

  it('renders attribute input', () => {
    expect(mk().find('[data-testid="config-field-attribute"]').exists()).toBe(true)
  })

  it('renders service_domain and service_name inputs', () => {
    expect(mk().find('[data-testid="config-field-service_domain"]').exists()).toBe(true)
    expect(mk().find('[data-testid="config-field-service_name"]').exists()).toBe(true)
  })

  it('renders service_data_key input', () => {
    expect(mk().find('[data-testid="config-field-service_data_key"]').exists()).toBe(true)
  })

  it('updating entity_id mutates cfg via v-model', async () => {
    const cfg = { entity_id: '', attribute: '', service_domain: '', service_name: '', service_data_key: '' }
    const w = mount(BindingFormHomeAssistant, { props: { cfg } })
    await w.find('[data-testid="config-field-entity_id"]').setValue('light.living_room')
    expect(cfg.entity_id).toBe('light.living_room')
  })
})
