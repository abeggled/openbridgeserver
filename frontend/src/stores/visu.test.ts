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
  beforeEach(() => {
    setActivePinia(createPinia())
    localStorage.clear()
  })

  afterEach(() => {
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
