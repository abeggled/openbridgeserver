// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { useVisuStore } from './visu'
import { cancelTokenRefresh } from '@/api/client'
import { notifyAuthTokenRefreshed } from '@/utils/authEvents'

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('visu store auth state', () => {
  // Der Store registriert seine Auth-Listener im Setup und entfernt sie nie.
  // Ohne Aufräumen hörte der Store jedes vorherigen Tests weiter mit und
  // verdeckte, ob der aktuelle Store richtig reagiert.
  let registeredListeners: Array<[string, EventListener]> = []

  beforeEach(() => {
    registeredListeners = []
    const addEventListener = window.addEventListener.bind(window)
    vi.spyOn(window, 'addEventListener').mockImplementation((type, handler, options) => {
      registeredListeners.push([type as string, handler as EventListener])
      addEventListener(type as string, handler as EventListener, options)
    })
    setActivePinia(createPinia())
    localStorage.clear()
  })

  afterEach(() => {
    for (const [type, handler] of registeredListeners) window.removeEventListener(type, handler)
    vi.restoreAllMocks()
    cancelTokenRefresh()
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('stores access and refresh token on login', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ id: 'u1', username: 'admin', is_admin: true })))
    const store = useVisuStore()

    await store.login('jwt-1', 'refresh-1')

    expect(localStorage.getItem('visu_jwt')).toBe('jwt-1')
    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-1')
    expect(localStorage.getItem('visu_is_admin')).toBe('1')
    expect(store.isLoggedIn).toBe(true)
    expect(store.isAdmin).toBe(true)
  })

  it('falls back to a non-admin session when the identity lookup fails', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 401 })))
    const store = useVisuStore()

    await store.login('jwt-1', 'refresh-1')

    expect(store.isLoggedIn).toBe(true)
    expect(store.isAdmin).toBe(false)
  })

  it('removes access token, refresh token and admin flag on logout', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ id: 'u1', username: 'admin', is_admin: true })))
    const store = useVisuStore()
    await store.login('jwt-1', 'refresh-1')

    store.logout()

    expect(localStorage.getItem('visu_jwt')).toBeNull()
    expect(localStorage.getItem('visu_refresh_token')).toBeNull()
    expect(localStorage.getItem('visu_is_admin')).toBeNull()
    expect(store.isLoggedIn).toBe(false)
    expect(store.isAdmin).toBe(false)
  })

  it('drops the mirrored session when the API reports a final 401', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ id: 'u1', username: 'admin', is_admin: true })))
    const store = useVisuStore()
    await store.login('jwt-1', 'refresh-1')

    localStorage.clear()
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))

    expect(store.isLoggedIn).toBe(false)
    expect(store.isAdmin).toBe(false)
  })

  it('keeps the session alive across a token rotation', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ id: 'u1', username: 'admin', is_admin: true })))
    const store = useVisuStore()
    await store.login('jwt-1', 'refresh-1')

    localStorage.setItem('visu_jwt', 'jwt-2')
    notifyAuthTokenRefreshed()
    await flushPromises()

    expect(store.isLoggedIn).toBe(true)
    expect(store.isAdmin).toBe(true)
  })

  it('picks up revoked admin rights on the next token rotation', async () => {
    let isAdmin = true
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ id: 'u1', username: 'admin', is_admin: isAdmin })))
    const store = useVisuStore()
    await store.login('jwt-1', 'refresh-1')
    expect(store.isAdmin).toBe(true)

    // Rechte werden entzogen, während die Sitzung offen bleibt
    isAdmin = false
    localStorage.setItem('visu_jwt', 'jwt-2')
    notifyAuthTokenRefreshed()
    await flushPromises()

    expect(store.isAdmin).toBe(false)
    expect(localStorage.getItem('visu_is_admin')).toBe('0')
  })

  it('picks up granted admin rights on the next token rotation', async () => {
    let isAdmin = false
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ id: 'u1', username: 'user', is_admin: isAdmin })))
    const store = useVisuStore()
    await store.login('jwt-1', 'refresh-1')
    expect(store.isAdmin).toBe(false)

    isAdmin = true
    notifyAuthTokenRefreshed()
    await flushPromises()

    expect(store.isAdmin).toBe(true)
  })

  it('keeps the cached role when the identity lookup is unreachable', async () => {
    let reachable = true
    vi.stubGlobal('fetch', vi.fn(async () => {
      if (!reachable) throw new TypeError('offline')
      return jsonResponse({ id: 'u1', username: 'admin', is_admin: true })
    }))
    const store = useVisuStore()
    await store.login('jwt-1', 'refresh-1')

    reachable = false
    notifyAuthTokenRefreshed()
    await flushPromises()

    expect(store.isAdmin).toBe(true)
  })

  it('discards a role lookup that a newer login has overtaken', async () => {
    let releaseAlice: (value: Response) => void = () => {}
    const alicePending = new Promise<Response>(resolve => { releaseAlice = resolve })
    let pendingForAlice = true

    vi.stubGlobal('fetch', vi.fn(async () => {
      if (pendingForAlice) return alicePending
      return jsonResponse({ id: 'u2', username: 'bob', is_admin: false })
    }))

    const store = useVisuStore()
    localStorage.setItem('visu_jwt', 'jwt-alice')
    localStorage.setItem('visu_refresh_token', 'refresh-alice')
    notifyAuthTokenRefreshed()          // startet Alices /auth/me

    pendingForAlice = false
    await store.login('jwt-bob', 'refresh-bob')   // Bob meldet sich an, kein Admin
    expect(store.isAdmin).toBe(false)

    // Alices verspätete Antwort darf Bobs Rolle nicht überschreiben
    releaseAlice(jsonResponse({ id: 'u1', username: 'alice', is_admin: true }))
    await flushPromises()

    expect(store.isAdmin).toBe(false)
  })

  it('discards a failed role lookup that a newer login has overtaken', async () => {
    let rejectAlice: (reason: unknown) => void = () => {}
    const alicePending = new Promise<Response>((_resolve, reject) => { rejectAlice = reject })
    let pendingForAlice = true

    vi.stubGlobal('fetch', vi.fn(async () => {
      if (pendingForAlice) return alicePending
      return jsonResponse({ id: 'u2', username: 'bob', is_admin: true })
    }))

    const store = useVisuStore()
    const aliceLogin = store.login('jwt-alice', 'refresh-alice')  // bleibt in /auth/me hängen

    pendingForAlice = false
    await store.login('jwt-bob', 'refresh-bob')
    expect(store.isAdmin).toBe(true)

    // Alices Abfrage scheitert erst jetzt — sie darf Bob nicht degradieren
    rejectAlice(new TypeError('offline'))
    await aliceLogin
    await flushPromises()

    expect(store.isAdmin).toBe(true)
  })

  it('reloads the tree after a renewal, so private nodes appear', async () => {
    let authorized = false
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/me')) {
        return jsonResponse({ id: 'u1', username: 'admin', is_admin: true })
      }
      // Vor der Erneuerung sieht das Backend einen abgelaufenen Bearer als anonym
      return jsonResponse(authorized
        ? [{ id: 'public', parent_id: null, name: 'P', type: 'PAGE', order: 0, access: 'public' },
           { id: 'private', parent_id: null, name: 'Q', type: 'PAGE', order: 1, access: 'user' }]
        : [{ id: 'public', parent_id: null, name: 'P', type: 'PAGE', order: 0, access: 'public' }])
    }))

    const store = useVisuStore()
    await store.loadTree()
    expect(store.nodes.map(n => n.id)).toEqual(['public'])

    authorized = true
    localStorage.setItem('visu_jwt', 'jwt-renewed')
    localStorage.setItem('visu_refresh_token', 'refresh-renewed')
    notifyAuthTokenRefreshed()
    await flushPromises()

    expect(store.nodes.map(n => n.id)).toEqual(['public', 'private'])
  })

  it('does not even start a tree reload for a superseded renewal', async () => {
    let releaseAliceMe: (value: Response) => void = () => {}
    const alicePendingMe = new Promise<Response>(resolve => { releaseAliceMe = resolve })
    let mePending = true

    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/me')) {
        return mePending ? alicePendingMe : jsonResponse({ id: 'u2', username: 'bob', is_admin: false })
      }
      return jsonResponse([])
    })
    vi.stubGlobal('fetch', fetchMock)

    const store = useVisuStore()
    localStorage.setItem('visu_jwt', 'jwt-alice')
    localStorage.setItem('visu_refresh_token', 'refresh-alice')
    notifyAuthTokenRefreshed()

    mePending = false
    await store.login('jwt-bob', 'refresh-bob')

    releaseAliceMe(jsonResponse({ id: 'u1', username: 'alice', is_admin: true }))
    await flushPromises()

    // Die überholte Erneuerung darf den Server gar nicht erst behelligen
    expect(fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/visu/tree'))).toHaveLength(0)
  })

  it('does not restore the admin flag from a lookup still running at logout', async () => {
    let releaseMe: (value: Response) => void = () => {}
    const pendingMe = new Promise<Response>(resolve => { releaseMe = resolve })

    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/me')) return pendingMe
      return jsonResponse([])
    }))

    const store = useVisuStore()
    localStorage.setItem('visu_jwt', 'jwt-1')
    localStorage.setItem('visu_refresh_token', 'refresh-1')
    notifyAuthTokenRefreshed()          // startet /auth/me

    store.logout()
    releaseMe(jsonResponse({ id: 'u1', username: 'admin', is_admin: true }))
    await flushPromises()

    expect(store.isAdmin).toBe(false)
    expect(store.isLoggedIn).toBe(false)
    expect(localStorage.getItem('visu_is_admin')).toBeNull()
  })

  it('does not restore the admin flag after a forced logout', async () => {
    let releaseMe: (value: Response) => void = () => {}
    const pendingMe = new Promise<Response>(resolve => { releaseMe = resolve })

    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/me')) return pendingMe
      return jsonResponse([])
    }))

    const store = useVisuStore()
    localStorage.setItem('visu_jwt', 'jwt-1')
    localStorage.setItem('visu_refresh_token', 'refresh-1')
    notifyAuthTokenRefreshed()

    localStorage.clear()
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))
    releaseMe(jsonResponse({ id: 'u1', username: 'admin', is_admin: true }))
    await flushPromises()

    expect(store.isAdmin).toBe(false)
  })

  it('prefers the authorized tree when the anonymous load finishes first', async () => {
    const publicNode = {
      id: 'public', parent_id: null, name: 'P', type: 'PAGE' as const, order: 0, access: 'public' as const,
    }
    const privateNode = {
      id: 'private', parent_id: null, name: 'Q', type: 'PAGE' as const, order: 1, access: 'user' as const,
    }
    let releaseAnonymous: (value: Response) => void = () => {}
    let releaseAuthorized: (value: Response) => void = () => {}
    let treeCall = 0

    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/me')) {
        return jsonResponse({ id: 'u1', username: 'admin', is_admin: true })
      }
      treeCall += 1
      if (treeCall === 1) return new Promise<Response>(resolve => { releaseAnonymous = resolve })
      return new Promise<Response>(resolve => { releaseAuthorized = resolve })
    }))

    const store = useVisuStore()
    const coldStart = store.loadTree()

    localStorage.setItem('visu_jwt', 'jwt-renewed')
    localStorage.setItem('visu_refresh_token', 'refresh-renewed')
    notifyAuthTokenRefreshed()
    await flushPromises()

    // Diesmal ist die anonyme Antwort die schnellere
    releaseAnonymous(jsonResponse([publicNode]))
    await coldStart
    await flushPromises()

    releaseAuthorized(jsonResponse([publicNode, privateNode]))
    await flushPromises()

    expect(store.nodes.map(n => n.id)).toContain('private')
  })

  it('keeps the renewed tree when a slower anonymous load finishes later', async () => {
    const publicNode = {
      id: 'public', parent_id: null, name: 'P', type: 'PAGE' as const, order: 0, access: 'public' as const,
    }
    const privateNode = {
      id: 'private', parent_id: null, name: 'Q', type: 'PAGE' as const, order: 1, access: 'user' as const,
    }
    let releaseInitial: (value: Response) => void = () => {}
    const pendingInitial = new Promise<Response>(resolve => { releaseInitial = resolve })
    let initialPending = true

    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/me')) {
        return jsonResponse({ id: 'u1', username: 'admin', is_admin: true })
      }
      if (initialPending) {
        initialPending = false
        return pendingInitial          // Kaltstart-Anfrage bleibt hängen
      }
      return jsonResponse([publicNode, privateNode])
    }))

    const store = useVisuStore()
    const slowInitialLoad = store.loadTree()

    localStorage.setItem('visu_jwt', 'jwt-renewed')
    localStorage.setItem('visu_refresh_token', 'refresh-renewed')
    notifyAuthTokenRefreshed()
    await flushPromises()
    expect(store.nodes.map(n => n.id)).toContain('private')

    // Die alte, anonym gefilterte Antwort trifft erst jetzt ein
    releaseInitial(jsonResponse([publicNode]))
    await slowInitialLoad
    await flushPromises()

    expect(store.nodes.map(n => n.id)).toContain('private')
  })

  it('keeps a node created while the renewal tree reload was in flight', async () => {
    const created = {
      id: 'new-node', parent_id: null, name: 'Neu', type: 'PAGE' as const, order: 1, access: 'public' as const,
    }
    let releaseTree: (value: Response) => void = () => {}
    const pendingTree = new Promise<Response>(resolve => { releaseTree = resolve })
    let treePending = false

    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
      if (String(url).endsWith('/auth/me')) {
        return jsonResponse({ id: 'u1', username: 'admin', is_admin: true })
      }
      if (init.method === 'POST') return jsonResponse(created)
      if (treePending) return pendingTree
      return jsonResponse([])
    }))

    const store = useVisuStore()
    localStorage.setItem('visu_jwt', 'jwt-1')
    localStorage.setItem('visu_refresh_token', 'refresh-1')
    treePending = true
    notifyAuthTokenRefreshed()
    await flushPromises()               // /auth/me durch, Baum-Request unterwegs

    treePending = false
    await store.createNode(created)
    expect(store.nodes.map(n => n.id)).toContain('new-node')

    // Der ältere Schnappschuss darf die Neuanlage nicht zurücknehmen
    releaseTree(jsonResponse([]))
    await flushPromises()

    expect(store.nodes.map(n => n.id)).toContain('new-node')
  })

  it('does not duplicate a node the reloaded tree already contains', async () => {
    // Der Server hat die Neuanlage committet, bevor er die Momentaufnahme
    // erstellt hat — die Antwort auf das POST ist aber noch unterwegs.
    const created = {
      id: 'new-node', parent_id: null, name: 'Neu', type: 'PAGE' as const, order: 1, access: 'public' as const,
    }
    let releaseTree: (value: Response) => void = () => {}
    let releaseCreate: (value: Response) => void = () => {}
    const pendingTree = new Promise<Response>(resolve => { releaseTree = resolve })
    const pendingCreate = new Promise<Response>(resolve => { releaseCreate = resolve })

    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
      if (String(url).endsWith('/auth/me')) {
        return jsonResponse({ id: 'u1', username: 'admin', is_admin: true })
      }
      if (init.method === 'POST') return pendingCreate
      return pendingTree
    }))

    const store = useVisuStore()
    localStorage.setItem('visu_jwt', 'jwt-1')
    localStorage.setItem('visu_refresh_token', 'refresh-1')
    notifyAuthTokenRefreshed()
    await flushPromises()               // /auth/me durch, Baum-Request unterwegs

    const pending = store.createNode(created)
    await flushPromises()
    releaseTree(jsonResponse([created]))
    await flushPromises()
    releaseCreate(jsonResponse(created))
    await pending

    expect(store.nodes.map(n => n.id)).toEqual(['new-node'])
  })

  it('does not resurrect a node the reloaded tree still contains', async () => {
    const doomed = {
      id: 'old-node', parent_id: null, name: 'Alt', type: 'PAGE' as const, order: 1, access: 'public' as const,
    }
    let releaseTree: (value: Response) => void = () => {}
    let releaseDelete: (value: Response) => void = () => {}
    const pendingTree = new Promise<Response>(resolve => { releaseTree = resolve })
    const pendingDelete = new Promise<Response>(resolve => { releaseDelete = resolve })

    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
      if (String(url).endsWith('/auth/me')) {
        return jsonResponse({ id: 'u1', username: 'admin', is_admin: true })
      }
      if (init.method === 'DELETE') return pendingDelete
      return pendingTree
    }))

    const store = useVisuStore()
    localStorage.setItem('visu_jwt', 'jwt-1')
    localStorage.setItem('visu_refresh_token', 'refresh-1')
    notifyAuthTokenRefreshed()
    await flushPromises()

    const pending = store.deleteNode('old-node')
    await flushPromises()
    // Momentaufnahme von vor dem Löschen — sie darf den Knoten nicht zurückholen
    releaseTree(jsonResponse([doomed]))
    await flushPromises()
    releaseDelete(new Response(null, { status: 204 }))
    await pending

    expect(store.nodes.map(n => n.id)).toEqual([])
  })

  it('counts a failed change, whose tree state is just as unknown', async () => {
    const created = {
      id: 'new-node', parent_id: null, name: 'Neu', type: 'PAGE' as const, order: 1, access: 'public' as const,
    }
    let releaseTree: (value: Response) => void = () => {}
    const pendingTree = new Promise<Response>(resolve => { releaseTree = resolve })

    vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
      if (String(url).endsWith('/auth/me')) {
        return jsonResponse({ id: 'u1', username: 'admin', is_admin: true })
      }
      if (init.method === 'POST') return new Response(null, { status: 500 })
      return pendingTree
    }))

    const store = useVisuStore()
    localStorage.setItem('visu_jwt', 'jwt-1')
    localStorage.setItem('visu_refresh_token', 'refresh-1')
    notifyAuthTokenRefreshed()
    await flushPromises()

    // Auch ein gescheitertes POST kann serverseitig committet haben
    await expect(store.createNode(created)).rejects.toBeTruthy()
    releaseTree(jsonResponse([created]))
    await flushPromises()

    expect(store.nodes.map(n => n.id)).toEqual([])
  })

  it('discards a tree reload that a newer login has overtaken', async () => {
    let releaseAliceTree: (value: Response) => void = () => {}
    const alicePendingTree = new Promise<Response>(resolve => { releaseAliceTree = resolve })
    let aliceTreePending = true

    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/me')) {
        return jsonResponse({ id: 'u1', username: 'someone', is_admin: false })
      }
      if (aliceTreePending) return alicePendingTree
      return jsonResponse([{ id: 'bob-page', parent_id: null, name: 'B', type: 'PAGE', order: 0, access: 'user' }])
    }))

    const store = useVisuStore()
    localStorage.setItem('visu_jwt', 'jwt-alice')
    localStorage.setItem('visu_refresh_token', 'refresh-alice')
    notifyAuthTokenRefreshed()          // startet Alices /auth/me und danach ihren Baum
    await flushPromises()

    aliceTreePending = false
    await store.login('jwt-bob', 'refresh-bob')
    await flushPromises()

    // Alices Baum trifft verspätet ein und darf Bobs Sicht nicht überschreiben
    releaseAliceTree(jsonResponse([
      { id: 'alice-private', parent_id: null, name: 'A', type: 'PAGE', order: 0, access: 'user' },
    ]))
    await flushPromises()

    expect(store.nodes.map(n => n.id)).not.toContain('alice-private')
  })

  it('survives a tree reload that fails after a renewal', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/me')) {
        return jsonResponse({ id: 'u1', username: 'admin', is_admin: true })
      }
      return new Response(null, { status: 500 })
    }))

    const store = useVisuStore()
    localStorage.setItem('visu_jwt', 'jwt-renewed')
    localStorage.setItem('visu_refresh_token', 'refresh-renewed')
    notifyAuthTokenRefreshed()
    await flushPromises()

    expect(store.isAdmin).toBe(true)
  })

  it('drops the role when the tokens are gone at rotation time', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse({ id: 'u1', username: 'admin', is_admin: true })))
    const store = useVisuStore()
    await store.login('jwt-1', 'refresh-1')

    localStorage.clear()
    notifyAuthTokenRefreshed()
    await flushPromises()

    expect(store.isLoggedIn).toBe(false)
    expect(store.isAdmin).toBe(false)
  })
})
