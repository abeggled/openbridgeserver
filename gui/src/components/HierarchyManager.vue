<template>
  <div class="flex flex-col gap-4">

    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-2">
      <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">Hierarchieverwaltung</h3>
      <div class="flex-1" />
      <button @click="openCreateTree" class="btn-primary btn-sm" data-testid="btn-create-tree">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
        </svg>
        Neuer Ast
      </button>
      <button @click="openEtsImport" class="btn-secondary btn-sm" data-testid="btn-ets-import">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3"/>
        </svg>
        Aus ETS importieren
      </button>
    </div>

    <!-- Feedback -->
    <div v-if="msg" :class="['p-3 rounded-lg text-sm border', msg.ok ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30']">
      {{ msg.text }}
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex justify-center py-8"><Spinner /></div>

    <!-- Empty state -->
    <div v-else-if="trees.length === 0" class="text-center py-12 text-sm text-slate-500">
      <svg class="w-10 h-10 mx-auto mb-3 text-slate-300 dark:text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 7h18M3 12h12M3 17h8"/>
      </svg>
      Noch keine Hierarchieäste. Erstelle einen neuen Ast oder importiere aus ETS.
    </div>

    <!-- Tree list -->
    <div v-else class="flex flex-col gap-3">
      <div v-for="tree in trees" :key="tree.id" class="card" :data-testid="`tree-${tree.id}`">
        <div class="card-header flex items-center gap-2">
          <svg class="w-4 h-4 text-blue-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7h18M3 12h12M3 17h8"/>
          </svg>
          <span
            @click="toggleTree(tree.id)"
            class="font-semibold text-sm text-slate-800 dark:text-slate-100 flex-1 cursor-pointer hover:text-blue-500 dark:hover:text-blue-400 transition-colors select-none">
            {{ tree.name }}
          </span>
          <span v-if="tree.description" class="text-xs text-slate-400 hidden sm:block">{{ tree.description }}</span>
          <button @click="toggleTree(tree.id)" class="btn-secondary btn-xs" :data-testid="`btn-expand-${tree.id}`">
            <svg class="w-3 h-3 transition-transform" :class="expandedTrees.has(tree.id) ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
            </svg>
          </button>
          <button @click="openEditTree(tree)" class="btn-secondary btn-xs" :data-testid="`btn-edit-tree-${tree.id}`" title="Umbenennen">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536M9 13l6.293-6.293a1 1 0 011.414 0l1.586 1.586a1 1 0 010 1.414L12 16H9v-3z"/>
            </svg>
          </button>
          <button @click="addRootNode(tree)" class="btn-secondary btn-xs" :data-testid="`btn-add-root-${tree.id}`" title="Obersten Knoten hinzufügen">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
            </svg>
          </button>
          <button @click="confirmDeleteTree(tree)" class="btn-danger btn-xs" :data-testid="`btn-delete-tree-${tree.id}`" title="Ast löschen">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7h6m-7 0a1 1 0 011-1h4a1 1 0 011 1m-7 0h8"/>
            </svg>
          </button>
        </div>

        <!-- Tree nodes (collapsible) -->
        <div v-if="expandedTrees.has(tree.id)" class="card-body pt-0">
          <div v-if="treeLoading.has(tree.id)" class="flex justify-center py-4"><Spinner size="sm" /></div>
          <div v-else-if="!treeNodes[tree.id]?.length" class="text-xs text-slate-500 py-2 text-center">
            Dieser Ast ist noch leer.
          </div>
          <div v-else class="tree-container">
            <HierarchyNodeTree
              :nodes="treeNodes[tree.id]"
              :tree-id="tree.id"
              @add-child="(parentNode) => openAddChildNode(tree, parentNode)"
              @edit="openEditNode"
              @delete="confirmDeleteNode"
              @reorder="({ node, siblings, index, direction }) => reorderNode(tree, node, siblings, index, direction)"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- ── Modal: Create/Edit Tree ── -->
    <div v-if="treeModal.open" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="treeModal.open = false">
      <div class="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-sm p-6 flex flex-col gap-4">
        <h3 class="font-semibold text-slate-800 dark:text-slate-100">
          {{ treeModal.isEdit ? 'Ast umbenennen' : 'Neuer Hierarchiebaum' }}
        </h3>
        <div class="form-group">
          <label class="label">Name</label>
          <input v-model="treeModal.name" ref="treeNameInput" type="text" class="input" placeholder="z.B. Gebäude, Gewerke, Funktion" @keydown.enter="saveTree" />
        </div>
        <div class="form-group">
          <label class="label">Beschreibung</label>
          <input v-model="treeModal.description" type="text" class="input" placeholder="Optional" @keydown.enter="saveTree" />
        </div>
        <div class="form-group">
          <label class="label">Anzeigestart-Ebene</label>
          <select v-model="treeModal.display_depth" class="input text-sm">
            <option :value="0">0 — Ast-Name (Standard, z.B. "Gebäude")</option>
            <option :value="1">1 — Erste Ebene (z.B. "EG" statt "Gebäude")</option>
            <option :value="2">2 — Zweite Ebene (z.B. "Wohnzimmer")</option>
            <option :value="3">3 — Dritte Ebene</option>
            <option :value="4">4 — Vierte Ebene</option>
          </select>
          <p class="text-xs text-slate-500 mt-1">Bestimmt, welche Ebene im verkürzten Pfad-Tag angezeigt wird. Der vollständige Pfad ist stets als Tooltip sichtbar.</p>
        </div>
        <div v-if="treeModal.msg" :class="['p-2 rounded text-sm', treeModal.msg.ok ? 'text-green-400' : 'text-red-400']">{{ treeModal.msg.text }}</div>
        <div class="flex gap-2 justify-end">
          <button @click="treeModal.open = false" class="btn-secondary">Abbrechen</button>
          <button @click="saveTree" class="btn-primary" :disabled="treeModal.saving">
            <Spinner v-if="treeModal.saving" size="sm" color="white" />
            Speichern
          </button>
        </div>
      </div>
    </div>

    <!-- ── Modal: Create/Edit Node ── -->
    <div v-if="nodeModal.open" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="nodeModal.open = false">
      <div class="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-sm p-6 flex flex-col gap-4">
        <h3 class="font-semibold text-slate-800 dark:text-slate-100">
          {{ nodeModal.isEdit ? 'Knoten bearbeiten' : 'Knoten hinzufügen' }}
        </h3>
        <div class="form-group">
          <label class="label">Name</label>
          <input v-model="nodeModal.name" ref="nodeNameInput" type="text" class="input" placeholder="z.B. Erdgeschoss, Heizung" @keydown.enter="saveNode" />
        </div>
        <div class="form-group">
          <label class="label">Beschreibung</label>
          <input v-model="nodeModal.description" type="text" class="input" placeholder="Optional" @keydown.enter="saveNode" />
        </div>
        <div v-if="nodeModal.msg" :class="['p-2 rounded text-sm', nodeModal.msg.ok ? 'text-green-400' : 'text-red-400']">{{ nodeModal.msg.text }}</div>
        <div class="flex gap-2 justify-end">
          <button @click="nodeModal.open = false" class="btn-secondary">Abbrechen</button>
          <button @click="saveNode" class="btn-primary" :disabled="nodeModal.saving">
            <Spinner v-if="nodeModal.saving" size="sm" color="white" />
            Speichern
          </button>
        </div>
      </div>
    </div>

    <!-- ── Modal: ETS Import ── -->
    <div v-if="etsModal.open" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="etsModal.open = false">
      <div class="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-lg p-6 flex flex-col gap-4">
        <h3 class="font-semibold text-slate-800 dark:text-slate-100">Hierarchie aus ETS importieren</h3>
        <p class="text-sm text-slate-500">
          Erzeugt einen neuen Hierarchiebaum aus den importierten ETS-Daten.
          <span v-if="['buildings','trades'].includes(etsModal.mode)">Gebäude/Gewerke-Modus nutzt die räumliche bzw. funktionale Struktur aus dem ETS-Projekt.</span>
          <span v-else>Gruppenbezeichnungen werden direkt aus dem ETS-Projekt übernommen.</span>
        </p>

        <!-- Schnell-Presets -->
        <div class="form-group">
          <label class="label">Perspektive</label>
          <div class="flex gap-2 flex-wrap">
            <button v-for="preset in etsPresets" :key="preset.name"
              @click="etsModal.treeName = preset.name; etsModal.mode = preset.mode"
              :class="['px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors',
                etsModal.treeName === preset.name && etsModal.mode === preset.mode
                  ? 'border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400'
                  : 'border-slate-200 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:border-blue-400']">
              {{ preset.name }}
            </button>
          </div>
          <p class="text-xs text-slate-500 mt-1.5">
            Wähle die Perspektive entsprechend der Struktur deines ETS-Projekts, oder vergib unten einen eigenen Namen.
          </p>
        </div>

        <div class="form-group">
          <label class="label">Name des neuen Astes</label>
          <input v-model="etsModal.treeName" type="text" class="input" placeholder="z.B. Gewerke" />
        </div>

        <div class="form-group">
          <label class="label">Modus</label>
          <select v-model="etsModal.mode" class="input text-sm">
            <optgroup label="Topologie (Gruppenadressen)">
              <option value="groups">3-stufig: Hauptgruppe → Mittelgruppe → GA</option>
              <option value="mid">2-stufig: Hauptgruppe → Mittelgruppe (ohne GA-Blätter)</option>
              <option value="flat">2-stufig: Hauptgruppe → GA (Mittelgruppen überspringen)</option>
            </optgroup>
            <optgroup label="Gebäudestruktur (aus ETS-Projekt)">
              <option value="buildings">Gebäude → Stockwerk → Raum (räumliche Hierarchie)</option>
              <option value="trades">Gewerke → Funktion (nach Gewerk gruppiert)</option>
            </optgroup>
          </select>
        </div>

        <div v-if="['buildings','trades'].includes(etsModal.mode)" class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
          <input id="auto-link" v-model="etsModal.autoLink" type="checkbox" class="rounded" />
          <label for="auto-link">DataPoints automatisch verknüpfen (über Gruppenadresse)</label>
        </div>

        <div v-if="etsModal.msg" :class="['p-2 rounded text-sm', etsModal.msg.ok ? 'text-green-400' : 'text-red-400']">{{ etsModal.msg.text }}</div>
        <div class="flex gap-2 justify-end">
          <button @click="etsModal.open = false" class="btn-secondary">Abbrechen</button>
          <button @click="doEtsImport" class="btn-primary" :disabled="etsModal.saving || !etsModal.treeName.trim()">
            <Spinner v-if="etsModal.saving" size="sm" color="white" />
            Importieren
          </button>
        </div>
      </div>
    </div>

    <!-- ── Modal: Confirm Delete ── -->
    <div v-if="deleteConfirm.open" class="fixed inset-0 z-50 flex items-center justify-center bg-black/50" @click.self="deleteConfirm.open = false">
      <div class="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-sm p-6 flex flex-col gap-4">
        <h3 class="font-semibold text-slate-800 dark:text-slate-100">{{ deleteConfirm.title }}</h3>
        <p class="text-sm text-slate-500">{{ deleteConfirm.message }}</p>
        <div class="flex gap-2 justify-end">
          <button @click="deleteConfirm.open = false" class="btn-secondary">Abbrechen</button>
          <button @click="deleteConfirm.action" class="btn-danger" :disabled="deleteConfirm.saving">
            <Spinner v-if="deleteConfirm.saving" size="sm" color="white" />
            Löschen
          </button>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, reactive, nextTick, onMounted } from 'vue'
import { hierarchyApi } from '@/api/client.js'
import HierarchyNodeTree from '@/components/HierarchyNodeTree.vue'
import Spinner from '@/components/ui/Spinner.vue'

// ── State ─────────────────────────────────────────────────────────────────

const loading     = ref(false)
const trees       = ref([])
const treeNodes   = reactive({})
const treeLoading = reactive(new Set())
const expandedTrees = reactive(new Set())
const msg         = ref(null)

// ── Modals ─────────────────────────────────────────────────────────────────

const treeNameInput = ref(null)
const nodeNameInput = ref(null)

const treeModal = reactive({ open: false, isEdit: false, id: null, name: '', description: '', display_depth: 0, saving: false, msg: null })
const nodeModal = reactive({ open: false, isEdit: false, id: null, treeId: null, parentId: null, name: '', description: '', saving: false, msg: null })
const etsModal  = reactive({ open: false, treeName: '', mode: 'groups', autoLink: true, saving: false, msg: null })

const etsPresets = [
  { name: 'Topologie', mode: 'groups'    },
  { name: 'Gebäude',   mode: 'buildings' },
  { name: 'Gewerke',   mode: 'trades'    },
]
const deleteConfirm = reactive({ open: false, title: '', message: '', saving: false, action: null })

// ── Load ───────────────────────────────────────────────────────────────────

async function loadTrees() {
  loading.value = true
  try {
    const { data } = await hierarchyApi.listTrees()
    trees.value = data
  } catch {
    showMsg('Fehler beim Laden der Hierarchien', false)
  } finally {
    loading.value = false
  }
}

async function loadTreeNodes(treeId) {
  treeLoading.add(treeId)
  try {
    const { data } = await hierarchyApi.getTreeNodes(treeId)
    treeNodes[treeId] = data
  } catch {
    showMsg('Fehler beim Laden der Knoten', false)
  } finally {
    treeLoading.delete(treeId)
  }
}

function toggleTree(treeId) {
  if (expandedTrees.has(treeId)) {
    expandedTrees.delete(treeId)
  } else {
    expandedTrees.add(treeId)
    if (!treeNodes[treeId]) loadTreeNodes(treeId)
  }
}

// ── Tree CRUD ──────────────────────────────────────────────────────────────

function openCreateTree() {
  Object.assign(treeModal, { open: true, isEdit: false, id: null, name: '', description: '', display_depth: 0, saving: false, msg: null })
  nextTick(() => treeNameInput.value?.focus())
}

function openEditTree(tree) {
  Object.assign(treeModal, { open: true, isEdit: true, id: tree.id, name: tree.name, description: tree.description, display_depth: tree.display_depth ?? 0, saving: false, msg: null })
  nextTick(() => treeNameInput.value?.focus())
}

async function saveTree() {
  if (!treeModal.name.trim()) return
  treeModal.saving = true
  treeModal.msg = null
  try {
    if (treeModal.isEdit) {
      await hierarchyApi.updateTree(treeModal.id, { name: treeModal.name, description: treeModal.description, display_depth: treeModal.display_depth })
    } else {
      await hierarchyApi.createTree({ name: treeModal.name, description: treeModal.description, display_depth: treeModal.display_depth })
    }
    treeModal.open = false
    await loadTrees()
  } catch (e) {
    treeModal.msg = { ok: false, text: e.response?.data?.detail || 'Fehler beim Speichern' }
  } finally {
    treeModal.saving = false
  }
}

function confirmDeleteTree(tree) {
  deleteConfirm.title = `Ast löschen: ${tree.name}`
  deleteConfirm.message = 'Dieser Ast und alle enthaltenen Knoten sowie Verknüpfungen werden unwiderruflich gelöscht.'
  deleteConfirm.saving = false
  deleteConfirm.action = async () => {
    deleteConfirm.saving = true
    try {
      await hierarchyApi.deleteTree(tree.id)
      deleteConfirm.open = false
      expandedTrees.delete(tree.id)
      delete treeNodes[tree.id]
      await loadTrees()
    } catch {
      showMsg('Fehler beim Löschen', false)
      deleteConfirm.open = false
    }
  }
  deleteConfirm.open = true
}

// ── Node CRUD ──────────────────────────────────────────────────────────────

function addRootNode(tree) {
  Object.assign(nodeModal, { open: true, isEdit: false, id: null, treeId: tree.id, parentId: null, name: '', description: '', saving: false, msg: null })
  if (!expandedTrees.has(tree.id)) {
    expandedTrees.add(tree.id)
    if (!treeNodes[tree.id]) loadTreeNodes(tree.id)
  }
  nextTick(() => nodeNameInput.value?.focus())
}

function openAddChildNode(tree, parentNode) {
  Object.assign(nodeModal, { open: true, isEdit: false, id: null, treeId: tree.id, parentId: parentNode.id, name: '', description: '', saving: false, msg: null })
  nextTick(() => nodeNameInput.value?.focus())
}

function openEditNode(node) {
  Object.assign(nodeModal, { open: true, isEdit: true, id: node.id, treeId: node.tree_id, parentId: node.parent_id, name: node.name, description: node.description, saving: false, msg: null })
  nextTick(() => nodeNameInput.value?.focus())
}

async function saveNode() {
  if (!nodeModal.name.trim()) return
  nodeModal.saving = true
  nodeModal.msg = null
  try {
    if (nodeModal.isEdit) {
      await hierarchyApi.updateNode(nodeModal.id, { name: nodeModal.name, description: nodeModal.description })
    } else {
      await hierarchyApi.createNode({ tree_id: nodeModal.treeId, parent_id: nodeModal.parentId, name: nodeModal.name, description: nodeModal.description })
    }
    nodeModal.open = false
    await loadTreeNodes(nodeModal.treeId)
  } catch (e) {
    nodeModal.msg = { ok: false, text: e.response?.data?.detail || 'Fehler beim Speichern' }
  } finally {
    nodeModal.saving = false
  }
}

function confirmDeleteNode(node) {
  deleteConfirm.title = `Knoten löschen: ${node.name}`
  deleteConfirm.message = 'Dieser Knoten und alle Unterknoten sowie Verknüpfungen werden unwiderruflich gelöscht.'
  deleteConfirm.saving = false
  deleteConfirm.action = async () => {
    deleteConfirm.saving = true
    try {
      await hierarchyApi.deleteNode(node.id)
      deleteConfirm.open = false
      await loadTreeNodes(node.tree_id)
    } catch {
      showMsg('Fehler beim Löschen', false)
      deleteConfirm.open = false
    }
  }
  deleteConfirm.open = true
}

// ── ETS Import ─────────────────────────────────────────────────────────────

function openEtsImport() {
  Object.assign(etsModal, { open: true, treeName: '', mode: 'groups', autoLink: true, saving: false, msg: null })
}

async function doEtsImport() {
  if (!etsModal.treeName.trim()) return
  etsModal.saving = true
  etsModal.msg = null
  try {
    const { data } = await hierarchyApi.importFromEts({
      tree_name: etsModal.treeName,
      mode: etsModal.mode,
      auto_link: etsModal.autoLink,
    })
    etsModal.msg = { ok: true, text: data.message }
    await loadTrees()
    // Neuen Baum sofort öffnen
    expandedTrees.add(data.tree_id)
    await loadTreeNodes(data.tree_id)
    setTimeout(() => { etsModal.open = false }, 1500)
  } catch (e) {
    etsModal.msg = { ok: false, text: e.response?.data?.detail || 'Fehler beim Importieren' }
  } finally {
    etsModal.saving = false
  }
}

// ── Reorder ────────────────────────────────────────────────────────────────

async function reorderNode(tree, node, siblings, index, direction) {
  const swapIndex = direction === 'up' ? index - 1 : index + 1
  if (swapIndex < 0 || swapIndex >= siblings.length) return

  const other = siblings[swapIndex]
  // Tausche die order-Werte beider Knoten
  const orderA = index      // neuer order für "node" → Position von "other"
  const orderB = swapIndex  // neuer order für "other" → Position von "node"

  try {
    await Promise.all([
      hierarchyApi.updateNode(node.id,  { order: orderB }),
      hierarchyApi.updateNode(other.id, { order: orderA }),
    ])
    await loadTreeNodes(tree.id)
  } catch {
    showMsg('Fehler beim Verschieben', false)
  }
}

// ── Utils ──────────────────────────────────────────────────────────────────

function showMsg(text, ok) {
  msg.value = { text, ok }
  setTimeout(() => { msg.value = null }, 4000)
}

onMounted(loadTrees)
</script>
