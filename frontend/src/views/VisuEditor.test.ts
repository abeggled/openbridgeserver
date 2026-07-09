// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import VisuEditor from './VisuEditor.vue'

const mocks = vi.hoisted(() => {
  const previewWidget = {
    props: ['pageId', 'sessionToken'],
    template: '<div data-testid="preview-widget" :data-page-id="pageId" :data-session-token="sessionToken"></div>',
  }
  return {
    getSessionToken: vi.fn(),
    push: vi.fn(),
    loadBackgrounds: vi.fn().mockResolvedValue(undefined),
    store: {
      treeLoaded: true,
      pageConfig: {
        grid_cols: 12,
        grid_row_height: 80,
        grid_cell_width: 80,
        background: null,
        widgets: [
          {
            id: 'camera-1',
            name: '',
            type: 'kamera',
            datapoint_id: null,
            status_datapoint_id: null,
            x: 0,
            y: 0,
            w: 2,
            h: 2,
            config: {
              url: 'http://camera.local/stream',
              useProxy: true,
            },
          },
        ],
      },
      loadTree: vi.fn().mockResolvedValue(undefined),
      loadBreadcrumb: vi.fn().mockResolvedValue(undefined),
      loadPage: vi.fn().mockResolvedValue(undefined),
      savePage: vi.fn().mockResolvedValue(undefined),
      createNode: vi.fn(),
      getNode: vi.fn((id: string) => {
        if (id === 'page-1') return { id: 'page-1', parent_id: 'root-1', type: 'PAGE', access: null, order: 0, name: 'Page' }
        if (id === 'root-1') return { id: 'root-1', parent_id: null, type: 'FOLDER', access: 'protected', order: 0, name: 'Root' }
        return undefined
      }),
      hasSessionToken: vi.fn(),
    },
    previewWidget,
  }
})

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

vi.mock('vue-router', () => ({
  useRouter: () => ({
    push: mocks.push,
    currentRoute: { value: { fullPath: '/visu/page-1/edit' } },
  }),
}))

vi.mock('@/stores/visu', () => ({
  useVisuStore: () => mocks.store,
}))

vi.mock('@/stores/theme', () => ({
  useThemeStore: () => ({ isDark: false, toggle: vi.fn() }),
}))

vi.mock('@/api/client', () => ({
  getSessionToken: mocks.getSessionToken,
  datapoints: { get: vi.fn().mockResolvedValue({}) },
  visuBackgrounds: {
    list: vi.fn().mockResolvedValue({ backgrounds: [] }),
    import: vi.fn(),
    delete: vi.fn(),
  },
}))

vi.mock('@/composables/useLocalizedText', () => ({
  useLocalizedText: () => ({ locale: 'de', widgetLabel: (label: string) => label }),
}))

vi.mock('@/composables/useVisuBackgrounds', () => ({
  useVisuBackgrounds: () => ({
    items: [],
    names: [],
    loading: false,
    error: '',
    loadList: mocks.loadBackgrounds,
    upload: vi.fn(),
    remove: vi.fn(),
  }),
}))

vi.mock('@/widgets/registry', () => ({
  WidgetRegistry: {
    get: vi.fn(() => ({
      component: mocks.previewWidget,
      label: 'Kamera',
      defaultW: 2,
      defaultH: 2,
      minW: 1,
      minH: 1,
      defaultConfig: {},
    })),
    all: vi.fn(() => [{
      type: 'kamera',
      component: mocks.previewWidget,
      label: 'Kamera',
      defaultW: 2,
      defaultH: 2,
      minW: 1,
      minH: 1,
      defaultConfig: {},
    }]),
  },
}))

vi.mock('@/widgets/ValueDisplay/index', () => ({}))
vi.mock('@/widgets/Toggle/index', () => ({}))
vi.mock('@/widgets/ButtonGroup/index', () => ({}))
vi.mock('@/widgets/Slider/index', () => ({}))
vi.mock('@/widgets/Chart/index', () => ({}))
vi.mock('@/widgets/Link/index', () => ({}))
vi.mock('@/widgets/WidgetRef/index', () => ({}))
vi.mock('@/widgets/Info/index', () => ({}))
vi.mock('@/widgets/Text/index', () => ({}))
vi.mock('@/widgets/Zeitschaltuhr/index', () => ({}))
vi.mock('@/widgets/Rolladen/index', () => ({}))
vi.mock('@/widgets/Licht/index', () => ({}))
vi.mock('@/widgets/Fenster/index', () => ({}))
vi.mock('@/widgets/Energiefluss/index', () => ({}))
vi.mock('@/widgets/Kamera/index', () => ({}))
vi.mock('@/widgets/QrCode/index', () => ({}))
vi.mock('@/widgets/IFrame/index', () => ({}))
vi.mock('@/widgets/Uhr/index', () => ({}))
vi.mock('@/widgets/RTR/index', () => ({}))
vi.mock('@/widgets/Wetter/index', () => ({}))
vi.mock('@/widgets/Stufenschalter/index', () => ({}))
vi.mock('@/widgets/Grundriss/index', () => ({}))
vi.mock('@/widgets/HorizontalBar/index', () => ({}))

describe('VisuEditor', () => {
  beforeEach(() => {
    mocks.getSessionToken.mockReset()
    mocks.getSessionToken.mockImplementation((nodeId: string) => (nodeId === 'root-1' ? 'session-root' : null))
    mocks.push.mockReset()
  })

  it('passes inherited protected page sessions to widget previews', async () => {
    const wrapper = mount(VisuEditor, {
      props: { id: 'page-1' },
      global: {
        mocks: {
          $t: (key: string) => key,
        },
        stubs: {
          AuthButton: true,
          Breadcrumb: true,
          DataPointPicker: true,
          MissingWidget: true,
        },
      },
    })

    await flushPromises()

    const preview = wrapper.get('[data-testid="preview-widget"]')
    expect(preview.attributes('data-page-id')).toBe('page-1')
    expect(preview.attributes('data-session-token')).toBe('session-root')
  })
})
