import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let knxprojApi
let hierarchyApi

beforeEach(() => {
  vi.resetModules()
  knxprojApi = {
    listDevices: vi.fn().mockResolvedValue({
      data: {
        items: [
          {
            pa: '1.1.1',
            name: 'Kitchen Switch',
            manufacturer: 'Siemens',
            order_number: '5WG1',
            app_ref: 'APP-KITCHEN',
            imported_at: '2026-06-01T00:00:00Z',
            hierarchy_links: [],
          },
        ],
        total: 1,
        page: 0,
        size: 25,
        pages: 1,
      },
    }),
    getDevice: vi.fn().mockResolvedValue({
      data: {
        pa: '1.1.1',
        name: 'Kitchen Switch',
        manufacturer: 'Siemens',
        order_number: '5WG1',
        app_ref: 'APP-KITCHEN',
        imported_at: '2026-06-01T00:00:00Z',
        hierarchy_links: [],
        comm_objects: [
          {
            id: 'co-1',
            number: '1',
            name: 'Switch',
            datapoint_type: 'DPT1.001',
            ga_addresses: ['1/2/3'],
            datapoints: [
              {
                id: 'dp-1',
                name: 'Kitchen Light',
                data_type: 'BOOLEAN',
                binding_id: 'binding-1',
                direction: 'BOTH',
                enabled: true,
                ga_address: '1/2/3',
                ga_role: 'group_address',
              },
            ],
          },
        ],
      },
    }),
    setDeviceHierarchyLinks: vi.fn().mockResolvedValue({
      data: {
        pa: '1.1.1',
        name: 'Kitchen Switch',
        manufacturer: 'Siemens',
        order_number: '5WG1',
        app_ref: 'APP-KITCHEN',
        imported_at: '2026-06-01T00:00:00Z',
        hierarchy_links: [
          { tree_id: 'tree-1', tree_name: 'Gebäude', node_id: 'node-kitchen', node_name: 'Küche' },
        ],
        comm_objects: [],
      },
    }),
  }
  hierarchyApi = {
    listTrees: vi.fn().mockResolvedValue({ data: [] }),
    getTreeNodes: vi.fn().mockResolvedValue({ data: [] }),
  }

  vi.doMock('@/api/client', () => ({ knxprojApi, hierarchyApi }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountView() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const { default: KnxDevicesView } = await import('@/views/KnxDevicesView.vue')
  const wrapper = mount(KnxDevicesView, {
    global: {
      plugins: [pinia],
      stubs: {
        RouterLink: {
          props: ['to'],
          template: '<a :data-to="JSON.stringify(to)"><slot /></a>',
        },
        HierarchyCombobox: {
          name: 'HierarchyCombobox',
          props: ['modelValue', 'placeholder'],
          emits: ['update:modelValue'],
          template: '<div><button type="button" data-testid="hierarchy-combobox-pick" @click="$emit(\'update:modelValue\', [\'tree-1:node-kitchen\'])">{{ placeholder }}</button></div>',
        },
      },
    },
  })
  await flushPromises()
  return wrapper
}

function deferred() {
  let resolve
  let reject
  const promise = new Promise((res, rej) => {
    resolve = res
    reject = rej
  })
  return { promise, resolve, reject }
}

describe('KnxDevicesView', () => {
  it('loads and renders KNX devices on mount', async () => {
    const wrapper = await mountView()

    expect(knxprojApi.listDevices).toHaveBeenCalledWith({
      q: '',
      manufacturer: '',
      order_number: '',
      hierarchy_node_id: '',
      page: 0,
      size: 25,
    })
    expect(wrapper.find('[data-testid="knx-device-row-1.1.1"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('Kitchen Switch')
    expect(wrapper.text()).toContain('Siemens')
    expect(wrapper.find('[data-testid="knx-devices-import-link"]').attributes('data-to')).toContain('importexport')
  })

  it('renders datapoints bound to a selected KNX device communication object', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-testid="knx-device-row-1.1.1"]').trigger('click')
    await flushPromises()

    const detailText = wrapper.find('[data-testid="knx-device-detail"]').text()
    expect(detailText).toContain('Gebundene Datenpunkte')
    expect(detailText).toContain('Kitchen Light')
    expect(detailText).toContain('dp-1')
    expect(detailText).toContain('Lesen/Schreiben')
    expect(wrapper.find('[data-testid="knx-device-bound-datapoint"]').exists()).toBe(true)
  })

  it('renders dashes for missing optional device fields', async () => {
    knxprojApi.listDevices.mockResolvedValueOnce({
      data: {
        items: [{ pa: '1.1.9', name: '', manufacturer: null, order_number: '', app_ref: null }],
        total: 1,
        page: 0,
        size: 25,
        pages: 1,
      },
    })

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="knx-device-row-1.1.9"]').text()).toContain('—')
  })

  it('renders hierarchy chips with the configured shortened display path', async () => {
    knxprojApi.listDevices.mockResolvedValueOnce({
      data: {
        items: [
          {
            pa: '1.1.5',
            name: 'Kitchen Sensor',
            manufacturer: 'MDT',
            order_number: 'BE-04001',
            app_ref: 'APP',
            hierarchy_links: [
              {
                tree_id: 'tree-1',
                tree_name: 'Gebäude',
                node_id: 'node-kitchen',
                node_name: 'Küche',
                node_path: ['Haus', 'EG'],
                display_depth: 2,
              },
            ],
          },
        ],
        total: 1,
        page: 0,
        size: 25,
        pages: 1,
      },
    })

    const wrapper = await mountView()
    const rowText = wrapper.find('[data-testid="knx-device-row-1.1.5"]').text()

    expect(rowText).toContain('EG')
    expect(rowText).toContain('Küche')
    expect(rowText).not.toContain('Gebäude')
    expect(rowText).not.toContain('Haus')
  })

  it('applies filters and resets to the first page', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-testid="knx-devices-search"]').setValue('kitchen')
    await wrapper.find('[data-testid="knx-devices-manufacturer"]').setValue('siemens')
    await wrapper.find('[data-testid="knx-devices-order-number"]').setValue('5WG')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(knxprojApi.listDevices).toHaveBeenLastCalledWith({
      q: 'kitchen',
      manufacturer: 'siemens',
      order_number: '5WG',
      hierarchy_node_id: '',
      page: 0,
      size: 25,
    })
  })

  it('applies hierarchy filters', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-testid="hierarchy-combobox-pick"]').trigger('click')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(knxprojApi.listDevices).toHaveBeenLastCalledWith({
      q: '',
      manufacturer: '',
      order_number: '',
      hierarchy_node_id: 'node-kitchen',
      page: 0,
      size: 25,
    })
  })

  it('ignores stale device-list responses from older requests', async () => {
    const initialLoad = deferred()
    knxprojApi.listDevices
      .mockReturnValueOnce(initialLoad.promise)
      .mockResolvedValueOnce({
        data: {
          items: [{ pa: '1.1.2', name: 'Filtered Device' }],
          total: 1,
          page: 0,
          size: 25,
          pages: 1,
        },
      })

    const wrapper = await mountView()
    await wrapper.find('[data-testid="knx-devices-search"]').setValue('filtered')
    await wrapper.find('form').trigger('submit')
    await flushPromises()

    expect(wrapper.find('[data-testid="knx-device-row-1.1.2"]').exists()).toBe(true)

    initialLoad.resolve({
      data: {
        items: [{ pa: '1.1.1', name: 'Stale Initial Device' }],
        total: 1,
        page: 0,
        size: 25,
        pages: 1,
      },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="knx-device-row-1.1.2"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="knx-device-row-1.1.1"]').exists()).toBe(false)
  })

  it('pages through device results within available bounds', async () => {
    knxprojApi.listDevices.mockResolvedValueOnce({
      data: {
        items: [{ pa: '1.1.1', name: 'First page' }],
        total: 50,
        page: 0,
        size: 25,
        pages: 2,
      },
    })
    knxprojApi.listDevices.mockResolvedValueOnce({
      data: {
        items: [{ pa: '1.1.26', name: 'Second page' }],
        total: 50,
        page: 1,
        size: 25,
        pages: 2,
      },
    })

    const wrapper = await mountView()
    await wrapper.find('[data-testid="knx-devices-next"]').trigger('click')
    await flushPromises()

    expect(knxprojApi.listDevices).toHaveBeenLastCalledWith(expect.objectContaining({ page: 1 }))
    expect(wrapper.find('[data-testid="knx-device-row-1.1.26"]').exists()).toBe(true)

    await wrapper.find('[data-testid="knx-devices-next"]').trigger('click')
    await flushPromises()
    expect(knxprojApi.listDevices).toHaveBeenCalledTimes(2)

    knxprojApi.listDevices.mockResolvedValueOnce({
      data: {
        items: [{ pa: '1.1.1', name: 'First page' }],
        total: 50,
        page: 0,
        size: 25,
        pages: 2,
      },
    })
    await wrapper.find('[data-testid="knx-devices-prev"]').trigger('click')
    await flushPromises()
    expect(knxprojApi.listDevices).toHaveBeenLastCalledWith(expect.objectContaining({ page: 0 }))
    expect(knxprojApi.listDevices).toHaveBeenCalledTimes(3)
  })

  it('loads device details when a row is selected', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-testid="knx-device-row-1.1.1"]').trigger('click')
    await flushPromises()

    expect(knxprojApi.getDevice).toHaveBeenCalledWith('1.1.1')
    expect(wrapper.find('[data-testid="knx-device-detail"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('DPT1.001')
    expect(wrapper.text()).toContain('1/2/3')
  })

  it('saves hierarchy assignments for the selected device', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-testid="knx-device-row-1.1.1"]').trigger('click')
    await flushPromises()
    await wrapper.findAll('[data-testid="hierarchy-combobox-pick"]')[1].trigger('click')
    await wrapper.find('[data-testid="knx-device-save-hierarchy-links"]').trigger('click')
    await flushPromises()

    expect(knxprojApi.setDeviceHierarchyLinks).toHaveBeenCalledWith('1.1.1', {
      node_ids: ['node-kitchen'],
    })
    expect(wrapper.text()).toContain('Küche')
  })

  it('does not overwrite the active detail panel with a stale hierarchy save response', async () => {
    knxprojApi.listDevices.mockResolvedValueOnce({
      data: {
        items: [
          { pa: '1.1.1', name: 'Kitchen Switch', manufacturer: 'Siemens', hierarchy_links: [] },
          { pa: '1.1.2', name: 'Hall Dimmer', manufacturer: 'Gira', hierarchy_links: [] },
        ],
        total: 2,
        page: 0,
        size: 25,
        pages: 1,
      },
    })
    knxprojApi.getDevice
      .mockResolvedValueOnce({
        data: {
          pa: '1.1.1',
          name: 'Kitchen Switch',
          manufacturer: 'Siemens',
          hierarchy_links: [],
          comm_objects: [],
        },
      })
      .mockResolvedValueOnce({
        data: {
          pa: '1.1.2',
          name: 'Hall Dimmer',
          manufacturer: 'Gira',
          hierarchy_links: [],
          comm_objects: [],
        },
      })
    const saveResponse = deferred()
    knxprojApi.setDeviceHierarchyLinks.mockReturnValueOnce(saveResponse.promise)

    const wrapper = await mountView()
    await wrapper.find('[data-testid="knx-device-row-1.1.1"]').trigger('click')
    await flushPromises()
    await wrapper.findAll('[data-testid="hierarchy-combobox-pick"]')[1].trigger('click')
    await wrapper.find('[data-testid="knx-device-save-hierarchy-links"]').trigger('click')
    await wrapper.find('[data-testid="knx-device-row-1.1.2"]').trigger('click')
    await flushPromises()

    saveResponse.resolve({
      data: {
        pa: '1.1.1',
        name: 'Kitchen Switch',
        manufacturer: 'Siemens',
        hierarchy_links: [
          { tree_id: 'tree-1', tree_name: 'Gebäude', node_id: 'node-kitchen', node_name: 'Küche' },
        ],
        comm_objects: [],
      },
    })
    await flushPromises()

    const detailText = wrapper.find('[data-testid="knx-device-detail"]').text()
    expect(detailText).toContain('1.1.2')
    expect(detailText).toContain('Hall Dimmer')
    expect(detailText).not.toContain('Kitchen Switch')
    expect(wrapper.find('[data-testid="knx-device-row-1.1.1"]').text()).toContain('Küche')
  })

  it('shows detail errors and clears the current detail panel', async () => {
    knxprojApi.getDevice.mockRejectedValueOnce({
      response: { data: { detail: 'Detail offline' } },
    })
    const wrapper = await mountView()

    await wrapper.find('[data-testid="knx-device-row-1.1.1"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="knx-devices-error"]').text()).toContain('Detail offline')
    expect(wrapper.find('[data-testid="knx-device-detail-empty"]').exists()).toBe(true)
  })

  it('uses the localized fallback message when device detail loading fails without API detail', async () => {
    knxprojApi.getDevice.mockRejectedValueOnce(new Error('network'))
    const wrapper = await mountView()

    await wrapper.find('[data-testid="knx-device-row-1.1.1"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="knx-devices-error"]').text()).toContain('KNX-Gerät konnte nicht geladen werden')
  })

  it('clears selected detail when the refreshed page no longer contains that device', async () => {
    const wrapper = await mountView()
    await wrapper.find('[data-testid="knx-device-row-1.1.1"]').trigger('click')
    await flushPromises()
    expect(wrapper.find('[data-testid="knx-device-detail"]').exists()).toBe(true)

    knxprojApi.listDevices.mockResolvedValueOnce({
      data: {
        items: [{ pa: '1.1.2', name: 'Hall Dimmer' }],
        total: 1,
        page: 0,
        size: 25,
        pages: 1,
      },
    })
    await wrapper.find('[data-testid="knx-devices-refresh"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="knx-device-detail-empty"]').exists()).toBe(true)
  })

  it('invalidates pending detail loads when refreshed results no longer contain the device', async () => {
    const pendingDetail = deferred()
    knxprojApi.getDevice.mockReturnValueOnce(pendingDetail.promise)
    const wrapper = await mountView()

    await wrapper.find('[data-testid="knx-device-row-1.1.1"]').trigger('click')

    knxprojApi.listDevices.mockResolvedValueOnce({
      data: {
        items: [{ pa: '1.1.2', name: 'Hall Dimmer' }],
        total: 1,
        page: 0,
        size: 25,
        pages: 1,
      },
    })
    await wrapper.find('[data-testid="knx-devices-refresh"]').trigger('click')
    await flushPromises()

    pendingDetail.resolve({
      data: {
        pa: '1.1.1',
        name: 'Kitchen Switch',
        comm_objects: [],
      },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="knx-device-row-1.1.2"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="knx-device-detail-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="knx-device-detail"]').exists()).toBe(false)
  })

  it('shows an empty state when no devices are available', async () => {
    knxprojApi.listDevices.mockResolvedValueOnce({
      data: { items: [], total: 0, page: 0, size: 25, pages: 1 },
    })

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="knx-devices-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="knx-devices-empty-import-link"]').exists()).toBe(true)
  })

  it('hides KNX project import actions for non-admin users', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u2', username: 'user', is_admin: false }

    knxprojApi.listDevices.mockResolvedValueOnce({
      data: { items: [], total: 0, page: 0, size: 25, pages: 1 },
    })

    const { default: KnxDevicesView } = await import('@/views/KnxDevicesView.vue')
    const wrapper = mount(KnxDevicesView, {
      global: {
        plugins: [pinia],
        stubs: {
          RouterLink: {
            props: ['to'],
            template: '<a :data-to="JSON.stringify(to)"><slot /></a>',
          },
          HierarchyCombobox: true,
        },
      },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="knx-devices-import-link"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="knx-devices-empty"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="knx-devices-empty-import-link"]').exists()).toBe(false)
  })

  it('keeps existing device rows visible when refresh fails', async () => {
    const wrapper = await mountView()

    knxprojApi.listDevices.mockRejectedValueOnce({
      response: { data: { detail: 'Gateway timeout' } },
    })
    await wrapper.find('[data-testid="knx-devices-refresh"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="knx-devices-error"]').text()).toContain('Gateway timeout')
    expect(wrapper.find('[data-testid="knx-device-row-1.1.1"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="knx-devices-empty"]').exists()).toBe(false)
  })

  it('suppresses the import empty state when the initial device load fails', async () => {
    knxprojApi.listDevices.mockRejectedValueOnce({
      response: { data: { detail: 'API unavailable' } },
    })

    const wrapper = await mountView()

    expect(wrapper.find('[data-testid="knx-devices-error"]').text()).toContain('API unavailable')
    expect(wrapper.find('[data-testid="knx-devices-empty"]').exists()).toBe(false)
    expect(wrapper.find('[data-testid="knx-devices-error-empty"]').exists()).toBe(true)
  })

  it('ignores stale device detail responses', async () => {
    knxprojApi.listDevices.mockResolvedValueOnce({
      data: {
        items: [
          { pa: '1.1.1', name: 'Kitchen Switch', manufacturer: 'Siemens' },
          { pa: '1.1.2', name: 'Hall Dimmer', manufacturer: 'Gira' },
        ],
        total: 2,
        page: 0,
        size: 25,
        pages: 1,
      },
    })
    const firstDetail = deferred()
    knxprojApi.getDevice
      .mockReturnValueOnce(firstDetail.promise)
      .mockResolvedValueOnce({
        data: {
          pa: '1.1.2',
          name: 'Hall Dimmer',
          manufacturer: 'Gira',
          comm_objects: [],
        },
      })

    const wrapper = await mountView()
    await wrapper.find('[data-testid="knx-device-row-1.1.1"]').trigger('click')
    await wrapper.find('[data-testid="knx-device-row-1.1.2"]').trigger('click')
    await flushPromises()

    firstDetail.resolve({
      data: {
        pa: '1.1.1',
        name: 'Kitchen Switch',
        manufacturer: 'Siemens',
        comm_objects: [],
      },
    })
    await flushPromises()

    expect(wrapper.find('[data-testid="knx-device-detail"]').text()).toContain('1.1.2')
    expect(wrapper.find('[data-testid="knx-device-detail"]').text()).toContain('Hall Dimmer')
    expect(wrapper.find('[data-testid="knx-device-detail"]').text()).not.toContain('Kitchen Switch')
  })

  it('clears the selected device detail panel', async () => {
    const wrapper = await mountView()

    await wrapper.find('[data-testid="knx-device-row-1.1.1"]').trigger('click')
    await flushPromises()
    await wrapper.find('[data-testid="knx-device-clear-detail"]').trigger('click')
    await flushPromises()

    expect(wrapper.find('[data-testid="knx-device-detail-empty"]').exists()).toBe(true)
  })
})
