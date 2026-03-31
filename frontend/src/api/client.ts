/**
 * API-Client für openTWS Visu
 *
 * - JWT aus localStorage (admin-Login)
 * - Session-Tokens aus sessionStorage (PIN-Auth pro Knoten)
 * - 401 → automatischer Redirect zur Login-Route
 */

const BASE = '/api/v1'

// ── Token-Verwaltung ──────────────────────────────────────────────────────────

export function getJwt(): string | null {
  return localStorage.getItem('visu_jwt')
}

export function setJwt(token: string): void {
  localStorage.setItem('visu_jwt', token)
}

export function clearJwt(): void {
  localStorage.removeItem('visu_jwt')
}

/** Session-Token für einen bestimmten Knoten (PIN-Auth), nur für diese Browser-Session */
export function getSessionToken(nodeId: string): string | null {
  return sessionStorage.getItem(`session_${nodeId}`)
}

export function setSessionToken(nodeId: string, token: string): void {
  sessionStorage.setItem(`session_${nodeId}`, token)
}

// ── Request-Helper ────────────────────────────────────────────────────────────

type RequestOptions = Omit<RequestInit, 'headers'> & {
  headers?: Record<string, string>
  /** Falls gesetzt, wird dieser Session-Token als X-Session-Token mitgeschickt */
  sessionToken?: string
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const jwt = getJwt()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...opts.headers,
  }

  if (jwt) headers['Authorization'] = `Bearer ${jwt}`
  if (opts.sessionToken) headers['X-Session-Token'] = opts.sessionToken

  const res = await fetch(`${BASE}${path}`, {
    ...opts,
    headers,
  })

  if (res.status === 401) {
    clearJwt()
    // Redirect zur Login-Seite — der Router fängt das auf
    window.dispatchEvent(new CustomEvent('visu:unauthorized'))
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({ detail: res.statusText }))
    throw new Error(body.detail ?? res.statusText)
  }

  // 204 No Content
  if (res.status === 204) return undefined as T

  return res.json() as Promise<T>
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export const auth = {
  login(username: string, password: string) {
    const form = new URLSearchParams({ username, password, grant_type: 'password' })
    return fetch(`${BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: form,
    }).then(async (res) => {
      if (!res.ok) {
        const body = await res.json().catch(() => ({ detail: res.statusText }))
        throw new Error(body.detail ?? 'Login fehlgeschlagen')
      }
      return res.json() as Promise<{ access_token: string; token_type: string }>
    })
  },
}

// ── Visu-Nodes ────────────────────────────────────────────────────────────────

import type { VisuNode, PageConfig, PinAuthResponse } from '@/types'

export const visu = {
  tree: () => request<VisuNode[]>('/visu/tree'),

  getNode: (id: string) => request<VisuNode>(`/visu/nodes/${id}`),

  createNode: (data: Partial<VisuNode>) =>
    request<VisuNode>('/visu/nodes', { method: 'POST', body: JSON.stringify(data) }),

  updateNode: (id: string, data: Partial<VisuNode>) =>
    request<VisuNode>(`/visu/nodes/${id}`, { method: 'PATCH', body: JSON.stringify(data) }),

  deleteNode: (id: string) =>
    request<void>(`/visu/nodes/${id}`, { method: 'DELETE' }),

  getBreadcrumb: (id: string) =>
    request<VisuNode[]>(`/visu/nodes/${id}/breadcrumb`),

  getChildren: (id: string) =>
    request<VisuNode[]>(`/visu/nodes/${id}/children`),

  copyNode: (id: string, targetParentId: string, newName: string) =>
    request<VisuNode>(`/visu/nodes/${id}/copy`, {
      method: 'POST',
      body: JSON.stringify({ target_parent_id: targetParentId, new_name: newName }),
    }),

  moveNode: (id: string, newParentId: string, order: number) =>
    request<VisuNode>(`/visu/nodes/${id}/move`, {
      method: 'PUT',
      body: JSON.stringify({ new_parent_id: newParentId, order }),
    }),

  pinAuth: (id: string, pin: string) =>
    request<PinAuthResponse>(`/visu/nodes/${id}/auth`, {
      method: 'POST',
      body: JSON.stringify({ pin }),
    }),

  getPage: (id: string, sessionToken?: string) =>
    request<PageConfig>(`/visu/pages/${id}`, { sessionToken }),

  savePage: (id: string, config: PageConfig) =>
    request<void>(`/visu/pages/${id}`, {
      method: 'PUT',
      body: JSON.stringify(config),
    }),
}

// ── DataPoints ────────────────────────────────────────────────────────────────

import type { DataPoint, PaginatedResponse } from '@/types'

export const datapoints = {
  search: (q: string, page = 0, size = 50) =>
    request<PaginatedResponse<DataPoint>>(
      `/search?q=${encodeURIComponent(q)}&page=${page}&size=${size}`
    ),

  get: (id: string) => request<DataPoint>(`/datapoints/${id}`),

  getValue: (id: string) =>
    request<{ v: unknown; u: string | null; t: string; q: string }>(
      `/datapoints/${id}/value`
    ),

  write: (id: string, value: unknown) =>
    request<void>(`/datapoints/${id}/value`, {
      method: 'POST',
      body: JSON.stringify({ value }),
    }),
}

// ── History ───────────────────────────────────────────────────────────────────

export const history = {
  query: (id: string, from: string, to: string, limit = 500) =>
    request<{ ts: string; v: unknown }[]>(
      `/history/${id}?from=${from}&to=${to}&limit=${limit}`
    ),
}
