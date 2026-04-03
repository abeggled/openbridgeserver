/**
 * API-Client für openTWS Visu
 *
 * - JWT aus localStorage (admin-Login)
 * - Session-Tokens aus sessionStorage (PIN-Auth pro Knoten)
 * - 401 → automatischer Redirect zur Login-Route
 */

const BASE = '/api/v1'

/** FastAPI gibt detail manchmal als Array zurück — immer zu String normalisieren */
function extractDetail(body: unknown, fallback: string): string {
  if (!body || typeof body !== 'object') return fallback
  const detail = (body as Record<string, unknown>).detail
  if (!detail) return fallback
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail
      .map((e) => (typeof e === 'object' && e !== null ? (e as Record<string, unknown>).msg ?? JSON.stringify(e) : String(e)))
      .join(', ')
  }
  return String(detail)
}

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
  const raw = sessionStorage.getItem(`session_${nodeId}`)
  if (!raw) return null
  try {
    const parsed = JSON.parse(raw)
    if (parsed && typeof parsed === 'object' && 'token' in parsed) {
      if (Date.now() > parsed.expiresAt) {
        sessionStorage.removeItem(`session_${nodeId}`)
        return null
      }
      return parsed.token as string
    }
  } catch { /* altes Format: plain string, unten zurückgeben */ }
  return raw
}

export function setSessionToken(nodeId: string, token: string, expiresIn = 3600): void {
  sessionStorage.setItem(`session_${nodeId}`, JSON.stringify({
    token,
    expiresAt: Date.now() + expiresIn * 1000,
  }))
}

// ── Write-Kontext ─────────────────────────────────────────────────────────────
// Wird von VisuViewer gesetzt bevor Widgets rendern; automatisch bei Write mitgeschickt.

interface WriteContext { pageId?: string; sessionToken?: string }
let _writeContext: WriteContext = {}

export function setWriteContext(ctx: WriteContext): void { _writeContext = ctx }
export function clearWriteContext(): void { _writeContext = {} }

// ── Request-Helper ────────────────────────────────────────────────────────────

type RequestOptions = Omit<RequestInit, 'headers'> & {
  headers?: Record<string, string>
  /** Falls gesetzt, wird dieser Session-Token als X-Session-Token mitgeschickt */
  sessionToken?: string
  /** 401 still throws but does NOT dispatch visu:unauthorized (no global redirect) */
  silent401?: boolean
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
    if (!opts.silent401) {
      clearJwt()
      // Redirect zur Login-Seite — der Router fängt das auf
      window.dispatchEvent(new CustomEvent('visu:unauthorized'))
    }
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => null)
    throw new Error(extractDetail(body, res.statusText))
  }

  // 204 No Content
  if (res.status === 204) return undefined as T

  return res.json() as Promise<T>
}

// ── Auth ──────────────────────────────────────────────────────────────────────

export const auth = {
  login(username: string, password: string) {
    return fetch(`${BASE}/auth/login`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    }).then(async (res) => {
      if (!res.ok) {
        const body = await res.json().catch(() => null)
        throw new Error(extractDetail(body, 'Login fehlgeschlagen'))
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

  moveNode: (id: string, newParentId: string | null, order: number) =>
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
  search: (q: string, page = 0, size = 50, type = '') => {
    const params = new URLSearchParams({ q, page: String(page), size: String(size) })
    if (type) params.set('type', type)
    return request<PaginatedResponse<DataPoint>>(`/search?${params}`)
  },

  get: (id: string) => request<DataPoint>(`/datapoints/${id}`),

  getValue: (id: string, silent401 = false) =>
    request<{ value: unknown; unit: string | null; ts: string | null; quality: string }>(
      `/datapoints/${id}/value`, { silent401 }
    ),

  write: (id: string, value: unknown) => {
    const headers: Record<string, string> = {}
    if (_writeContext.pageId)      headers['X-Page-Id']       = _writeContext.pageId
    if (_writeContext.sessionToken) headers['X-Session-Token'] = _writeContext.sessionToken
    return request<void>(`/datapoints/${id}/value`, {
      method: 'POST',
      body: JSON.stringify({ value }),
      headers,
    })
  },
}

// ── History ───────────────────────────────────────────────────────────────────

export const history = {
  query: (id: string, from: string, to: string, limit = 500) =>
    request<{ ts: string; v: unknown; u: string | null; q: string }[]>(
      `/history/${id}?from=${from}&to=${to}&limit=${limit}`
    ),
}
