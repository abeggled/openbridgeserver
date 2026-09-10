<template>
  <div class="flex flex-col gap-4">

    <!-- Toolbar -->
    <div class="flex flex-wrap items-center gap-2">
      <h3 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('hierarchy.title') }}</h3>
      <div class="flex-1" />
      <button @click="openCreateTree" class="btn-primary btn-sm" data-testid="btn-create-tree">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
        </svg>
        {{ $t('hierarchy.newHierarchy') }}
      </button>
      <button @click="openEtsImport" class="btn-secondary btn-sm" data-testid="btn-ets-import">
        <svg class="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M4 16v2a2 2 0 002 2h12a2 2 0 002-2v-2M7 10l5 5 5-5M12 15V3"/>
        </svg>
        {{ $t('hierarchy.importFromEts') }}
      </button>
    </div>

    <!-- Feedback -->
    <div v-if="msg" :class="['p-3 rounded-lg text-sm border', msg.ok ? 'bg-green-500/10 text-green-400 border-green-500/30' : 'bg-red-500/10 text-red-400 border-red-500/30']">
      {{ msg.text }}
    </div>

    <!-- Logic-graph palette (#1217) — drag onto a folder below to link it there -->
    <div class="card" data-testid="logic-graph-palette">
      <div class="card-header">
        <h4 class="font-semibold text-sm text-slate-800 dark:text-slate-100">{{ $t('hierarchy.logicGraphsTitle') }}</h4>
      </div>
      <div class="card-body pt-2 flex flex-col gap-2">
        <p class="text-xs text-slate-500">{{ $t('hierarchy.logicGraphsHint') }}</p>
        <div v-if="logicStore.graphs.length === 0" class="text-xs text-slate-500 py-2 text-center">
          {{ $t('hierarchy.logicGraphsEmpty') }}
        </div>
        <div v-else class="flex flex-wrap gap-2 max-h-32 overflow-y-auto">
          <div
            v-for="graph in logicStore.graphs" :key="graph.id"
            draggable="true"
            @dragstart="onGraphDragStart(graph, $event)"
            class="flex items-center gap-1.5 px-2.5 py-1 rounded-full border border-slate-200 dark:border-slate-600 text-xs cursor-grab active:cursor-grabbing bg-slate-50 dark:bg-slate-700/40 hover:border-blue-400 transition-colors"
            :class="graph.enabled ? 'text-slate-700 dark:text-slate-200' : 'text-slate-400'"
            :data-testid="`palette-graph-${graph.id}`">
            <svg class="w-3.5 h-3.5 shrink-0 text-teal-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v4a1 1 0 01-1 1H4m8-5v18m4-9h4m-4-5h4m-4 10h4"/>
            </svg>
            {{ graph.name }}
          </div>
        </div>
      </div>
    </div>

    <!-- Loading -->
    <div v-if="loading" class="flex justify-center py-8"><Spinner /></div>

    <!-- Empty state -->
    <div v-else-if="trees.length === 0" class="text-center py-12 text-sm text-slate-500">
      <svg class="w-10 h-10 mx-auto mb-3 text-slate-300 dark:text-slate-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
        <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M3 7h18M3 12h12M3 17h8"/>
      </svg>
      {{ $t('hierarchy.emptyHierarchy') }}
    </div>

    <!-- Tree list -->
    <div v-else class="flex flex-col gap-3">
      <div v-for="tree in trees" :key="tree.id" class="card" :data-testid="`tree-${tree.id}`">
        <div
          :class="['card-header flex items-center gap-2', dragOverTreeId === tree.id ? 'ring-2 ring-blue-400 bg-blue-50 dark:bg-blue-500/10' : '']"
          @dragover.prevent="dragOverTreeId = tree.id"
          @dragleave="dragOverTreeId === tree.id && (dragOverTreeId = null)"
          @drop.prevent="onDropOnTree(tree, $event)">
          <svg class="w-4 h-4 text-blue-500 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M3 7h18M3 12h12M3 17h8"/>
          </svg>
          <span
            @click="toggleTree(tree.id)"
            class="font-semibold text-sm text-slate-800 dark:text-slate-100 flex-1 cursor-pointer hover:text-blue-500 dark:hover:text-blue-400 transition-colors select-none">
            {{ tree.name }}
          </span>
          <span v-if="tree.description" class="text-xs text-slate-400 hidden sm:block">{{ formatTreeDescription(tree.description) }}</span>
          <button @click="toggleTree(tree.id)" class="btn-secondary btn-xs" :data-testid="`btn-expand-${tree.id}`">
            <svg class="w-3 h-3 transition-transform" :class="expandedTrees.has(tree.id) ? 'rotate-180' : ''" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 9l-7 7-7-7"/>
            </svg>
          </button>
          <button @click="openEditTree(tree)" class="btn-secondary btn-xs" :data-testid="`btn-edit-tree-${tree.id}`" :title="$t('hierarchy.rename')">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M15.232 5.232l3.536 3.536M9 13l6.293-6.293a1 1 0 011.414 0l1.586 1.586a1 1 0 010 1.414L12 16H9v-3z"/>
            </svg>
          </button>
          <button @click="addRootNode(tree)" class="btn-secondary btn-xs" :data-testid="`btn-add-root-${tree.id}`" :title="$t('hierarchy.addNode')">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M12 4v16m8-8H4"/>
            </svg>
          </button>
          <button @click="confirmDeleteTree(tree)" class="btn-danger btn-xs" :data-testid="`btn-delete-tree-${tree.id}`" :title="$t('hierarchy.deleteNode')">
            <svg class="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6M9 7h6m-7 0a1 1 0 011-1h4a1 1 0 011 1m-7 0h8"/>
            </svg>
          </button>
        </div>

        <!-- Logic graphs linked directly to the tree's own top level (#1217
             follow-up) — no visible folder represents them, shown right on
             the card. -->
        <div v-if="treeRootGraphs[tree.id]?.length" class="px-3 pb-2 flex flex-wrap gap-1">
          <span v-for="graph in treeRootGraphs[tree.id]" :key="graph.link_id"
            class="inline-flex items-center gap-1 px-2 py-0.5 rounded-full text-[11px] bg-teal-50 dark:bg-teal-500/10 text-teal-700 dark:text-teal-300 border border-teal-200 dark:border-teal-500/30"
            :data-testid="`linked-graph-tree-${tree.id}-${graph.id}`">
            {{ graph.name }}
            <button type="button" @click="unlinkTreeGraph(tree, graph)" :title="$t('hierarchy.unlinkLogicGraph')" class="hover:text-red-500" :data-testid="`btn-unlink-graph-tree-${tree.id}-${graph.id}`">✕</button>
          </span>
        </div>

        <!-- Tree nodes (collapsible) -->
        <div v-if="expandedTrees.has(tree.id)" class="card-body pt-0">
          <div v-if="treeLoading.has(tree.id)" class="flex justify-center py-4"><Spinner size="sm" /></div>
          <div v-else-if="!treeNodes[tree.id]?.length" class="text-xs text-slate-500 py-2 text-center">
            {{ $t('hierarchy.emptyHierarchy') }}
          </div>
          <div v-else class="tree-container">
            <HierarchyNodeTree
              :nodes="treeNodes[tree.id]"
              :tree-id="tree.id"
              @add-child="(parentNode) => openAddChildNode(tree, parentNode)"
              @edit="openEditNode"
              @delete="confirmDeleteNode"
              @reorder="({ node, siblings, index, direction }) => reorderNode(tree, node, siblings, index, direction)"
              @link-graph-error="() => showMsg(t('hierarchy.linkLogicGraphError'), false)"
              @unlink-graph-error="() => showMsg(t('hierarchy.unlinkLogicGraphError'), false)"
              @load-graphs-error="() => showMsg(t('hierarchy.loadLogicGraphsError'), false)"
            />
          </div>
        </div>
      </div>
    </div>

    <!-- ── Modal: Create/Edit Tree ── -->
    <div v-if="treeModal.open" class="fixed top-0 left-0 bottom-0 z-50 flex items-center justify-center bg-black/50" :style="{ right: help.reservedRight }" @click.self="treeModal.open = false">
      <div class="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-sm p-6 flex flex-col gap-4">
        <h3 class="font-semibold text-slate-800 dark:text-slate-100">
          {{ treeModal.isEdit ? $t('hierarchy.rename') : $t('hierarchy.newHierarchy') }}
        </h3>
        <div class="form-group">
          <label class="label">Name</label>
          <input v-model="treeModal.name" ref="treeNameInput" type="text" class="input" :placeholder="treeModal.isEdit ? '' : $t('hierarchy.namePlaceholder')" @keydown.enter="saveTree" />
        </div>
        <div class="form-group">
          <label class="label">{{ $t('hierarchy.description') }}</label>
          <input v-model="treeModal.description" type="text" class="input" :placeholder="$t('hierarchy.optional')" @keydown.enter="saveTree" />
        </div>
        <div class="form-group">
          <label class="label">{{ $t('hierarchy.displayStartLevel') }}</label>
          <select v-model="treeModal.display_depth" class="input text-sm" data-testid="select-display-depth">
            <option v-for="opt in depthOptions" :key="opt.value" :value="opt.value" :disabled="opt.disabled">{{ opt.label }}</option>
          </select>
          <p class="text-xs text-slate-500 mt-1">{{ $t('hierarchy.displayStartLevelHelp') }}</p>
        </div>
        <div v-if="treeModal.msg" :class="['p-2 rounded text-sm', treeModal.msg.ok ? 'text-green-400' : 'text-red-400']">{{ treeModal.msg.text }}</div>
        <div class="flex gap-2 justify-end">
          <button @click="treeModal.open = false" class="btn-secondary">{{ $t('common.cancel') }}</button>
          <button @click="saveTree" class="btn-primary" :disabled="treeModal.saving">
            <Spinner v-if="treeModal.saving" size="sm" color="white" />
            {{ $t('common.save') }}
          </button>
        </div>
      </div>
    </div>

    <!-- ── Modal: Create/Edit Node ── -->
    <div v-if="nodeModal.open" class="fixed top-0 left-0 bottom-0 z-50 flex items-center justify-center bg-black/50" :style="{ right: help.reservedRight }" @click.self="nodeModal.open = false">
      <div class="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-sm p-6 flex flex-col gap-4">
        <h3 class="font-semibold text-slate-800 dark:text-slate-100">
          {{ nodeModal.isEdit ? $t('hierarchy.editNode') : $t('hierarchy.addNode') }}
        </h3>
        <div class="form-group">
          <label class="label">Name</label>
          <input v-model="nodeModal.name" ref="nodeNameInput" type="text" class="input" :placeholder="$t('hierarchy.nodePlaceholder')" @keydown.enter="saveNode" />
        </div>
        <div class="form-group">
          <label class="label">{{ $t('hierarchy.description') }}</label>
          <input v-model="nodeModal.description" type="text" class="input" :placeholder="$t('hierarchy.optional')" @keydown.enter="saveNode" />
        </div>
        <div v-if="nodeModal.msg" :class="['p-2 rounded text-sm', nodeModal.msg.ok ? 'text-green-400' : 'text-red-400']">{{ nodeModal.msg.text }}</div>
        <div class="flex gap-2 justify-end">
          <button @click="nodeModal.open = false" class="btn-secondary">{{ $t('common.cancel') }}</button>
          <button @click="saveNode" class="btn-primary" :disabled="nodeModal.saving">
            <Spinner v-if="nodeModal.saving" size="sm" color="white" />
            {{ $t('common.save') }}
          </button>
        </div>
      </div>
    </div>

    <!-- ── Modal: ETS Import ── -->
    <div v-if="etsModal.open" class="fixed top-0 left-0 bottom-0 z-50 flex items-center justify-center bg-black/50" :style="{ right: help.reservedRight }" @click.self="etsModal.open = false">
      <div class="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-lg p-6 flex flex-col gap-4">
        <h3 class="font-semibold text-slate-800 dark:text-slate-100">{{ $t('hierarchy.importTitle') }}</h3>
        <p class="text-sm text-slate-500">
          <span v-if="['buildings','trades'].includes(etsModal.mode)">{{ $t('hierarchy.importDescription') }}</span>
          <span v-else>{{ $t('hierarchy.importDescription') }}</span>
        </p>

        <!-- Schnell-Presets -->
        <div class="form-group">
          <label class="label">{{ $t('hierarchy.perspective') }}</label>
          <div class="flex gap-2 flex-wrap">
            <button v-for="preset in etsPresets" :key="preset.key"
              @click="etsModal.treeName = $t(preset.nameKey); etsModal.mode = preset.mode"
              :class="['px-3 py-1.5 rounded-lg border text-sm font-medium transition-colors',
                etsModal.treeName === $t(preset.nameKey) && etsModal.mode === preset.mode
                  ? 'border-blue-500 bg-blue-500/10 text-blue-600 dark:text-blue-400'
                  : 'border-slate-200 dark:border-slate-600 text-slate-600 dark:text-slate-300 hover:border-blue-400']">
              {{ $t(preset.nameKey) }}
            </button>
          </div>
          <p class="text-xs text-slate-500 mt-1.5">
            {{ $t('hierarchy.perspectiveHelp') }}
          </p>
        </div>

        <div class="form-group">
          <label class="label">Name</label>
          <input v-model="etsModal.treeName" type="text" class="input" :placeholder="$t('hierarchy.namePlaceholder')" />
        </div>

        <div class="form-group">
          <label class="label">{{ $t('hierarchy.mode') }}</label>
          <select v-model="etsModal.mode" class="input text-sm">
            <optgroup :label="$t('hierarchy.modeTopology')">
              <option value="groups">{{ $t('hierarchy.modeTopology3Level') }}</option>
              <option value="mid">{{ $t('hierarchy.modeTopology2LevelMid') }}</option>
              <option value="flat">{{ $t('hierarchy.modeTopology2LevelGA') }}</option>
            </optgroup>
            <optgroup :label="$t('hierarchy.modeBuilding')">
              <option value="buildings">{{ $t('hierarchy.modeBuildingLevels') }}</option>
              <option value="trades">{{ $t('hierarchy.modeTrades') }}</option>
            </optgroup>
          </select>
        </div>

        <div v-if="['buildings','trades'].includes(etsModal.mode)" class="flex items-center gap-2 text-sm text-slate-600 dark:text-slate-300">
          <input id="auto-link" v-model="etsModal.autoLink" type="checkbox" class="rounded" />
          <label for="auto-link">{{ $t('hierarchy.autoLink') }}</label>
        </div>

        <div v-if="etsModal.msg" :class="['p-2 rounded text-sm', etsModal.msg.ok ? 'text-green-400' : 'text-red-400']">{{ etsModal.msg.text }}</div>
        <div class="flex gap-2 justify-end">
          <button @click="etsModal.open = false" class="btn-secondary">{{ $t('common.cancel') }}</button>
          <button @click="doEtsImport" class="btn-primary" :disabled="etsModal.saving || !etsModal.treeName.trim()">
            <Spinner v-if="etsModal.saving" size="sm" color="white" />
            {{ $t('hierarchy.importFromEts') }}
          </button>
        </div>
      </div>
    </div>

    <!-- ── Modal: Confirm Delete ── -->
    <div v-if="deleteConfirm.open" class="fixed top-0 left-0 bottom-0 z-50 flex items-center justify-center bg-black/50" :style="{ right: help.reservedRight }" @click.self="deleteConfirm.open = false">
      <div class="bg-white dark:bg-slate-800 rounded-xl shadow-2xl w-full max-w-sm p-6 flex flex-col gap-4">
        <h3 class="font-semibold text-slate-800 dark:text-slate-100">{{ deleteConfirm.heading }}</h3>
        <p class="text-sm text-slate-500">{{ deleteConfirm.body }}</p>
        <div class="flex gap-2 justify-end">
          <button @click="deleteConfirm.open = false" class="btn-secondary">{{ $t('common.cancel') }}</button>
          <button @click="deleteConfirm.action" class="btn-danger" :disabled="deleteConfirm.saving">
            <Spinner v-if="deleteConfirm.saving" size="sm" color="white" />
            {{ $t('common.delete') }}
          </button>
        </div>
      </div>
    </div>

  </div>
</template>

<script setup>
import { ref, reactive, computed, nextTick, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import { hierarchyApi } from '@/api/client.js'
import HierarchyNodeTree from '@/components/HierarchyNodeTree.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { buildDepthOptions } from '@/utils/hierarchyDepthOptions.js'
import { useHelpStore } from '@/stores/help'
import { useLogicStore } from '@/stores/logic'
import { LOGIC_GRAPH_DRAG_MIME } from '@/utils/hierarchyLogicGraphDrag.js'

const { t } = useI18n()
const help = useHelpStore()
const logicStore = useLogicStore()

// Dragging a logic graph onto a node folder links it there — additive, the
// palette entry stays (a graph can be dropped onto many folders/trees).
function onGraphDragStart(graph, event) {
  event.dataTransfer.setData(LOGIC_GRAPH_DRAG_MIME, graph.id)
  event.dataTransfer.effectAllowed = 'copy'
}

// ── Graphs linked directly to a tree's own top level (#1217 follow-up) ─────
// A tree's hidden root node (tree.root_node_id) behaves like an ordinary
// node for linking purposes — dropping on the card header itself just links
// to that node, sparing the user from creating a same-named sub-folder.
const treeRootGraphs = reactive({})
const dragOverTreeId = ref(null)

async function loadTreeRootGraphs(tree) {
  if (!tree.root_node_id) return
  try {
    const { data } = await hierarchyApi.getNodeLogicGraphs(tree.root_node_id)
    treeRootGraphs[tree.id] = data
  } catch {
    showMsg(t('hierarchy.loadLogicGraphsError'), false)
  }
}

async function onDropOnTree(tree, event) {
  dragOverTreeId.value = null
  const graphId = event.dataTransfer.getData(LOGIC_GRAPH_DRAG_MIME)
  if (!graphId || !tree.root_node_id) return
  try {
    await hierarchyApi.createLogicGraphLink({ node_id: tree.root_node_id, graph_id: graphId })
    await loadTreeRootGraphs(tree)
  } catch {
    showMsg(t('hierarchy.linkLogicGraphError'), false)
  }
}

async function unlinkTreeGraph(tree, graph) {
  try {
    await hierarchyApi.deleteLogicGraphLink(tree.root_node_id, graph.id)
    await loadTreeRootGraphs(tree)
  } catch {
    showMsg(t('hierarchy.unlinkLogicGraphError'), false)
  }
}

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

function formatTreeDescription(desc) {
  if (!desc) return ''
  if (desc.startsWith('ets_import:')) {
    const mode = desc.slice('ets_import:'.length)
    const modeLabel = t(`hierarchy.etsMode.${mode}`, mode)
    return t('hierarchy.etsImportDesc', { mode: modeLabel })
  }
  return desc
}

const treeModal = reactive({ open: false, isEdit: false, id: null, name: '', description: '', display_depth: 0, saving: false, msg: null })

const depthOptions = computed(() =>
  buildDepthOptions({
    isEdit: treeModal.isEdit,
    tree: treeModal.isEdit ? { id: treeModal.id, name: treeModal.name } : null,
    rootNodes: treeModal.isEdit ? treeNodes[treeModal.id] : null,
    t,
  })
)
const nodeModal = reactive({ open: false, isEdit: false, id: null, treeId: null, parentId: null, name: '', description: '', saving: false, msg: null })
const etsModal  = reactive({ open: false, treeName: '', mode: 'groups', autoLink: true, saving: false, msg: null })

const etsPresets = [
  { key: 'topology', nameKey: 'hierarchy.presetTopology', mode: 'groups'    },
  { key: 'building', nameKey: 'hierarchy.presetBuilding', mode: 'buildings' },
  { key: 'trades',   nameKey: 'hierarchy.presetTrades',   mode: 'trades'    },
]
const deleteConfirm = reactive({ open: false, heading: '', body: '', saving: false, action: null })

// ── Load ───────────────────────────────────────────────────────────────────

async function loadTrees() {
  loading.value = true
  try {
    const { data } = await hierarchyApi.listTrees()
    trees.value = data
    await Promise.all(data.map(loadTreeRootGraphs))
  } catch {
    showMsg(t('hierarchy.errorLoading'), false)
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
    showMsg(t('hierarchy.errorLoadingNodes'), false)
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
  if (!treeNodes[tree.id]) loadTreeNodes(tree.id)
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
    treeModal.msg = { ok: false, text: e.response?.data?.detail || t('hierarchy.errorSaving') }
  } finally {
    treeModal.saving = false
  }
}

function confirmDeleteTree(tree) {
  deleteConfirm.heading = `${t('hierarchy.deleteNode')}: ${tree.name}`
  deleteConfirm.body = t('hierarchy.confirmDeleteHierarchy')
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
      showMsg(t('hierarchy.errorDeleting'), false)
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
    nodeModal.msg = { ok: false, text: e.response?.data?.detail || t('hierarchy.errorSaving') }
  } finally {
    nodeModal.saving = false
  }
}

function confirmDeleteNode(node) {
  deleteConfirm.heading = `${t('hierarchy.deleteNode')}: ${node.name}`
  deleteConfirm.body = t('hierarchy.confirmDeleteNode')
  deleteConfirm.saving = false
  deleteConfirm.action = async () => {
    deleteConfirm.saving = true
    try {
      await hierarchyApi.deleteNode(node.id)
      deleteConfirm.open = false
      await loadTreeNodes(node.tree_id)
    } catch {
      showMsg(t('hierarchy.errorDeleting'), false)
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
    etsModal.msg = { ok: false, text: e.response?.data?.detail || t('hierarchy.errorImporting') }
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
    showMsg(t('hierarchy.errorSaving'), false)
  }
}

// ── Utils ──────────────────────────────────────────────────────────────────

function showMsg(text, ok) {
  msg.value = { text, ok }
  setTimeout(() => { msg.value = null }, 4000)
}

onMounted(() => {
  loadTrees()
  // Settings → Hierarchy can be the first page visited in a session, so the
  // logic store may not have fetched its graph list yet — safe/idempotent
  // to re-fetch even if LogicView already populated it.
  logicStore.fetchGraphs()
})
</script>
