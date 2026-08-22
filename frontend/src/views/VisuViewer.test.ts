// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import VisuViewer from './VisuViewer.vue'

const mocks = vi.hoisted(() => {
  const node = {
    id: 'page-1',
    parent_id: null,
    name: 'Privat',
    type: 'PAGE',
    order: 0,
    icon: null,
    access: 'user',
    page_config: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-01T00:00:00Z',
  }
  return {
    node,
    push: vi.fn(),
    getJwt: vi.fn(),
    loadPage: vi.fn().mockResolvedValue(undefined),
  }
})

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.push, currentRoute: { value: { fullPath: '/page-1' } } }),
}))

vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({
    treeLoaded: true,
    pageConfig: null,
    isAdmin: false,
    nodes: [mocks.node],
    breadcrumb: [],
    getNode: (id: string) => (id === mocks.node.id ? mocks.node : undefined),
    loadTree: vi.fn().mockResolvedValue(undefined),
    loadBreadcrumb: vi.fn().mockResolvedValue(undefined),
    loadPage: mocks.loadPage,
    hasSessionToken: () => false,
  }),
}))

vi.mock('@/stores/datapoints', () => ({
  useDatapointsStore: () => ({
    subscribe: vi.fn(),
    fetchInitialValues: vi.fn().mockResolvedValue(undefined),
    values: {},
  }),
}))

vi.mock('@/stores/theme', () => ({ useThemeStore: () => ({ isDark: false, toggle: vi.fn() }) }))

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: () => ({ connect: vi.fn(), disconnect: vi.fn(), connected: { value: true } }),
}))

vi.mock('@/api/client', () => ({
  getJwt: mocks.getJwt,
  getSessionToken: () => null,
  setWriteContext: vi.fn(),
  clearWriteContext: vi.fn(),
  visuBackgrounds: { publicUrl: (n: string) => `/bg/${n}` },
}))

function mountViewer() {
  return mount(VisuViewer, {
    props: { id: 'page-1' },
    global: { mocks: { $t: (key: string) => key }, stubs: { Breadcrumb: true, NodeOverview: true, AuthButton: true } },
  })
}

describe('VisuViewer session end', () => {
  beforeEach(() => {
    mocks.push.mockClear()
    mocks.getJwt.mockReturnValue('jwt-1')
  })

  it('leaves a private page for the login route when the session ends', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    mocks.push.mockClear()

    // Der proaktive Refresh wurde endgültig abgelehnt und hat die Tokens geräumt
    mocks.getJwt.mockReturnValue(null)
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))
    await flushPromises()

    expect(mocks.push).toHaveBeenCalledWith(expect.objectContaining({ name: 'login' }))
    wrapper.unmount()
  })

  it('stays put while the session is still valid', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    mocks.push.mockClear()

    window.dispatchEvent(new CustomEvent('visu:unauthorized'))
    await flushPromises()

    expect(mocks.push).not.toHaveBeenCalled()
    wrapper.unmount()
  })

  it('leaves a public page alone when an unrelated request is rejected', async () => {
    mocks.node.access = 'public'
    const wrapper = mountViewer()
    await flushPromises()
    mocks.push.mockClear()

    mocks.getJwt.mockReturnValue(null)
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))
    await flushPromises()

    expect(mocks.push).not.toHaveBeenCalled()
    mocks.node.access = 'user'
    wrapper.unmount()
  })

  it('ignores the event once the viewer is gone', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    wrapper.unmount()
    mocks.push.mockClear()

    mocks.getJwt.mockReturnValue(null)
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))
    await flushPromises()

    expect(mocks.push).not.toHaveBeenCalled()
  })
})
