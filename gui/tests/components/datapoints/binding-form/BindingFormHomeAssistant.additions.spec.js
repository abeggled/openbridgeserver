import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BindingFormHomeAssistant from '@/components/datapoints/binding-form/BindingFormHomeAssistant.vue'

const BASE_CFG = { entity_id: '', attribute: '', service_domain: '', service_name: '', service_data_key: '' }

function mk(cfg = {}) {
  return mount(BindingFormHomeAssistant, { props: { cfg: { ...BASE_CFG, ...cfg } } })
}

describe('BindingFormHomeAssistant — v-model mutations (optional fields)', () => {
  it('mutates cfg.attribute via input', async () => {
    const cfg = { ...BASE_CFG }
    const w = mount(BindingFormHomeAssistant, { props: { cfg } })
    await w.find('[data-testid="config-field-attribute"]').setValue('brightness')
    expect(cfg.attribute).toBe('brightness')
    w.unmount()
  })

  it('mutates cfg.service_domain via input', async () => {
    const cfg = { ...BASE_CFG }
    const w = mount(BindingFormHomeAssistant, { props: { cfg } })
    await w.find('[data-testid="config-field-service_domain"]').setValue('light')
    expect(cfg.service_domain).toBe('light')
    w.unmount()
  })

  it('mutates cfg.service_name via input', async () => {
    const cfg = { ...BASE_CFG }
    const w = mount(BindingFormHomeAssistant, { props: { cfg } })
    await w.find('[data-testid="config-field-service_name"]').setValue('turn_on')
    expect(cfg.service_name).toBe('turn_on')
    w.unmount()
  })

  it('mutates cfg.service_data_key via input', async () => {
    const cfg = { ...BASE_CFG }
    const w = mount(BindingFormHomeAssistant, { props: { cfg } })
    await w.find('[data-testid="config-field-service_data_key"]').setValue('brightness_pct')
    expect(cfg.service_data_key).toBe('brightness_pct')
    w.unmount()
  })
})
