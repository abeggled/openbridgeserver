// #1296: the datapoint's KNX context shows group addresses in the project's style.
import { mount, flushPromises } from '@vue/test-utils'
import { describe, it, expect, vi, beforeEach } from 'vitest'
import DataPointDetailView from '@/views/DataPointDetailView.vue'

const SHOWN = { ThreeLevel: ['1/0/234', '1/0/235'], TwoLevel: ['1/234', '1/235'], Free: ['2282', '2283'] }

const apiMocks = vi.hoisted(() => ({
  dpApi: { get: vi.fn(), listBindings: vi.fn(), knxContext: vi.fn(), writeValue: vi.fn() },
  logicApi: { datapointUsages: vi.fn() },
  systemApi: { datatypes: vi.fn() },
  knxprojApi: { listGA: vi.fn() },
}))

vi.mock('@/api/client', () => apiMocks)

beforeEach(() => {
  vi.clearAllMocks()
  apiMocks.systemApi.datatypes.mockResolvedValue({ data: [{ name: 'BOOLEAN' }] })
  apiMocks.dpApi.get.mockResolvedValue({
    data: { id: 'dp-1', name: 'Licht', data_type: 'BOOLEAN', unit: null, tags: [], mqtt_topic: 'dp/dp-1/value', value: null, quality: 'uncertain' },
  })
  apiMocks.dpApi.listBindings.mockResolvedValue({
    data: [{ id: 'b-1', enabled: true, direction: 'BOTH', adapter_type: 'KNX', config: { group_address: '1/0/234', state_group_address: '1/0/235' } }],
  })
  apiMocks.dpApi.knxContext.mockResolvedValue({
    data: {
      datapoint_id: 'dp-1',
      group_addresses: [
        { address: '1/0/234', name: 'Licht Schalten', description: '', dpt: 'DPT1.001', roles: ['group_address'], devices: [] },
        { address: '1/0/235', name: 'Licht Status', description: '', dpt: 'DPT1.001', roles: ['state_group_address'], devices: [] },
      ],
    },
  })
  apiMocks.logicApi.datapointUsages.mockResolvedValue({ data: [] })
})

describe.each(Object.keys(SHOWN))('DataPointDetailView in a %s project', (style) => {
  it('shows command and feedback address in the project style, with their names', async () => {
    apiMocks.knxprojApi.listGA.mockResolvedValue({ data: { total: 0, items: [], group_address_style: style } })
    const wrapper = mount(DataPointDetailView, {
      props: { id: 'dp-1' },
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
    await flushPromises()
    expect(wrapper.findAll('[data-testid="datapoint-knx-ga"]').map(item => item.text())).toEqual(SHOWN[style])
    const context = wrapper.find('[data-testid="datapoint-knx-context"]').text()
    expect(context).toContain('Licht Schalten')
    expect(context).toContain('Licht Status')
  })
})

describe('DataPointDetailView when the project style cannot be loaded', () => {
  it('shows a notice in the KNX context and keeps the addresses readable', async () => {
    apiMocks.knxprojApi.listGA.mockRejectedValue(new Error('offline'))
    const wrapper = mount(DataPointDetailView, {
      props: { id: 'dp-1' },
      global: { stubs: { RouterLink: { template: '<a><slot /></a>' }, DataPointHierarchyCard: { template: '<div />' }, DataPointForm: { template: '<div />' }, BindingForm: { template: '<div />' }, Modal: { template: '<div />' }, ConfirmDialog: { template: '<div />' } } },
    })
    await flushPromises()
    expect(wrapper.findAll('[data-testid="datapoint-knx-ga"]').map(item => item.text())).toEqual(SHOWN.ThreeLevel)
    expect(wrapper.find('[data-testid="datapoint-knx-context"] [data-testid="ga-style-notice"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Licht Schalten')
  })
})
