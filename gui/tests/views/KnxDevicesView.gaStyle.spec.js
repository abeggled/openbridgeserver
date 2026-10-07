// #1296: the KNX device view shows group addresses in the project's style.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

const SHOWN = { ThreeLevel: ['1/0/234', '1/0/235'], TwoLevel: ['1/234', '1/235'], Free: ['2282', '2283'] }

const device = {
  pa: '1.1.5', name: 'Testaktor', manufacturer: 'MDT', order_number: 'AKS', app_ref: 'APP', imported_at: '2026-06-01T00:00:00Z',
  hierarchy_links: [],
  comm_objects: [
    {
      id: 'co-1', number: '1', name: 'Schalten', datapoint_type: 'DPT1.001',
      ga_addresses: ['1/0/234', '1/0/235'],
      datapoints: [
        { id: 'dp-1', name: 'Licht', data_type: 'BOOLEAN', binding_id: 'b-1', direction: 'BOTH', enabled: true, ga_address: '1/0/235', ga_role: 'state_group_address' },
      ],
    },
  ],
}

function mockApi(style) {
  const knxprojApi = {
    listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [], group_address_style: style } }),
    listDevices: vi.fn().mockResolvedValue({ data: { items: [{ ...device, comm_objects: undefined }], total: 1, page: 0, size: 25, pages: 1 } }),
    getDevice: vi.fn().mockResolvedValue({ data: device }),
  }
  const hierarchyApi = { listTrees: vi.fn().mockResolvedValue({ data: [] }), getTreeNodes: vi.fn().mockResolvedValue({ data: [] }) }
  vi.doMock('@/api/client', () => ({ knxprojApi, hierarchyApi }))
}

beforeEach(() => { vi.resetModules() })
afterEach(() => { vi.doUnmock('@/api/client') })

async function openDevice(style) {
  mockApi(style)
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const { default: KnxDevicesView } = await import('@/views/KnxDevicesView.vue')
  const wrapper = mount(KnxDevicesView, {
    global: {
      plugins: [pinia],
      stubs: { RouterLink: { template: '<a><slot /></a>' }, HierarchyCombobox: { template: '<div />' } },
    },
  })
  await flushPromises()
  await wrapper.find('[data-testid="knx-device-row-1.1.5"]').trigger('click')
  await flushPromises()
  return wrapper
}

describe.each(Object.keys(SHOWN))('KnxDevicesView in a %s project', (style) => {
  it('shows the linked group addresses and the bound datapoint address in the project style', async () => {
    const wrapper = await openDevice(style)
    expect(wrapper.findAll('[data-testid="knx-device-ga"]').map(chip => chip.text())).toEqual(SHOWN[style])
    expect(wrapper.find('[data-testid="knx-device-bound-ga"]').text()).toBe(SHOWN[style][1])
  })
})

describe('KnxDevicesView when the project style cannot be loaded', () => {
  it('says so next to the three-level addresses, and a retry switches to the project style', async () => {
    const knxprojApi = {
      listGA: vi.fn().mockRejectedValueOnce(new Error('offline')).mockResolvedValue({ data: { total: 0, items: [], group_address_style: 'TwoLevel' } }),
      listDevices: vi.fn().mockResolvedValue({ data: { items: [{ ...device, comm_objects: undefined }], total: 1, page: 0, size: 25, pages: 1 } }),
      getDevice: vi.fn().mockResolvedValue({ data: device }),
    }
    vi.doMock('@/api/client', () => ({ knxprojApi, hierarchyApi: { listTrees: vi.fn().mockResolvedValue({ data: [] }), getTreeNodes: vi.fn().mockResolvedValue({ data: [] }) } }))
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const { default: KnxDevicesView } = await import('@/views/KnxDevicesView.vue')
    const wrapper = mount(KnxDevicesView, { global: { plugins: [pinia], stubs: { RouterLink: { template: '<a><slot /></a>' }, HierarchyCombobox: { template: '<div />' } } } })
    await flushPromises()
    await wrapper.find('[data-testid="knx-device-row-1.1.5"]').trigger('click')
    await flushPromises()

    expect(wrapper.findAll('[data-testid="knx-device-ga"]').map(chip => chip.text())).toEqual(SHOWN.ThreeLevel)
    const notice = wrapper.find('[data-testid="ga-style-notice"]')
    expect(notice.text()).toContain('dreistufig')
    await notice.find('button').trigger('click')
    await flushPromises()
    expect(wrapper.findAll('[data-testid="knx-device-ga"]').map(chip => chip.text())).toEqual(SHOWN.TwoLevel)
    expect(wrapper.find('[data-testid="ga-style-notice"]').exists()).toBe(false)
  })
})
