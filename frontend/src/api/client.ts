/**
 * API-Client für open bridge server Visu
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

export function getIsAdmin(): boolean {
  return localStorage.getItem('visu_is_admin') === '1'
}

export function setIsAdmin(value: boolean): void {
  localStorage.setItem('visu_is_admin', value ? '1' : '0')
}

export function clearIsAdmin(): void {
  localStorage.removeItem('visu_is_admin')
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

interface WriteContext {
  pageId?: string
  sessionToken?: string
  /** Knoten, der das Access-Level definiert (für Session-Token-Verwaltung bei Ablauf) */
  definingId?: string
}
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

  me() {
    return request<{ id: string; username: string; is_admin: boolean }>('/auth/me', { silent401: true })
  },
}

// ── Visu-Nodes ────────────────────────────────────────────────────────────────

import type { VisuNode, PageConfig, PinAuthResponse, UserResponse } from '@/types'

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

  copyNode: (id: string, targetParentId: string | null, newName: string) =>
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
      silent401: true,
    }),

  getPage: (id: string, sessionToken?: string) =>
    request<PageConfig>(`/visu/pages/${id}`, { sessionToken }),

  /** Lädt alle Widget-Instanzen einer Seite ohne Zugriffsprüfung — für WidgetRef. */
  getWidgetRef: (pageId: string) =>
    request<import('@/types').WidgetInstance[]>(`/visu/widget-ref/${pageId}`, { silent401: true }),

  savePage: (id: string, config: PageConfig) =>
    request<void>(`/visu/pages/${id}`, {
      method: 'PUT',
      body: JSON.stringify(config),
    }),

  getNodeUsers: (id: string) =>
    request<string[]>(`/visu/nodes/${id}/users`),

  setNodeUsers: (id: string, usernames: string[]) =>
    request<void>(`/visu/nodes/${id}/users`, {
      method: 'PUT',
      body: JSON.stringify({ usernames }),
    }),

  exportNode: (id: string) => request<unknown>(`/visu/nodes/${id}/export`),

  importNodes: (payload: unknown) =>
    request<VisuNode>('/visu/nodes/import', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),
}

// ── Users ─────────────────────────────────────────────────────────────────────

export const users = {
  list: () => request<UserResponse[]>('/auth/users'),
}

// ── DataPoints ────────────────────────────────────────────────────────────────

import type { DataPoint, PaginatedResponse } from '@/types'

export interface BindingOut {
  id: string
  datapoint_id: string
  adapter_type: string
  adapter_instance_id: string | null
  instance_name: string | null
  direction: string
  config: Record<string, unknown>
  enabled: boolean
  created_at: string
  updated_at: string
}

export const datapoints = {
  search: (q: string, page = 0, size = 50, type = '') => {
    const params = new URLSearchParams({ q, page: String(page), size: String(size) })
    if (type) params.set('type', type)
    return request<PaginatedResponse<DataPoint>>(`/search/?${params}`)
  },

  get: (id: string) => request<DataPoint>(`/datapoints/${id}`),

  getValue: (id: string, silent401 = false) => {
    const headers: Record<string, string> = {}
    if (_writeContext.pageId)       headers['X-Page-Id']       = _writeContext.pageId
    if (_writeContext.sessionToken) headers['X-Session-Token'] = _writeContext.sessionToken
    return request<{ value: unknown; unit: string | null; ts: string | null; quality: string }>(
      `/datapoints/${id}/value`, { silent401, headers }
    )
  },

  listBindings: (dpId: string) =>
    request<BindingOut[]>(`/datapoints/${dpId}/bindings`),

  updateBinding: (dpId: string, bindingId: string, data: { config?: Record<string, unknown>; enabled?: boolean }) =>
    request<BindingOut>(`/datapoints/${dpId}/bindings/${bindingId}`, {
      method: 'PATCH',
      body: JSON.stringify(data),
    }),

  createBinding: (dpId: string, data: { adapter_instance_id: string; direction: string; config?: Record<string, unknown>; enabled?: boolean }) =>
    request<BindingOut>(`/datapoints/${dpId}/bindings`, {
      method: 'POST',
      body: JSON.stringify(data),
    }),

  deleteBinding: (dpId: string, bindingId: string) =>
    request<void>(`/datapoints/${dpId}/bindings/${bindingId}`, { method: 'DELETE' }),

  write: async (id: string, value: unknown) => {
    const headers: Record<string, string> = {}
    if (_writeContext.pageId)      headers['X-Page-Id']       = _writeContext.pageId
    if (_writeContext.sessionToken) headers['X-Session-Token'] = _writeContext.sessionToken
    try {
      return await request<void>(`/datapoints/${id}/value`, {
        method: 'POST',
        body: JSON.stringify({ value }),
        headers,
      })
    } catch (err: unknown) {
      if (err instanceof Error && err.message === 'Valid session token required') {
        // Session abgelaufen (z.B. nach Server-Neustart) — Token löschen und Re-Auth auslösen
        const defId = _writeContext.definingId
        if (defId) sessionStorage.removeItem(`session_${defId}`)
        window.dispatchEvent(new CustomEvent('visu:session-expired'))
      }
      throw err
    }
  },
}

// ── Adapters (Visu-seitig, nur Lesezugriff) ───────────────────────────────────

export interface AdapterInstanceSummary {
  id: string
  adapter_type: string
  name: string
  running: boolean
  connected: boolean
}

export interface InstanceBindingEntry {
  binding_id: string
  datapoint_id: string
  datapoint_name: string
  enabled: boolean
  config: Record<string, unknown>
}

export interface HolidayEntry {
  date: string
  name: string
}

export const adapters = {
  listInstances: () =>
    request<AdapterInstanceSummary[]>('/adapters/instances'),

  instanceBindings: (instanceId: string) =>
    request<InstanceBindingEntry[]>(`/adapters/instances/${instanceId}/bindings`),

  zsuHolidays: (instanceId: string, year = 0) =>
    request<HolidayEntry[]>(`/adapters/instances/${instanceId}/holidays${year ? `?year=${year}` : ''}`),
}

// ── Icons ─────────────────────────────────────────────────────────────────────

export interface IconOut {
  name: string
  size: number
  content: string  // inline SVG UTF-8
}

export interface IconListOut {
  total: number
  icons: IconOut[]
}

export const icons = {
  list: () => request<IconListOut>('/icons/'),
}

// ── History ───────────────────────────────────────────────────────────────────

export const history = {
  query: (id: string, from: string, to: string, limit = 10000) => {
    const headers: Record<string, string> = {}
    if (_writeContext.pageId)      headers['X-Page-Id']       = _writeContext.pageId
    if (_writeContext.sessionToken) headers['X-Session-Token'] = _writeContext.sessionToken
    return request<{ ts: string; v: unknown; u: string | null; q: string }[]>(
      `/history/${id}?from=${from}&to=${to}&limit=${limit}`,
      { headers, silent401: true },
    )
  },
}
