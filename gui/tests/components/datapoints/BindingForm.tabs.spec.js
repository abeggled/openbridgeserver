import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const MQTT_INSTANCE  = { id: 'mqtt-1',  name: 'MQTT',    adapter_type: 'MQTT' }
const IOB_INSTANCE   = { id: 'iob-1',   name: 'ioBroker', adapter_type: 'IOBROKER' }
const MODBUS_INSTANCE = { id: 'mod-1',  name: 'Modbus',  adapter_type: 'MODBUS_TCP' }

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi: {
      createBinding: vi.fn().mockResolvedValue({}),
      updateBinding: vi.fn().mockResolvedValue({}),
    },
    adapterApi: {
      listInstances:      vi.fn().mockResolvedValue({ data: [MQTT_INSTANCE, IOB_INSTANCE, MODBUS_INSTANCE] }),
      knxDpts:            vi.fn().mockResolvedValue({ data: [] }),
      knxGroupAddresses:  vi.fn().mockResolvedValue({ data: [] }),
      mqttBrowseTopics:   vi.fn().mockResolvedValue({ data: [] }),
      mqttSamplePayload:  vi.fn().mockResolvedValue({ data: { payload: '{}' } }),
      iobrokerBrowseStates: vi.fn().mockResolvedValue({ data: [] }),
      snmpWalk:           vi.fn().mockResolvedValue({ data: [] }),
      getZsuHolidays:     vi.fn().mockResolvedValue({ data: [] }),
    },
    knxprojApi: { listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }) },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

async function mountForm(props = {}) {
  const mod = await import('@/components/datapoints/BindingForm.vue')
  const wrapper = mount(mod.default, {
    props: { dpId: 'dp-1', dpPersistValue: false, dpDataType: 'FLOAT', ...props },
    attachTo: document.body,
  })
  await flushPromises()
  return wrapper
}

async function selectInstance(wrapper, instanceId) {
  await wrapper.find('[data-testid="select-adapter-instance"]').setValue(instanceId)
  await flushPromises()
}

function findTabBtn(wrapper, label) {
  return wrapper.findAll('button[type="button"]').find(b => b.text().includes(label))
}

async function clickTab(wrapper, label) {
  const btn = findTabBtn(wrapper, label)
  if (!btn) throw new Error(`Tab "${label}" not found in: ${wrapper.findAll('button').map(b => b.text()).join(', ')}`)
  await btn.trigger('click')
  await flushPromises()
}

// ─── visibleTabs — only conn shown for ZEITSCHALTUHR ─────────────────────────

describe('BindingForm visibleTabs — ZEITSCHALTUHR only shows conn tab', () => {
  it('shows only the Verbindung tab when adapter is ZEITSCHALTUHR', async () => {
    const w = await mountForm({
      initial: {
        id: 'b1', adapter_type: 'ZEITSCHALTUHR', adapter_instance_id: 'zt-1',
        direction: 'SOURCE', enabled: true,
        config: { timer_type: 'daily', weekdays: [0,1,2,3,4,5,6] },
      },
    })
    // Only "Verbindung" tab button should exist; no "Transformation" or "Filter"
    expect(findTabBtn(w, 'Transformation')).toBeFalsy()
    expect(findTabBtn(w, 'Filter')).toBeFalsy()
    w.unmount()
  })
})

// ─── visibleTabs — IOBROKER hides advanced tabs until toggled ─────────────────

describe('BindingForm visibleTabs — IOBROKER', () => {
  it('initially shows only conn tab for IOBROKER', async () => {
    const w = await mountForm()
    await selectInstance(w, 'iob-1')
    // No transform/filter buttons yet (showAdvancedTabs = false by default)
    expect(findTabBtn(w, 'Transformation')).toBeFalsy()
    w.unmount()
  })
})

// ─── IOBROKER watch — resets tab and advanced flag ────────────────────────────

describe('BindingForm — IOBROKER watch resets activeTab', () => {
  it('switching adapter type to IOBROKER resets activeTab to conn', async () => {
    const w = await mountForm()
    // Start with MQTT in DEST direction (so transform + filter tabs visible)
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()

    // Click transform tab
    await clickTab(w, 'Transformation')

    // Verify transform section is active (shown content)
    const transformSection = w.findAll('div').find(d =>
      d.text().includes('Formel') && d.attributes('class')?.includes('flex-col')
    )
    expect(transformSection).toBeTruthy()

    // Now switch to IOBROKER — watch on selectedAdapterType fires
    await selectInstance(w, 'iob-1')

    // The IOBROKER watch fires: activeTab → 'conn', showAdvancedTabs → false
    // So transform/filter tabs are hidden again and only conn is shown
    expect(findTabBtn(w, 'Transformation')).toBeFalsy()
    w.unmount()
  })
})

// ─── Transform tab — formula preset (onPresetSelect) ─────────────────────────

describe('BindingForm — transform tab formula preset', () => {
  it('shows transform tab for MQTT in DEST direction', async () => {
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()

    expect(findTabBtn(w, 'Transformation')).toBeTruthy()
    w.unmount()
  })

  it('selecting a formula preset fills value_formula and submits it', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    await clickTab(w, 'Transformation')

    const presetSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'x * 1000')
    )
    expect(presetSelect).toBeTruthy()
    await presetSelect.setValue('x * 1000')
    await presetSelect.trigger('change')

    await w.find('form').trigger('submit')
    await flushPromises()
    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      value_formula: 'x * 1000',
    }))
    w.unmount()
  })

  it('selecting __custom__ from preset does NOT set value_formula (manual input)', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    await clickTab(w, 'Transformation')

    const presetSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'x * 1000')
    )
    await presetSelect.setValue('__custom__')
    await presetSelect.trigger('change')

    // value_formula stays empty because __custom__ branch does nothing but expose the input
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      value_formula: null,
    }))
    w.unmount()
  })

  it('clearing preset selection (empty string) clears value_formula', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    await clickTab(w, 'Transformation')

    const presetSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'x * 1000')
    )
    // First select something, then clear it
    await presetSelect.setValue('x * 1000')
    await presetSelect.trigger('change')
    await presetSelect.setValue('')
    await presetSelect.trigger('change')

    await w.find('form').trigger('submit')
    await flushPromises()
    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      value_formula: null,
    }))
    w.unmount()
  })
})

// ─── Transform tab — value_map preset ────────────────────────────────────────

describe('BindingForm — transform tab value_map', () => {
  it('selecting a named value_map preset submits its map in the payload', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    await clickTab(w, 'Transformation')

    const vmSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'num_invert')
    )
    expect(vmSelect).toBeTruthy()
    await vmSelect.setValue('num_invert')
    await vmSelect.trigger('change')

    await w.find('form').trigger('submit')
    await flushPromises()
    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      value_map: { '0': '1', '1': '0' },
    }))
    w.unmount()
  })

  it('selecting custom shows the textarea for JSON entry', async () => {
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    await clickTab(w, 'Transformation')

    const vmSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'custom')
    )
    await vmSelect.setValue('custom')
    await vmSelect.trigger('change')
    await flushPromises()

    expect(w.find('textarea').exists()).toBe(true)
    w.unmount()
  })

  it('invalid custom JSON shows error in the transform tab', async () => {
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    await clickTab(w, 'Transformation')

    const vmSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'custom')
    )
    await vmSelect.setValue('custom')
    await vmSelect.trigger('change')
    await flushPromises()

    const ta = w.find('textarea')
    await ta.setValue('{ not json }')
    await ta.trigger('input')
    await flushPromises()

    // onValueMapCustomInput shows error
    expect(w.text()).toMatch(/JSON|ungültig/i)
    w.unmount()
  })

  it('valid custom JSON clears the error', async () => {
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    await clickTab(w, 'Transformation')

    const vmSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'custom')
    )
    await vmSelect.setValue('custom')
    await vmSelect.trigger('change')
    await flushPromises()

    const ta = w.find('textarea')
    await ta.setValue('{ not json }')
    await ta.trigger('input')
    await flushPromises()

    await ta.setValue('{"a": "b"}')
    await ta.trigger('input')
    await flushPromises()

    // Error should be gone
    expect(w.find('p.text-xs.text-red-400').exists()).toBe(false)
    w.unmount()
  })

  it('switching away from custom preset clears custom textarea', async () => {
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    await clickTab(w, 'Transformation')

    const vmSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'custom')
    )
    await vmSelect.setValue('custom')
    await vmSelect.trigger('change')
    await flushPromises()

    const ta = w.find('textarea')
    await ta.setValue('{"a": "b"}')
    await ta.trigger('input')
    await flushPromises()

    // Switch to a named preset
    await vmSelect.setValue('num_invert')
    await vmSelect.trigger('change')
    await flushPromises()

    // Custom textarea should no longer exist (v-if on 'custom')
    expect(w.find('textarea').exists()).toBe(false)
    w.unmount()
  })
})

// ─── Filter tab — throttle unit conversions ───────────────────────────────────

describe('BindingForm — filter tab throttle units', () => {
  async function mountWithFilter() {
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    await clickTab(w, 'Filter')
    return w
  }

  it('ms throttle converts 1:1', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountWithFilter()
    const [throttleInput] = w.findAll('input[type="number"]')
    await throttleInput.setValue('200')
    const unitSelect = w.find('select.input.w-24')
    await unitSelect.setValue('ms')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      send_throttle_ms: 200,
    }))
    w.unmount()
  })

  it('seconds throttle multiplies by 1000', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountWithFilter()
    const [throttleInput] = w.findAll('input[type="number"]')
    await throttleInput.setValue('5')
    const unitSelect = w.find('select.input.w-24')
    await unitSelect.setValue('s')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      send_throttle_ms: 5000,
    }))
    w.unmount()
  })

  it('hours throttle multiplies by 3_600_000', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountWithFilter()
    const [throttleInput] = w.findAll('input[type="number"]')
    await throttleInput.setValue('2')
    const unitSelect = w.find('select.input.w-24')
    await unitSelect.setValue('h')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      send_throttle_ms: 2 * 3_600_000,
    }))
    w.unmount()
  })

  it('zero throttle value sends null (not 0)', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountWithFilter()
    const [throttleInput] = w.findAll('input[type="number"]')
    await throttleInput.setValue('0')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      send_throttle_ms: null,
    }))
    w.unmount()
  })

  it('send_on_change checkbox is submitted when checked', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountWithFilter()
    const checkbox = w.find('input[type="checkbox"]#send_on_change')
    await checkbox.setChecked(true)
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      send_on_change: true,
    }))
    w.unmount()
  })

  it('min_delta is submitted when filled', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountWithFilter()
    const numberInputs = w.findAll('input[type="number"]')
    // throttle_value is [0], min_delta is [1]
    await numberInputs[1].setValue('0.5')
    await w.find('form').trigger('submit')
    await flushPromises()
    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      send_min_delta: 0.5,
    }))
    w.unmount()
  })
})

// ─── Transform tab — custom value_map submit ─────────────────────────────────

describe('BindingForm — custom value_map JSON is parsed on submit', () => {
  it('submits valid custom value_map JSON as the resolved map', async () => {
    const { dpApi } = (await import('@/api/client'))
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    await clickTab(w, 'Transformation')

    const vmSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'custom')
    )
    await vmSelect.setValue('custom')
    await vmSelect.trigger('change')
    await flushPromises()

    await w.find('textarea').setValue('{"yes":"true","no":"false"}')
    await w.find('form').trigger('submit')
    await flushPromises()

    expect(dpApi.createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      value_map: { yes: 'true', no: 'false' },
    }))
    w.unmount()
  })
})

// ─── MODBUS — transform and filter tabs visible ───────────────────────────────

describe('BindingForm — MODBUS shows transform tab', () => {
  it('shows Transformation tab for Modbus with DEST direction', async () => {
    const w = await mountForm()
    await selectInstance(w, 'mod-1')
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()
    expect(findTabBtn(w, 'Transformation')).toBeTruthy()
    w.unmount()
  })
})

// ─── Initial binding with formula/value_map restores transform state ──────────

describe('BindingForm — initial binding restores transform tab state', () => {
  it('restores formula_preset to __custom__ when initial has a non-preset formula', async () => {
    const w = await mountForm({
      initial: {
        id: 'b1', adapter_type: 'MQTT', adapter_instance_id: 'mqtt-1',
        direction: 'DEST', enabled: true,
        config: { topic: 't', retain: false },
        value_formula: 'x + 1',
        value_map: null,
      },
    })
    await clickTab(w, 'Transformation')
    // Custom formula → preset should be __custom__
    const presetSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'x * 1000')
    )
    expect(presetSelect?.element.value).toBe('__custom__')
    w.unmount()
  })

  it('sets formula_preset to __custom__ for any non-empty formula on load', async () => {
    // The component always sets formula_preset = f ? '__custom__' : '' when loading initial
    const w = await mountForm({
      initial: {
        id: 'b2', adapter_type: 'MQTT', adapter_instance_id: 'mqtt-1',
        direction: 'DEST', enabled: true,
        config: { topic: 't', retain: false },
        value_formula: 'x * 1000',
        value_map: null,
      },
    })
    await clickTab(w, 'Transformation')
    const presetSelect = w.findAll('select').find(s =>
      s.findAll('option').some(o => o.element.value === 'x * 1000')
    )
    // formula_preset is always '__custom__' when loaded from initial (line 666 of BindingForm.vue)
    expect(presetSelect?.element.value).toBe('__custom__')
    // The formula input should still show the actual formula
    const formulaInput = w.find('input.input.flex-1.font-mono')
    expect(formulaInput.element.value).toBe('x * 1000')
    w.unmount()
  })
})
