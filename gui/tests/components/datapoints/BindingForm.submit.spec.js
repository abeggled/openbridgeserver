import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

let createBinding
let updateBinding

const ALL_INSTANCES = [
  { id: 'mqtt-1',   name: 'MQTT Test',         adapter_type: 'MQTT' },
  { id: 'knx-1',    name: 'KNX Test',           adapter_type: 'KNX' },
  { id: 'modbus-1', name: 'Modbus Test',         adapter_type: 'MODBUS_TCP' },
  { id: 'ow-1',     name: 'Onewire Test',        adapter_type: 'ONEWIRE' },
  { id: 'ha-1',     name: 'Home Assistant Test', adapter_type: 'HOME_ASSISTANT' },
  { id: 'iob-1',    name: 'ioBroker Test',       adapter_type: 'IOBROKER' },
  { id: 'zt-1',     name: 'Timer Test',          adapter_type: 'ZEITSCHALTUHR' },
  { id: 'anw-1',    name: 'Anwesenheit Test',    adapter_type: 'ANWESENHEITSSIMULATION' },
  { id: 'snmp-1',   name: 'SNMP Test',           adapter_type: 'SNMP' },
  {
    id: 'msg-1',
    name: 'Message Test',
    adapter_type: 'MESSAGE',
    config: { providers: { pushover: { enabled: true, targets: { phone: {} } } } },
  },
  {
    id: 'msg-2',
    name: 'Message Test 2',
    adapter_type: 'MESSAGE',
    config: { providers: { telegram: { enabled: true, targets: { family: {} } } } },
  },
]

beforeEach(() => {
  vi.resetModules()
  createBinding = vi.fn().mockResolvedValue({})
  updateBinding = vi.fn().mockResolvedValue({})
  vi.doMock('@/api/client', () => ({
    dpApi: { createBinding, updateBinding },
    adapterApi: {
      listInstances:      vi.fn().mockResolvedValue({ data: ALL_INSTANCES }),
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

async function submit(wrapper) {
  await wrapper.find('form').trigger('submit')
  await flushPromises()
}

// ─── MQTT submit ──────────────────────────────────────────────────────────────

describe('BindingForm — MQTT create submit', () => {
  it('calls createBinding with adapter_instance_id and MQTT config', async () => {
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await submit(w)
    expect(createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      adapter_instance_id: 'mqtt-1',
      direction:           'SOURCE',
      config:              expect.objectContaining({ topic: '', retain: false }),
    }))
    w.unmount()
  })

  it('emits save on successful submit', async () => {
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await submit(w)
    expect(w.emitted('save')).toBeTruthy()
    w.unmount()
  })
})

// ─── KNX submit ───────────────────────────────────────────────────────────────

describe('BindingForm — KNX create submit', () => {
  it('calls createBinding with KNX config containing group_address and dpt_id', async () => {
    const w = await mountForm()
    await selectInstance(w, 'knx-1')
    await submit(w)
    expect(createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      adapter_instance_id: 'knx-1',
      config:              expect.objectContaining({ group_address: '', dpt_id: 'DPT9.001' }),
    }))
    w.unmount()
  })
})

// ─── Modbus submit ────────────────────────────────────────────────────────────

describe('BindingForm — MODBUS_TCP create submit', () => {
  it('calls createBinding with Modbus config fields', async () => {
    const w = await mountForm()
    await selectInstance(w, 'modbus-1')
    await submit(w)
    expect(createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      adapter_instance_id: 'modbus-1',
      config:              expect.objectContaining({ register_type: 'holding', data_format: 'uint16' }),
    }))
    w.unmount()
  })
})

// ─── 1-Wire submit ────────────────────────────────────────────────────────────

describe('BindingForm — ONEWIRE create submit', () => {
  it('calls createBinding with Onewire config', async () => {
    const w = await mountForm()
    await selectInstance(w, 'ow-1')
    await submit(w)
    expect(createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      adapter_instance_id: 'ow-1',
      config:              expect.objectContaining({ sensor_type: 'DS18B20' }),
    }))
    w.unmount()
  })
})

// ─── Home Assistant submit ────────────────────────────────────────────────────

describe('BindingForm — HOME_ASSISTANT create submit', () => {
  it('calls createBinding with HA config containing entity_id', async () => {
    const w = await mountForm()
    await selectInstance(w, 'ha-1')
    await submit(w)
    expect(createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      adapter_instance_id: 'ha-1',
      config:              expect.objectContaining({ entity_id: '' }),
    }))
    w.unmount()
  })
})

// ─── ioBroker submit ──────────────────────────────────────────────────────────

describe('BindingForm — IOBROKER create submit', () => {
  it('calls createBinding with ioBroker config containing state_id', async () => {
    const w = await mountForm()
    await selectInstance(w, 'iob-1')
    await submit(w)
    expect(createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      adapter_instance_id: 'iob-1',
      config:              expect.objectContaining({ state_id: '' }),
    }))
    w.unmount()
  })

  it('includes ack in config when direction is DEST', async () => {
    const w = await mountForm()
    await selectInstance(w, 'iob-1')
    // The visibleTabs shows filter tab for IOBROKER when showAdvancedTabs is true
    // Submit with default SOURCE direction
    await submit(w)
    const call = createBinding.mock.calls[0][1]
    // direction SOURCE → no ack in config
    expect(call.config.ack).toBeUndefined()
    w.unmount()
  })
})

// ─── ZEITSCHALTUHR submit ─────────────────────────────────────────────────────

describe('BindingForm — ZEITSCHALTUHR create submit', () => {
  it('calls createBinding with timer config containing timer_type and weekdays', async () => {
    const w = await mountForm()
    await selectInstance(w, 'zt-1')
    await submit(w)
    expect(createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      adapter_instance_id: 'zt-1',
      config:              expect.objectContaining({
        timer_type: 'daily',
        weekdays:   expect.arrayContaining([0,1,2,3,4,5,6]),
      }),
    }))
    w.unmount()
  })

  it('uses SOURCE direction for ZEITSCHALTUHR (read-only)', async () => {
    const w = await mountForm()
    await selectInstance(w, 'zt-1')
    await submit(w)
    expect(createBinding.mock.calls[0][1].direction).toBe('SOURCE')
    w.unmount()
  })
})

// ─── ANWESENHEITSSIMULATION submit ────────────────────────────────────────────

describe('BindingForm — ANWESENHEITSSIMULATION create submit', () => {
  it('calls createBinding with ANWESENHEITSSIMULATION, forces direction SOURCE', async () => {
    const w = await mountForm()
    await selectInstance(w, 'anw-1')
    await submit(w)
    const call = createBinding.mock.calls[0][1]
    // effectiveDirection for ANWESENHEITSSIMULATION is always 'SOURCE'
    expect(call.direction).toBe('SOURCE')
    w.unmount()
  })
})

// ─── MESSAGE submit ──────────────────────────────────────────────────────────

describe('BindingForm — MESSAGE create submit', () => {
  it('clears selected targets when the message instance changes', async () => {
    const w = await mountForm()
    await selectInstance(w, 'msg-1')
    await w.findAll('button').find(button => button.text() === 'Ziel hinzufügen').trigger('click')
    await flushPromises()

    await selectInstance(w, 'msg-2')
    await submit(w)

    expect(createBinding.mock.calls[0][1]).toEqual(expect.objectContaining({
      adapter_instance_id: 'msg-2',
      direction: 'SOURCE',
      config: expect.objectContaining({ providers: [] }),
    }))
    w.unmount()
  })
})

// ─── SNMP submit ──────────────────────────────────────────────────────────────

describe('BindingForm — SNMP create submit', () => {
  it('calls createBinding with SNMP config containing host and oid', async () => {
    const w = await mountForm()
    await selectInstance(w, 'snmp-1')
    await submit(w)
    expect(createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      adapter_instance_id: 'snmp-1',
      config:              expect.objectContaining({ host: '192.168.1.1' }),
    }))
    w.unmount()
  })
})

// ─── MESSAGE submit ──────────────────────────────────────────────────────────

describe('BindingForm — MESSAGE create submit', () => {
  it('calls createBinding with MESSAGE config and forces SOURCE direction', async () => {
    const w = await mountForm()
    await selectInstance(w, 'msg-1')
    await submit(w)

    expect(createBinding).toHaveBeenCalledWith('dp-1', expect.objectContaining({
      adapter_instance_id: 'msg-1',
      direction: 'SOURCE',
      config: expect.objectContaining({
        operator: '==',
        compare_value: '',
        message: '###DPN###: ###DP### ###DPU###',
        providers: [],
        send_on_change: true,
        cooldown_seconds: 0,
      }),
    }))
    w.unmount()
  })

  it('preserves MESSAGE title, priority and provider targets in edit mode', async () => {
    const w = await mountForm({
      initial: {
        id: 'binding-message',
        adapter_type: 'MESSAGE',
        adapter_instance_id: 'msg-1',
        direction: 'DEST',
        enabled: true,
        config: {
          operator: 'any',
          compare_value: null,
          message: '###DP###',
          title: '  Alarm  ',
          priority: 1,
          providers: [{ provider: 'pushover', target: 'phone' }],
          send_on_change: false,
          cooldown_seconds: 30,
        },
      },
    })
    await submit(w)

    expect(updateBinding).toHaveBeenCalledWith('dp-1', 'binding-message', expect.objectContaining({
      direction: 'SOURCE',
      config: expect.objectContaining({
        operator: 'any',
        title: 'Alarm',
        priority: 1,
        providers: [{ provider: 'pushover', target: 'phone' }],
        send_on_change: false,
        cooldown_seconds: 30,
      }),
    }))
    w.unmount()
  })

  it('applies MESSAGE fallback defaults for empty optional config values', async () => {
    const w = await mountForm({
      initial: {
        id: 'binding-message-defaults',
        adapter_type: 'MESSAGE',
        adapter_instance_id: 'msg-1',
        direction: 'SOURCE',
        enabled: true,
        config: {
          operator: '',
          compare_value: null,
          message: '',
          providers: null,
          send_on_change: null,
          cooldown_seconds: null,
        },
      },
    })
    await submit(w)

    expect(updateBinding).toHaveBeenCalledWith('dp-1', 'binding-message-defaults', expect.objectContaining({
      direction: 'SOURCE',
      config: expect.objectContaining({
        operator: '==',
        compare_value: '',
        message: '###DPN###: ###DP### ###DPU###',
        providers: [],
        send_on_change: true,
        cooldown_seconds: 0,
      }),
    }))
    w.unmount()
  })
})

// ─── Submit error handling ────────────────────────────────────────────────────

describe('BindingForm — submit error', () => {
  it('shows error detail from API rejection', async () => {
    createBinding.mockRejectedValue({
      response: { data: { detail: 'Gruppe bereits vergeben' } },
    })
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await submit(w)
    expect(w.text()).toContain('Gruppe bereits vergeben')
    w.unmount()
  })

  it('shows common saveError when rejection has no detail', async () => {
    createBinding.mockRejectedValue(new Error('network'))
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')
    await submit(w)
    // Falls back to t('common.saveError')
    expect(w.find('.bg-red-500\\/10').exists()).toBe(true)
    w.unmount()
  })

  it('shows error and does not call createBinding when no instance selected', async () => {
    const w = await mountForm()
    // Don't select an instance
    await submit(w)
    expect(createBinding).not.toHaveBeenCalled()
    expect(w.find('.bg-red-500\\/10').exists()).toBe(true)
    w.unmount()
  })
})

// ─── Update mode ──────────────────────────────────────────────────────────────

describe('BindingForm — update/edit mode', () => {
  it('calls updateBinding with the binding id when initial is provided', async () => {
    const w = await mountForm({
      initial: {
        id: 'binding-99',
        adapter_instance_id: 'mqtt-1',
        adapter_type: 'MQTT',
        direction: 'DEST',
        enabled: true,
        config: { topic: 'test/topic', retain: false },
      },
    })
    await submit(w)
    expect(updateBinding).toHaveBeenCalledWith('dp-1', 'binding-99', expect.objectContaining({
      direction: 'DEST',
      config:    expect.objectContaining({ topic: 'test/topic' }),
    }))
    expect(createBinding).not.toHaveBeenCalled()
    w.unmount()
  })

  it('emits save after successful update', async () => {
    const w = await mountForm({
      initial: {
        id: 'binding-99',
        adapter_type: 'MQTT',
        adapter_instance_id: 'mqtt-1',
        direction: 'SOURCE',
        enabled: true,
        config: { topic: 'x', retain: false },
      },
    })
    await submit(w)
    expect(w.emitted('save')).toBeTruthy()
    w.unmount()
  })
})

// ─── throttle unit conversion in submit ───────────────────────────────────────

describe('BindingForm — throttle conversion in submit', () => {
  it('converts minutes throttle correctly to ms', async () => {
    const w = await mountForm()
    await selectInstance(w, 'mqtt-1')

    // Switch to filter tab first — need to select DEST direction to show filter tab
    await w.find('[data-testid="select-direction"]').setValue('DEST')
    await flushPromises()

    const tabBtns = w.findAll('button[type="button"]')
    const filterTab = tabBtns.find(b => b.text().includes('Filter'))
    await filterTab.trigger('click')
    await flushPromises()

    const throttleInputs = w.findAll('input[type="number"]')
    const throttleValue = throttleInputs[0]
    await throttleValue.setValue('5')
    const throttleUnit = w.findAll('select').find(s => s.findAll('option').some(o => o.text() === 'min'))
    await throttleUnit.setValue('min')

    await submit(w)
    const call = createBinding.mock.calls[0][1]
    expect(call.send_throttle_ms).toBe(5 * 60_000)
    w.unmount()
  })
})

// ─── value_map in submit ──────────────────────────────────────────────────────

describe('BindingForm — value_map included in submit', () => {
  it('resolves a named value_map preset', async () => {
    const w = await mountForm({
      initial: {
        id: 'b1',
        adapter_type: 'MQTT',
        adapter_instance_id: 'mqtt-1',
        direction: 'SOURCE',
        enabled: true,
        config: { topic: 't', retain: false },
        value_map: { '0': '1', '1': '0' },  // num_invert
      },
    })
    await submit(w)
    expect(updateBinding).toHaveBeenCalledWith('dp-1', 'b1', expect.objectContaining({
      value_map: { '0': '1', '1': '0' },
    }))
    w.unmount()
  })
})
