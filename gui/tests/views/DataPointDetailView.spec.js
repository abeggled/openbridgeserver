import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import DataPointDetailView from '@/views/DataPointDetailView.vue'

const apiMocks = vi.hoisted(() => ({
  dpApi: {
    get: vi.fn(),
    listBindings: vi.fn(),
    knxContext: vi.fn(),
    writeValue: vi.fn(),
  },
  logicApi: {
    datapointUsages: vi.fn(),
  },
  systemApi: {
    datatypes: vi.fn(),
  },
}))

vi.mock('@/api/client', () => apiMocks)

function mountView() {
  return mount(DataPointDetailView, {
    props: { id: 'dp-internal' },
    global: {
      stubs: {
        RouterLink: { template: '<a><slot /></a>' },
        DataPointHierarchyCard: { template: '<div />' },
        DataPointForm: { template: '<div />' },
        BindingForm: { template: '<div />' },
        Modal: { template: '<div v-if="modelValue"><slot /></div>', props: ['modelValue'] },
        ConfirmDialog: { template: '<div />' },
      },
    },
  })
}

describe('DataPointDetailView', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    apiMocks.systemApi.datatypes.mockResolvedValue({ data: [{ name: 'FLOAT' }] })
    apiMocks.dpApi.get.mockResolvedValue({
      data: {
        id: 'dp-internal',
        name: 'Internal Temperature',
        data_type: 'FLOAT',
        unit: '°C',
        tags: [],
        mqtt_topic: 'dp/dp-internal/value',
        mqtt_alias: null,
        persist_value: true,
        record_history: true,
        value: null,
        quality: 'uncertain',
        created_at: '2026-06-11T10:00:00+00:00',
        updated_at: '2026-06-11T10:00:00+00:00',
      },
    })
    apiMocks.dpApi.listBindings.mockResolvedValue({ data: [] })
    apiMocks.dpApi.knxContext.mockResolvedValue({ data: { datapoint_id: 'dp-internal', group_addresses: [] } })
    apiMocks.logicApi.datapointUsages.mockResolvedValue({ data: [] })
    apiMocks.dpApi.writeValue.mockResolvedValue({})
  })

  it('shows the live value in the regional format and its live timestamp (#1073)', async () => {
    const { useWebSocketStore } = await import('@/stores/websocket')
    useWebSocketStore().liveValues['dp-internal'] = {
      value: 1234.5,
      quality: 'good',
      ts: '2026-06-12T08:30:00+00:00',
    }

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('1.234,5 °C')
    expect(wrapper.text()).toContain('12.06.2026')

    delete useWebSocketStore().liveValues['dp-internal']
  })

  it('shows a system DataPoint as read-only without write form or edit button (#1370)', async () => {
    const { data } = await apiMocks.dpApi.get()
    apiMocks.dpApi.get.mockResolvedValue({ data: { ...data, data_type: 'BOOLEAN', system: true } })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="dp-system-badge"]').text()).toBe('System')
    expect(wrapper.find('[data-testid="dp-system-readonly"]').exists()).toBe(true)
    expect(wrapper.findAll('button').some(button => button.text() === 'true')).toBe(false)
    expect(wrapper.find('[data-testid="btn-edit-dp"]').exists()).toBe(false)
  })

  it('offers the edit button and no system badge for ordinary DataPoints', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="dp-system-badge"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="dp-system-readonly"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="btn-edit-dp"]').exists()).toBe(true)
  })

  it('allows writing an internal datapoint without writable adapter bindings', async () => {
    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).not.toContain('Kein schreibbares Binding vorhanden.')

    const input = wrapper.find('input[type="text"]')
    await input.setValue('21.5')

    const writeButton = wrapper.findAll('button').find(button => button.text() === 'Schreiben')
    expect(writeButton).toBeTruthy()
    await writeButton.trigger('click')
    await flushPromises()

    expect(apiMocks.dpApi.writeValue).toHaveBeenCalledWith('dp-internal', 21.5)
  })

  it('does not expose the write form for source-only adapter bindings', async () => {
    apiMocks.dpApi.listBindings.mockResolvedValue({
      data: [{ id: 'binding-source', enabled: true, direction: 'SOURCE', adapter_type: 'KNX', config: {} }],
    })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('Kein schreibbares Binding vorhanden.')
    expect(wrapper.find('input[type="text"]').exists()).toBe(false)
    expect(wrapper.findAll('button').some(button => button.text() === 'Schreiben')).toBe(false)
  })

  it('allows writing when only MESSAGE bindings are attached', async () => {
    apiMocks.dpApi.listBindings.mockResolvedValue({
      data: [{ id: 'binding-message', enabled: true, direction: 'DEST', adapter_type: 'MESSAGE', config: {} }],
    })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).not.toContain('Kein schreibbares Binding vorhanden.')
    expect(wrapper.find('input[type="text"]').exists()).toBe(true)
  })

  it('does not expose the write form while bindings are still loading', async () => {
    let resolveBindings
    apiMocks.dpApi.listBindings.mockReturnValue(new Promise(resolve => { resolveBindings = resolve }))

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('input[type="text"]').exists()).toBe(false)
    expect(wrapper.findAll('button').some(button => button.text() === 'Schreiben')).toBe(false)

    resolveBindings({ data: [] })
    await flushPromises()

    expect(wrapper.find('input[type="text"]').exists()).toBe(true)
  })

  it('rejects invalid temporal write values before posting', async () => {
    apiMocks.dpApi.get.mockResolvedValue({
      data: {
        id: 'dp-internal',
        name: 'Internal Date',
        data_type: 'DATE',
        unit: null,
        tags: [],
        mqtt_topic: 'dp/dp-internal/value',
        mqtt_alias: null,
        persist_value: true,
        record_history: true,
        value: null,
        quality: 'uncertain',
        created_at: '2026-06-11T10:00:00+00:00',
        updated_at: '2026-06-11T10:00:00+00:00',
      },
    })
    const wrapper = mountView()
    await flushPromises()

    const input = wrapper.find('input[type="text"]')
    await input.setValue('2026-02-31')

    const writeButton = wrapper.findAll('button').find(button => button.text() === 'Schreiben')
    await writeButton.trigger('click')
    await flushPromises()

    expect(apiMocks.dpApi.writeValue).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Bitte ein gültiges Datum oder eine gültige Zeit eingeben.')
  })

  it('labels logic usages by direction instead of node type', async () => {
    apiMocks.logicApi.datapointUsages.mockResolvedValue({
      data: [
        {
          graph_id: 'graph-1',
          graph_name: 'HTTP sync',
          graph_enabled: true,
          node_id: 'api-1',
          node_type: 'api_client',
          direction: 'SOURCE',
        },
        {
          graph_id: 'graph-2',
          graph_name: 'Write back',
          graph_enabled: true,
          node_id: 'write-1',
          node_type: 'datapoint_write',
          direction: 'DEST',
        },
      ],
    })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.text()).toContain('HTTP sync')
    expect(wrapper.text()).toContain('Logik liest im verlinkten Blatt dieses Objekt')
    expect(wrapper.text()).toContain('Write back')
    expect(wrapper.text()).toContain('Logik schreibt im verlinkten Blatt auf dieses Objekt')
  })

  it('renders KNX group address context instead of raw binding JSON', async () => {
    apiMocks.dpApi.listBindings.mockResolvedValue({
      data: [
        {
          id: 'binding-knx',
          enabled: true,
          direction: 'BOTH',
          adapter_type: 'KNX',
          config: { group_address: '1/2/3', state_group_address: '1/2/4' },
        },
      ],
    })
    apiMocks.dpApi.knxContext.mockResolvedValue({
      data: {
        datapoint_id: 'dp-internal',
        group_addresses: [
          {
            address: '1/2/3',
            name: 'Licht Küche',
            description: '',
            dpt: 'DPT1.001',
            roles: ['group_address'],
            devices: [
              {
                pa: '1.1.10',
                name: 'Küche Aktor',
                comm_objects: [{ id: 'co-1', number: '1', name: 'Schalten', datapoint_type: 'DPT1.001' }],
              },
            ],
          },
          {
            address: '1/2/4',
            name: 'Status Küche',
            description: '',
            dpt: 'DPT1.001',
            roles: ['state_group_address'],
            devices: [],
          },
        ],
      },
    })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="datapoint-knx-context"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('1/2/3')
    expect(wrapper.text()).toContain('Licht Küche')
    expect(wrapper.text()).toContain('1.1.10')
    expect(wrapper.text()).toContain('Küche Aktor')
    expect(wrapper.text()).toContain('Rückmelde-GA')
    expect(wrapper.text()).not.toContain('"group_address"')
  })

  it('renders KNX context when adapter_type is lowercase "knx"', async () => {
    apiMocks.dpApi.listBindings.mockResolvedValue({
      data: [
        {
          id: 'binding-knx-lower',
          enabled: true,
          direction: 'BOTH',
          adapter_type: 'knx',
          config: { group_address: '2/3/4' },
        },
      ],
    })
    apiMocks.dpApi.knxContext.mockResolvedValue({
      data: {
        datapoint_id: 'dp-internal',
        group_addresses: [
          {
            address: '2/3/4',
            name: 'Licht Bad',
            description: '',
            dpt: 'DPT1.001',
            roles: ['group_address'],
            devices: [],
          },
        ],
      },
    })

    const wrapper = mountView()
    await flushPromises()

    expect(wrapper.find('[data-testid="datapoint-knx-context"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('2/3/4')
  })

  describe('add binding after editing one (#1312)', () => {
    const existing = { id: 'binding-1', enabled: true, direction: 'SOURCE', adapter_type: 'MQTT', config: {} }

    function mountWithBindingFormProbe() {
      return mount(DataPointDetailView, {
        props: { id: 'dp-internal' },
        global: {
          stubs: {
            RouterLink: { template: '<a><slot /></a>' },
            DataPointHierarchyCard: { template: '<div />' },
            DataPointForm: { template: '<div />' },
            BindingForm: { name: 'BindingForm', props: ['initial'], emits: ['save', 'cancel'], template: '<div />' },
            Modal: { template: '<div v-if="modelValue"><slot /></div>', props: ['modelValue'] },
            ConfirmDialog: { template: '<div />' },
          },
        },
      })
    }

    beforeEach(() => {
      apiMocks.dpApi.listBindings.mockResolvedValue({ data: [existing] })
    })

    it.each(['cancel', 'save'])('opens an empty form after an edit was closed via %s', async (closeEvent) => {
      const wrapper = mountWithBindingFormProbe()
      await flushPromises()

      await wrapper.find('[data-testid="btn-edit-binding"]').trigger('click')
      expect(wrapper.findComponent({ name: 'BindingForm' }).props('initial')).toEqual(existing)

      wrapper.findComponent({ name: 'BindingForm' }).vm.$emit(closeEvent)
      await flushPromises()
      expect(wrapper.findComponent({ name: 'BindingForm' }).exists()).toBe(false)

      await wrapper.find('[data-testid="btn-add-binding"]').trigger('click')
      expect(wrapper.findComponent({ name: 'BindingForm' }).props('initial')).toBeNull()
    })

    it('still opens the form prefilled when editing', async () => {
      const wrapper = mountWithBindingFormProbe()
      await flushPromises()

      await wrapper.find('[data-testid="btn-add-binding"]').trigger('click')
      expect(wrapper.findComponent({ name: 'BindingForm' }).props('initial')).toBeNull()

      await wrapper.find('[data-testid="btn-edit-binding"]').trigger('click')
      expect(wrapper.findComponent({ name: 'BindingForm' }).props('initial')).toEqual(existing)
    })
  })
})
