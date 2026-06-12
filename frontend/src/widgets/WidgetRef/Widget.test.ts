// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { WidgetRegistry } from '@/widgets/registry'
import WidgetRef from './Widget.vue'

const mocks = vi.hoisted(() => ({
  getWidgetRef: vi.fn(),
  fetchInitialValues: vi.fn(),
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
  getValue: vi.fn(),
}))

vi.mock('@/api/client', () => ({
  getSessionToken: (nodeId: string) => {
    const raw = sessionStorage.getItem(`session_${nodeId}`)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed.token as string
  },
  visu: {
    getWidgetRef: mocks.getWidgetRef,
  },
}))

vi.mock('@/stores/datapoints', () => ({
  useDatapointsStore: () => ({
    fetchInitialValues: mocks.fetchInitialValues,
    subscribe: mocks.subscribe,
    unsubscribe: mocks.unsubscribe,
    getValue: mocks.getValue,
  }),
}))

const RefTarget = defineComponent({
  props: {
    config: { type: Object, required: true },
    datapointId: { type: String, default: null },
    value: { type: Object, default: null },
    statusValue: { type: Object, default: null },
    editorMode: { type: Boolean, required: true },
    pageId: { type: String, default: null },
    sessionToken: { type: String, default: null },
  },
  template: '<div data-testid="ref-target" :data-page-id="pageId" :data-session-token="sessionToken"></div>',
})

beforeEach(() => {
  vi.clearAllMocks()
  sessionStorage.clear()
  WidgetRegistry.register({
    type: 'RefTarget',
    label: 'RefTarget',
    icon: '',
    minW: 1,
    minH: 1,
    defaultW: 1,
    defaultH: 1,
    component: RefTarget,
    configComponent: RefTarget,
    defaultConfig: {},
    compatibleTypes: ['*'],
  })
})

describe('WidgetRef source page context', () => {
  it('forwards the source page id to initial values and the rendered widget', async () => {
    mocks.getWidgetRef.mockResolvedValue([
      {
        id: 'widget-1',
        name: 'Referenced Camera',
        type: 'RefTarget',
        datapoint_id: 'dp-1',
        status_datapoint_id: null,
        x: 0,
        y: 0,
        w: 2,
        h: 2,
        config: {},
      },
    ])

    const wrapper = mount(WidgetRef, {
      props: {
        config: {
          source_page_id: 'source-page',
          source_widget_name: 'Referenced Camera',
        },
        datapointId: null,
        value: null,
        statusValue: null,
        editorMode: false,
      },
    })
    await flushPromises()

    expect(mocks.getWidgetRef).toHaveBeenCalledWith('source-page')
    expect(mocks.fetchInitialValues).toHaveBeenCalledWith(['dp-1'], { pageId: 'source-page' })
    expect(wrapper.get('[data-testid="ref-target"]').attributes('data-page-id')).toBe('source-page')
  })

  it('forwards the source page session token when one exists', async () => {
    sessionStorage.setItem(
      'session_source-page',
      JSON.stringify({ token: 'session-1', expiresAt: Date.now() + 60_000 }),
    )
    mocks.getWidgetRef.mockResolvedValue([
      {
        id: 'widget-1',
        name: 'Referenced Chart',
        type: 'RefTarget',
        datapoint_id: 'dp-1',
        status_datapoint_id: null,
        x: 0,
        y: 0,
        w: 2,
        h: 2,
        config: {},
      },
    ])

    const wrapper = mount(WidgetRef, {
      props: {
        config: {
          source_page_id: 'source-page',
          source_widget_name: 'Referenced Chart',
        },
        datapointId: null,
        value: null,
        statusValue: null,
        editorMode: false,
      },
    })
    await flushPromises()

    expect(mocks.fetchInitialValues).toHaveBeenCalledWith(['dp-1'], {
      pageId: 'source-page',
      sessionToken: 'session-1',
    })
    expect(wrapper.get('[data-testid="ref-target"]').attributes('data-session-token')).toBe('session-1')
  })
})
