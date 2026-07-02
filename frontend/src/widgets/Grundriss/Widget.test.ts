// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { defineComponent } from 'vue'
import { WidgetRegistry } from '@/widgets/registry'
import GrundrissWidget from './Widget.vue'

vi.mock('@/stores/datapoints', () => ({
  useDatapointsStore: () => ({
    getValue: vi.fn(() => null),
  }),
}))

const MiniTarget = defineComponent({
  props: {
    pageId: { type: String, default: null },
    sessionToken: { type: String, default: null },
  },
  template: '<div data-testid="mini-target" :data-page-id="pageId" :data-session-token="sessionToken"></div>',
})

beforeEach(() => {
  vi.stubGlobal(
    'ResizeObserver',
    class {
      observe() {}
      disconnect() {}
    },
  )
  WidgetRegistry.register({
    type: 'MiniTarget',
    label: 'MiniTarget',
    icon: '',
    minW: 1,
    minH: 1,
    defaultW: 1,
    defaultH: 1,
    component: MiniTarget,
    configComponent: MiniTarget,
    defaultConfig: {},
    compatibleTypes: ['*'],
  })
})

describe('Grundriss mini widget context', () => {
  it('forwards source page context to non-camera mini widgets', () => {
    const wrapper = mount(GrundrissWidget, {
      props: {
        config: {
          image: 'data:image/png;base64,AA==',
          imageNaturalW: 100,
          imageNaturalH: 100,
          miniWidgets: [
            {
              id: 'mini-1',
              label: 'Mini',
              widgetType: 'MiniTarget',
              config: {},
              datapointId: null,
              statusDatapointId: null,
              x: 50,
              y: 50,
              wPx: 40,
              hPx: 40,
              visible: true,
            },
          ],
        },
        datapointId: null,
        value: null,
        statusValue: null,
        editorMode: false,
        pageId: 'source-page',
        sessionToken: 'session-1',
      },
      global: {
        mocks: {
          $t: (key: string) => key,
        },
      },
    })

    const mini = wrapper.get('[data-testid="mini-target"]')
    expect(mini.attributes('data-page-id')).toBe('source-page')
    expect(mini.attributes('data-session-token')).toBe('session-1')
  })
})
