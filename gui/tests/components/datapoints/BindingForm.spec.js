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
  return { wrapper, adapterApi, dpApi }
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

  it('ignores a stale scan error after switching to a different instance', async () => {
    let rejectFirstScan
    const firstScan = new Promise((_resolve, reject) => { rejectFirstScan = reject })
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

    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-2')
    await flushPromises()
    const scanBtn2 = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn2.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('29.SECOND')

    // The stale ow-1 scan now rejects — must not show its error over ow-2's results.
    rejectFirstScan({ response: { data: { detail: 'stale owserver error' } } })
    await flushPromises()

    expect(wrapper.text()).toContain('29.SECOND')
    expect(wrapper.text()).not.toContain('stale owserver error')
  })

  it('ignores a stale alias-save response after switching to a different instance', async () => {
    let resolveAliasSave
    const aliasSave = new Promise(resolve => { resolveAliasSave = resolve })
    const onewireBrowseSensors = vi.fn()
      .mockResolvedValueOnce({
        data: [{ rom_id: '28.SHARED', family: '28', properties: ['temperature'], alias: null }],
      })
      .mockResolvedValueOnce({
        data: [{ rom_id: '28.SHARED', family: '28', properties: ['temperature'], alias: null }],
      })
    const onewireSetAlias = vi.fn().mockImplementationOnce(() => aliasSave)
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors, onewireSetAlias })

    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()
    let scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    const aliasInput = wrapper.findAll('input').find(i => i.attributes('placeholder')?.includes('Label'))
    await aliasInput.setValue('ow-1 Label')
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('Speichern'))
    await saveBtn.trigger('click') // PATCH for ow-1 — stays pending

    // Switch to ow-2 before the ow-1 alias save resolves, and scan its (unrelated) sensor sharing the same rom_id.
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-2')
    await flushPromises()
    scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    // The stale ow-1 alias save now resolves — must not label ow-2's sensor.
    resolveAliasSave({ data: { rom_id: '28.SHARED', label: 'ow-1 Label' } })
    await flushPromises()

    const aliasField = wrapper.findAll('input').find(i => i.attributes('placeholder')?.includes('Label'))
    expect(aliasField.element.value).toBe('')
  })

  it('ignores a stale alias-save error after switching to a different instance', async () => {
    let rejectAliasSave
    const aliasSave = new Promise((_resolve, reject) => { rejectAliasSave = reject })
    const onewireBrowseSensors = vi.fn()
      .mockResolvedValueOnce({
        data: [{ rom_id: '28.SHARED', family: '28', properties: ['temperature'], alias: null }],
      })
      .mockResolvedValueOnce({
        data: [{ rom_id: '28.SHARED', family: '28', properties: ['temperature'], alias: null }],
      })
    const onewireSetAlias = vi.fn().mockImplementationOnce(() => aliasSave)
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors, onewireSetAlias })

    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()
    let scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    const aliasInput = wrapper.findAll('input').find(i => i.attributes('placeholder')?.includes('Label'))
    await aliasInput.setValue('ow-1 Label')
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('Speichern'))
    await saveBtn.trigger('click') // PATCH for ow-1 — stays pending

    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-2')
    await flushPromises()
    scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    // The stale ow-1 alias save now rejects — must not show its error on ow-2's form.
    rejectAliasSave({ response: { data: { detail: 'stale alias save error' } } })
    await flushPromises()

    expect(wrapper.text()).not.toContain('stale alias save error')
  })

  it('shows an error and skips the scan when no 1-Wire instance is selected', async () => {
    // Reachable when editing a binding whose stored adapter instance was deleted:
    // initial.adapter_type is kept (so the ONEWIRE subform still renders) but
    // initial.adapter_instance_id is gone, so selectedInstanceId is falsy even
    // though the real Scan button (disabled without an instance) can't be clicked.
    const onewireBrowseSensors = vi.fn()
    const { wrapper } = await mountBindingForm(
      { initial: { adapter_type: 'ONEWIRE', adapter_instance_id: null } },
      { onewireBrowseSensors },
    )

    const onewireForm = wrapper.findComponent({ name: 'BindingFormOnewire' })
    expect(onewireForm.exists()).toBe(true)
    onewireForm.vm.$emit('onewire-browse')
    await flushPromises()

    expect(onewireBrowseSensors).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Bitte zuerst eine 1-Wire-Instanz wählen')
  })

  it('skips the alias save when no 1-Wire instance is selected', async () => {
    const onewireSetAlias = vi.fn()
    const { wrapper } = await mountBindingForm(
      { initial: { adapter_type: 'ONEWIRE', adapter_instance_id: null } },
      { onewireSetAlias },
    )

    const onewireForm = wrapper.findComponent({ name: 'BindingFormOnewire' })
    onewireForm.vm.$emit('update-onewire-alias-draft', { romId: '28.4B057F0A1C10', label: 'Gästebad' })
    onewireForm.vm.$emit('save-onewire-alias', '28.4B057F0A1C10')
    await flushPromises()

    expect(onewireSetAlias).not.toHaveBeenCalled()
  })

  it('skips the alias save when no label was typed yet', async () => {
    const onewireBrowseSensors = vi.fn().mockResolvedValue({
      data: [{ rom_id: '28.4B057F0A1C10', family: '28', properties: ['temperature'], alias: null }],
    })
    const onewireSetAlias = vi.fn()
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors, onewireSetAlias })
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()

    const scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    // Click Save directly, without typing into the alias input first.
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('Speichern'))
    await saveBtn.trigger('click')
    await flushPromises()

    expect(onewireSetAlias).not.toHaveBeenCalled()
  })

  it('shows an error when a 1-Wire scan finds no sensors', async () => {
    const onewireBrowseSensors = vi.fn().mockResolvedValue({ data: [] })
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors })
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()

    const scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('Keine Sensoren gefunden')
  })

  it('shows an error when a 1-Wire scan request fails', async () => {
    const onewireBrowseSensors = vi.fn().mockRejectedValue({ response: { data: { detail: 'owserver unreachable' } } })
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors })
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()

    const scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('owserver unreachable')
  })

  it('falls back to a generic message when a 1-Wire scan fails without a response detail', async () => {
    const onewireBrowseSensors = vi.fn().mockRejectedValue(new Error('network down'))
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors })
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()

    const scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    expect(wrapper.text()).toContain('1-Wire-Sensor-Scan fehlgeschlagen')
  })

  it('applies a saved 1-Wire alias only if its sensor is still in the current scan list', async () => {
    // A fresh scan (still the same instance) can replace onewireSensors while an
    // alias PATCH for a sensor from the previous list is still in flight — the
    // stale sensor is simply gone from the new list, so there's nothing to label.
    let resolveAliasSave
    const aliasSave = new Promise(resolve => { resolveAliasSave = resolve })
    const onewireBrowseSensors = vi.fn()
      .mockResolvedValueOnce({
        data: [{ rom_id: '28.4B057F0A1C10', family: '28', properties: ['temperature'], alias: null }],
      })
      .mockResolvedValueOnce({
        data: [{ rom_id: '29.OTHER', family: '29', properties: ['temperature'], alias: null }],
      })
    const onewireSetAlias = vi.fn().mockImplementationOnce(() => aliasSave)
    const { wrapper } = await mountBindingForm({}, { onewireBrowseSensors, onewireSetAlias })
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()

    let scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()

    const aliasInput = wrapper.findAll('input').find(i => i.attributes('placeholder')?.includes('Label'))
    await aliasInput.setValue('Gästebad')
    const saveBtn = wrapper.findAll('button').find(b => b.text().includes('Speichern'))
    await saveBtn.trigger('click') // PATCH stays pending

    // Re-scan the same instance before the alias save resolves — the sensor list changes.
    scanBtn = wrapper.findAll('button').find(b => b.text().includes('Scannen'))
    await scanBtn.trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('29.OTHER')

    resolveAliasSave({ data: { rom_id: '28.4B057F0A1C10', label: 'Gästebad' } })
    await flushPromises()

    expect(wrapper.text()).not.toContain('28.4B057F0A1C10')
  })

  it('shows an error when saving a 1-Wire alias fails', async () => {
    const onewireBrowseSensors = vi.fn().mockResolvedValue({
      data: [{ rom_id: '28.4B057F0A1C10', family: '28', properties: ['temperature'], alias: null }],
    })
    const onewireSetAlias = vi.fn().mockRejectedValue({ response: { data: { detail: 'alias save failed' } } })
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

    expect(wrapper.text()).toContain('alias save failed')
  })

  it('falls back to a generic message when saving a 1-Wire alias fails without a response detail', async () => {
    const onewireBrowseSensors = vi.fn().mockResolvedValue({
      data: [{ rom_id: '28.4B057F0A1C10', family: '28', properties: ['temperature'], alias: null }],
    })
    const onewireSetAlias = vi.fn().mockRejectedValue(new Error('network down'))
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

    expect(wrapper.text()).toContain('Alias konnte nicht gespeichert werden')
  })

  it('defaults property to temperature when the field is left empty', async () => {
    const { wrapper, dpApi } = await mountBindingForm({})
    await wrapper.find('[data-testid="select-adapter-instance"]').setValue('ow-1')
    await flushPromises()

    const sensorIdInput = wrapper.findAll('input').find(i => i.attributes('placeholder')?.includes('28.4B057F0A1C10'))
    await sensorIdInput.setValue('28.4B057F0A1C10')

    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(dpApi.createBinding).toHaveBeenCalledWith(
      'dp-1',
      expect.objectContaining({ config: expect.objectContaining({ property: 'temperature' }) }),
    )
  })
})
