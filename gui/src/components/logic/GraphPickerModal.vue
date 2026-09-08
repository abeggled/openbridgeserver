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

        <!-- Logic graphs -->
        <button v-for="graph in result.logic_graphs" :key="graph.link_id" type="button"
          class="flex items-center gap-2 px-3 py-2 rounded-lg text-left text-sm hover:bg-blue-50 dark:hover:bg-blue-500/10 transition-colors"
          @click="pick(graph)" :data-testid="`picker-graph-${graph.id}`">
          <svg class="w-4 h-4 shrink-0 text-teal-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 3v4a1 1 0 01-1 1H4m8-5v18m4-9h4m-4-5h4m-4 10h4"/>
          </svg>
          <span class="flex-1 truncate" :class="graph.enabled ? 'text-slate-700 dark:text-slate-200' : 'text-slate-400'">
            {{ graph.name }}{{ graph.enabled ? '' : $t('logic.graphDisabledSuffix') }}
          </span>
        </button>
      </div>
    </div>
  </Modal>
</template>

<script setup>
import { ref, computed, watch, h } from 'vue'
import { useRouter } from 'vue-router'
import { useI18n } from 'vue-i18n'
import Modal from '@/components/ui/Modal.vue'
import Spinner from '@/components/ui/Spinner.vue'
import { hierarchyApi } from '@/api/client.js'

const props = defineProps({
  modelValue: { type: Boolean, default: false },
})
const emit = defineEmits(['update:modelValue', 'select'])

const router = useRouter()
const { t } = useI18n()

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

function pick(graph) {
  emit('select', graph.id)
  open.value = false
}

function goOrganize() {
  open.value = false
  router.push('/settings?tab=hierarchy')
}

// Reset to the top level every time the popup is (re)opened — immediate so a
// component mounted already-open (e.g. tests) also browses right away.
watch(open, (v) => {
  if (v) {
    treeCrumb.value = null
    nodeCrumbs.value = []
    unassignedMode.value = false
    browse()
  }
}, { immediate: true })
</script>
