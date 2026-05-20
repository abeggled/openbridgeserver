/**
 * API helpers for E2E tests.
 *
 * Token strategy (avoids hitting the server's login rate-limiter):
 *   1. Read the access_token from .auth/admin.json — the storageState file
 *      that auth.setup.ts writes before any spec runs.
 *   2. Fall back to a fresh login only if the file is missing (e.g. running
 *      a single spec in isolation without auth setup).
 *
 * The resolved token is cached at module level (one value per worker process).
 */

import * as fs from 'fs'
import * as path from 'path'
import * as dotenv from 'dotenv'
import { expect, type Page } from '@playwright/test'

// Load the repository-root .env so `OBS_HTTP_HOST_PORT` resolves the same way
// it does in playwright.config.ts when the runner hasn't pre-populated it.
dotenv.config({ path: path.resolve(__dirname, '..', '..', '.env') })

const OBS_HTTP_HOST_PORT = process.env.OBS_HTTP_HOST_PORT ?? '8080'
export const BASE_URL = process.env.BASE_URL ?? `http://localhost:${OBS_HTTP_HOST_PORT}`

// URL that the **server itself** (inside its container or process) uses to
// reach its own API — needed by specs that wire URLs into logic nodes which
// the backend then dereferences (`api_client`, webhooks, …). It is *not*
// generally the same as BASE_URL: in a Docker-Compose deployment the host
// port mapping (e.g. 8082 → 8080) is invisible inside the container, so the
// server still has to talk to `localhost:8080`. Default matches the in-
// container listen port; override via `INTERNAL_BASE_URL` for bare-metal
// or non-standard setups.
export const INTERNAL_BASE_URL = process.env.INTERNAL_BASE_URL ?? 'http://localhost:8080'

const E2E_USER = process.env.E2E_USER ?? 'admin'
const E2E_PASS = process.env.E2E_PASS ?? 'admin'

// Resolved once per worker process.
let _cachedToken: string | null = null

function _readTokenFromStorageState(): string | null {
  try {
    // Path relative to this helpers.ts file: ../.auth/admin.json
    // helpers.ts lives in tests/gui/ — .auth/admin.json is in the same directory
    const stateFile = path.resolve(__dirname, '.auth/admin.json')
    if (!fs.existsSync(stateFile)) return null
    const state = JSON.parse(fs.readFileSync(stateFile, 'utf-8'))
    for (const origin of state.origins ?? []) {
      for (const item of (origin.localStorage ?? []) as Array<{ name: string; value: string }>) {
        if (item.name === 'access_token') return item.value
      }
    }
  } catch {
    // Ignore read/parse errors → fall through to fresh login
  }
  return null
}

export async function getToken(): Promise<string> {
  if (_cachedToken) return _cachedToken
  // Prefer the token saved by auth.setup.ts (no network call, no rate-limit risk)
  _cachedToken = _readTokenFromStorageState()
  if (_cachedToken) return _cachedToken
  // Fallback: fresh login (e.g. isolated run without auth setup)
  const res = await fetch(`${BASE_URL}/api/v1/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ username: E2E_USER, password: E2E_PASS }),
  })
  if (!res.ok) throw new Error(`Login failed: ${res.status}`)
  const data = await res.json()
  _cachedToken = data.access_token as string
  return _cachedToken
}

export async function apiGet(path: string): Promise<unknown> {
  const token = await getToken()
  const res = await fetch(`${BASE_URL}${path}`, {
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok) throw new Error(`GET ${path} failed: ${res.status}`)
  return res.json()
}

export async function apiPost(path: string, body: unknown): Promise<unknown> {
  const token = await getToken()
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'POST',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`POST ${path} failed: ${res.status} — ${text}`)
  }
  // Some POST endpoints return 204 No Content (e.g. set datapoint value)
  if (res.status === 204) return null
  return res.json()
}

export async function apiPut(path: string, body: unknown): Promise<unknown> {
  const token = await getToken()
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'PUT',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`PUT ${path} failed: ${res.status} — ${text}`)
  }
  // PUT /api/v1/visu/pages returns 204 No Content
  if (res.status === 204) return null
  return res.json()
}

export async function apiPatch(path: string, body: unknown): Promise<unknown> {
  const token = await getToken()
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'PATCH',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`PATCH ${path} failed: ${res.status} — ${text}`)
  }
  if (res.status === 204) return null
  return res.json()
}

export async function apiDelete(path: string): Promise<void> {
  const token = await getToken()
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'DELETE',
    headers: { Authorization: `Bearer ${token}` },
  })
  if (!res.ok && res.status !== 404) {
    throw new Error(`DELETE ${path} failed: ${res.status}`)
  }
}

/**
 * Navigate to the Monitor (/ringbuffer) and wait until it is interactive.
 *
 * The Monitor holds a persistent WebSocket plus a 10 s /stats poll, so
 * `page.waitForLoadState('networkidle')` never settles and times out. Wait
 * for the always-rendered status badge instead — once it is visible the view
 * has mounted and the initial query has run.
 */
export async function gotoMonitor(page: Page): Promise<void> {
  await page.goto('/ringbuffer')
  await waitForMonitorReady(page)
}

/** Wait for the Monitor view to be interactive (e.g. after a page.reload()). */
export async function waitForMonitorReady(page: Page): Promise<void> {
  await expect(page.locator('[data-testid="status-badge"]')).toBeVisible({ timeout: 15_000 })
}

/**
 * Navigate to the Monitor and wait until it is ready to receive live pushes.
 *
 * Tests that push values via the API and expect them to appear through the
 * live `ringbuffer_entry` push MUST use this. Two preconditions must hold
 * before the test pushes a value, otherwise the entry is silently lost:
 *   1. The WebSocket is connected — shown by the "Live" status badge. The
 *      server does not replay events written before the handshake.
 *   2. The initial table query has returned — until then `load()` overwrites
 *      `entries` and would clobber a live entry that arrived during loading.
 */
export async function gotoMonitorLive(page: Page): Promise<void> {
  const initialQuery = page
    .waitForResponse(
      (r) =>
        /\/api\/v1\/ringbuffer\/(query\/v2|filtersets\/query)$/.test(new URL(r.url()).pathname),
      { timeout: 20_000 },
    )
    .catch(() => null)
  await page.goto('/ringbuffer')
  await expect(page.locator('[data-testid="status-badge"]')).toContainText('Live', { timeout: 20_000 })
  await initialQuery
}

/**
 * Delete every ringbuffer filterset.
 *
 * Filtersets are global admin state. A test (or a previously failed test
 * whose `finally` cleanup was skipped) can leave a `topbar_active` set
 * behind. RingBufferView then loads it on mount, and onLiveEntry gates every
 * live `ringbuffer_entry` against that set — dropping pushes for any DP that
 * does not match. Call this in a beforeEach so each Monitor test starts from
 * a clean, unfiltered live feed.
 */
export async function deleteAllFiltersets(): Promise<void> {
  const sets = (await apiGet('/api/v1/ringbuffer/filtersets')) as Array<{ id: string }>
  for (const set of sets) {
    await apiDelete(`/api/v1/ringbuffer/filtersets/${set.id}`)
  }
}

/** Upload a single SVG file to the icon library. `name` is the filename without extension. */
export async function apiUploadIcon(name: string, svgContent: string): Promise<void> {
  const token = await getToken()
  const formData = new FormData()
  formData.append(
    'files',
    new Blob([svgContent], { type: 'image/svg+xml' }),
    `${name}.svg`,
  )
  const res = await fetch(`${BASE_URL}/api/v1/icons/import`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${token}` },
    body: formData,
  })
  if (!res.ok) {
    const text = await res.text()
    throw new Error(`Icon upload failed: ${res.status} — ${text}`)
  }
}

/** Delete one or more icons from the icon library by name (without .svg extension). */
export async function apiDeleteIcons(names: string[]): Promise<void> {
  await apiDeleteWithBody('/api/v1/icons/', { names })
}

export async function apiDeleteWithBody(path: string, body: unknown): Promise<unknown> {
  const token = await getToken()
  const res = await fetch(`${BASE_URL}${path}`, {
    method: 'DELETE',
    headers: {
      Authorization: `Bearer ${token}`,
      'Content-Type': 'application/json',
    },
    body: JSON.stringify(body),
  })
  if (!res.ok && res.status !== 404) {
    const text = await res.text()
    throw new Error(`DELETE ${path} failed: ${res.status} — ${text}`)
  }
  if (res.status === 204) return null
  return res.json()
}
