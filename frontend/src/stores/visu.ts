/**
 * Pinia-Store: Visu-Struktur und Navigations-State
 *
 * - Baum aller VisuNodes (flach, mit parent_id)
 * - Aktueller Knoten + Breadcrumb
 * - Auth-State (JWT für private, Session-Tokens für protected)
 */

import { defineStore } from 'pinia'
import { ref, computed } from 'vue'
import { visu as visuApi, getJwt, setJwt, clearJwt, setSessionToken, getSessionToken } from '@/api/client'
import type { VisuNode, PageConfig } from '@/types'

export const useVisuStore = defineStore('visu', () => {
  // ── Baum ──────────────────────────────────────────────────────────────────
  const nodes = ref<VisuNode[]>([])
  const treeLoaded = ref(false)

  async function loadTree() {
    nodes.value = await visuApi.tree()
    treeLoaded.value = true
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
  const isLoggedIn = computed(() => !!_jwt.value)

  function login(token: string) {
    setJwt(token)
    _jwt.value = token
  }

  function logout() {
    clearJwt()
    _jwt.value = null
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
    return node
  }

  async function updateNode(id: string, data: Partial<VisuNode>): Promise<VisuNode> {
    const node = await visuApi.updateNode(id, data)
    const idx = nodes.value.findIndex((n) => n.id === id)
    if (idx !== -1) nodes.value[idx] = node
    return node
  }

  async function deleteNode(id: string): Promise<void> {
    await visuApi.deleteNode(id)
    nodes.value = nodes.value.filter((n) => n.id !== id)
  }

  async function copyNode(id: string, targetParentId: string, newName: string): Promise<VisuNode> {
    const node = await visuApi.copyNode(id, targetParentId, newName)
    nodes.value.push(node)
    return node
  }

  async function moveNode(id: string, newParentId: string | null, order: number): Promise<void> {
    const node = await visuApi.moveNode(id, newParentId, order)
    const idx = nodes.value.findIndex((n) => n.id === id)
    if (idx !== -1) nodes.value[idx] = node
  }

  return {
    // State
    nodes, treeLoaded, breadcrumb, pageConfig, isLoggedIn,
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
