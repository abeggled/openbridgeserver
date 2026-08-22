// @vitest-environment jsdom
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import {
  ApiRequestError,
  cancelTokenRefresh,
  clearAuthTokens,
  displaySettings,
  messageArchives,
  refreshAccessToken,
  scheduleTokenRefresh,
  setTokens,
  visu,
  visuBackgrounds,
} from './client'
import { AUTH_TOKEN_REFRESHED_EVENT } from '@/utils/authEvents'

describe('structured API errors', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
  })

  it('preserves the stable code and actionable details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: 'visu_target_audience_datapoints_denied',
        username: 'alice',
        datapoint_ids: ['blocked.dp'],
      },
    }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })))

    const error = await visu.updateNode('node-1', { access: 'user', usernames: ['alice'] }).catch(value => value)

    expect(error).toBeInstanceOf(ApiRequestError)
    expect(error).toMatchObject({
      status: 403,
      code: 'visu_target_audience_datapoints_denied',
      details: {
        username: 'alice',
        datapoint_ids: ['blocked.dp'],
      },
    })
  })

  it('preserves the HTTP status for plain API error details', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: 'Forbidden',
    }), {
      status: 403,
      headers: { 'Content-Type': 'application/json' },
    })))

    const error = await messageArchives.markRead('archive-1', 'entry-1').catch(value => value)

    expect(error).toBeInstanceOf(ApiRequestError)
    expect(error).toMatchObject({
      message: 'Forbidden',
      status: 403,
    })
  })
})

// ── Token-Refresh (Issue #1160) ───────────────────────────────────────────────

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** JWT-artiger Token mit frei wählbarem Payload (Signatur wird clientseitig nie geprüft) */
function fakeJwt(payload: Record<string, unknown>): string {
  const encoded = btoa(JSON.stringify(payload)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `header.${encoded}.signature`
}

/** Deutlich länger als der grösste Retry-Abstand des proaktiven Refresh */
const RETRY_WINDOW_MS = 3_600_000

function refreshCalls(fetchMock: ReturnType<typeof vi.fn>): unknown[][] {
  return fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/auth/refresh'))
}

/**
 * fetch-Mock: `/auth/refresh` liefert `refreshResponse`, jede andere Route
 * antwortet nur dann mit 200, wenn sie den erneuerten Token mitschickt.
 */
function stubAuthFetch(refreshResponse: () => Response | Promise<Response>, freshToken = 'jwt-new') {
  const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
    if (String(url).endsWith('/auth/refresh')) return refreshResponse()
    const headers = (init.headers ?? {}) as Record<string, string>
    if (headers['Authorization'] === `Bearer ${freshToken}`) return jsonResponse([{ id: 'node-1' }])
    return new Response(null, { status: 401 })
  })
  vi.stubGlobal('fetch', fetchMock)
  return fetchMock
}

describe('access token refresh', () => {
  beforeEach(() => {
    localStorage.clear()
    localStorage.setItem('visu_jwt', 'jwt-old')
    localStorage.setItem('visu_refresh_token', 'refresh-old')
    localStorage.setItem('visu_is_admin', '1')
  })

  afterEach(() => {
    cancelTokenRefresh()
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('renews the token on 401 and retries the original request', async () => {
    const fetchMock = stubAuthFetch(() => jsonResponse({ access_token: 'jwt-new', refresh_token: 'refresh-new' }))
    const refreshed = vi.fn()
    window.addEventListener(AUTH_TOKEN_REFRESHED_EVENT, refreshed)

    await expect(visu.tree()).resolves.toEqual([{ id: 'node-1' }])

    expect(refreshCalls(fetchMock)).toHaveLength(1)
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-new')
    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-new')
    expect(refreshed).toHaveBeenCalledTimes(1)
    window.removeEventListener(AUTH_TOKEN_REFRESHED_EVENT, refreshed)
  })

  it('shares a single refresh call across concurrent requests', async () => {
    const fetchMock = stubAuthFetch(() => jsonResponse({ access_token: 'jwt-new', refresh_token: 'refresh-new' }))

    await Promise.all([visu.tree(), visu.getNode('node-1'), visu.getBreadcrumb('node-1')])

    expect(refreshCalls(fetchMock)).toHaveLength(1)
  })

  it('reuses a token that another request has already renewed', async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
      if (String(url).endsWith('/auth/refresh')) {
        return jsonResponse({ access_token: 'jwt-other', refresh_token: 'refresh-other' })
      }
      const headers = (init.headers ?? {}) as Record<string, string>
      if (headers['Authorization'] === 'Bearer jwt-new') return jsonResponse([{ id: 'node-1' }])
      // Ein paralleler Request war schneller und hat den Token bereits rotiert
      localStorage.setItem('visu_jwt', 'jwt-new')
      return new Response(null, { status: 401 })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(visu.tree()).resolves.toEqual([{ id: 'node-1' }])

    expect(refreshCalls(fetchMock)).toHaveLength(0)
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-new')
  })

  it('clears both tokens and the admin flag when the refresh is rejected', async () => {
    stubAuthFetch(() => new Response(null, { status: 401 }))
    const unauthorized = vi.fn()
    window.addEventListener('visu:unauthorized', unauthorized)

    await expect(visu.tree()).rejects.toMatchObject({ status: 401 })

    expect(localStorage.getItem('visu_jwt')).toBeNull()
    expect(localStorage.getItem('visu_refresh_token')).toBeNull()
    expect(localStorage.getItem('visu_is_admin')).toBeNull()
    expect(unauthorized).toHaveBeenCalledTimes(1)
    window.removeEventListener('visu:unauthorized', unauthorized)
  })

  it('does not attempt a refresh without a stored refresh token', async () => {
    localStorage.removeItem('visu_refresh_token')
    const fetchMock = stubAuthFetch(() => jsonResponse({ access_token: 'jwt-new', refresh_token: 'refresh-new' }))

    await expect(visu.tree()).rejects.toMatchObject({ status: 401 })

    expect(refreshCalls(fetchMock)).toHaveLength(0)
    expect(localStorage.getItem('visu_jwt')).toBeNull()
  })

  it('sends no Authorization header and no refresh for an anonymous viewer', async () => {
    localStorage.clear()
    const fetchMock = stubAuthFetch(() => jsonResponse({ access_token: 'jwt-new', refresh_token: 'refresh-new' }))
    const unauthorized = vi.fn()
    window.addEventListener('visu:unauthorized', unauthorized)

    await expect(visu.tree()).rejects.toMatchObject({ status: 401 })

    const headers = (fetchMock.mock.calls[0][1] as RequestInit).headers as Record<string, string>
    expect(headers.Authorization).toBeUndefined()
    expect(refreshCalls(fetchMock)).toHaveLength(0)
    expect(unauthorized).toHaveBeenCalledTimes(1)
    window.removeEventListener('visu:unauthorized', unauthorized)
  })

  it('keeps the tokens and stays silent for a silent401 route', async () => {
    stubAuthFetch(() => new Response(null, { status: 401 }))
    const unauthorized = vi.fn()
    window.addEventListener('visu:unauthorized', unauthorized)

    await expect(displaySettings.get()).rejects.toMatchObject({ status: 401 })

    expect(localStorage.getItem('visu_jwt')).toBe('jwt-old')
    expect(unauthorized).not.toHaveBeenCalled()
    window.removeEventListener('visu:unauthorized', unauthorized)
  })

  it('treats a rejected PIN as a failed login instead of an expired token', async () => {
    const fetchMock = stubAuthFetch(() => jsonResponse({ access_token: 'jwt-new', refresh_token: 'refresh-new' }))

    await expect(visu.pinAuth('node-1', '0000')).rejects.toMatchObject({ status: 401 })

    expect(refreshCalls(fetchMock)).toHaveLength(0)
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-old')
  })

  it('keeps the session when the refresh cannot reach the server', async () => {
    const fetchMock = stubAuthFetch(() => Promise.reject(new TypeError('offline')))

    await expect(visu.tree()).rejects.toMatchObject({ status: 401 })

    // Ein Netzwerkfehler sagt nichts über die Gültigkeit des Refresh-Tokens —
    // ihn zu löschen würde einen vollen Login erzwingen (Codex-Review).
    expect(refreshCalls(fetchMock)).toHaveLength(1)
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-old')
    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-old')
  })

  it('keeps the session when the refresh answers with an unusable body', async () => {
    stubAuthFetch(() => jsonResponse({ token_type: 'bearer' }))

    await expect(visu.tree()).rejects.toMatchObject({ status: 401 })

    // Eine kaputte Antwort macht den Refresh-Token nicht ungültig
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-old')
    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-old')
  })

  it('leaves a newly started session alone when a stale refresh returns', async () => {
    vi.stubGlobal('fetch', vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/refresh')) {
        // Anderer Tab meldet sich neu an, während dieser Refresh unterwegs ist
        localStorage.setItem('visu_jwt', 'jwt-new-session')
        localStorage.setItem('visu_refresh_token', 'refresh-new-session')
        return jsonResponse({ access_token: 'jwt-stale', refresh_token: 'refresh-stale' })
      }
      return new Response(null, { status: 401 })
    }))

    await expect(visu.tree()).rejects.toMatchObject({ status: 401 })

    expect(localStorage.getItem('visu_jwt')).toBe('jwt-new-session')
    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-new-session')
  })

  it('keeps the session when the refresh hits a server error', async () => {
    stubAuthFetch(() => new Response(null, { status: 503 }))

    await expect(visu.tree()).rejects.toMatchObject({ status: 401 })

    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-old')
  })

  it('ignores a refresh response without an access token', async () => {
    localStorage.setItem('visu_jwt', 'jwt-old')
    localStorage.setItem('visu_refresh_token', 'refresh-old')
    stubAuthFetch(() => jsonResponse({ token_type: 'bearer' }))

    expect(await refreshAccessToken()).toBeNull()
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-old')
  })

  it('ignores a refresh response that is not valid JSON', async () => {
    stubAuthFetch(() => new Response('not json', { status: 200 }))

    expect(await refreshAccessToken()).toBeNull()
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-old')
  })

  it('rotates only the access token when the response omits a refresh token', async () => {
    stubAuthFetch(() => jsonResponse({ access_token: 'jwt-new' }))

    expect(await refreshAccessToken()).toBe('jwt-new')
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-new')
    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-old')
  })

  it('renews the token for a rejected multipart background import and retries it', async () => {
    const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
      if (String(url).endsWith('/auth/refresh')) {
        return jsonResponse({ access_token: 'jwt-new', refresh_token: 'refresh-new' })
      }
      const headers = (init.headers ?? {}) as Record<string, string>
      if (headers['Authorization'] === 'Bearer jwt-new') {
        return jsonResponse({ imported: 1, skipped: 0, names: ['a.png'], message: 'ok' })
      }
      return new Response(null, { status: 401 })
    })
    vi.stubGlobal('fetch', fetchMock)

    const result = await visuBackgrounds.import([new File(['x'], 'a.png', { type: 'image/png' })])

    expect(result.imported).toBe(1)
    expect(refreshCalls(fetchMock)).toHaveLength(1)
  })

  it('logs out when a multipart background import cannot be renewed', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response(null, { status: 401 })))
    const unauthorized = vi.fn()
    window.addEventListener('visu:unauthorized', unauthorized)

    await expect(
      visuBackgrounds.import([new File(['x'], 'a.png', { type: 'image/png' })]),
    ).rejects.toThrow('Unauthorized')

    expect(localStorage.getItem('visu_jwt')).toBeNull()
    expect(localStorage.getItem('visu_refresh_token')).toBeNull()
    expect(unauthorized).toHaveBeenCalledTimes(1)
    window.removeEventListener('visu:unauthorized', unauthorized)
  })
})

describe('refresh across session boundaries (Codex review)', () => {
  beforeEach(() => {
    localStorage.clear()
  })

  afterEach(() => {
    cancelTokenRefresh()
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  it('does not revive a session that was logged out while the refresh was in flight', async () => {
    let releaseRefresh: (res: Response) => void = () => {}
    vi.stubGlobal('fetch', vi.fn(() => new Promise<Response>(resolve => { releaseRefresh = resolve })))
    localStorage.setItem('visu_jwt', 'jwt-old')
    localStorage.setItem('visu_refresh_token', 'refresh-old')

    const pending = refreshAccessToken()
    clearAuthTokens()
    releaseRefresh(jsonResponse({ access_token: 'jwt-new', refresh_token: 'refresh-new' }))

    expect(await pending).toBeNull()
    expect(localStorage.getItem('visu_jwt')).toBeNull()
    expect(localStorage.getItem('visu_refresh_token')).toBeNull()
  })

  it('does not replay a request under a different account', async () => {
    const alice = fakeJwt({ sub: 'alice', exp: Math.floor(Date.now() / 1000) + 3600 })
    const bob = fakeJwt({ sub: 'bob', exp: Math.floor(Date.now() / 1000) + 3600 })
    localStorage.setItem('visu_jwt', alice)
    localStorage.setItem('visu_refresh_token', 'refresh-alice')

    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/refresh')) {
        return jsonResponse({ access_token: bob, refresh_token: 'refresh-bob' })
      }
      // Anderer Tab meldet sich als bob an, während der Request unterwegs ist
      localStorage.setItem('visu_jwt', bob)
      return new Response(null, { status: 401 })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(visu.deleteNode('node-of-alice')).rejects.toMatchObject({ status: 401 })

    const deletes = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/visu/nodes/node-of-alice'))
    expect(deletes).toHaveLength(1)
  })

  it('still retries when the renewed token belongs to the same account', async () => {
    const before = fakeJwt({ sub: 'alice', exp: Math.floor(Date.now() / 1000) + 3600 })
    const after = fakeJwt({ sub: 'alice', exp: Math.floor(Date.now() / 1000) + 7200 })
    localStorage.setItem('visu_jwt', before)
    localStorage.setItem('visu_refresh_token', 'refresh-alice')

    const fetchMock = vi.fn(async (url: string, init: RequestInit = {}) => {
      if (String(url).endsWith('/auth/refresh')) {
        return jsonResponse({ access_token: after, refresh_token: 'refresh-alice-2' })
      }
      const headers = (init.headers ?? {}) as Record<string, string>
      if (headers['Authorization'] === `Bearer ${after}`) return new Response(null, { status: 204 })
      return new Response(null, { status: 401 })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(visu.deleteNode('node-of-alice')).resolves.toBeUndefined()

    const deletes = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/visu/nodes/node-of-alice'))
    expect(deletes).toHaveLength(2)
  })
})

  it('does not replay a multipart import under a different account', async () => {
    const alice = fakeJwt({ sub: 'alice', exp: Math.floor(Date.now() / 1000) + 3600 })
    const bob = fakeJwt({ sub: 'bob', exp: Math.floor(Date.now() / 1000) + 3600 })
    localStorage.setItem('visu_jwt', alice)
    localStorage.setItem('visu_refresh_token', 'refresh-alice')

    const fetchMock = vi.fn(async (url: string) => {
      if (String(url).endsWith('/auth/refresh')) {
        return jsonResponse({ access_token: bob, refresh_token: 'refresh-bob' })
      }
      localStorage.setItem('visu_jwt', bob)
      return new Response(null, { status: 401 })
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      visuBackgrounds.import([new File(['x'], 'a.png', { type: 'image/png' })]),
    ).rejects.toThrow('Unauthorized')

    const imports = fetchMock.mock.calls.filter(([url]) => String(url).endsWith('/visu/backgrounds/import'))
    expect(imports).toHaveLength(1)
    // Bobs Anmeldung darf durch Alices veralteten Request nicht abgeräumt werden
    expect(localStorage.getItem('visu_jwt')).toBe(bob)
  })

  it('leaves the other account signed in when a stale request is rejected', async () => {
    const alice = fakeJwt({ sub: 'alice', exp: Math.floor(Date.now() / 1000) + 3600 })
    const bob = fakeJwt({ sub: 'bob', exp: Math.floor(Date.now() / 1000) + 3600 })
    localStorage.setItem('visu_jwt', alice)
    localStorage.setItem('visu_refresh_token', 'refresh-alice')

    vi.stubGlobal('fetch', vi.fn(async () => {
      localStorage.setItem('visu_jwt', bob)
      localStorage.setItem('visu_refresh_token', 'refresh-bob')
      return new Response(null, { status: 401 })
    }))

    await expect(visu.tree()).rejects.toMatchObject({ status: 401 })

    expect(localStorage.getItem('visu_jwt')).toBe(bob)
    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-bob')
  })

describe('setTokens', () => {
  beforeEach(() => localStorage.clear())
  afterEach(() => {
    cancelTokenRefresh()
    localStorage.clear()
  })

  it('stores both tokens', () => {
    setTokens('jwt-1', 'refresh-1')
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-1')
    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-1')
  })

  it('keeps the existing refresh token when none is supplied', () => {
    localStorage.setItem('visu_refresh_token', 'refresh-keep')
    setTokens('jwt-2')
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-2')
    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-keep')
  })

  it('drops access token, refresh token and admin flag together', () => {
    setTokens('jwt-3', 'refresh-3')
    localStorage.setItem('visu_is_admin', '1')

    clearAuthTokens()

    expect(localStorage.getItem('visu_jwt')).toBeNull()
    expect(localStorage.getItem('visu_refresh_token')).toBeNull()
    expect(localStorage.getItem('visu_is_admin')).toBeNull()
  })
})

describe('proactive token refresh', () => {
  beforeEach(() => {
    vi.useFakeTimers()
    localStorage.clear()
  })

  afterEach(() => {
    cancelTokenRefresh()
    vi.useRealTimers()
    vi.unstubAllGlobals()
    localStorage.clear()
  })

  /** Wie viele Timer `scheduleTokenRefresh()` zusätzlich anlegt (jsdom hält eigene) */
  function scheduledTimers(): number {
    const before = vi.getTimerCount()
    scheduleTokenRefresh()
    return vi.getTimerCount() - before
  }

  it('refreshes shortly before the access token expires', async () => {
    const fetchMock = stubAuthFetch(() => jsonResponse({ access_token: 'jwt-new', refresh_token: 'refresh-new' }))
    localStorage.setItem('visu_jwt', fakeJwt({ exp: Math.floor(Date.now() / 1000) + 3600 }))
    localStorage.setItem('visu_refresh_token', 'refresh-old')

    expect(scheduledTimers()).toBe(1)

    await vi.advanceTimersByTimeAsync(3600_000 - 60_000)

    expect(refreshCalls(fetchMock)).toHaveLength(1)
    expect(localStorage.getItem('visu_jwt')).toBe('jwt-new')
  })

  it('waits out the minimum delay when the access token is already expired', async () => {
    const fetchMock = stubAuthFetch(() => jsonResponse({ access_token: 'jwt-new', refresh_token: 'refresh-new' }))
    localStorage.setItem('visu_jwt', fakeJwt({ exp: Math.floor(Date.now() / 1000) - 10 }))
    localStorage.setItem('visu_refresh_token', 'refresh-old')

    scheduleTokenRefresh()

    // Der Mindestabstand hält /auth/refresh unter dem 10/min-Limit
    await vi.advanceTimersByTimeAsync(9_000)
    expect(refreshCalls(fetchMock)).toHaveLength(0)

    await vi.advanceTimersByTimeAsync(1_000)
    expect(refreshCalls(fetchMock)).toHaveLength(1)
  })

  it('schedules nothing without an access token', () => {
    localStorage.setItem('visu_refresh_token', 'refresh-old')
    expect(scheduledTimers()).toBe(0)
  })

  it('schedules nothing without a refresh token', () => {
    localStorage.setItem('visu_jwt', fakeJwt({ exp: Math.floor(Date.now() / 1000) + 3600 }))
    expect(scheduledTimers()).toBe(0)
  })

  it.each([
    ['a token without a payload segment', 'opaque-token'],
    ['a payload that is not JSON', 'header.bm90LWpzb24.signature'],
    ['a payload without an exp claim', fakeJwt({ sub: 'admin' })],
    ['a non-numeric exp claim', fakeJwt({ exp: 'soon' })],
  ])('schedules nothing for %s', (_label, token) => {
    localStorage.setItem('visu_jwt', token)
    localStorage.setItem('visu_refresh_token', 'refresh-old')
    expect(scheduledTimers()).toBe(0)
  })

  it('retries with a growing delay while the server is unreachable', async () => {
    const fetchMock = stubAuthFetch(() => Promise.reject(new TypeError('offline')))
    localStorage.setItem('visu_jwt', fakeJwt({ exp: Math.floor(Date.now() / 1000) + 3600 }))
    localStorage.setItem('visu_refresh_token', 'refresh-old')

    scheduleTokenRefresh()
    await vi.advanceTimersByTimeAsync(3600_000 - 60_000)
    expect(refreshCalls(fetchMock)).toHaveLength(1)

    // Erster Retry nach 30 s, der zweite erst nach 60 s
    await vi.advanceTimersByTimeAsync(30_000)
    expect(refreshCalls(fetchMock)).toHaveLength(2)

    await vi.advanceTimersByTimeAsync(30_000)
    expect(refreshCalls(fetchMock)).toHaveLength(2)
    await vi.advanceTimersByTimeAsync(30_000)
    expect(refreshCalls(fetchMock)).toHaveLength(3)
  })

  it('retries a rate-limited refresh and resumes the normal schedule on success', async () => {
    let attempts = 0
    const fetchMock = stubAuthFetch(() => {
      attempts += 1
      if (attempts === 1) return new Response(null, { status: 429 })
      return jsonResponse({
        access_token: fakeJwt({ exp: Math.floor(Date.now() / 1000) + 3600 }),
        refresh_token: 'refresh-new',
      })
    })
    localStorage.setItem('visu_jwt', fakeJwt({ exp: Math.floor(Date.now() / 1000) + 3600 }))
    localStorage.setItem('visu_refresh_token', 'refresh-old')

    scheduleTokenRefresh()
    await vi.advanceTimersByTimeAsync(3600_000 - 60_000)
    await vi.advanceTimersByTimeAsync(30_000)

    expect(refreshCalls(fetchMock)).toHaveLength(2)
    expect(localStorage.getItem('visu_refresh_token')).toBe('refresh-new')

    // Nach dem Erfolg zählt wieder die Laufzeit des neuen Tokens, kein Retry-Takt
    await vi.advanceTimersByTimeAsync(60_000)
    expect(refreshCalls(fetchMock)).toHaveLength(2)
  })

  it('stops retrying once the refresh token is rejected for good', async () => {
    const fetchMock = stubAuthFetch(() => new Response(null, { status: 401 }))
    localStorage.setItem('visu_jwt', fakeJwt({ exp: Math.floor(Date.now() / 1000) + 3600 }))
    localStorage.setItem('visu_refresh_token', 'refresh-old')

    scheduleTokenRefresh()
    await vi.advanceTimersByTimeAsync(3600_000 - 60_000)
    expect(refreshCalls(fetchMock)).toHaveLength(1)

    await vi.advanceTimersByTimeAsync(RETRY_WINDOW_MS)
    expect(refreshCalls(fetchMock)).toHaveLength(1)
  })

  it('stops retrying after a logout has dropped the refresh token', async () => {
    const fetchMock = stubAuthFetch(() => {
      clearAuthTokens()
      return Promise.reject(new TypeError('offline'))
    })
    localStorage.setItem('visu_jwt', fakeJwt({ exp: Math.floor(Date.now() / 1000) + 3600 }))
    localStorage.setItem('visu_refresh_token', 'refresh-old')

    scheduleTokenRefresh()
    await vi.advanceTimersByTimeAsync(3600_000 - 60_000)
    expect(refreshCalls(fetchMock)).toHaveLength(1)

    await vi.advanceTimersByTimeAsync(RETRY_WINDOW_MS)
    expect(refreshCalls(fetchMock)).toHaveLength(1)
  })

  it('keeps a sane cadence for a one-minute token lifetime', async () => {
    const oneMinuteToken = () => fakeJwt({ exp: Math.floor(Date.now() / 1000) + 60 })
    const fetchMock = stubAuthFetch(() => jsonResponse({
      access_token: oneMinuteToken(),
      refresh_token: 'refresh-new',
    }))
    localStorage.setItem('visu_jwt', oneMinuteToken())
    localStorage.setItem('visu_refresh_token', 'refresh-old')

    scheduleTokenRefresh()
    await vi.advanceTimersByTimeAsync(60_000)

    // Halbe Restlaufzeit Vorlauf → alle 30 s, weit unter dem 10/min-Limit
    expect(refreshCalls(fetchMock)).toHaveLength(2)
  })

  it('ends the session when the scheduled refresh is rejected for good', async () => {
    stubAuthFetch(() => new Response(null, { status: 401 }))
    localStorage.setItem('visu_jwt', fakeJwt({ exp: Math.floor(Date.now() / 1000) + 3600 }))
    localStorage.setItem('visu_refresh_token', 'refresh-old')
    localStorage.setItem('visu_is_admin', '1')
    const unauthorized = vi.fn()
    window.addEventListener('visu:unauthorized', unauthorized)

    scheduleTokenRefresh()
    await vi.advanceTimersByTimeAsync(3600_000 - 60_000)

    expect(localStorage.getItem('visu_jwt')).toBeNull()
    expect(localStorage.getItem('visu_refresh_token')).toBeNull()
    expect(localStorage.getItem('visu_is_admin')).toBeNull()
    expect(unauthorized).toHaveBeenCalledTimes(1)
    window.removeEventListener('visu:unauthorized', unauthorized)
  })

  it('replaces a previously scheduled refresh instead of stacking timers', () => {
    localStorage.setItem('visu_jwt', fakeJwt({ exp: Math.floor(Date.now() / 1000) + 3600 }))
    localStorage.setItem('visu_refresh_token', 'refresh-old')

    const baseline = vi.getTimerCount()
    scheduleTokenRefresh()
    scheduleTokenRefresh()
    expect(vi.getTimerCount() - baseline).toBe(1)

    cancelTokenRefresh()
    expect(vi.getTimerCount() - baseline).toBe(0)
    cancelTokenRefresh()
    expect(vi.getTimerCount() - baseline).toBe(0)
  })
})
