import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import DataPointDetailView from '@/views/DataPointDetailView.vue'

const apiMocks = vi.hoisted(() => ({
  dpApi: {
    get: vi.fn(),
    listBindings: vi.fn(),
    writeValue: vi.fn(),
    deleteBinding: vi.fn(),
    update: vi.fn(),
  },
  logicApi: {
    datapointUsages: vi.fn(),
  },
  systemApi: {
    datatypes: vi.fn(),
  },
  searchApi: {
    search: vi.fn(),
  },
}))

vi.mock('@/api/client', () => apiMocks)

function makeDP(overrides = {}) {
  return {
    id: 'dp-1',
    name: 'Test DP',
    data_type: 'FLOAT',
    unit: null,
    tags: [],
    mqtt_topic: 'dp/dp-1/value',
    mqtt_alias: null,
    persist_value: true,
    record_history: false,
    value: null,
    quality: 'uncertain',
    created_at: '2026-01-01T00:00:00+00:00',
    updated_at: '2026-01-01T00:00:00+00:00',
    ...overrides,
  }
}

function mountView(dp = makeDP(), stubs = {}) {
  return mount(DataPointDetailView, {
    props: { id: dp.id },
    global: {
      stubs: {
        RouterLink:             { template: '<a><slot /></a>' },
        DataPointHierarchyCard: { template: '<div />' },
        DataPointForm:          { template: '<div />' },
        BindingForm:            { template: '<div />' },
        Modal:                  { template: '<div v-if="modelValue"><slot /></div>', props: ['modelValue'] },
        ConfirmDialog:          { template: '<div />' },
        ...stubs,
      },
    },
  })
}

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.systemApi.datatypes.mockResolvedValue({ data: [{ name: 'FLOAT' }] })
  apiMocks.dpApi.listBindings.mockResolvedValue({ data: [] })
  apiMocks.logicApi.datapointUsages.mockResolvedValue({ data: [] })
  apiMocks.dpApi.writeValue.mockResolvedValue({})
  apiMocks.dpApi.deleteBinding.mockResolvedValue({})
  apiMocks.dpApi.update.mockResolvedValue({ data: makeDP() })
})

// ─── BOOLEAN write form ───────────────────────────────────────────────────────

describe('DataPointDetailView — BOOLEAN write form', () => {
  it('shows true/false buttons instead of text input for BOOLEAN datapoint', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'BOOLEAN' }) })
    const wrapper = mountView(makeDP({ data_type: 'BOOLEAN' }))
    await flushPromises()
    const buttons = wrapper.findAll('button')
    expect(buttons.some(b => b.text() === 'true')).toBe(true)
    expect(buttons.some(b => b.text() === 'false')).toBe(true)
    expect(wrapper.find('input[type="text"]').exists()).toBe(false)
  })

  it('writes true when "true" button is clicked', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'BOOLEAN' }) })
    const wrapper = mountView(makeDP({ data_type: 'BOOLEAN' }))
    await flushPromises()
    const trueBtn = wrapper.findAll('button').find(b => b.text() === 'true')
    await trueBtn.trigger('click')
    await flushPromises()
    expect(apiMocks.dpApi.writeValue).toHaveBeenCalledWith('dp-1', true)
  })

  it('writes false when "false" button is clicked', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'BOOLEAN' }) })
    const wrapper = mountView(makeDP({ data_type: 'BOOLEAN' }))
    await flushPromises()
    const falseBtn = wrapper.findAll('button').find(b => b.text() === 'false')
    await falseBtn.trigger('click')
    await flushPromises()
    expect(apiMocks.dpApi.writeValue).toHaveBeenCalledWith('dp-1', false)
  })
})

// ─── coerceTime ───────────────────────────────────────────────────────────────

describe('DataPointDetailView — TIME write', () => {
  it('writes a valid time value', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'TIME' }) })
    const wrapper = mountView(makeDP({ data_type: 'TIME' }))
    await flushPromises()
    await wrapper.find('input[type="text"]').setValue('14:30')
    await wrapper.findAll('button').find(b => b.text() === 'Schreiben').trigger('click')
    await flushPromises()
    expect(apiMocks.dpApi.writeValue).toHaveBeenCalledWith('dp-1', '14:30')
  })

  it('shows error for invalid time value', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'TIME' }) })
    const wrapper = mountView(makeDP({ data_type: 'TIME' }))
    await flushPromises()
    await wrapper.find('input[type="text"]').setValue('nicht-valide')
    await wrapper.findAll('button').find(b => b.text() === 'Schreiben').trigger('click')
    await flushPromises()
    expect(apiMocks.dpApi.writeValue).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Bitte ein gültiges Datum oder eine gültige Zeit eingeben.')
  })

  it('accepts time with seconds', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'TIME' }) })
    const wrapper = mountView(makeDP({ data_type: 'TIME' }))
    await flushPromises()
    await wrapper.find('input[type="text"]').setValue('08:05:30')
    await wrapper.findAll('button').find(b => b.text() === 'Schreiben').trigger('click')
    await flushPromises()
    expect(apiMocks.dpApi.writeValue).toHaveBeenCalledWith('dp-1', '08:05:30')
  })
})

// ─── coerceDateTime ───────────────────────────────────────────────────────────

describe('DataPointDetailView — DATETIME write', () => {
  it('writes a valid datetime value', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'DATETIME' }) })
    const wrapper = mountView(makeDP({ data_type: 'DATETIME' }))
    await flushPromises()
    await wrapper.find('input[type="text"]').setValue('2026-06-27T10:00:00')
    await wrapper.findAll('button').find(b => b.text() === 'Schreiben').trigger('click')
    await flushPromises()
    expect(apiMocks.dpApi.writeValue).toHaveBeenCalledWith('dp-1', '2026-06-27T10:00:00')
  })

  it('shows error for invalid datetime value', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'DATETIME' }) })
    const wrapper = mountView(makeDP({ data_type: 'DATETIME' }))
    await flushPromises()
    await wrapper.find('input[type="text"]').setValue('kein-datum')
    await wrapper.findAll('button').find(b => b.text() === 'Schreiben').trigger('click')
    await flushPromises()
    expect(apiMocks.dpApi.writeValue).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Bitte ein gültiges Datum oder eine gültige Zeit eingeben.')
  })
})

// ─── coerceWriteValue — NaN guard ─────────────────────────────────────────────

describe('DataPointDetailView — numeric NaN guard', () => {
  it('shows invalid-number error when INTEGER receives non-numeric input', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'INTEGER' }) })
    const wrapper = mountView(makeDP({ data_type: 'INTEGER' }))
    await flushPromises()
    await wrapper.find('input[type="text"]').setValue('abc')
    await wrapper.findAll('button').find(b => b.text() === 'Schreiben').trigger('click')
    await flushPromises()
    expect(apiMocks.dpApi.writeValue).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Bitte eine gültige Zahl eingeben.')
  })

  it('shows invalid-number error when FLOAT receives non-numeric input', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'FLOAT' }) })
    const wrapper = mountView(makeDP({ data_type: 'FLOAT' }))
    await flushPromises()
    await wrapper.find('input[type="text"]').setValue('xyz')
    await wrapper.findAll('button').find(b => b.text() === 'Schreiben').trigger('click')
    await flushPromises()
    expect(apiMocks.dpApi.writeValue).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Bitte eine gültige Zahl eingeben.')
  })
})

// ─── coerceWriteValue — STRING fallback ───────────────────────────────────────

describe('DataPointDetailView — STRING write', () => {
  it('writes raw string for STRING datapoint', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'STRING' }) })
    const wrapper = mountView(makeDP({ data_type: 'STRING' }))
    await flushPromises()
    await wrapper.find('input[type="text"]').setValue('Hello World')
    await wrapper.findAll('button').find(b => b.text() === 'Schreiben').trigger('click')
    await flushPromises()
    expect(apiMocks.dpApi.writeValue).toHaveBeenCalledWith('dp-1', 'Hello World')
  })
})

// ─── Write success feedback ───────────────────────────────────────────────────

describe('DataPointDetailView — write success feedback', () => {
  it('shows success text after successful write', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'FLOAT' }) })
    const wrapper = mountView(makeDP({ data_type: 'FLOAT' }))
    await flushPromises()
    await wrapper.find('input[type="text"]').setValue('23.5')
    await wrapper.findAll('button').find(b => b.text() === 'Schreiben').trigger('click')
    await flushPromises()
    expect(wrapper.text()).toContain('Wert geschrieben.')
  })
})

// ─── canWriteValue with DEST binding ─────────────────────────────────────────

describe('DataPointDetailView — canWriteValue with writable bindings', () => {
  it('shows write form when DEST binding exists', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'FLOAT' }) })
    apiMocks.dpApi.listBindings.mockResolvedValue({
      data: [{ id: 'b-1', enabled: true, direction: 'DEST', adapter_type: 'KNX', config: {} }],
    })
    const wrapper = mountView(makeDP({ data_type: 'FLOAT' }))
    await flushPromises()
    expect(wrapper.find('input[type="text"]').exists()).toBe(true)
    expect(wrapper.findAll('button').some(b => b.text() === 'Schreiben')).toBe(true)
  })

  it('shows write form when BOTH binding exists', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP({ data_type: 'FLOAT' }) })
    apiMocks.dpApi.listBindings.mockResolvedValue({
      data: [{ id: 'b-2', enabled: true, direction: 'BOTH', adapter_type: 'MQTT', config: {} }],
    })
    const wrapper = mountView(makeDP({ data_type: 'FLOAT' }))
    await flushPromises()
    expect(wrapper.find('input[type="text"]').exists()).toBe(true)
  })
})

// ─── Logic usages ─────────────────────────────────────────────────────────────

describe('DataPointDetailView — logic usages', () => {
  it('shows logic usage graph name when usages are returned', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP() })
    apiMocks.logicApi.datapointUsages.mockResolvedValue({
      data: [
        { node_id: 'n-1', graph_id: 'g-1', graph_name: 'Logikblatt Heizung', graph_enabled: true, direction: 'SOURCE', node_type: 'datapoint_read' },
      ],
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Logikblatt Heizung')
  })

  it('shows disabled badge for disabled graph', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP() })
    apiMocks.logicApi.datapointUsages.mockResolvedValue({
      data: [
        { node_id: 'n-2', graph_id: 'g-2', graph_name: 'Inaktiv', graph_enabled: false, direction: 'DEST', node_type: 'datapoint_write' },
      ],
    })
    const wrapper = mountView()
    await flushPromises()
    expect(wrapper.text()).toContain('Inaktiv')
    // Disabled badge is rendered (graph_enabled: false)
    expect(wrapper.html()).toContain('badge')
  })

  it('shows no-logic-bindings message when usages list is empty', async () => {
    apiMocks.dpApi.get.mockResolvedValue({ data: makeDP() })
    apiMocks.logicApi.datapointUsages.mockResolvedValue({ data: [] })
    const wrapper = mountView()
    await flushPromises()
    // German: "Keine Logik-Verknüpfungen"
    expect(wrapper.text()).toContain('Keine')
  })
})
