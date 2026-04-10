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

const BASE_URL = process.env.BASE_URL ?? 'http://localhost:8080'
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
