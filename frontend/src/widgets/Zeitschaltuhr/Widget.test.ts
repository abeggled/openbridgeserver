// @vitest-environment jsdom
/**
 * Issue #1008 — the output badge must only be coloured green/grey (active/inactive)
 * for boolean objects. A window position of 50 % must not read as "active".
 */
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { datapoints } from '@/api/client'
import { useDatapointsStore } from '@/stores/datapoints'
import type { DataPointValue } from '@/types'
import ZeitschaltuhrWidget from './Widget.vue'

const apiMocks = vi.hoisted(() => ({
  listBindings: vi.fn(),
  updateBinding: vi.fn(),
  get: vi.fn(),
  getJwt: vi.fn(() => 'jwt'),
}))

vi.mock('@/api/client', () => ({
  datapoints: {
    listBindings: apiMocks.listBindings,
    updateBinding: apiMocks.updateBinding,
    get: apiMocks.get,
  },
  getJwt: apiMocks.getJwt,
}))

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: () => ({
    onMessage: vi.fn(),
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
  }),
}))

const listBindingsMock = vi.mocked(datapoints.listBindings)
const getMock = vi.mocked(datapoints.get)

let wrapper: VueWrapper | null = null

const ACTIVE_CLASS = 'bg-green-100'
const NEUTRAL_CLASS = 'bg-gray-200'

function datapoint(dataType: string) {
  return {
    id: 'dp-1',
    name: 'Fensterposition',
    data_type: dataType,
    unit: null,
    tags: [],
    mqtt_topic: 'dp/dp-1/value',
    mqtt_alias: null,
    created_at: '2026-06-11T00:00:00Z',
    updated_at: '2026-06-11T00:00:00Z',
  }
}

function value(v: unknown): DataPointValue {
  return { id: 'dp-1', v, u: null, t: '2026-06-11T08:00:00Z', q: 'good' }
}

async function mountWidget(dataType: string, live: unknown) {
  listBindingsMock.mockResolvedValue([] as never)
  getMock.mockResolvedValue(datapoint(dataType) as never)

  const store = useDatapointsStore()
  store.values['dp-1'] = value(live)

  wrapper = mount(ZeitschaltuhrWidget, {
    props: {
      config: { label: 'Fenster', datapoint_id: 'dp-1', instance_id: 'zsu-1', mode: 'full' },
      datapointId: 'dp-1',
      value: null,
      statusValue: null,
      editorMode: false,
    },
    global: {
      mocks: { $t: (key: string) => key },
      stubs: { Teleport: true, ZeitschaltuhrAddRemoveModal: true },
    },
  })
  await flushPromises()
  return wrapper
}

function badge(w: VueWrapper) {
  return w.findAll('span').find((s) => s.classes().some((c) => c.startsWith('bg-gray-') || c.startsWith('bg-green-')))!
}

beforeEach(() => {
  setActivePinia(createPinia())
  apiMocks.getJwt.mockReturnValue('jwt')
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.clearAllMocks()
})

describe('Zeitschaltuhr widget — output badge colouring', () => {
  it('colours a true BOOLEAN value as active', async () => {
    const w = await mountWidget('BOOLEAN', true)
    expect(badge(w).classes()).toContain(ACTIVE_CLASS)
    expect(w.text()).toContain('true')
  })

  it('colours a false BOOLEAN value as inactive, not neutral', async () => {
    const w = await mountWidget('BOOLEAN', false)
    expect(badge(w).classes()).toContain('bg-gray-100')
  })

  it('accepts 1/0 on a BOOLEAN object', async () => {
    expect(badge(await mountWidget('BOOLEAN', 1)).classes()).toContain(ACTIVE_CLASS)
  })

  it.each([
    ['FLOAT', 50],
    ['INTEGER', 1],
    ['STRING', 'on'],
    ['DATE', '2026-12-24'],
  ])('shows %s values neutrally instead of green "active"', async (dataType, live) => {
    const w = await mountWidget(dataType, live)
    expect(badge(w).classes()).toContain(NEUTRAL_CLASS)
    expect(badge(w).classes()).not.toContain(ACTIVE_CLASS)
    expect(w.text()).toContain(String(live))
  })

  it('still colours a genuinely boolean value on an UNKNOWN object', async () => {
    expect(badge(await mountWidget('UNKNOWN', true)).classes()).toContain(ACTIVE_CLASS)
  })

  it('shows a non-boolean value on an UNKNOWN object neutrally', async () => {
    expect(badge(await mountWidget('UNKNOWN', 50)).classes()).toContain(NEUTRAL_CLASS)
  })

  it('falls back to UNKNOWN when the datapoint cannot be loaded', async () => {
    listBindingsMock.mockResolvedValue([] as never)
    getMock.mockRejectedValue(new Error('boom'))
    const store = useDatapointsStore()
    store.values['dp-1'] = value(50)

    wrapper = mount(ZeitschaltuhrWidget, {
      props: {
        config: { label: 'Fenster', datapoint_id: 'dp-1', instance_id: 'zsu-1', mode: 'full' },
        datapointId: 'dp-1',
        value: null,
        statusValue: null,
        editorMode: false,
      },
      global: { mocks: { $t: (key: string) => key }, stubs: { Teleport: true, ZeitschaltuhrAddRemoveModal: true } },
    })
    await flushPromises()
    expect(badge(wrapper).classes()).toContain(NEUTRAL_CLASS)
  })

  it('does not fetch the datapoint in editor mode', async () => {
    listBindingsMock.mockResolvedValue([] as never)
    getMock.mockResolvedValue(datapoint('FLOAT') as never)
    wrapper = mount(ZeitschaltuhrWidget, {
      props: {
        config: { label: 'Fenster', datapoint_id: 'dp-1', instance_id: 'zsu-1', mode: 'full' },
        datapointId: 'dp-1',
        value: null,
        statusValue: null,
        editorMode: true,
      },
      global: { mocks: { $t: (key: string) => key }, stubs: { Teleport: true, ZeitschaltuhrAddRemoveModal: true } },
    })
    await flushPromises()
    expect(getMock).not.toHaveBeenCalled()
  })
})

describe('Zeitschaltuhr widget — anonymous viewers', () => {
  it('does not request the datapoint without a JWT (would trigger the login redirect)', async () => {
    apiMocks.getJwt.mockReturnValue('')
    listBindingsMock.mockResolvedValue([] as never)
    getMock.mockResolvedValue(datapoint('FLOAT') as never)
    const store = useDatapointsStore()
    store.values['dp-1'] = value(50)

    wrapper = mount(ZeitschaltuhrWidget, {
      props: {
        config: { label: 'Fenster', datapoint_id: 'dp-1', instance_id: 'zsu-1', mode: 'full' },
        datapointId: 'dp-1',
        value: null,
        statusValue: null,
        editorMode: false,
      },
      global: { mocks: { $t: (key: string) => key }, stubs: { Teleport: true, ZeitschaltuhrAddRemoveModal: true } },
    })
    await flushPromises()

    expect(getMock).not.toHaveBeenCalled()
    expect(badge(wrapper).classes()).toContain(NEUTRAL_CLASS)
  })

  it('still colours a boolean value without a JWT', async () => {
    apiMocks.getJwt.mockReturnValue('')
    listBindingsMock.mockResolvedValue([] as never)
    const store = useDatapointsStore()
    store.values['dp-1'] = value(true)

    wrapper = mount(ZeitschaltuhrWidget, {
      props: {
        config: { label: 'Fenster', datapoint_id: 'dp-1', instance_id: 'zsu-1', mode: 'full' },
        datapointId: 'dp-1',
        value: null,
        statusValue: null,
        editorMode: false,
      },
      global: { mocks: { $t: (key: string) => key }, stubs: { Teleport: true, ZeitschaltuhrAddRemoveModal: true } },
    })
    await flushPromises()

    expect(badge(wrapper).classes()).toContain(ACTIVE_CLASS)
  })
})
