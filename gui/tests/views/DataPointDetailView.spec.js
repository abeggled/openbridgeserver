import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import DataPointDetailView from '@/views/DataPointDetailView.vue'

const apiMocks = vi.hoisted(() => ({
  dpApi: {
    get: vi.fn(),
    listBindings: vi.fn(),
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
    apiMocks.logicApi.datapointUsages.mockResolvedValue({ data: [] })
    apiMocks.dpApi.writeValue.mockResolvedValue({})
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
})
