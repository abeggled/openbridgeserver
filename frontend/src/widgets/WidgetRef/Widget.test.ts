// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { WidgetRegistry } from '@/widgets/registry'
import WidgetRef from './Widget.vue'

const mocks = vi.hoisted(() => ({
  getBreadcrumb: vi.fn(),
  getWidgetRef: vi.fn(),
  fetchInitialValues: vi.fn(),
  subscribe: vi.fn(),
  unsubscribe: vi.fn(),
  getValue: vi.fn(),
  wsConnect: vi.fn(),
  wsDisconnect: vi.fn(),
  wsSubscribe: vi.fn(),
  wsUnsubscribe: vi.fn(),
  wsOnMessage: vi.fn(),
  wsDispatch: vi.fn(),
  messageHandlers: [] as Array<(msg: Record<string, unknown>) => void>,
}))

vi.mock('@/api/client', () => ({
  getJwt: () => localStorage.getItem('visu_jwt'),
  getSessionToken: (nodeId: string) => {
    const raw = sessionStorage.getItem(`session_${nodeId}`)
    if (!raw) return null
    const parsed = JSON.parse(raw)
    return parsed.token as string
  },
  visu: {
    getBreadcrumb: mocks.getBreadcrumb,
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

vi.mock('@/composables/useWebSocket', () => ({
  createWebSocketClient: () => ({
    connected: { value: false },
    connect: mocks.wsConnect,
    disconnect: mocks.wsDisconnect,
    subscribe: mocks.wsSubscribe,
    unsubscribe: mocks.wsUnsubscribe,
    onMessage: mocks.wsOnMessage,
  }),
  useWebSocket: () => ({
    dispatch: mocks.wsDispatch,
  }),
}))

const RefTarget = defineComponent({
  props: {
    config: { type: Object, required: true },
    datapointId: { type: String, default: null },
    value: { type: Object, default: null },
    statusValue: { type: Object, default: null },
    editorMode: { type: Boolean, required: true },
    readonly: { type: Boolean, default: false },
    pageId: { type: String, default: null },
    sessionToken: { type: String, default: null },
    writeContext: { type: Object, default: null },
  },
  template:
    '<div data-testid="ref-target" :data-page-id="pageId" :data-session-token="sessionToken" :data-write-page-id="writeContext && writeContext.pageId" :data-write-session-token="writeContext && writeContext.sessionToken" :data-readonly="readonly" :data-value="value?.v"></div>',
})

  beforeEach(() => {
    vi.clearAllMocks()
    sessionStorage.clear()
    localStorage.clear()
    mocks.messageHandlers.length = 0
    mocks.wsOnMessage.mockImplementation((handler: (msg: Record<string, unknown>) => void) => {
      mocks.messageHandlers.push(handler)
      return vi.fn()
  })
  mocks.getBreadcrumb.mockResolvedValue([{ id: 'source-page', access: 'public' }])
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
        readonly: true,
      },
    })
    await flushPromises()

    expect(mocks.getWidgetRef).toHaveBeenCalledWith('source-page', 'source-page')
    expect(mocks.fetchInitialValues).toHaveBeenCalledWith(['dp-1'], { pageId: 'source-page' })
    expect(mocks.wsConnect).toHaveBeenCalledWith({ pageId: 'source-page', preferPageScope: true })
    expect(mocks.wsSubscribe).toHaveBeenCalledWith(['dp-1'])
    expect(wrapper.get('[data-testid="ref-target"]').attributes('data-page-id')).toBe('source-page')
    expect(wrapper.get('[data-testid="ref-target"]').attributes('data-write-page-id')).toBe('source-page')
    expect(wrapper.get('[data-testid="ref-target"]').attributes('data-readonly')).toBe('true')
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
    expect(mocks.wsConnect).toHaveBeenCalledWith({
      pageId: 'source-page',
      sessionToken: 'session-1',
      preferPageScope: true,
    })
    expect(wrapper.get('[data-testid="ref-target"]').attributes('data-session-token')).toBe('session-1')
    expect(wrapper.get('[data-testid="ref-target"]').attributes('data-write-session-token')).toBe('session-1')
  })

  it('uses the defining protected node session token for inherited source pages', async () => {
    sessionStorage.setItem(
      'session_protected-root',
      JSON.stringify({ token: 'session-root', expiresAt: Date.now() + 60_000 }),
    )
    mocks.getBreadcrumb.mockResolvedValue([
      { id: 'protected-root', access: 'protected' },
      { id: 'source-page', access: null },
    ])
    mocks.getWidgetRef.mockResolvedValue([
      {
        id: 'widget-1',
        name: 'Inherited Widget',
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
          source_widget_name: 'Inherited Widget',
        },
        datapointId: null,
        value: null,
        statusValue: null,
        editorMode: false,
      },
    })
    await flushPromises()

    expect(mocks.getWidgetRef).toHaveBeenCalledWith('source-page', 'protected-root')
    expect(mocks.fetchInitialValues).toHaveBeenCalledWith(['dp-1'], {
      pageId: 'source-page',
      sessionToken: 'session-root',
    })
    expect(mocks.wsConnect).toHaveBeenCalledWith({
      pageId: 'source-page',
      sessionToken: 'session-root',
      preferPageScope: true,
    })
    expect(wrapper.get('[data-testid="ref-target"]').attributes('data-session-token')).toBe('session-root')
  })

  it('keeps JWT transport for protected source pages without a source session token', async () => {
    mocks.getBreadcrumb.mockResolvedValue([
      { id: 'protected-root', access: 'protected' },
      { id: 'source-page', access: null },
    ])
    mocks.getWidgetRef.mockResolvedValue([
      {
        id: 'widget-1',
        name: 'Protected Widget',
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

    mount(WidgetRef, {
      props: {
        config: {
          source_page_id: 'source-page',
          source_widget_name: 'Protected Widget',
        },
        datapointId: null,
        value: null,
        statusValue: null,
        editorMode: false,
      },
    })
    await flushPromises()

    expect(mocks.wsConnect).toHaveBeenCalledWith({ pageId: 'source-page' })
  })

  it('keeps JWT transport for public source pages when the viewer is logged in', async () => {
    localStorage.setItem('visu_jwt', 'jwt-token')
    mocks.getWidgetRef.mockResolvedValue([
      {
        id: 'widget-1',
        name: 'Public Widget',
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

    mount(WidgetRef, {
      props: {
        config: {
          source_page_id: 'source-page',
          source_widget_name: 'Public Widget',
        },
        datapointId: null,
        value: null,
        statusValue: null,
        editorMode: false,
      },
    })
    await flushPromises()

    expect(mocks.wsConnect).toHaveBeenCalledWith({ pageId: 'source-page' })
  })

  it('renders live values received through the source-scoped websocket', async () => {
    mocks.getWidgetRef.mockResolvedValue([
      {
        id: 'widget-1',
        name: 'Referenced Meter',
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
          source_widget_name: 'Referenced Meter',
        },
        datapointId: null,
        value: null,
        statusValue: null,
        editorMode: false,
      },
    })
    await flushPromises()

    mocks.messageHandlers[0]({ id: 'dp-1', v: 42, u: 'W', t: '2026-06-15T00:00:00.000Z', q: 'good' })
    await wrapper.vm.$nextTick()

    expect(wrapper.get('[data-testid="ref-target"]').attributes('data-value')).toBe('42')
  })

  it('forwards source-scoped websocket messages to child widget listeners', async () => {
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

    mount(WidgetRef, {
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

    const msg = { id: 'dp-1', v: 42, u: 'W', t: '2026-06-15T00:00:00.000Z', q: 'good' }
    mocks.messageHandlers[0](msg)

    expect(mocks.wsDispatch).toHaveBeenCalledWith(msg)
  })
})
