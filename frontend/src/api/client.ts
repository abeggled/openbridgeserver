/**
 * API-Client für open bridge server Visu
 *
 * - JWT aus localStorage (admin-Login)
 * - Refresh-Token aus localStorage — erneuert den JWT transparent (Issue #1160)
 * - Session-Tokens aus sessionStorage (PIN-Auth pro Knoten)
 * - 401 → einmaliger Refresh-Versuch, sonst Redirect zur Login-Route
 */

import { notifyAuthTokenRefreshed } from '@/utils/authEvents'

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

export class ApiRequestError extends Error {
  constructor(
    message: string,
    readonly status?: number,
    readonly code?: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message)
    this.name = 'ApiRequestError'
  }
}

// ── Token-Verwaltung ──────────────────────────────────────────────────────────

const JWT_KEY = 'visu_jwt'
const REFRESH_KEY = 'visu_refresh_token'
const IS_ADMIN_KEY = 'visu_is_admin'

export function getJwt(): string | null {
  return localStorage.getItem(JWT_KEY)
}

function setJwt(token: string): void {
  localStorage.setItem(JWT_KEY, token)
}

function clearJwt(): void {
  localStorage.removeItem(JWT_KEY)
}

function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY)
}

function setRefreshToken(token: string): void {
  localStorage.setItem(REFRESH_KEY, token)
}

function clearRefreshToken(): void {
  localStorage.removeItem(REFRESH_KEY)
}

/** Beide Tokens nach Login/Refresh ablegen und den proaktiven Refresh neu planen */
export function setTokens(accessToken: string, refreshToken?: string | null): void {
  setJwt(accessToken)
  if (refreshToken) setRefreshToken(refreshToken)
  scheduleTokenRefresh()
}

/** Access-Token, Refresh-Token und Admin-Flag verwerfen (Logout / endgültiges 401) */
export function clearAuthTokens(): void {
  cancelTokenRefresh()
  clearJwt()
  clearRefreshToken()
  clearIsAdmin()
}

export function getIsAdmin(): boolean {
  return localStorage.getItem(IS_ADMIN_KEY) === '1'
}

export function setIsAdmin(value: boolean): void {
  localStorage.setItem(IS_ADMIN_KEY, value ? '1' : '0')
}

function clearIsAdmin(): void {
  localStorage.removeItem(IS_ADMIN_KEY)
}

// ── Token-Refresh ─────────────────────────────────────────────────────────────
// /auth/refresh ist auf 10 Requests/Minute limitiert, die Visu feuert beim
// Seitenaufbau aber viele Requests parallel. Alle 401-Antworten teilen sich
// deshalb genau einen In-Flight-Refresh.

/**
 * - `renewed`    — neuer Access-Token liegt vor
 * - `rejected`   — Refresh-Token abgelehnt; die Sitzung ist endgültig vorbei
 * - `missing`    — gar kein Refresh-Token gespeichert; nichts zu erneuern
 * - `transient`  — Server nicht erreichbar, 5xx, Rate-Limit oder unbrauchbare
 *                  Antwort; Tokens behalten und später erneut versuchen
 * - `superseded` — eine andere Anmeldung hat die Tokens übernommen; sie gehören
 *                  nicht mehr zu diesem Request und dürfen nicht angefasst werden
 */
type RefreshOutcome = 'renewed' | 'rejected' | 'missing' | 'transient' | 'superseded'
type RefreshResult = { token: string | null; outcome: RefreshOutcome }

/** Nur diese Ausgänge bedeuten, dass die gespeicherte Sitzung wertlos ist */
function endsSession(outcome: RefreshOutcome): boolean {
  return outcome === 'rejected' || outcome === 'missing'
}

let _refreshInFlight: Promise<RefreshResult> | null = null

async function performRefresh(): Promise<RefreshResult> {
  const refreshToken = getRefreshToken()
  if (!refreshToken) return { token: null, outcome: 'missing' }

  /**
   * Hat zwischenzeitlich eine Abmeldung oder eine andere Anmeldung die Tokens
   * übernommen, geht uns dieses Ergebnis nichts mehr an — weder ein Erfolg, der
   * die alte Sitzung wiederbeleben würde, noch eine Ablehnung, die die neue
   * abräumen würde. Nach jedem `await` erneut prüfen.
   */
  const superseded = () => getRefreshToken() !== refreshToken

  let res: Response | null = null
  try {
    res = await fetch(`${BASE}/auth/refresh`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })
  } catch {
    // Netzwerkfehler — Tokens behalten, der nächste Versuch kann klappen
  }
  if (superseded()) return { token: null, outcome: 'superseded' }
  if (res === null) return { token: null, outcome: 'transient' }
  if (!res.ok) {
    // 408 (Gateway-Timeout) und 429 sagen nichts über die Gültigkeit des
    // Refresh-Tokens aus — genauso wenig wie ein 5xx.
    const transient = res.status === 408 || res.status === 429 || res.status >= 500
    return { token: null, outcome: transient ? 'transient' : 'rejected' }
  }
  const data = await res.json().catch(() => null) as { access_token?: string; refresh_token?: string } | null
  if (superseded()) return { token: null, outcome: 'superseded' }
  // Unbrauchbare Antwort wie ein Serverfehler behandeln — der Refresh-Token ist
  // deswegen nicht ungültig und darf nicht verworfen werden.
  if (!data?.access_token) return { token: null, outcome: 'transient' }
  setTokens(data.access_token, data.refresh_token)
  notifyAuthTokenRefreshed()
  return { token: data.access_token, outcome: 'renewed' }
}

/** Genau ein Refresh gleichzeitig — parallele Aufrufer teilen sich den Promise */
function refreshSession(): Promise<RefreshResult> {
  if (_refreshInFlight) return _refreshInFlight
  const pending = performRefresh().finally(() => { _refreshInFlight = null })
  _refreshInFlight = pending
  return pending
}

/**
 * Access-Token erneuern. Parallele Aufrufe teilen sich denselben Promise.
 * Liefert den neuen Token oder `null`, wenn kein Refresh möglich war.
 */
export async function refreshAccessToken(): Promise<string | null> {
  return (await refreshSession()).token
}

// ── Proaktiver Refresh ────────────────────────────────────────────────────────
// Eine offene Viewer-Seite (Wandpanel) feuert keine HTTP-Requests mehr, sobald
// sie geladen ist. Ohne 401 gäbe es also keinen Auslöser für den Refresh und die
// WebSocket-Verbindung verlöre nach Token-Ablauf still ihren Datapoint-Scope.
// Deshalb wird der Refresh zusätzlich kurz vor Ablauf des JWT eingeplant.

const REFRESH_LEEWAY_MS = 60_000
// /auth/refresh erlaubt 10 Requests/Minute — nie öfter als alle 10 s erneuern.
const MIN_REFRESH_DELAY_MS = 10_000
const RETRY_BASE_MS = 30_000
const RETRY_MAX_MS = 600_000
// setTimeout rechnet mit 32-Bit-Vorzeichen: alles darüber wird auf 1 ms gekürzt.
// security.jwt_expire_minutes ist unbegrenzt, 43200 (30 Tage) läuft darüber.
const MAX_TIMER_MS = 2_147_483_647

let _refreshTimer: ReturnType<typeof setTimeout> | null = null
let _retryDelay = RETRY_BASE_MS

/** JWT-Payload lesen; `null` wenn er nicht dekodierbar ist */
function jwtClaims(token: string): Record<string, unknown> | null {
  const payload = token.split('.')[1]
  if (!payload) return null
  const base64 = payload.replace(/-/g, '+').replace(/_/g, '/')
  const padded = base64 + '='.repeat((4 - (base64.length % 4)) % 4)
  try {
    const parsed: unknown = JSON.parse(atob(padded))
    return typeof parsed === 'object' && parsed !== null ? parsed as Record<string, unknown> : null
  } catch {
    return null
  }
}

/** `exp` (ms seit Epoch) aus dem JWT-Payload lesen; `null` wenn nicht lesbar */
function jwtExpiresAt(token: string): number | null {
  const exp = jwtClaims(token)?.exp
  return typeof exp === 'number' ? exp * 1000 : null
}

/** Frühester der beiden Ablaufzeitpunkte; `null` wenn keiner lesbar ist */
function earliestExpiry(...expiries: Array<number | null>): number | null {
  const known = expiries.filter((value): value is number => value !== null)
  return known.length === 0 ? null : Math.min(...known)
}

/** Angemeldeter Benutzer laut JWT; `null` wenn nicht lesbar */
function jwtSubject(token: string): string | null {
  const sub = jwtClaims(token)?.sub
  return typeof sub === 'string' ? sub : null
}

export function cancelTokenRefresh(): void {
  if (_refreshTimer !== null) {
    clearTimeout(_refreshTimer)
    _refreshTimer = null
  }
}

function armRefreshTimer(delay: number): void {
  cancelTokenRefresh()
  if (delay > MAX_TIMER_MS) {
    // Lange Laufzeiten in Etappen abwarten, sonst kürzt setTimeout auf 1 ms und
    // jeder Durchlauf würde sofort erneut /auth/refresh aufrufen.
    _refreshTimer = setTimeout(() => {
      _refreshTimer = null
      scheduleTokenRefresh()
    }, MAX_TIMER_MS)
    return
  }
  _refreshTimer = setTimeout(() => {
    _refreshTimer = null
    void refreshSession().then(({ outcome }) => {
      // Erfolg plant sich über setTokens() → scheduleTokenRefresh() selbst neu.
      if (outcome === 'renewed') return
      if (outcome === 'transient' && getRefreshToken()) {
        // Ohne erneuten Versuch bliebe eine dauerhaft geöffnete Viewer-Seite bis
        // zum nächsten HTTP-401 ohne Erneuerung — und ein Wandpanel feuert keinen.
        armRefreshTimer(_retryDelay)
        _retryDelay = Math.min(_retryDelay * 2, RETRY_MAX_MS)
        return
      }
      if (outcome === 'superseded') {
        // Ein anderer Tab hat die Sitzung im gemeinsamen localStorage erneuert.
        // Ohne neuen Timer bliebe dieser Tab ohne Erneuerung zurück; ohne das
        // Event behielte sein WebSocket den alten JWT im Subprotokoll und
        // verlöre nach dessen Ablauf still den Datapoint-Scope. Der gespeicherte
        // Token ist bereits der neue — die Verbraucher müssen ihn nur abholen.
        scheduleTokenRefresh()
        notifyAuthTokenRefreshed()
        return
      }
      if (outcome === 'rejected') {
        // Refresh-Token endgültig ungültig. Ohne Aufräumen liefe die Seite bis
        // zum Ablauf des Access-Tokens weiter und verlöre danach still die
        // WebSocket-Werte, statt die Sitzung sichtbar zu beenden.
        clearAuthTokens()
        window.dispatchEvent(new CustomEvent('visu:unauthorized'))
      }
    })
  }, delay)
}

/** Refresh kurz vor Ablauf des aktuellen JWT einplanen (idempotent) */
export function scheduleTokenRefresh(): void {
  cancelTokenRefresh()
  _retryDelay = RETRY_BASE_MS
  const jwt = getJwt()
  const refreshToken = getRefreshToken()
  if (!jwt || !refreshToken) return
  // Der Refresh-Token läuft fest nach 30 Tagen ab (`_REFRESH_DAYS`), der
  // Access-Token nach `security.jwt_expire_minutes` — das kann länger sein.
  // Massgeblich ist, was zuerst abläuft: nach dem Refresh-Token gibt es nichts
  // mehr zu erneuern, die Sitzung liesse sich nie über Tag 30 hinaus verlängern.
  const expiresAt = earliestExpiry(jwtExpiresAt(jwt), jwtExpiresAt(refreshToken))
  if (expiresAt === null) return
  const remaining = expiresAt - Date.now()
  // Bei kurzer Token-Laufzeit (security.jwt_expire_minutes: 1) würde ein fester
  // Vorlauf von 60 s jeden neuen Token sofort wieder fällig machen — höchstens
  // die halbe Restlaufzeit vorziehen.
  const leeway = Math.min(REFRESH_LEEWAY_MS, Math.max(remaining, 0) / 2)
  armRefreshTimer(Math.max(remaining - leeway, MIN_REFRESH_DELAY_MS))
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

export interface WriteContext {
  pageId?: string
  sessionToken?: string
  /** Knoten, der das Access-Level definiert (für Session-Token-Verwaltung bei Ablauf) */
  definingId?: string
}
let _writeContext: WriteContext = {}

export function setWriteContext(ctx: WriteContext): void { _writeContext = ctx }
export function clearWriteContext(): void { _writeContext = {} }
export function getWriteContext(): WriteContext { return _writeContext }

// ── Request-Helper ────────────────────────────────────────────────────────────

type RequestOptions = Omit<RequestInit, 'headers'> & {
  headers?: Record<string, string>
  /** Falls gesetzt, wird dieser Session-Token als X-Session-Token mitgeschickt */
  sessionToken?: string
  /** 401 still throws but does NOT dispatch visu:unauthorized (no global redirect) */
  silent401?: boolean
  /**
   * 401 bedeutet auf dieser Route eine fehlgeschlagene Anmeldung (z. B. falscher
   * PIN), nicht einen abgelaufenen JWT — kein Refresh-Versuch, kein Retry.
   */
  noAuthRefresh?: boolean
}

function buildHeaders(opts: RequestOptions, jwtOverride?: string): Record<string, string> {
  const jwt = jwtOverride ?? getJwt()
  const headers: Record<string, string> = {
    'Content-Type': 'application/json',
    ...opts.headers,
  }

  if (jwt) headers['Authorization'] = `Bearer ${jwt}`
  if (opts.sessionToken) headers['X-Session-Token'] = opts.sessionToken
  return headers
}

/**
 * Erneuerten Access-Token für einen mit 401 abgewiesenen Request besorgen.
 *
 * Hat ein paralleler Request den Token schon erneuert, wird dieser genutzt statt
 * ein weiterer Refresh gegen das Rate-Limit von /auth/refresh gefeuert. Der
 * Retry darf dabei nie die Anmeldung wechseln: meldet sich in einem anderen Tab
 * ein anderer Benutzer an, gehört der gespeicherte Token diesem — der
 * ursprüngliche, womöglich schreibende Request würde sonst unter fremden
 * Rechten wiederholt. Nicht dekodierbare Tokens können keinen Wechsel belegen
 * und blockieren den Retry deshalb nicht.
 */
type Renewal = { token: string | null; sessionIsOver: boolean }

async function renewedTokenFor(previous: string | null): Promise<Renewal> {
  const current = getJwt()
  if (current && current !== previous) {
    // Ein paralleler Request war schneller — kein weiterer Refresh nötig.
    return { token: sameAccount(previous, current) ? current : null, sessionIsOver: false }
  }
  const { token, outcome } = await refreshSession()
  if (!token) return { token: null, sessionIsOver: endsSession(outcome) }
  // Ein Kontowechsel darf die Tokens des neuen Kontos nicht abräumen.
  return { token: sameAccount(previous, token) ? token : null, sessionIsOver: false }
}

/** `false` nur wenn beide Tokens lesbar sind und zu verschiedenen Benutzern gehören */
function sameAccount(previous: string | null, next: string): boolean {
  const before = previous === null ? null : jwtSubject(previous)
  const after = jwtSubject(next)
  return before === null || after === null || before === after
}

/**
 * Darf ein endgültiges 401 die gespeicherte Sitzung abräumen?
 *
 * Nur wenn im Speicher noch die Anmeldung steht, mit der dieser Request
 * unterwegs war. Hat sich währenddessen jemand anderes angemeldet, gehört sie
 * ihm — sein Token wegen unseres veralteten Requests zu löschen würde ihn aus
 * einer gültigen Sitzung werfen.
 */
function mayClearSession(usedJwt: string | null): boolean {
  const current = getJwt()
  if (current === null) return true
  if (usedJwt === null) return false
  return sameAccount(usedJwt, current)
}

/**
 * Meint dieses 401 die PIN-Sitzung der Seite statt den Access-Token?
 *
 * Ein `protected`-Knoten weist Requests ohne gültigen Session-Token ab, auch
 * wenn der Benutzer angemeldet ist (`_page_scoped_archive_access`, `/history`,
 * `/weather`). Den JWT zu erneuern hilft dabei nicht: der Retry liefe in
 * dasselbe 401, verbrauchte aber Rate-Limit von /auth/refresh und löste über
 * das Refresh-Event WebSocket-Neuaufbau, Rollenabfrage und Baum-Reload aus.
 */
async function isPageSessionRejection(res: Response): Promise<boolean> {
  const body = await res.clone().json().catch(() => null)
  return extractDetail(body, '') === 'Valid session token required'
}

type RenewalOptions = { silent401?: boolean; noAuthRefresh?: boolean }

/**
 * Request absetzen und bei 401 einmal mit erneuertem Token wiederholen.
 *
 * Einziger Ort für die Sitzungsregeln — JSON- und Multipart-Requests teilen
 * ihn sich, damit nicht wieder eine Kopie hinterherhinkt.
 */
async function sendWithRenewal(
  send: (jwtOverride?: string) => Promise<Response>,
  opts: RenewalOptions = {},
): Promise<Response> {
  const jwtBefore = getJwt()
  let usedJwt = jwtBefore
  let res = await send()

  // Abgelaufener Access-Token: genau einen Refresh anstossen (geteilt über alle
  // parallelen Requests) und den ursprünglichen Request einmal wiederholen.
  let sessionIsOver = true
  if (res.status === 401 && !opts.noAuthRefresh) {
    if (await isPageSessionRejection(res)) {
      // Nicht der Access-Token ist abgelaufen, sondern die PIN-Sitzung der
      // Seite — die Anmeldung bleibt gültig und unangetastet.
      sessionIsOver = false
    } else {
      const renewal = await renewedTokenFor(jwtBefore)
      if (renewal.token) {
        usedJwt = renewal.token
        res = await send(renewal.token)
      } else {
        sessionIsOver = renewal.sessionIsOver
      }
    }
  }

  if (res.status === 401 && !opts.silent401) {
    // Nur aufräumen, wenn die Sitzung wirklich hin ist — ein 5xx oder ein
    // Netzwerkfehler beim Refresh darf den 30-Tage-Token nicht wegwerfen —
    // und nur, wenn sie noch uns gehört.
    if (sessionIsOver && mayClearSession(usedJwt)) clearAuthTokens()
    // Redirect zur Login-Seite — der Router fängt das auf. Nur melden, wenn die
    // Anmeldung tatsächlich weg ist: bei einem Netzwerkfehler, 408, 429 oder
    // 5xx auf /auth/refresh behalten wir die Tokens bewusst, und ein Redirect
    // zum Login würde den Benutzer trotz gültiger Sitzung aus /manage werfen.
    if (sessionIsOver || !getJwt()) {
      window.dispatchEvent(new CustomEvent('visu:unauthorized'))
    }
  }
  return res
}

async function request<T>(path: string, opts: RequestOptions = {}): Promise<T> {
  const send = (jwtOverride?: string) => fetch(`${BASE}${path}`, {
    ...opts,
    headers: buildHeaders(opts, jwtOverride),
  })

  const res = await sendWithRenewal(send, opts)

  if (res.status === 401) {
    throw new ApiRequestError('Unauthorized', 401)
  }

  if (!res.ok) {
    const body = await res.json().catch(() => null)
    const detail = body && typeof body === 'object'
      ? (body as Record<string, unknown>).detail
      : null
    if (detail && typeof detail === 'object' && !Array.isArray(detail)) {
      const details = detail as Record<string, unknown>
      const code = typeof details.code === 'string' ? details.code : undefined
      throw new ApiRequestError(code ?? res.statusText, res.status, code, details)
    }
    throw new ApiRequestError(extractDetail(body, res.statusText), res.status)
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
      return res.json() as Promise<{ access_token: string; refresh_token: string; token_type: string }>
    })
  },

  /**
   * Wird direkt nach Login und Refresh aufgerufen — der Token ist also frisch.
   * `noAuthRefresh` verhindert, dass ein 401 hier eine weitere Erneuerung und
   * damit erneut diesen Aufruf anstösst.
   */
  me() {
    return request<{ id: string; username: string; is_admin: boolean }>('/auth/me', {
      silent401: true,
      noAuthRefresh: true,
    })
  },
}

// ── Visu-Nodes ────────────────────────────────────────────────────────────────

import type { VisuNode, VisuNodeUpdate, PageConfig, PinAuthResponse, UserResponse } from '@/types'

export const visu = {
  tree: () => request<VisuNode[]>('/visu/tree'),

  getNode: (id: string) => request<VisuNode>(`/visu/nodes/${id}`),

  createNode: (data: Partial<VisuNode>) =>
    request<VisuNode>('/visu/nodes', { method: 'POST', body: JSON.stringify(data) }),

  updateNode: (id: string, data: VisuNodeUpdate) =>
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
      noAuthRefresh: true,
    }),

  getPage: (id: string, sessionToken?: string) =>
    request<PageConfig>(`/visu/pages/${id}`, { sessionToken }),

  /** Lädt alle Widget-Instanzen einer Seite ohne Zugriffsprüfung — für WidgetRef. */
  getWidgetRef: (pageId: string, sessionNodeId = pageId) =>
    request<import('@/types').WidgetRefInstance[]>(`/visu/widget-ref/${pageId}`, {
      sessionToken: getSessionToken(sessionNodeId) ?? undefined,
      silent401: true,
    }),

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

  getValue: (id: string, silent401 = false, context?: WriteContext) => {
    const effectiveContext = context ?? _writeContext
    const headers: Record<string, string> = {}
    if (effectiveContext.pageId)       headers['X-Page-Id']       = effectiveContext.pageId
    if (effectiveContext.sessionToken) headers['X-Session-Token'] = effectiveContext.sessionToken
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

  write: async (id: string, value: unknown, context: WriteContext = _writeContext) => {
    const headers: Record<string, string> = {}
    if (context.pageId)      headers['X-Page-Id']       = context.pageId
    if (context.sessionToken) headers['X-Session-Token'] = context.sessionToken
    try {
      return await request<void>(`/datapoints/${id}/value`, {
        method: 'POST',
        body: JSON.stringify({ value }),
        headers,
      })
    } catch (err: unknown) {
      if (err instanceof Error && err.message === 'Valid session token required') {
        // Session abgelaufen (z.B. nach Server-Neustart) — Token löschen und Re-Auth auslösen
        const defId = context.definingId
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

// ── VISU Backgrounds ─────────────────────────────────────────────────────────

export interface BackgroundOut {
  name: string
  filename: string
  size: number
  mime_type: string
  url: string
}

export interface BackgroundListOut {
  total: number
  backgrounds: BackgroundOut[]
}

export interface BackgroundImportOut {
  imported: number
  skipped: number
  names: string[]
  message: string
}

export const visuBackgrounds = {
  list: () => request<BackgroundListOut>('/visu/backgrounds'),

  import: async (files: File[]) => {
    const formData = new FormData()
    for (const file of files) formData.append('files', file, file.name)

    const send = (jwtOverride?: string) => {
      const jwt = jwtOverride ?? getJwt()
      const headers: Record<string, string> = {}
      if (jwt) headers['Authorization'] = `Bearer ${jwt}`
      return fetch(`${BASE}/visu/backgrounds/import`, {
        method: 'POST',
        headers,
        body: formData,
      })
    }

    const res = await sendWithRenewal(send)
    if (res.status === 401) {
      throw new ApiRequestError('Unauthorized', 401)
    }
    if (!res.ok) {
      const body = await res.json().catch(() => null)
      throw new Error(extractDetail(body, res.statusText))
    }
    return res.json() as Promise<BackgroundImportOut>
  },

  delete: (names: string[]) =>
    request<{ deleted: number; names: string[]; not_found: string[] }>('/visu/backgrounds', {
      method: 'DELETE',
      body: JSON.stringify({ names }),
    }),

  publicUrl: (name: string) => `${BASE}/visu/backgrounds/${encodeURIComponent(name)}`,
}

// ── History ───────────────────────────────────────────────────────────────────

export const history = {
  query: (id: string, from: string, to: string, limit = 10000, context?: WriteContext) => {
    const effectiveContext = context ?? _writeContext
    const headers: Record<string, string> = {}
    if (effectiveContext.pageId)      headers['X-Page-Id']       = effectiveContext.pageId
    if (effectiveContext.sessionToken) headers['X-Session-Token'] = effectiveContext.sessionToken
    return request<{ ts: string; v: unknown; u: string | null; q: string }[]>(
      `/history/${id}?from=${from}&to=${to}&limit=${limit}`,
      { headers, silent401: true },
    )
  },
  aggregate: (id: string, from: string, to: string, interval: string, fn = 'avg', context?: WriteContext) => {
    const effectiveContext = context ?? _writeContext
    const headers: Record<string, string> = {}
    if (effectiveContext.pageId)      headers['X-Page-Id']       = effectiveContext.pageId
    if (effectiveContext.sessionToken) headers['X-Session-Token'] = effectiveContext.sessionToken
    return request<{ bucket: string; v: unknown; n?: number | null }[]>(
      `/history/${id}/aggregate?fn=${fn}&interval=${interval}&from=${from}&to=${to}`,
      { headers, silent401: true },
    )
  },
}

// ── Message Archives ─────────────────────────────────────────────────────────

export interface MessageArchiveOut {
  id: string
  name: string
  description: string
  tags: string[]
  default_type: string | null
  color: string
  retention_max_entries: number | null
  retention_max_age_days: number | null
  created_at: string
  updated_at: string
  entry_count: number
  oldest_entry_at: string | null
  newest_entry_at: string | null
  db_status: string
  db_path: string
}

export interface MessageArchiveEntry {
  id: string
  archive_id: string
  archive_name: string
  archive_color: string
  created_at: string
  updated_at: string
  type: string
  severity: string
  status: string
  source: string
  title: string
  message: string
  payload: Record<string, unknown>
  acknowledged_at: string | null
  acknowledged_by: string | null
  read_at: string | null
  is_read: boolean
}

export const messageArchives = {
  list: () => {
    const headers: Record<string, string> = {}
    if (_writeContext.pageId)      headers['X-Page-Id']       = _writeContext.pageId
    if (_writeContext.sessionToken) headers['X-Session-Token'] = _writeContext.sessionToken
    return request<MessageArchiveOut[]>('/message-archives', { headers, silent401: true })
  },
  entries: (params: Record<string, string | number | undefined>) => {
    const query = new URLSearchParams()
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== '') query.set(key, String(value))
    }
    const headers: Record<string, string> = {}
    if (_writeContext.pageId)      headers['X-Page-Id']       = _writeContext.pageId
    if (_writeContext.sessionToken) headers['X-Session-Token'] = _writeContext.sessionToken
    return request<{ items: MessageArchiveEntry[]; total: number; limit: number; offset: number }>(
      `/message-archives/entries?${query.toString()}`,
      { headers, silent401: true },
    )
  },
  markRead: (archiveId: string, entryId: string) => {
    const headers: Record<string, string> = {}
    if (_writeContext.pageId)      headers['X-Page-Id']       = _writeContext.pageId
    if (_writeContext.sessionToken) headers['X-Session-Token'] = _writeContext.sessionToken
    return request<MessageArchiveEntry>(`/message-archives/${archiveId}/entries/${entryId}/read`, { method: 'POST', headers, silent401: true })
  },
  acknowledge: (archiveId: string, entryId: string) => {
    const headers: Record<string, string> = {}
    if (_writeContext.pageId)      headers['X-Page-Id']       = _writeContext.pageId
    if (_writeContext.sessionToken) headers['X-Session-Token'] = _writeContext.sessionToken
    return request<MessageArchiveEntry>(`/message-archives/${archiveId}/entries/${entryId}/acknowledge`, { method: 'POST', headers, silent401: true })
  },
}

// ── Display-Settings (öffentlich, ohne Login — Issue #1073) ──────────────────

export interface DisplaySettings {
  language: string
  timezone: string
  date_format: string
  time_format: string
  region_format: string
  currency: string
  resolved_region_format: string
  resolved_currency: string
  supported_region_formats: string[]
  supported_currencies: string[]
}

export const displaySettings = {
  get: () => request<DisplaySettings>('/system/display-settings', { silent401: true }),
}
