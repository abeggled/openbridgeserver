<script setup lang="ts">
/**
 * TreeManager — GUI für das Seitenmanagement
 *
 * Features:
 * - Baum-Ansicht aller Knoten (LOCATION / PAGE) mit Expand/Collapse
 * - Knoten erstellen (Typ, Name, Icon)
 * - Umbenennen, Icon ändern, Zugangsberechtigung setzen, PIN vergeben
 * - Reihenfolge ändern (↑ ↓)
 * - In die Hierarchie verschieben (Modal)
 * - Löschen mit Bestätigung
 * - Navigation zu Viewer / Editor
 */
import { ref, computed, onMounted } from 'vue'
import { useRouter } from 'vue-router'
import { useVisuStore } from '@/stores/visu'
import { useThemeStore } from '@/stores/theme'
import type { VisuNode, NodeType, AccessLevel } from '@/types'

const router = useRouter()
const store  = useVisuStore()
const theme  = useThemeStore()

// ── Lade-State ────────────────────────────────────────────────────────────────
const loading = ref(true)
const saving  = ref(false)
const flashOk = ref(false)
const errorMsg = ref('')

// ── Baum-State ────────────────────────────────────────────────────────────────
const expanded   = ref(new Set<string>())
const selectedId = ref<string | null>(null)

const selectedNode = computed(() =>
  selectedId.value ? store.getNode(selectedId.value) : null,
)

// Kinder sortiert nach order
function getChildren(parentId: string | null): VisuNode[] {
  return store.nodes
    .filter(n => n.parent_id === parentId)
    .sort((a, b) => a.order - b.order)
}

function hasChildren(id: string) {
  return store.nodes.some(n => n.parent_id === id)
}

function toggleExpand(id: string, e: MouseEvent) {
  e.stopPropagation()
  if (expanded.value.has(id)) expanded.value.delete(id)
  else expanded.value.add(id)
}

// Abgeflachter Baum für v-for (depth-first)
interface FlatNode { node: VisuNode; depth: number }
const flatTree = computed<FlatNode[]>(() => {
  const result: FlatNode[] = []
  function walk(parentId: string | null, depth: number) {
    for (const node of getChildren(parentId)) {
      result.push({ node, depth })
      if (expanded.value.has(node.id)) walk(node.id, depth + 1)
    }
  }
  walk(null, 0)
  return result
})

// ── Edit-Formular ─────────────────────────────────────────────────────────────
const editName       = ref('')
const editIcon       = ref('')
const editAccess     = ref<AccessLevel | null>(null)
const editPin        = ref('')
const editPinConfirm = ref('')

function selectNode(node: VisuNode) {
  selectedId.value  = node.id
  editName.value    = node.name
  editIcon.value    = node.icon ?? ''
  editAccess.value  = node.access
  editPin.value     = ''
  editPinConfirm.value = ''
  errorMsg.value    = ''
}

async function saveNode() {
  if (!selectedId.value || !editName.value.trim()) return
  if (editAccess.value === 'protected' && editPin.value && editPin.value !== editPinConfirm.value) {
    errorMsg.value = 'PINs stimmen nicht überein'
    return
  }
  saving.value  = true
  errorMsg.value = ''
  try {
    const patch: Partial<VisuNode> = {
      name:   editName.value.trim(),
      icon:   editIcon.value || null,
      access: editAccess.value,
    }
    if (editPin.value) patch.access_pin = editPin.value
    await store.updateNode(selectedId.value, patch)
    editPin.value        = ''
    editPinConfirm.value = ''
    flashOk.value        = true
    setTimeout(() => { flashOk.value = false }, 2000)
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : 'Fehler beim Speichern'
  } finally {
    saving.value = false
  }
}

// ── Neuer Knoten ──────────────────────────────────────────────────────────────
const showAddModal = ref(false)
const addParentId  = ref<string | null>(null)
const addType      = ref<NodeType>('PAGE')
const addName      = ref('')
const addIcon      = ref('')

function openAddModal(parentId: string | null) {
  addParentId.value = parentId
  addType.value     = 'PAGE'
  addName.value     = ''
  addIcon.value     = ''
  errorMsg.value    = ''
  showAddModal.value = true
}

async function createNode() {
  if (!addName.value.trim()) return
  saving.value = true
  try {
    const siblings = getChildren(addParentId.value)
    const order    = siblings.length ? Math.max(...siblings.map(s => s.order)) + 1 : 0
    const created  = await store.createNode({
      parent_id: addParentId.value,
      name:      addName.value.trim(),
      type:      addType.value,
      icon:      addIcon.value || null,
      order,
    })
    if (addParentId.value) expanded.value.add(addParentId.value)
    showAddModal.value = false
    selectNode(created)
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : 'Fehler beim Erstellen'
  } finally {
    saving.value = false
  }
}

// ── Reihenfolge ───────────────────────────────────────────────────────────────
async function moveOrder(node: VisuNode, dir: -1 | 1) {
  const siblings = getChildren(node.parent_id)
  const idx  = siblings.findIndex(n => n.id === node.id)
  const swap = siblings[idx + dir]
  if (!swap) return
  saving.value = true
  try {
    await Promise.all([
      store.updateNode(node.id, { order: swap.order }),
      store.updateNode(swap.id, { order: node.order }),
    ])
    await store.loadTree()
  } finally {
    saving.value = false
  }
}

// ── Verschieben ───────────────────────────────────────────────────────────────
const showMoveModal  = ref(false)
const moveTargetId   = ref<string | null>(null)
const moveDestId     = ref<string | null>(null)   // null = Wurzel

function getDescendants(id: string): VisuNode[] {
  const children = getChildren(id)
  return [...children, ...children.flatMap(c => getDescendants(c.id))]
}

function nodePath(node: VisuNode): string {
  const parts: string[] = []
  let cur: VisuNode | undefined = node
  while (cur) {
    parts.unshift(cur.name)
    cur = cur.parent_id ? store.getNode(cur.parent_id) : undefined
  }
  return parts.join(' / ')
}

const moveOptions = computed(() => {
  if (!moveTargetId.value) return []
  const excluded = new Set([moveTargetId.value, ...getDescendants(moveTargetId.value).map(n => n.id)])
  const locations = store.nodes
    .filter(n => n.type === 'LOCATION' && !excluded.has(n.id))
    .map(n => ({ id: n.id, path: nodePath(n) }))
    .sort((a, b) => a.path.localeCompare(b.path))
  return [{ id: null as string | null, path: '— Wurzel (kein übergeordneter Knoten) —' }, ...locations]
})

function openMoveModal(nodeId: string) {
  moveTargetId.value = nodeId
  const node = store.getNode(nodeId)
  moveDestId.value   = node?.parent_id ?? null
  showMoveModal.value = true
}

async function doMove() {
  if (!moveTargetId.value) return
  saving.value = true
  try {
    const siblings = getChildren(moveDestId.value)
    const order    = siblings.length ? Math.max(...siblings.map(s => s.order)) + 1 : 0
    await store.moveNode(moveTargetId.value, moveDestId.value, order)
    await store.loadTree()
    if (moveDestId.value) expanded.value.add(moveDestId.value)
    showMoveModal.value = false
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : 'Fehler beim Verschieben'
  } finally {
    saving.value = false
  }
}

// ── Löschen ───────────────────────────────────────────────────────────────────
const showDeleteModal  = ref(false)
const deleteTargetNode = ref<VisuNode | null>(null)

function confirmDelete(node: VisuNode) {
  deleteTargetNode.value = node
  showDeleteModal.value  = true
}

async function doDelete() {
  if (!deleteTargetNode.value) return
  saving.value = true
  try {
    await store.deleteNode(deleteTargetNode.value.id)
    if (selectedId.value === deleteTargetNode.value.id) selectedId.value = null
    showDeleteModal.value  = false
    deleteTargetNode.value = null
  } catch (e) {
    errorMsg.value = e instanceof Error ? e.message : 'Fehler beim Löschen'
  } finally {
    saving.value = false
  }
}

// ── Zugangs-Optionen ──────────────────────────────────────────────────────────
const ACCESS_OPTIONS = [
  { value: null        as AccessLevel | null, label: 'Erben',      desc: 'Vom übergeordneten Knoten',  icon: '↑' },
  { value: 'public'    as AccessLevel,        label: 'Öffentlich', desc: 'Ohne Anmeldung sichtbar',    icon: '🌐' },
  { value: 'protected' as AccessLevel,        label: 'PIN',        desc: 'PIN-Eingabe erforderlich',   icon: '🔐' },
  { value: 'private'   as AccessLevel,        label: 'Privat',     desc: 'Nur Admins (JWT)',           icon: '🔒' },
]

function accessBadge(access: AccessLevel | null) {
  switch (access) {
    case 'public':    return 'bg-green-500/10 text-green-600 dark:text-green-400 border border-green-500/30'
    case 'protected': return 'bg-amber-500/10 text-amber-600 dark:text-amber-400 border border-amber-500/30'
    case 'private':   return 'bg-red-500/10   text-red-600   dark:text-red-400   border border-red-500/30'
    default:          return 'bg-gray-500/10  text-gray-500  dark:text-gray-500  border border-gray-500/20'
  }
}

function accessLabel(access: AccessLevel | null) {
  return ACCESS_OPTIONS.find(o => o.value === access)?.label ?? '—'
}

// ── Quick-Icons ───────────────────────────────────────────────────────────────
const QUICK_ICONS = ['🏠','🏡','📁','📄','⚡','💡','🌡️','🔒','🚿','🛁','🛋️','🍳','🛏️',
  '🪟','🚪','🌿','🔆','🔅','💧','🌬️','🎵','📹','🔔','🌞','🌙','🎛️','🎚️','🔌','🌐','⚙️']

// ── Init ──────────────────────────────────────────────────────────────────────
onMounted(async () => {
  try {
    await store.loadTree()
    store.rootNodes.forEach(n => expanded.value.add(n.id))
  } finally {
    loading.value = false
  }
})
</script>

<template>
  <div class="h-screen flex flex-col bg-white dark:bg-gray-950 text-gray-900 dark:text-gray-100 overflow-hidden">

    <!-- ── Header ──────────────────────────────────────────────────────────── -->
    <header class="flex-shrink-0 border-b border-gray-200 dark:border-gray-800 px-4 py-2.5 flex items-center gap-3 bg-gray-50 dark:bg-gray-900">
      <span class="text-lg font-semibold">🗂 Seiten-Manager</span>
      <div class="flex-1" />
      <button
        class="text-xs text-gray-400 dark:text-gray-500 hover:text-gray-700 dark:hover:text-gray-200 px-2 py-1 rounded transition-colors"
        :title="theme.isDark ? 'Heller Modus' : 'Dunkler Modus'"
        @click="theme.toggle()"
      >{{ theme.isDark ? '☀️' : '🌙' }}</button>
      <button
        class="text-sm text-gray-500 dark:text-gray-400 hover:text-gray-900 dark:hover:text-gray-100 border border-gray-300 dark:border-gray-700 px-3 py-1.5 rounded transition-colors"
        @click="router.push({ name: 'tree' })"
      >← Zurück</button>
    </header>

    <!-- ── Hauptbereich ────────────────────────────────────────────────────── -->
    <div v-if="loading" class="flex-1 flex items-center justify-center text-gray-400 dark:text-gray-500">
      Lade …
    </div>

    <div v-else class="flex-1 flex min-h-0">

      <!-- ── Baum (links) ──────────────────────────────────────────────────── -->
      <div class="w-80 flex-shrink-0 flex flex-col border-r border-gray-200 dark:border-gray-800 bg-gray-50 dark:bg-gray-900 overflow-y-auto">
        <div class="flex items-center justify-between px-3 pt-3 pb-2">
          <span class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">Struktur</span>
          <button
            class="text-xs flex items-center gap-1 text-blue-600 dark:text-blue-400 hover:text-blue-500 font-medium px-2 py-1 rounded hover:bg-blue-500/10 transition-colors"
            @click="openAddModal(null)"
          >＋ Neu</button>
        </div>

        <!-- Baum-Einträge -->
        <div class="flex-1 pb-4">
          <div
            v-for="{ node, depth } in flatTree"
            :key="node.id"
            class="group flex items-center gap-1 px-2 py-1.5 cursor-pointer transition-colors text-sm"
            :class="[
              selectedId === node.id
                ? 'bg-blue-500/10 dark:bg-blue-500/15 text-blue-700 dark:text-blue-300'
                : 'hover:bg-gray-100 dark:hover:bg-gray-800 text-gray-700 dark:text-gray-300',
            ]"
            :style="{ paddingLeft: `${depth * 16 + 8}px` }"
            @click="selectNode(node)"
          >
            <!-- Expand/Collapse -->
            <button
              class="w-4 h-4 flex items-center justify-center text-gray-400 flex-shrink-0 text-xs"
              @click="toggleExpand(node.id, $event)"
            >
              <span v-if="hasChildren(node.id)">{{ expanded.has(node.id) ? '▾' : '▸' }}</span>
              <span v-else class="text-gray-200 dark:text-gray-700">·</span>
            </button>

            <!-- Icon + Name -->
            <span class="flex-shrink-0 leading-none">{{ node.icon ?? (node.type === 'PAGE' ? '📄' : '📁') }}</span>
            <span class="flex-1 truncate text-sm">{{ node.name }}</span>

            <!-- Type-Badge -->
            <span class="flex-shrink-0 text-xs px-1 rounded"
              :class="node.type === 'PAGE' ? 'text-blue-400 dark:text-blue-500' : 'text-purple-400 dark:text-purple-500'">
              {{ node.type === 'PAGE' ? 'Seite' : 'Bereich' }}
            </span>

            <!-- Access-Badge -->
            <span v-if="node.access" class="flex-shrink-0 text-xs px-1.5 py-0.5 rounded" :class="accessBadge(node.access)">
              {{ accessLabel(node.access) }}
            </span>

            <!-- Aktions-Buttons (nur sichtbar bei Hover/Selektion) -->
            <div
              class="flex-shrink-0 flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity"
              :class="{ '!opacity-100': selectedId === node.id }"
            >
              <button title="Nach oben" class="w-5 h-5 flex items-center justify-center text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 text-xs rounded hover:bg-gray-200 dark:hover:bg-gray-700"
                @click.stop="moveOrder(node, -1)">↑</button>
              <button title="Nach unten" class="w-5 h-5 flex items-center justify-center text-gray-400 hover:text-gray-700 dark:hover:text-gray-200 text-xs rounded hover:bg-gray-200 dark:hover:bg-gray-700"
                @click.stop="moveOrder(node, 1)">↓</button>
              <button title="Kind hinzufügen" class="w-5 h-5 flex items-center justify-center text-gray-400 hover:text-blue-500 text-xs rounded hover:bg-gray-200 dark:hover:bg-gray-700"
                @click.stop="openAddModal(node.id)">＋</button>
              <button title="Löschen" class="w-5 h-5 flex items-center justify-center text-gray-400 hover:text-red-500 text-xs rounded hover:bg-gray-200 dark:hover:bg-gray-700"
                @click.stop="confirmDelete(node)">✕</button>
            </div>
          </div>

          <!-- Leerer Zustand -->
          <div v-if="flatTree.length === 0" class="px-4 py-8 text-center text-sm text-gray-400 dark:text-gray-600">
            Noch keine Seiten vorhanden.<br/>
            <button class="mt-2 text-blue-500 hover:underline" @click="openAddModal(null)">Ersten Knoten erstellen</button>
          </div>
        </div>
      </div>

      <!-- ── Eigenschaften (rechts) ────────────────────────────────────────── -->
      <div class="flex-1 overflow-y-auto">

        <!-- Kein Knoten ausgewählt -->
        <div v-if="!selectedNode" class="flex flex-col items-center justify-center h-full gap-3 text-center text-gray-400 dark:text-gray-600">
          <span class="text-5xl">👈</span>
          <p class="text-sm">Knoten aus dem Baum auswählen<br/>oder neuen Knoten erstellen</p>
        </div>

        <!-- Knoten-Eigenschaften -->
        <div v-else class="max-w-xl mx-auto px-6 py-6 space-y-6">

          <!-- Titel -->
          <div class="flex items-center gap-3">
            <span class="text-4xl leading-none">{{ selectedNode.icon ?? (selectedNode.type === 'PAGE' ? '📄' : '📁') }}</span>
            <div>
              <h1 class="text-xl font-semibold text-gray-900 dark:text-gray-100">{{ selectedNode.name }}</h1>
              <span class="text-xs px-2 py-0.5 rounded font-medium"
                :class="selectedNode.type === 'PAGE' ? 'bg-blue-500/10 text-blue-600 dark:text-blue-400' : 'bg-purple-500/10 text-purple-600 dark:text-purple-400'">
                {{ selectedNode.type === 'PAGE' ? '📄 Seite' : '📁 Bereich' }}
              </span>
            </div>
          </div>

          <!-- Trennlinie -->
          <hr class="border-gray-200 dark:border-gray-800" />

          <!-- Navigations-Shortcuts -->
          <div class="flex gap-2">
            <button
              class="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded border border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-gray-400 dark:hover:border-gray-500 transition-colors"
              @click="router.push({ name: 'viewer', params: { id: selectedNode.id } })"
            >👁 Anzeigen</button>
            <button
              v-if="selectedNode.type === 'PAGE'"
              class="flex items-center gap-1.5 text-sm px-3 py-1.5 rounded border border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-300 hover:border-gray-400 dark:hover:border-gray-500 transition-colors"
              @click="router.push({ name: 'editor', params: { id: selectedNode.id } })"
            >✏️ Layout bearbeiten</button>
          </div>

          <!-- ── Allgemein ──────────────────────────────────────────────────── -->
          <section class="space-y-4">
            <h2 class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">Allgemein</h2>

            <!-- Name -->
            <div>
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
              <input
                v-model="editName"
                type="text"
                placeholder="Knotenname"
                class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500 focus:ring-1 focus:ring-blue-500/30"
              />
            </div>

            <!-- Icon -->
            <div>
              <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Icon</label>
              <div class="flex items-center gap-2">
                <input
                  v-model="editIcon"
                  type="text"
                  placeholder="Emoji …"
                  class="w-24 bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-center text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
                  maxlength="4"
                />
                <!-- Schnellauswahl -->
                <div class="flex flex-wrap gap-1">
                  <button
                    v-for="ic in QUICK_ICONS"
                    :key="ic"
                    class="text-base w-8 h-8 flex items-center justify-center rounded hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                    :class="editIcon === ic ? 'ring-2 ring-blue-500 bg-blue-50 dark:bg-blue-500/10' : ''"
                    @click="editIcon = ic"
                  >{{ ic }}</button>
                </div>
              </div>
            </div>
          </section>

          <!-- ── Zugangsberechtigung ────────────────────────────────────────── -->
          <section class="space-y-3">
            <h2 class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">Zugangsberechtigung</h2>

            <div class="space-y-2">
              <label
                v-for="opt in ACCESS_OPTIONS"
                :key="String(opt.value)"
                class="flex items-center gap-3 p-3 rounded-lg border cursor-pointer transition-colors"
                :class="editAccess === opt.value
                  ? 'border-blue-500 bg-blue-500/5 dark:bg-blue-500/10'
                  : 'border-gray-200 dark:border-gray-800 hover:border-gray-300 dark:hover:border-gray-700'"
              >
                <input
                  type="radio"
                  :value="opt.value"
                  v-model="editAccess"
                  class="text-blue-500 focus:ring-blue-500"
                />
                <span class="text-lg leading-none">{{ opt.icon }}</span>
                <div class="flex-1 min-w-0">
                  <div class="text-sm font-medium text-gray-900 dark:text-gray-100">{{ opt.label }}</div>
                  <div class="text-xs text-gray-500 dark:text-gray-400">{{ opt.desc }}</div>
                </div>
              </label>
            </div>

            <!-- PIN-Felder (nur wenn protected gewählt) -->
            <div v-if="editAccess === 'protected'" class="space-y-2 pl-2 border-l-2 border-amber-500/40">
              <p class="text-xs text-amber-600 dark:text-amber-400 font-medium">
                PIN {{ selectedNode.access === 'protected' ? 'ändern (leer lassen = behalten)' : 'vergeben' }}
              </p>
              <div class="grid grid-cols-2 gap-2">
                <div>
                  <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Neuer PIN</label>
                  <input
                    v-model="editPin"
                    type="password"
                    inputmode="numeric"
                    placeholder="z.B. 1234"
                    class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-amber-500 focus:ring-1 focus:ring-amber-500/30"
                  />
                </div>
                <div>
                  <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">PIN bestätigen</label>
                  <input
                    v-model="editPinConfirm"
                    type="password"
                    inputmode="numeric"
                    placeholder="Wiederholen"
                    class="w-full bg-white dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:border-amber-500 focus:ring-1 focus:ring-amber-500/30"
                    :class="editPin && editPinConfirm && editPin !== editPinConfirm ? 'border-red-400' : ''"
                  />
                </div>
              </div>
              <p v-if="editPin && editPinConfirm && editPin !== editPinConfirm" class="text-xs text-red-500">
                PINs stimmen nicht überein
              </p>
            </div>
          </section>

          <!-- Fehler -->
          <p v-if="errorMsg" class="text-sm text-red-500 dark:text-red-400 bg-red-500/10 px-3 py-2 rounded-lg">{{ errorMsg }}</p>

          <!-- Speichern -->
          <button
            class="w-full py-2 rounded-lg text-sm font-medium transition-colors"
            :class="flashOk
              ? 'bg-green-500 text-white'
              : 'bg-blue-600 hover:bg-blue-500 disabled:opacity-50 text-white'"
            :disabled="saving || !editName.trim()"
            @click="saveNode"
          >
            {{ flashOk ? '✓ Gespeichert' : (saving ? 'Speichere …' : '💾 Speichern') }}
          </button>

          <!-- ── Gefahrenzone ───────────────────────────────────────────────── -->
          <section class="space-y-3 pt-2">
            <h2 class="text-xs font-semibold text-gray-400 dark:text-gray-500 uppercase tracking-wider">Weitere Aktionen</h2>
            <div class="flex gap-2">
              <button
                class="flex-1 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:border-blue-500 hover:text-blue-600 dark:hover:text-blue-400 transition-colors"
                @click="openMoveModal(selectedNode.id)"
              >🔀 Verschieben</button>
              <button
                class="flex-1 py-2 text-sm rounded-lg border border-red-200 dark:border-red-900/50 text-red-500 dark:text-red-400 hover:bg-red-500/10 transition-colors"
                @click="confirmDelete(selectedNode)"
              >🗑 Löschen</button>
            </div>
          </section>

        </div>
      </div>
    </div>

    <!-- ════════════════════════════════════════════════════════════════
         MODAL: Neuer Knoten
    ═══════════════════════════════════════════════════════════════════ -->
    <Teleport to="body">
      <div v-if="showAddModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4" @click.self="showAddModal = false">
        <div class="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-sm p-6 space-y-5">
          <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">Neuer Knoten</h2>

          <!-- Typ -->
          <div class="grid grid-cols-2 gap-2">
            <button
              class="py-3 rounded-xl border-2 text-sm font-medium transition-colors flex flex-col items-center gap-1"
              :class="addType === 'LOCATION' ? 'border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:border-gray-300'"
              @click="addType = 'LOCATION'"
            >
              <span class="text-2xl">📁</span> Bereich
            </button>
            <button
              class="py-3 rounded-xl border-2 text-sm font-medium transition-colors flex flex-col items-center gap-1"
              :class="addType === 'PAGE' ? 'border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400' : 'border-gray-200 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:border-gray-300'"
              @click="addType = 'PAGE'"
            >
              <span class="text-2xl">📄</span> Seite
            </button>
          </div>

          <!-- Name -->
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Name</label>
            <input
              v-model="addName"
              type="text"
              placeholder="z.B. Wohnzimmer"
              autofocus
              class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
              @keydown.enter="createNode"
            />
          </div>

          <!-- Icon -->
          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-1">Icon (optional)</label>
            <div class="flex items-center gap-2">
              <input
                v-model="addIcon"
                type="text"
                placeholder="Emoji"
                maxlength="4"
                class="w-20 bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-center text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
              />
              <div class="flex flex-wrap gap-1">
                <button
                  v-for="ic in QUICK_ICONS.slice(0, 16)"
                  :key="ic"
                  class="text-base w-7 h-7 flex items-center justify-center rounded hover:bg-gray-100 dark:hover:bg-gray-800 transition-colors"
                  :class="addIcon === ic ? 'ring-2 ring-blue-500' : ''"
                  @click="addIcon = ic"
                >{{ ic }}</button>
              </div>
            </div>
          </div>

          <p v-if="addParentId" class="text-xs text-gray-500 dark:text-gray-400">
            Wird erstellt unter: <strong>{{ store.getNode(addParentId)?.name ?? '?' }}</strong>
          </p>

          <!-- Buttons -->
          <div class="flex gap-2 pt-1">
            <button class="flex-1 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
              @click="showAddModal = false">Abbrechen</button>
            <button
              class="flex-1 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium disabled:opacity-50 transition-colors"
              :disabled="!addName.trim() || saving"
              @click="createNode"
            >{{ saving ? '…' : 'Erstellen' }}</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ════════════════════════════════════════════════════════════════
         MODAL: Verschieben
    ═══════════════════════════════════════════════════════════════════ -->
    <Teleport to="body">
      <div v-if="showMoveModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4" @click.self="showMoveModal = false">
        <div class="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-sm p-6 space-y-4">
          <h2 class="text-lg font-semibold text-gray-900 dark:text-gray-100">
            Verschieben: <span class="text-blue-500">{{ moveTargetId ? store.getNode(moveTargetId)?.name : '' }}</span>
          </h2>

          <div>
            <label class="block text-sm font-medium text-gray-700 dark:text-gray-300 mb-2">Neuer übergeordneter Knoten</label>
            <select
              v-model="moveDestId"
              class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded-lg px-3 py-2 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
            >
              <option v-for="opt in moveOptions" :key="String(opt.id)" :value="opt.id">
                {{ opt.path }}
              </option>
            </select>
          </div>

          <div class="flex gap-2">
            <button class="flex-1 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-400"
              @click="showMoveModal = false">Abbrechen</button>
            <button
              class="flex-1 py-2 text-sm rounded-lg bg-blue-600 hover:bg-blue-500 text-white font-medium disabled:opacity-50"
              :disabled="saving"
              @click="doMove"
            >{{ saving ? '…' : 'Verschieben' }}</button>
          </div>
        </div>
      </div>
    </Teleport>

    <!-- ════════════════════════════════════════════════════════════════
         MODAL: Löschen bestätigen
    ═══════════════════════════════════════════════════════════════════ -->
    <Teleport to="body">
      <div v-if="showDeleteModal" class="fixed inset-0 z-50 flex items-center justify-center bg-black/60 backdrop-blur-sm px-4" @click.self="showDeleteModal = false">
        <div class="bg-white dark:bg-gray-900 rounded-2xl shadow-2xl w-full max-w-sm p-6 space-y-4">
          <div class="flex items-center gap-3">
            <span class="text-3xl">⚠️</span>
            <div>
              <h2 class="text-base font-semibold text-gray-900 dark:text-gray-100">Wirklich löschen?</h2>
              <p class="text-sm text-gray-500 dark:text-gray-400">
                <strong>{{ deleteTargetNode?.name }}</strong> und alle untergeordneten Knoten werden unwiderruflich gelöscht.
              </p>
            </div>
          </div>

          <div class="flex gap-2">
            <button class="flex-1 py-2 text-sm rounded-lg border border-gray-300 dark:border-gray-700 text-gray-600 dark:text-gray-400 hover:bg-gray-50 dark:hover:bg-gray-800"
              @click="showDeleteModal = false">Abbrechen</button>
            <button
              class="flex-1 py-2 text-sm rounded-lg bg-red-600 hover:bg-red-500 text-white font-medium disabled:opacity-50"
              :disabled="saving"
              @click="doDelete"
            >{{ saving ? '…' : '🗑 Löschen' }}</button>
          </div>
        </div>
      </div>
    </Teleport>

  </div>
</template>
