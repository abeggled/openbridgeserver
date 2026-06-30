// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { datapoints } from '@/api/client'
import ZeitschaltuhrAddRemoveModal from './ZeitschaltuhrAddRemoveModal.vue'

const apiMocks = vi.hoisted(() => ({
  listBindings: vi.fn(),
  updateBinding: vi.fn(),
  deleteBinding: vi.fn(),
  createBinding: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  datapoints: apiMocks,
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) => {
      if (key === 'common.yes') return 'Yes'
      if (key === 'common.no') return 'No'
      if (key === 'zst.deleteConfirm') return `Delete ${params?.label}?`
      return key
    },
  }),
}))

const listBindingsMock = vi.mocked(datapoints.listBindings)
const deleteBindingMock = vi.mocked(datapoints.deleteBinding)

let wrapper: VueWrapper | null = null

function binding(id: string, adapterInstanceId: string, adapterType: string, value: string) {
  return {
    id,
    datapoint_id: 'dp-1',
    adapter_type: adapterType,
    adapter_instance_id: adapterInstanceId,
    instance_name: adapterType,
    direction: 'SOURCE',
    config: {
      timer_type: 'daily',
      time_ref: 'absolute',
      hour: 8,
      minute: 15,
      value,
    },
    enabled: true,
    created_at: '2026-06-11T00:00:00Z',
    updated_at: '2026-06-11T00:00:00Z',
  }
}

async function mountModal() {
  wrapper = mount(ZeitschaltuhrAddRemoveModal, {
    props: {
      datapointId: 'dp-1',
      instanceId: 'zsu-instance',
      mode: 'full',
    },
    global: {
      mocks: {
        $t: (key: string, params?: Record<string, unknown>) => {
          if (key === 'common.yes') return 'Yes'
          if (key === 'common.no') return 'No'
          if (key === 'zst.deleteConfirm') return `Delete ${params?.label}?`
          return key
        },
      },
      stubs: {
        Teleport: true,
        ZeitschaltuhrBindingModal: true,
      },
    },
  })
  await flushPromises()
  return wrapper
}

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.clearAllMocks()
  document.body.innerHTML = ''
})

describe('ZeitschaltuhrAddRemoveModal binding filtering', () => {
  it('only exposes bindings for the configured scheduler instance', async () => {
    listBindingsMock.mockResolvedValue([
      binding('zsu-binding', 'zsu-instance', 'ZEITSCHALTUHR', 'zsu-value'),
      binding('knx-binding', 'knx-instance', 'KNX', 'knx-value'),
    ])
    deleteBindingMock.mockResolvedValue(undefined)

    await mountModal()

    expect(wrapper!.findAll('[data-testid="zsu-binding-row"]')).toHaveLength(1)
    expect(wrapper!.text()).toContain('zsu-value')
    expect(wrapper!.text()).not.toContain('knx-value')

    await wrapper!.get('[data-testid="zsu-delete-btn"]').trigger('click')
    await wrapper!.get('[data-testid="zsu-confirm-delete"]').trigger('click')

    expect(deleteBindingMock).toHaveBeenCalledTimes(1)
    expect(deleteBindingMock).toHaveBeenCalledWith('dp-1', 'zsu-binding')
  })
})
