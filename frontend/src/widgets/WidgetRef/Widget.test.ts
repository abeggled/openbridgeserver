// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { defineComponent } from 'vue'
import { describe, expect, it, vi } from 'vitest'
import { visu } from '@/api/client'
import { WidgetRegistry } from '@/widgets/registry'
import WidgetRef from './Widget.vue'

vi.mock('@/api/client', () => ({
  visu: {
    getWidgetRef: vi.fn(),
  },
}))

vi.mock('@/stores/datapoints', () => ({
  useDatapointsStore: () => ({
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    fetchInitialValues: vi.fn(),
    getValue: vi.fn(() => null),
  }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string, params?: Record<string, unknown>) =>
      key === 'widgets.widgetref.widgetNotFound' ? `Widget ${params?.name} not found` : key,
  }),
}))

const DummyWidget = defineComponent({
  props: {
    readonly: Boolean,
  },
  template: '<div data-testid="dummy-widget" :data-readonly="readonly"></div>',
})

WidgetRegistry.register({
  type: 'DummyReadonlyWidget',
  label: 'Dummy',
  icon: 'D',
  minW: 1,
  minH: 1,
  defaultW: 1,
  defaultH: 1,
  component: DummyWidget,
  configComponent: DummyWidget,
  defaultConfig: {},
  compatibleTypes: ['*'],
})

describe('WidgetRef Widget.vue', () => {
  it('forwards readonly mode to referenced widgets', async () => {
    vi.mocked(visu.getWidgetRef).mockResolvedValue([
      {
        id: 'source-widget',
        name: 'Archive',
        type: 'DummyReadonlyWidget',
        datapoint_id: null,
        status_datapoint_id: null,
        x: 0,
        y: 0,
        w: 1,
        h: 1,
        config: {},
      },
    ])

    const wrapper = mount(WidgetRef, {
      props: {
        config: { source_page_id: 'source-page', source_widget_name: 'Archive' },
        datapointId: null,
        value: null,
        statusValue: null,
        editorMode: false,
        readonly: true,
      },
      global: {
        mocks: {
          $t: (key: string) => key,
        },
      },
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="dummy-widget"]').attributes('data-readonly')).toBe('true')
  })

  it('forwards readonly mode from the referenced source page', async () => {
    vi.mocked(visu.getWidgetRef).mockResolvedValue([
      {
        id: 'source-widget',
        name: 'Archive',
        type: 'DummyReadonlyWidget',
        datapoint_id: null,
        status_datapoint_id: null,
        x: 0,
        y: 0,
        w: 1,
        h: 1,
        config: {},
        source_page_readonly: true,
      },
    ])

    const wrapper = mount(WidgetRef, {
      props: {
        config: { source_page_id: 'source-page', source_widget_name: 'Archive' },
        datapointId: null,
        value: null,
        statusValue: null,
        editorMode: false,
        readonly: false,
      },
      global: {
        mocks: {
          $t: (key: string) => key,
        },
      },
    })
    await flushPromises()

    expect(wrapper.get('[data-testid="dummy-widget"]').attributes('data-readonly')).toBe('true')
  })
})
