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
  const { reactive } = require('vue') as typeof import('vue')
  const loadBreadcrumb = vi.fn().mockResolvedValue(undefined)
  const store = reactive({
    treeLoaded: true,
    pageConfig: null,
    isAdmin: false,
    nodes: [node] as Array<typeof node>,
    breadcrumb: [] as Array<typeof node>,
    getNode(id: string) { return this.nodes.find(n => n.id === id) },
    loadTree: vi.fn().mockResolvedValue(undefined),
    loadBreadcrumb,
    loadPage: vi.fn().mockResolvedValue(undefined),
    hasSessionToken: () => false,
  })
  return { node, store, loadBreadcrumb, push: vi.fn(), getJwt: vi.fn() }
})

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: mocks.push, currentRoute: { value: { fullPath: '/page-1' } } }),
}))

vi.mock('@/stores/visu', () => ({
  useVisuStore: () => mocks.store,
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
    mocks.store.loadPage.mockClear()
    mocks.store.loadTree.mockClear()
    mocks.loadBreadcrumb.mockReset()
    mocks.loadBreadcrumb.mockResolvedValue(undefined)
    mocks.getJwt.mockReturnValue('jwt-1')
    mocks.node.access = 'user'
    mocks.store.nodes = [mocks.node]
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

  it('redirects a concealed private page whose breadcrumb is already hidden', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    mocks.push.mockClear()

    // Ohne Anmeldung verbirgt das Backend den privaten Knoten mit 404 — ein
    // erneutes load() liefe in den Fehlerpfad, bevor es umleiten könnte.
    mocks.getJwt.mockReturnValue(null)
    mocks.loadBreadcrumb.mockRejectedValue(new Error('common.loadError'))
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

  it('redirects when the node is missing from the anonymously filtered tree', async () => {
    // Kaltstart mit abgelaufenem Token: der private Knoten fehlt im Baum, also
    // liefe resolveAccessNode() auf den Default 'public' hinaus.
    mocks.store.nodes = []
    mocks.loadBreadcrumb.mockRejectedValue(new Error('common.loadError'))
    const wrapper = mountViewer()
    await flushPromises()
    mocks.push.mockClear()

    mocks.getJwt.mockReturnValue(null)
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))
    await flushPromises()

    expect(mocks.push).toHaveBeenCalledWith(expect.objectContaining({ name: 'login' }))
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

  it('retries the load once a renewal makes the node visible', async () => {
    // Kaltstart mit abgelaufenem Token: der Baum kam anonym gefiltert, der
    // private Knoten fehlt und die Seite landet im Fehlerzustand.
    mocks.store.nodes = []
    mocks.loadBreadcrumb.mockRejectedValueOnce(new Error('common.loadError'))
    const wrapper = mountViewer()
    await flushPromises()
    expect(wrapper.text()).toContain('common.loadError')

    // Nach der Erneuerung holt der Store den Baum neu
    mocks.store.nodes = [mocks.node]
    await flushPromises()

    expect(mocks.store.loadPage).toHaveBeenCalledWith('page-1')
    wrapper.unmount()
  })

  it('recovers when the authorized tree arrives before the concealed breadcrumb fails', async () => {
    // Kaltstart mit abgelaufenem Token: die Erneuerung holt den Baum neu,
    // während der erste Lauf noch auf den Breadcrumb wartet. Der Knoten ist
    // dann da, bevor der Fehler gesetzt wird — ohne Vormerkung käme danach
    // keine Änderung mehr, die einen zweiten Versuch auslösen könnte.
    mocks.store.nodes = []
    let rejectBreadcrumb: (error: Error) => void = () => {}
    mocks.loadBreadcrumb.mockImplementationOnce(() => new Promise((_resolve, reject) => {
      rejectBreadcrumb = reject
    }))
    const wrapper = mountViewer()
    await flushPromises()

    mocks.store.nodes = [mocks.node]
    await flushPromises()
    rejectBreadcrumb(new Error('common.loadError'))
    await flushPromises()

    expect(mocks.store.loadPage).toHaveBeenCalledWith('page-1')
    expect(wrapper.text()).not.toContain('common.loadError')
    wrapper.unmount()
  })

  it('returns to the tree when a renewal removes the current page', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    mocks.push.mockClear()

    // Die Berechtigung wurde entzogen: die Erneuerung holt den Baum neu und
    // der angezeigte Knoten fehlt darin.
    mocks.store.nodes = []
    await flushPromises()

    expect(mocks.push).toHaveBeenCalledWith(expect.objectContaining({ name: 'tree' }))
    wrapper.unmount()
  })

  it('stays put when a page switch replaces the node', async () => {
    const wrapper = mountViewer()
    await flushPromises()
    mocks.push.mockClear()

    // Navigation auf einen Knoten, den der Baum (noch) nicht kennt: das ist
    // kein Entzug, sondern lädt bereits über den id-Watcher.
    await wrapper.setProps({ id: 'page-2' })
    await flushPromises()

    expect(mocks.push).not.toHaveBeenCalledWith(expect.objectContaining({ name: 'tree' }))
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
