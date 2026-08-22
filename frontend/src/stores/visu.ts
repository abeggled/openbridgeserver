/**
 * Pinia-Store: Visu-Struktur und Navigations-State
 *
 * - Baum aller VisuNodes (flach, mit parent_id)
 * - Aktueller Knoten + Breadcrumb
 * - Auth-State (JWT für private, Session-Tokens für protected)
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { visu as visuApi, auth as authApi, getJwt, setTokens, clearAuthTokens, getIsAdmin, setIsAdmin, setSessionToken, getSessionToken } from '@/api/client'
import { AUTH_TOKEN_REFRESHED_EVENT } from '@/utils/authEvents'
import type { VisuNode, VisuNodeUpdate, PageConfig } from '@/types'

export const useVisuStore = defineStore('visu', () => {
  // ── Baum ──────────────────────────────────────────────────────────────────
  const nodes = ref<VisuNode[]>([])
  const treeLoaded = ref(false)

  // Jede Anmeldung, Erneuerung und Abmeldung eröffnet eine Generation. Alles,
  // was danach asynchron zurückkommt, darf nur übernommen werden, solange keine
  // neuere Generation begonnen hat.
  let authGeneration = 0

  // Lokale Änderungen am Baum (Anlegen, Verschieben, Löschen). Eine Antwort, die
  // vor einer Änderung angefordert und danach zugestellt wurde, ist veraltet.
  let treeMutations = 0

  /**
   * Baum laden und nur übernehmen, wenn die Antwort noch die maßgebliche ist.
   *
   * Zwei Dinge machen sie ungültig: eine lokale Änderung, die nach der Anfrage
   * passiert ist, und ein Sitzungswechsel. Letzteres deckt beide Reihenfolgen
   * ab — die anonym gefilterte Sicht vom Kaltstart gehört zur Generation davor
   * und wird verworfen, egal ob sie vor oder nach der autorisierten eintrifft.
   * Ein reiner „hat sich was geändert"-Zähler konnte das nicht unterscheiden
   * und verwarf je nach Reihenfolge die falsche Antwort.
   */
  async function loadTreeFor(generation: number) {
    const mutationsBefore = treeMutations
    const fresh = await visuApi.tree()
    if (mutationsBefore !== treeMutations) return
    if (generation !== authGeneration) return
    nodes.value = fresh
    treeLoaded.value = true
  }

  async function loadTree() {
    await loadTreeFor(authGeneration)
  }

  function getNode(id: string): VisuNode | undefined {
    return nodes.value.find((n) => n.id === id)
  }

  function getChildren(parentId: string | null): VisuNode[] {
    return nodes.value
      .filter((n) => n.parent_id === parentId)
      .sort((a, b) => a.order - b.order)
  }

  const rootNodes = computed(() => getChildren(null))

  // ── Breadcrumb ────────────────────────────────────────────────────────────
  const breadcrumb = ref<VisuNode[]>([])

  async function loadBreadcrumb(nodeId: string) {
    breadcrumb.value = await visuApi.getBreadcrumb(nodeId)
  }

  // ── Page-Config ───────────────────────────────────────────────────────────
  const pageConfig = ref<PageConfig | null>(null)

  async function loadPage(nodeId: string) {
    const sessionToken = getSessionToken(nodeId) ?? undefined
    pageConfig.value = await visuApi.getPage(nodeId, sessionToken)
  }

  async function savePage(nodeId: string, config: PageConfig) {
    await visuApi.savePage(nodeId, config)
    pageConfig.value = config
  }

  // ── Auth ──────────────────────────────────────────────────────────────────
  // Reaktiver Spiegel des localStorage-JWT — wird bei login/logout aktualisiert
  const _jwt = ref<string | null>(getJwt())
  const _isAdmin = ref<boolean>(getIsAdmin())
  const isLoggedIn = computed(() => !!_jwt.value)
  const isAdmin = computed(() => _isAdmin.value)

  /** Spiegel wieder an den localStorage angleichen (nach erzwungenem Logout) */
  function syncAuthState() {
    // Wie beim Logout: laufende Abfragen der alten Generation verwerfen.
    authGeneration += 1
    _jwt.value = getJwt()
    _isAdmin.value = getIsAdmin()
  }

  /**
   * Nach einer Token-Erneuerung den Admin-Status neu erfragen.
   *
   * Die Sitzung läuft jetzt bis zu 30 Tage durch, statt täglich über einen
   * neuen Login zu gehen. Ein aus dem localStorage kopiertes Flag bliebe
   * dadurch beliebig lange stehen: einem entzogenen Admin blieben die
   * Bedienelemente erhalten (deren Requests dann mit 403 scheitern), einem neu
   * ernannten fehlten sie bis zum nächsten manuellen Login.
   */
  // Jede Anmeldung und jede Erneuerung eröffnet eine Generation. Alles, was
  // danach asynchron zurückkommt — Rolle wie Baum — darf nur übernommen werden,
  // solange keine neuere Generation begonnen hat. Sonst schreibt die verspätete
  // Antwort der alten Anmeldung in die Sitzung der neuen.

  async function applyIdentity(generation: number, onFailure: 'deny' | 'keep') {
    try {
      const me = await authApi.me()
      if (generation !== authGeneration) return
      setIsAdmin(me.is_admin)
      _isAdmin.value = me.is_admin
    } catch {
      if (generation !== authGeneration) return
      if (onFailure === 'deny') {
        setIsAdmin(false)
        _isAdmin.value = false
      } else {
        // Abfrage nicht möglich — zwischengespeicherten Stand behalten; eine
        // wirklich beendete Sitzung räumt visu:unauthorized ab.
        _isAdmin.value = getIsAdmin()
      }
    }
  }

  async function refreshAuthState() {
    const generation = ++authGeneration
    _jwt.value = getJwt()
    if (!_jwt.value) {
      _isAdmin.value = false
      return
    }
    await applyIdentity(generation, 'keep')
    if (generation !== authGeneration) return
    // Beim Kaltstart mit abgelaufenem Access-Token liefert das Backend keine
    // 401, sondern eine anonym gefilterte Sicht (`_optional_visu_principal`
    // verwirft den abgelaufenen Bearer). Der Baum im Store stammt dann aus
    // dieser Sicht und muss nach der Erneuerung neu geholt werden, sonst
    // bleiben private Knoten unsichtbar.
    try {
      await loadTreeFor(generation)
    } catch {
      // Nächste Erneuerung oder Navigation versucht es erneut
    }
  }

  // Ein fehlgeschlagener Refresh räumt beide Tokens und das Admin-Flag ab; ein
  // erfolgreicher rotiert den JWT. Beides muss im reaktiven Spiegel ankommen.
  window.addEventListener('visu:unauthorized', syncAuthState)
  window.addEventListener(AUTH_TOKEN_REFRESHED_EVENT, () => { void refreshAuthState() })

  async function login(accessToken: string, refreshToken?: string | null) {
    const generation = ++authGeneration
    setTokens(accessToken, refreshToken)
    _jwt.value = accessToken
    // Admin-Status direkt nach Login ermitteln
    await applyIdentity(generation, 'deny')
  }

  function logout() {
    // Generation weiterzählen, sonst übernimmt eine noch laufende
    // /auth/me-Abfrage danach wieder ihr Ergebnis und stellt das Admin-Flag her.
    authGeneration += 1
    clearAuthTokens()
    _jwt.value = null
    _isAdmin.value = false
  }

  /** PIN-Auth für einen protected Knoten */
  async function authenticatePin(nodeId: string, pin: string): Promise<void> {
    const { session_token, expires_in } = await visuApi.pinAuth(nodeId, pin)
    setSessionToken(nodeId, session_token, expires_in ?? 3600)
  }

  function hasSessionToken(nodeId: string): boolean {
    return !!getSessionToken(nodeId)
  }

  // ── CRUD ──────────────────────────────────────────────────────────────────
  async function createNode(data: Partial<VisuNode>): Promise<VisuNode> {
    const node = await visuApi.createNode(data)
    nodes.value.push(node)
    treeMutations += 1
    return node
  }

  async function updateNode(id: string, data: VisuNodeUpdate): Promise<VisuNode> {
    const node = await visuApi.updateNode(id, data)
    const idx = nodes.value.findIndex((n) => n.id === id)
    if (idx !== -1) nodes.value[idx] = node
    treeMutations += 1
    return node
  }

  async function deleteNode(id: string): Promise<void> {
    await visuApi.deleteNode(id)
    nodes.value = nodes.value.filter((n) => n.id !== id)
    treeMutations += 1
  }

  async function copyNode(id: string, targetParentId: string | null, newName: string): Promise<VisuNode> {
    const node = await visuApi.copyNode(id, targetParentId, newName)
    nodes.value.push(node)
    treeMutations += 1
    return node
  }

  async function moveNode(id: string, newParentId: string | null, order: number): Promise<void> {
    const node = await visuApi.moveNode(id, newParentId, order)
    const idx = nodes.value.findIndex((n) => n.id === id)
    if (idx !== -1) nodes.value[idx] = node
    treeMutations += 1
  }

  return {
    // State
    nodes, treeLoaded, breadcrumb, pageConfig, isLoggedIn, isAdmin,
    // Tree
    loadTree, getNode, getChildren, rootNodes,
    // Breadcrumb
    loadBreadcrumb,
    // Page
    loadPage, savePage,
    // Auth
    login, logout, authenticatePin, hasSessionToken,
    // CRUD
    createNode, updateNode, deleteNode, copyNode, moveNode,
  }
})
