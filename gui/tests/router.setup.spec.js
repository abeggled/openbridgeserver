/**
 * Router guard for first-run setup (#1229).
 *
 * While the installation has no owner, every route must end up on the setup
 * page — the auth guard's redirect to /login would otherwise send the user to
 * a page the backend refuses to serve.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'

const fetchSetupRequired = vi.fn()

beforeEach(() => {
  vi.resetModules()
  fetchSetupRequired.mockReset()
  localStorage.clear()
})

afterEach(() => {
  vi.doUnmock('@/utils/setupStatus')
  vi.doUnmock('@/api/client')
})

async function loadRouter(setupRequired) {
  fetchSetupRequired.mockResolvedValue(setupRequired)
  vi.doMock('@/utils/setupStatus', () => ({
    fetchSetupRequired,
    markSetupComplete: vi.fn(),
    resetSetupStatusCache: vi.fn(),
  }))
  const { default: router } = await import('@/router')
  return router
}

describe('setup guard — installation without an owner', () => {
  it.each(['/', '/datapoints', '/login', '/settings'])('sends %s to the setup page', async (path) => {
    const router = await loadRouter(true)

    await router.push(path)

    expect(router.currentRoute.value.name).toBe('Setup')
  })

  it('leaves the setup page itself alone', async () => {
    const router = await loadRouter(true)

    await router.push('/setup')

    expect(router.currentRoute.value.name).toBe('Setup')
  })
})

describe('setup guard — configured installation', () => {
  it('sends the setup page to the login', async () => {
    const router = await loadRouter(false)

    await router.push('/setup')

    expect(router.currentRoute.value.name).toBe('Login')
  })

  it('still applies the auth guard', async () => {
    const router = await loadRouter(false)

    await router.push('/datapoints')

    expect(router.currentRoute.value.name).toBe('Login')
  })

  it('lets an authenticated user through', async () => {
    localStorage.setItem('access_token', 'a-token')
    const router = await loadRouter(false)

    await router.push('/datapoints')

    expect(router.currentRoute.value.name).toBe('DataPoints')
  })
})

describe('setup guard — status call that never settles', () => {
  it('still resolves navigation instead of blocking it forever', async () => {
    // Real setup-status module, stalled transport: the guard awaits this answer
    // before it resolves any route, so it has to give up on its own.
    vi.useFakeTimers()
    vi.doMock('@/api/client', () => ({ setupApi: { status: () => new Promise(() => {}) } }))
    const { default: router } = await import('@/router')

    const navigation = router.push('/login')
    await vi.advanceTimersByTimeAsync(10000)
    await navigation

    expect(router.currentRoute.value.name).toBe('Login')
    vi.useRealTimers()
  })
})
