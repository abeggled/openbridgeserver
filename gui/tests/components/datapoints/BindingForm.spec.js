import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountBindingForm(props) {
  const dpApi = {
    createBinding: vi.fn().mockResolvedValue({}),
    updateBinding: vi.fn().mockResolvedValue({}),
  }
  const adapterApi = {
    listInstances: vi.fn().mockResolvedValue({
      data: [
        { id: 'mqtt-inst-1', name: 'MQTT Test', adapter_type: 'MQTT' },
        {
          id: 'message-inst-1',
          name: 'Notifications',
          adapter_type: 'MESSAGE',
          config: { providers: { pushover: { enabled: true, targets: { phone: {} } } } },
        },
      ],
    }),
    knxDpts: vi.fn().mockResolvedValue({ data: [] }),
    mqttBrowseTopics: vi.fn().mockResolvedValue({ data: [] }),
    mqttSamplePayload: vi.fn().mockResolvedValue({ data: { payload: '{}' } }),
  }
  vi.doMock('@/api/client', () => ({ dpApi, adapterApi }))
  const mod = await import('@/components/datapoints/BindingForm.vue')
  const wrapper = mount(mod.default, {
    props: {
      dpId: 'dp-1',
      dpPersistValue: false,
      dpDataType: 'FLOAT',
      ...props,
    },
    attachTo: document.body,
  })
  await flushPromises()
  return { wrapper }
}

describe('BindingForm', () => {
  it('bleibt im Create-Flow stabil beim Wechsel auf Richtung DEST (MQTT)', async () => {
    const { wrapper } = await mountBindingForm({})
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('mqtt-inst-1')
    await flushPromises()
    await wrapper.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    expect(wrapper.find('[data-testid="input-mqtt-topic"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Payload-Template')
  })

  it('zeigt MQTT-Felder im Edit-Flow auch ohne initial.adapter_type', async () => {
    const { wrapper } = await mountBindingForm({
      initial: {
        id: 'binding-1',
        adapter_instance_id: 'mqtt-inst-1',
        direction: 'DEST',
        enabled: true,
        config: { topic: 'sensor/test' },
      },
    })
    expect(wrapper.find('[data-testid="input-mqtt-topic"]').exists()).toBe(true)
    expect(wrapper.find('#mqtt_retain').exists()).toBe(true)
  })

  it('zeigt MESSAGE-Felder ohne Richtungsauswahl und ohne Transform-Tabs', async () => {
    const { wrapper } = await mountBindingForm({})
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('message-inst-1')
    await flushPromises()

    expect(wrapper.text()).toContain('MESSAGE Binding')
    expect(wrapper.find('[data-testid="select-direction"]').exists()).toBe(false)
    expect(wrapper.findAll('.tab-btn').map(button => button.text())).toEqual(['Verbindung'])
  })
})
