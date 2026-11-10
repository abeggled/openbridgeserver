// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { defineComponent, reactive, ref } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { VisuNode } from '@/types'
import { usePageNavigation } from './usePageNavigation'

const router = vi.hoisted(() => ({ push: vi.fn(), back: vi.fn() }))
const route = reactive({ params: { id: 'current' }, fullPath: '/current' })

vi.mock('vue-router', () => ({
  useRouter: () => router,
  useRoute: () => route,
}))

function node(id: string, type: 'PAGE' | 'LOCATION', parent_id: string | null, access: string | null): VisuNode {
  return { id, type, parent_id, access, name: id } as unknown as VisuNode
}

const store = vi.hoisted(() => ({
  nodes: {} as Record<string, unknown>,
  children: {} as Record<string, unknown[]>,
  sessions: new Set<string>(),
  isLoggedIn: false,
}))

vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({
    getNode: (id: string) => store.nodes[id],
    getChildren: (id: string) => store.children[id] ?? [],
    hasSessionToken: (id: string) => store.sessions.has(id),
    get isLoggedIn() { return store.isLoggedIn },
  }),
}))

function setup(targetId: string, historyBack = false, editorMode = false) {
  let api!: ReturnType<typeof usePageNavigation>
  const Host = defineComponent({
    setup() {
      api = usePageNavigation({ targetId: ref(targetId), historyBack: () => historyBack, editorMode })
      return () => null
    },
  })
  mount(Host)
  return api
}

beforeEach(() => {
  store.nodes = {}
  store.children = {}
  store.sessions = new Set()
  store.isLoggedIn = false
})

afterEach(() => {
  router.push.mockReset()
  router.back.mockReset()
  window.history.replaceState(null, '')
})

describe('usePageNavigation', () => {
  it('opens a page directly', () => {
    store.nodes.p = node('p', 'PAGE', null, null)
    setup('p').navigate()
    expect(router.push).toHaveBeenCalledWith({ name: 'viewer', params: { id: 'p' } })
  })

  it('opens an unknown target id as a page', () => {
    setup('gone').navigate()
    expect(router.push).toHaveBeenCalledWith({ name: 'viewer', params: { id: 'gone' } })
  })

  it('opens the first visible page of an area', () => {
    store.nodes.loc = node('loc', 'LOCATION', null, 'public')
    store.children.loc = [
      node('sub', 'LOCATION', 'loc', null),
      node('private', 'PAGE', 'loc', 'user'),
      node('first', 'PAGE', 'loc', null),
    ]
    store.nodes.private = store.children.loc[1]
    store.nodes.first = store.children.loc[2]

    setup('loc').navigate()

    expect(router.push).toHaveBeenCalledWith({ name: 'viewer', params: { id: 'first' } })
  })

  it('includes user-only pages of an area once logged in', () => {
    store.isLoggedIn = true
    store.nodes.loc = node('loc', 'LOCATION', null, null)
    store.children.loc = [node('private', 'PAGE', 'loc', 'user')]

    setup('loc').navigate()

    expect(router.push).toHaveBeenCalledWith({ name: 'viewer', params: { id: 'private' } })
  })

  it('opens the area itself when it has no visible page', () => {
    store.nodes.loc = node('loc', 'LOCATION', null, null)
    setup('loc').navigate()
    expect(router.push).toHaveBeenCalledWith({ name: 'viewer', params: { id: 'loc' } })
  })

  it('opens a PIN-protected area itself without a session, so the viewer asks for the PIN', () => {
    store.nodes.root = node('root', 'LOCATION', null, 'protected')
    store.nodes.loc = node('loc', 'LOCATION', 'root', null)
    store.children.loc = [node('first', 'PAGE', 'loc', null)]

    setup('loc').navigate()

    expect(router.push).toHaveBeenCalledWith({ name: 'viewer', params: { id: 'loc' } })
  })

  it('opens the first page of a PIN-protected area with a session', () => {
    store.nodes.root = node('root', 'LOCATION', null, 'protected')
    store.nodes.loc = node('loc', 'LOCATION', 'root', null)
    store.children.loc = [node('first', 'PAGE', 'loc', null)]
    store.sessions.add('root')

    setup('loc').navigate()

    expect(router.push).toHaveBeenCalledWith({ name: 'viewer', params: { id: 'first' } })
  })

  it('goes back when history back is set and there is history', () => {
    window.history.replaceState({ back: '/prev' }, '')
    const nav = setup('p', true)
    expect(nav.canGoBack.value).toBe(true)
    expect(nav.hasDestination.value).toBe(true)

    nav.navigate()

    expect(router.back).toHaveBeenCalledTimes(1)
    expect(router.push).not.toHaveBeenCalled()
  })

  it('has no destination and does nothing without a target and without history', () => {
    const nav = setup('', true)
    expect(nav.hasDestination.value).toBe(false)

    nav.navigate()

    expect(router.back).not.toHaveBeenCalled()
    expect(router.push).not.toHaveBeenCalled()
  })

  it('never navigates and reports no history in the editor', () => {
    window.history.replaceState({ back: '/prev' }, '')
    const nav = setup('p', true, true)
    expect(nav.canGoBack.value).toBe(false)

    nav.navigate()

    expect(router.back).not.toHaveBeenCalled()
    expect(router.push).not.toHaveBeenCalled()
  })
})
