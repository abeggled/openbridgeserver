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
  get: vi.fn(),
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
const createBindingMock = vi.mocked(datapoints.createBinding)
const getMock = vi.mocked(datapoints.get)

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

describe('ZeitschaltuhrAddRemoveModal — switching value of a new schedule point', () => {
  // Codex review on PR #1155: an empty config gets the adapter's default "1" at
  // fire time, which no temporal object can hold — the point used to be stored
  // and then silently dropped at every firing, and the API now rejects it.
  it.each([
    ['DATE', /^\d{4}-\d{2}-\d{2}$/],
    ['TIME', /^00:00:00$/],
    ['DATETIME', /^\d{4}-\d{2}-\d{2}T00:00:00$/],
    ['FLOAT', /^1$/],
  ])('seeds a %s schedule point with a value that type accepts', async (dataType, shape) => {
    listBindingsMock.mockResolvedValue([])
    getMock.mockResolvedValue({ id: 'dp-1', data_type: dataType } as never)
    createBindingMock.mockResolvedValue(binding('new-1', 'zsu-instance', 'ZEITSCHALTUHR', 'x') as never)

    const w = await mountModal()
    await w.get('[data-testid="zsu-add-btn"]').trigger('click')
    await flushPromises()

    const config = createBindingMock.mock.calls[0][1].config as Record<string, string>
    expect(config.value).toMatch(shape as RegExp)
  })

  it('falls back to the untyped default when the object cannot be read', async () => {
    listBindingsMock.mockResolvedValue([])
    getMock.mockRejectedValue(new Error('boom'))
    createBindingMock.mockResolvedValue(binding('new-1', 'zsu-instance', 'ZEITSCHALTUHR', 'x') as never)

    const w = await mountModal()
    await w.get('[data-testid="zsu-add-btn"]').trigger('click')
    await flushPromises()

    expect((createBindingMock.mock.calls[0][1].config as Record<string, string>).value).toBe('1')
  })
})
