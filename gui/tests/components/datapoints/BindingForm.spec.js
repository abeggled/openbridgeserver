import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountBindingForm(props, apiOverrides = {}) {
  const dpApi = {
    createBinding: vi.fn().mockResolvedValue({}),
    updateBinding: vi.fn().mockResolvedValue({}),
  }
  const adapterApi = {
    listInstances: vi.fn().mockResolvedValue({
      data: [
        { id: 'mqtt-inst-1', name: 'MQTT Test', adapter_type: 'MQTT' },
        { id: 'ow-1', name: 'Onewire Test', adapter_type: 'ONEWIRE' },
        { id: 'ow-2', name: 'Onewire Test 2', adapter_type: 'ONEWIRE' },
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
    onewireBrowseSensors: vi.fn().mockResolvedValue({ data: [] }),
    onewireSetAlias: vi.fn().mockResolvedValue({ data: {} }),
    ...apiOverrides,
  }
  const messageArchivesApi = {
    list: vi.fn().mockResolvedValue({ data: { archives: [] } }),
  }
  vi.doMock('@/api/client', () => ({ dpApi, adapterApi, messageArchivesApi }))
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
  return { wrapper, adapterApi }
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

describe('BindingForm — 1-Wire sensor scan/select/alias flow', () => {
  it('scans sensors and fills sensor_id/property when a property is selected', async () => {
    const onewireBrowseSensors = vi.fn().mockResolvedValue({
      data: [{ rom_id: '28.4B057F0A1C10', family: '28', properties: ['temperature', 'humidity'], alias: null }],
    })
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors })
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()

    const scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    expect(onewireBrowseSensors).toHaveBeenCalledWith('ow-1')
    expect(wrapper.text()).toContain('28.4B057F0A1C10')

    const propertyBtn = wrapper.findAll('button').find(b => b.text() === 'humidity')
    await propertyBtn.trigger('click')

    const sensorIdInput = wrapper.findAll('input').find(i => i.element.value === '28.4B057F0A1C10')
    expect(sensorIdInput).toBeTruthy()
    const propertyInput = wrapper.findAll('input').find(i => i.element.value === 'humidity')
    expect(propertyInput).toBeTruthy()
  })

  it('saves an alias for a scanned sensor', async () => {
    const onewireBrowseSensors = vi.fn().mockResolvedValue({
      data: [{ rom_id: '28.4B057F0A1C10', family: '28', properties: ['temperature'], alias: null }],
    })
    const onewireSetAlias = vi.fn().mockResolvedValue({ data: { rom_id: '28.4B057F0A1C10', label: 'Gästebad' } })
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors, onewireSetAlias })
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()

    const scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    const aliasInput = wrapper.findAll('input').find(i => i.attributes('placeholder')?.includes('Label'))
    await aliasInput.setValue('Gästebad')
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('Speichern'))
    await saveBtn.trigger('click')
    await flushPromises()

    expect(onewireSetAlias).toHaveBeenCalledWith('ow-1', '28.4B057F0A1C10', 'Gästebad')
  })

  it('clears stale scan results when switching to a different 1-Wire instance', async () => {
    const onewireBrowseSensors = vi.fn().mockResolvedValue({
      data: [{ rom_id: '28.4B057F0A1C10', family: '28', properties: ['temperature'], alias: null }],
    })
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors })
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()

    const scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('28.4B057F0A1C10')

    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-2')
    await flushPromises()

    expect(wrapper.text()).not.toContain('28.4B057F0A1C10')
  })

  it('discards a late scan response for an instance that is no longer selected', async () => {
    let resolveFirstScan
    const firstScan = new Promise(resolve => { resolveFirstScan = resolve })
    const onewireBrowseSensors = vi.fn()
      .mockImplementationOnce(() => firstScan)
      .mockResolvedValueOnce({
        data: [{ rom_id: '29.SECOND', family: '29', properties: ['temperature'], alias: null }],
      })
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors })
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()

    const scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click') // scan for ow-1 — stays pending

    // Switch to ow-2 before the ow-1 scan resolves, and scan it too.
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-2')
    await flushPromises()
    const scanBtn2 = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn2.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('29.SECOND')

    // The stale ow-1 scan now resolves — must not overwrite ow-2's results.
    resolveFirstScan({
      data: [{ rom_id: '28.STALE', family: '28', properties: ['temperature'], alias: null }],
    })
    await flushPromises()

    expect(wrapper.text()).toContain('29.SECOND')
    expect(wrapper.text()).not.toContain('28.STALE')
  })
})
