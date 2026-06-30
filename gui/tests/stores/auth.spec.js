import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'

function makeStorage(overrides = {}) {
  const store = { ...overrides }
  return {
    getItem:    vi.fn(k => store[k] ?? null),
    setItem:    vi.fn((k, v) => { store[k] = v }),
    removeItem: vi.fn(k => { delete store[k] }),
    _store:     store,
  }
}

function overrideStorage(storage) {
  Object.defineProperty(window,     'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
}

let authApiMock

beforeEach(() => {
  vi.resetModules()
  authApiMock = {
    login: vi.fn(),
    me:    vi.fn(),
  }
  vi.doMock('@/api/client', () => ({ authApi: authApiMock }))
  overrideStorage(makeStorage())
  setActivePinia(createPinia())
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

describe('useAuthStore', () => {
  it('login success stores tokens, loads user, returns true', async () => {
    authApiMock.login.mockResolvedValue({ data: { access_token: 'at', refresh_token: 'rt' } })
    authApiMock.me.mockResolvedValue({ data: { id: 1, username: 'admin', is_admin: true } })
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()

    const result = await store.login('admin', 'secret')

    expect(result).toBe(true)
    expect(localStorage.setItem).toHaveBeenCalledWith('access_token', 'at')
    expect(localStorage.setItem).toHaveBeenCalledWith('refresh_token', 'rt')
    expect(store.user).toEqual({ id: 1, username: 'admin', is_admin: true })
    expect(store.loading).toBe(false)
    expect(store.error).toBeNull()
  })

  it('login failure with server detail sets error and returns false', async () => {
    authApiMock.login.mockRejectedValue({ response: { data: { detail: 'Invalid credentials' } } })
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()

    const result = await store.login('admin', 'wrong')

    expect(result).toBe(false)
    expect(store.error).toBe('Invalid credentials')
    expect(store.loading).toBe(false)
  })

  it('login failure without response detail uses German fallback message', async () => {
    authApiMock.login.mockRejectedValue(new Error('network error'))
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()

    const result = await store.login('admin', 'wrong')

    expect(result).toBe(false)
    expect(store.error).toBe('Login fehlgeschlagen')
  })

  it('login clears previous error before attempting', async () => {
    authApiMock.login.mockResolvedValue({ data: { access_token: 'at', refresh_token: 'rt' } })
    authApiMock.me.mockResolvedValue({ data: { id: 1 } })
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()
    store.error = 'old error'

    await store.login('admin', 'secret')

    expect(store.error).toBeNull()
  })

  it('loadMe sets user on success', async () => {
    authApiMock.me.mockResolvedValue({ data: { id: 1, username: 'yves', is_admin: false } })
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()

    await store.loadMe()

    expect(store.user).toEqual({ id: 1, username: 'yves', is_admin: false })
  })

  it('loadMe sets user to null on failure', async () => {
    authApiMock.me.mockRejectedValue(new Error('unauthorized'))
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()
    store.user = { id: 1 }

    await store.loadMe()

    expect(store.user).toBeNull()
  })

  it('logout removes tokens from localStorage and resets user', async () => {
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()
    store.user = { id: 1, username: 'admin' }

    store.logout()

    expect(localStorage.removeItem).toHaveBeenCalledWith('access_token')
    expect(localStorage.removeItem).toHaveBeenCalledWith('refresh_token')
    expect(store.user).toBeNull()
  })

  it('isLoggedIn returns true when access_token is in localStorage', async () => {
    overrideStorage(makeStorage({ access_token: 'tok' }))
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()
    expect(store.isLoggedIn).toBe(true)
  })

  it('isLoggedIn returns false when access_token is absent', async () => {
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()
    expect(store.isLoggedIn).toBe(false)
  })

  it('isAdmin reflects user.is_admin', async () => {
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()
    expect(store.isAdmin).toBe(false)

    store.user = { is_admin: true }
    expect(store.isAdmin).toBe(true)
  })

  it('username reflects user.username and defaults to empty string', async () => {
    const { useAuthStore } = await import('@/stores/auth')
    const store = useAuthStore()
    expect(store.username).toBe('')

    store.user = { username: 'yves' }
    expect(store.username).toBe('yves')
  })
})
