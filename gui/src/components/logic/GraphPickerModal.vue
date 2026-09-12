<template>
  <Modal v-model="open" :title="$t('logic.graphPicker.title')" max-width="md">
    <template #header-actions>
      <button type="button" class="btn-secondary btn-sm" @click="goOrganize" data-testid="btn-organize-graphs">
        {{ $t('logic.graphPicker.organize') }}
      </button>
    </template>

    <div class="flex flex-col gap-3">
      <!-- Breadcrumb -->
      <nav class="flex items-center gap-1 text-xs text-slate-500 dark:text-slate-400 flex-wrap" data-testid="graph-picker-breadcrumb">
        <button type="button" class="hover:text-blue-500 dark:hover:text-blue-400" @click="goToRoot" data-testid="crumb-root">
          {{ $t('logic.graphPicker.breadcrumbRoot') }}
        </button>
        <template v-if="treeCrumb">
          <span>／</span>
          <button type="button" class="hover:text-blue-500 dark:hover:text-blue-400" @click="goToTree" data-testid="crumb-tree">
            {{ treeCrumb.name }}
          </button>
        </template>
        <template v-if="unassignedMode">
          <span>／</span>
          <span class="text-slate-600 dark:text-slate-300" data-testid="crumb-unassigned">{{ $t('logic.graphPicker.unassigned') }}</span>
        </template>
        <template v-for="(crumb, i) in nodeCrumbs" :key="crumb.id">
          <span>／</span>
          <button type="button" class="hover:text-blue-500 dark:hover:text-blue-400" @click="goToNodeCrumb(i)" :data-testid="`crumb-node-${crumb.id}`">
            {{ crumb.name }}
          </button>
        </template>
      </nav>

      <div v-if="loading" class="flex justify-center py-8"><Spinner /></div>
      <div v-else-if="errorMsg" class="text-sm text-red-400 py-4 text-center">{{ errorMsg }}</div>
      <div v-else class="flex flex-col gap-1 max-h-[60vh] overflow-y-auto">
        <div v-if="isRootLevel && result.trees.length === 0 && !result.has_unassigned_logic_graphs" class="text-sm text-slate-500 py-6 text-center">
          {{ $t('logic.graphPicker.noTrees') }}
        </div>
        <div v-else-if="!isRootLevel && result.subfolders.length === 0 && result.logic_graphs.length === 0" class="text-sm text-slate-500 py-6 text-center">
          {{ $t('logic.graphPicker.empty') }}
        </div>

        <!-- Trees (root level) -->
        <button v-for="tree in result.trees" :key="tree.id" type="button"
          class="flex items-center gap-2 px-3 py-2 rounded-lg text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-700/50 transition-colors"
          @click="openTree(tree)" :data-testid="`picker-tree-${tree.id}`">
          <FolderIcon class="text-blue-500" />
          <span class="flex-1 truncate text-slate-700 dark:text-slate-200">{{ tree.name }}</span>
        </button>

        <!-- Pseudo-folder for unlinked graphs (root level only, #1217 follow-up) -->
        <button v-if="isRootLevel && result.has_unassigned_logic_graphs" type="button"
          class="flex items-center gap-2 px-3 py-2 rounded-lg text-left text-sm border border-dashed border-slate-300 dark:border-slate-600 hover:bg-slate-100 dark:hover:bg-slate-700/50 transition-colors"
          @click="openUnassigned" data-testid="picker-unassigned">
          <FolderIcon class="text-slate-400" />
          <span class="flex-1 truncate text-slate-500 dark:text-slate-400">{{ $t('logic.graphPicker.unassigned') }}</span>
        </button>

        <!-- Subfolders -->
        <button v-for="folder in result.subfolders" :key="folder.id" type="button"
          class="flex items-center gap-2 px-3 py-2 rounded-lg text-left text-sm hover:bg-slate-100 dark:hover:bg-slate-700/50 transition-colors"
          @click="openNode(folder)" :data-testid="`picker-folder-${folder.id}`">
          <FolderIcon class="text-amber-500" />
          <span class="flex-1 truncate text-slate-700 dark:text-slate-200">{{ folder.name }}</span>
        </button>

        <!-- Logic graphs — assignment to a hierarchy position now happens
             entirely here (#1233 follow-up): Settings → Hierarchy only
             manages the tree/node structure itself. -->
        <div v-for="graph in result.logic_graphs" :key="graph.link_id ?? graph.id"
          :class="['flex flex-col gap-1 px-1 py-1 rounded-lg hover:bg-blue-50 dark:hover:bg-blue-500/10 transition-colors',
            graph.id === activeGraphId ? 'ring-1 ring-blue-400 bg-blue-50/60 dark:bg-blue-500/10' : '']">
          <button type="button"
            class="flex items-center gap-2 min-w-0 px-3 py-2 rounded-lg text-left text-sm"
            @click="pick(graph)" :data-testid="`picker-graph-${graph.id}`">
            <svg class="w-4 h-4 shrink-0 text-teal-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
              <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v4a1 1 0 01-1 1H4m8-5v18m4-9h4m-4-5h4m-4 10h4"/>
            </svg>
            <span class="flex-1 truncate" :class="graph.enabled ? 'text-slate-700 dark:text-slate-200' : 'text-slate-400'">
              {{ graph.name }}{{ graph.enabled ? '' : $t('logic.graphDisabledSuffix') }}
            </span>
            <span v-if="graph.id === activeGraphId" class="text-[10px] uppercase tracking-wide text-blue-500 shrink-0" :data-testid="`picker-graph-current-${graph.id}`">
              {{ $t('logic.graphPicker.currentGraph') }}
            </span>
          </button>
          <div class="flex items-center gap-1 pl-9 flex-wrap">
            <button type="button" class="btn-secondary btn-xs shrink-0"
              @click="openAssign(graph)" :data-testid="`picker-graph-assign-${graph.id}`">
              {{ $t('logic.graphPicker.assign') }}
            </button>
            <!-- Only a real hierarchy position (not the unassigned pseudo-folder)
                 has a link here to remove. -->
            <button v-if="!unassignedMode" type="button"
              class="btn-secondary btn-xs shrink-0"
              :title="$t('logic.graphPicker.removeHereTitle')"
              @click="removeHere(graph)" :data-testid="`picker-graph-remove-${graph.id}`">
              {{ $t('logic.graphPicker.removeHere') }}
            </button>
            <button type="button"
              class="btn-secondary btn-xs text-red-400 shrink-0"
              :title="unassignedMode ? $t('logic.graphPicker.deleteHereTitle') : $t('logic.graphPicker.deleteTitle')"
              @click="confirmDelete(graph)" :data-testid="`picker-graph-delete-${graph.id}`">
              {{ $t('common.delete') }}
            </button>
          </div>
        </div>
      </div>
    </div>

    <!-- Assign a hierarchy position (additive — existing assignments stay) -->
    <Modal v-model="assignModal.open" :title="$t('logic.graphPicker.assign')" max-width="sm">
      <div class="flex flex-col gap-4">
        <p class="text-xs text-slate-500">{{ $t('logic.graphPicker.assignHint') }}</p>
        <HierarchyCombobox v-model="assignModal.nodes" include-tree-roots data-testid="assign-hierarchy-combobox" />
        <div v-if="assignModal.msg" class="text-sm text-red-400">{{ assignModal.msg }}</div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="assignModal.open = false" class="btn-secondary">{{ $t('common.cancel') }}</button>
          <button type="button" class="btn-primary" :disabled="assignModal.nodes.length === 0 || assignModal.saving"
            @click="confirmAssign" data-testid="btn-assign-confirm">
            <Spinner v-if="assignModal.saving" size="sm" color="white" />
            {{ $t('logic.graphPicker.assignConfirm') }}
          </button>
        </div>
      </div>
    </Modal>

    <ConfirmDialog v-model="showDeleteConfirm"
      :title="$t('logic.deleteGraph')"
      :message="deleteMessage"
      :confirm-label="$t('common.delete')"
      @confirm="doDelete" />
  </Modal>
</template>

<script setup>
import { ref, reactive, computed, watch, h } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Modal from '@/components/ui/Modal.vue'
import Spinner from '@/components/ui/Spinner.vue'
import ConfirmDialog from '@/components/ui/ConfirmDialog.vue'
import HierarchyCombobox from '@/components/ui/HierarchyCombobox.vue'
import { hierarchyApi } from '@/api/client.js'
import { useLogicStore } from '@/stores/logic'
import { parseHierarchyCompositeId } from '@/utils/hierarchyDisplay.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
  // The graph currently open in the Logic editor, if any (#1233 follow-up) —
  // used to preselect/highlight its row and to auto-navigate to the
  // hierarchy position it was last opened from.
  activeGraphId: { type: String, default: '' },
})
const emit = defineEmits(['update:modelValue', 'select', 'graph-deleted'])

const router = useRouter()
const { t } = useI18n()
const logicStore = useLogicStore()

// Small inline folder icon — avoids a new asset file for one shared glyph.
const FolderIcon = {
  render: () =>
    h('svg', { class: 'w-4 h-4 shrink-0', fill: 'none', stroke: 'currentColor', viewBox: '0 0 24 24' }, [
      h('path', { 'stroke-linecap': 'round', 'stroke-linejoin': 'round', 'stroke-width': '2', d: 'M3 7a2 2 0 012-2h4l2 2h8a2 2 0 012 2v8a2 2 0 01-2 2H5a2 2 0 01-2-2V7z' }),
    ]),
}

const open = computed({
  get: () => props.modelValue,
  set: (v) => emit('update:modelValue', v),
})

// ── Navigation state — forest → tree → node, plus the flat "unassigned"
// pseudo-folder (#1217 follow-up), which is a fourth, leaf-only level of its
// own reachable only from the forest root and never nested under a tree. ──
const treeCrumb      = ref(null)  // { id, name } | null (null = root/forest level)
const nodeCrumbs     = ref([])    // [{ id, name }, …] ancestor chain within treeCrumb
const unassignedMode = ref(false) // true while browsing the "Nicht zugeordnet" pseudo-folder

const isRootLevel = computed(() => treeCrumb.value === null && !unassignedMode.value)

const loading  = ref(false)
const errorMsg = ref('')
const result   = ref({ trees: [], subfolders: [], logic_graphs: [], has_unassigned_logic_graphs: false })

// Remembers, per graph id, the hierarchy position last browsed while that
// graph was visible in the current listing. This component instance stays
// mounted across opens/closes (only Modal's own v-if toggles visibility), so
// reopening the picker on the same active graph can jump straight back to
// where it was found instead of resetting to the root every time (#1233
// follow-up: "expand the hierarchy level it was opened from").
const lastLocationByGraph = {}

function currentLocation() {
  return { treeCrumb: treeCrumb.value, nodeCrumbs: nodeCrumbs.value, unassignedMode: unassignedMode.value }
}

function applyLocation(loc) {
  treeCrumb.value = loc.treeCrumb
  nodeCrumbs.value = loc.nodeCrumbs
  unassignedMode.value = loc.unassignedMode
}

async function browse() {
  loading.value = true
  errorMsg.value = ''
  try {
    let params = {}
    if (unassignedMode.value) {
      params = { unassigned: true }
    } else {
      if (treeCrumb.value) params.tree_id = treeCrumb.value.id
      const lastNode = nodeCrumbs.value[nodeCrumbs.value.length - 1]
      if (lastNode) params.node_id = lastNode.id
    }
    const { data } = await hierarchyApi.browse(params)
    result.value = data
    if (props.activeGraphId && data.logic_graphs.some((g) => g.id === props.activeGraphId)) {
      lastLocationByGraph[props.activeGraphId] = currentLocation()
    }
  } catch {
    errorMsg.value = t('logic.graphPicker.errorLoading')
  } finally {
    loading.value = false
  }
}

function goToRoot() {
  treeCrumb.value = null
  nodeCrumbs.value = []
  unassignedMode.value = false
  browse()
}
function openTree(tree) {
  treeCrumb.value = { id: tree.id, name: tree.name }
  nodeCrumbs.value = []
  unassignedMode.value = false
  browse()
}
function goToTree() {
  nodeCrumbs.value = []
  browse()
}
function openNode(folder) {
  nodeCrumbs.value = [...nodeCrumbs.value, { id: folder.id, name: folder.name }]
  browse()
}
function goToNodeCrumb(index) {
  nodeCrumbs.value = nodeCrumbs.value.slice(0, index + 1)
  browse()
}
function openUnassigned() {
  treeCrumb.value = null
  nodeCrumbs.value = []
  unassignedMode.value = true
  browse()
}

// Navigates to wherever `activeGraphId` currently lives: a remembered
// location from earlier this session, else its first hierarchy assignment
// (fetched once), else the "Nicht zugeordnet" pseudo-folder.
async function locateActiveGraph() {
  const remembered = lastLocationByGraph[props.activeGraphId]
  if (remembered) {
    applyLocation(remembered)
    await browse()
    return
  }
  try {
    const { data: assignments } = await hierarchyApi.getLogicGraphNodes(props.activeGraphId)
    const first = assignments[0]
    if (!first) {
      applyLocation({ treeCrumb: null, nodeCrumbs: [], unassignedMode: true })
    } else if (first.is_tree_root) {
      applyLocation({ treeCrumb: { id: first.tree_id, name: first.tree_name }, nodeCrumbs: [], unassignedMode: false })
    } else {
      const crumbs = [
        ...first.node_path.map((seg) => ({ id: seg.node_id, name: seg.node_name })),
        { id: first.node_id, name: first.node_name },
      ]
      applyLocation({ treeCrumb: { id: first.tree_id, name: first.tree_name }, nodeCrumbs: crumbs, unassignedMode: false })
    }
  } catch {
    applyLocation({ treeCrumb: null, nodeCrumbs: [], unassignedMode: false })
  }
  await browse()
}

function pick(graph) {
  emit('select', graph.id)
  open.value = false
}

// Unlinks the graph from whichever hierarchy position is currently being
// browsed (this level's node — a tree's own root included). Purely
// organizational: if this was the graph's last link, it simply reappears
// under "Nicht zugeordnet" next time the picker is opened at the root level.
async function removeHere(graph) {
  await hierarchyApi.deleteLogicGraphLinkById(graph.link_id)
  await browse()
}

// ── Assign a hierarchy position (#1233 follow-up) ───────────────────────────
// Replaces the drag&drop-onto-a-node assignment previously offered on the
// Settings → Hierarchy page — every row here, assigned or not, offers this.
// Additive: existing assignments elsewhere are never touched.
const assignModal = reactive({ open: false, graph: null, nodes: [], saving: false, msg: null })

function openAssign(graph) {
  assignModal.graph = graph
  assignModal.nodes = []
  assignModal.msg = null
  assignModal.open = true
}

async function confirmAssign() {
  if (!assignModal.graph || assignModal.nodes.length === 0) return
  assignModal.saving = true
  assignModal.msg = null
  try {
    const results = await Promise.allSettled(
      assignModal.nodes.map((compositeId) => {
        const parsed = parseHierarchyCompositeId(compositeId)
        if (!parsed) return Promise.reject(new Error('invalid_composite_id'))
        return hierarchyApi.createLogicGraphLink({ node_id: parsed.node_id, graph_id: assignModal.graph.id })
      }),
    )
    await browse()
    if (results.some((r) => r.status === 'rejected')) {
      assignModal.msg = t('logic.graphPicker.assignError')
    } else {
      assignModal.open = false
    }
  } finally {
    assignModal.saving = false
  }
}

// Full graph deletion — reachable from every row now, not just the
// unassigned pseudo-folder: cascades any remaining hierarchy links via the
// existing ON DELETE CASCADE foreign key, so no extra cleanup is needed here.
const showDeleteConfirm = ref(false)
const deleteTarget = ref(null)
const deleteMessage = computed(() =>
  deleteTarget.value ? t('logic.graphPicker.deleteConfirm', { name: deleteTarget.value.name }) : '',
)
function confirmDelete(graph) {
  deleteTarget.value = graph
  showDeleteConfirm.value = true
}
async function doDelete() {
  const id = deleteTarget.value.id
  deleteTarget.value = null
  await logicStore.deleteGraph(id)
  emit('graph-deleted', id)
  await browse()
}

function goOrganize() {
  open.value = false
  router.push('/settings?tab=hierarchy')
}

// Reset to the top level every time the popup is (re)opened — or, when a
// graph is currently open in the editor, navigate straight to where it was
// found. Immediate so a component mounted already-open (e.g. tests) also
// browses right away.
watch(open, (v) => {
  if (!v) return
  if (props.activeGraphId) {
    locateActiveGraph()
    return
  }
  treeCrumb.value = null
  nodeCrumbs.value = []
  unassignedMode.value = false
  browse()
}, { immediate: true })
</script>
