<template>
  <div class="flex flex-col h-full" style="height: calc(100vh - 4rem)">
    <!-- Toolbar -->
    <div class="flex items-center gap-3 px-4 py-2 bg-surface-800 border-b border-slate-200 dark:border-slate-700/60 flex-shrink-0">
      <h2 class="text-sm font-bold text-slate-800 dark:text-slate-100">Logikmodul</h2>
      <div class="flex-1" />
      <!-- Graph selector -->
      <select v-model="activeGraphId" @change="loadGraph"
        class="input text-xs py-1 px-2 max-w-[200px]" data-testid="select-graph">
        <option value="">— Graph wählen —</option>
        <option v-for="g in store.graphs" :key="g.id" :value="g.id">{{ g.name }}</option>
      </select>
      <button @click="newGraph" class="btn-primary btn-sm">+ Neu</button>
      <button v-if="activeGraphId" @click="saveGraph" class="btn-secondary btn-sm" :disabled="saving">
        <Spinner v-if="saving" size="sm" color="white" />
        Speichern
      </button>
      <button v-if="activeGraphId" @click="runGraph" class="btn-secondary btn-sm text-green-400" data-testid="btn-run">
        &#9654; Ausführen
      </button>
      <button v-if="activeGraphId" @click="toggleDebug"
        :class="['btn-secondary btn-sm', debugMode ? 'text-amber-400 ring-1 ring-amber-400/50' : 'text-slate-400']"
        title="Debug-Modus: zeigt Werte nach Ausführen" data-testid="btn-debug">
        &#128270; Debug
      </button>
      <button v-if="activeGraphId" @click="openRenameGraph" class="btn-secondary btn-sm" title="Graph umbenennen" data-testid="btn-rename">
        ✏ Umbenennen
      </button>
      <button v-if="activeGraphId" @click="doDuplicateGraph" class="btn-secondary btn-sm" title="Graph duplizieren" data-testid="btn-duplicate">
        ⧉ Duplizieren
      </button>
      <button v-if="activeGraphId" @click="doExportGraph" class="btn-secondary btn-sm" title="Graph als JSON exportieren" data-testid="btn-export">
        ↓ Exportieren
      </button>
      <label class="btn-secondary btn-sm cursor-pointer" title="Graph aus JSON importieren" data-testid="btn-import">
        ↑ Importieren
        <input type="file" accept=".json" class="hidden" @change="onImportFile" data-testid="input-import-file" />
      </label>
      <button v-if="activeGraphId" @click="confirmDeleteGraph" class="btn-icon text-red-400">
        <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
          <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
        </svg>
      </button>
    </div>

    <!-- Status bar -->
    <div v-if="statusMsg" :class="['px-4 py-1.5 text-xs flex-shrink-0', statusMsg.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400']">
      {{ statusMsg.text }}
    </div>

    <!-- Main area -->
    <div class="flex flex-1 overflow-hidden">
      <!-- Node Palette -->
      <NodePalette :node-types="store.nodeTypes" />

      <!-- Canvas -->
      <div class="flex-1 relative" ref="canvasWrapper"
           @dragover.prevent @drop="onDrop">
        <VueFlow
          v-if="activeGraphId"
          id="logic-canvas"
          v-model:nodes="nodes"
          v-model:edges="edges"
          :node-types="nodeTypeComponents"
          :default-edge-options="{ type: 'smoothstep', animated: true, interactionWidth: 20, style: { stroke: '#475569', strokeWidth: 2 } }"
          :delete-key-code="['Backspace', 'Delete']"
          fit-view-on-init
          class="logic-canvas"
          @connect="onConnect"
          @node-click="onNodeClick"
        >
          <Background :pattern-color="bgPatternColor" :gap="20" />
          <Controls class="logic-controls" />
          <MiniMap class="logic-minimap" node-color="#475569" />
        </VueFlow>

        <div v-else class="absolute inset-0 flex items-center justify-center text-slate-600 flex-col gap-3">
          <svg class="w-16 h-16 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1" d="M13 10V3L4 14h7v7l9-11h-7z"/>
          </svg>
          <p class="text-sm">Graph wählen oder neu erstellen</p>
        </div>
      </div>

      <!-- Config Panel -->
      <NodeConfigPanel
        v-if="selectedNode"
        :node="selectedNode"
        :node-types="store.nodeTypes"
        :node-outputs="lastRunOutputs"
        @update="onNodeDataUpdate"
        @close="selectedNode = null"
      />
    </div>

    <!-- New Graph Modal -->
    <Modal v-model="showNewGraph" title="Neuer Logic Graph" max-width="sm">
      <form @submit.prevent="doCreateGraph" class="flex flex-col gap-4">
        <div class="form-group">
          <label class="label">Name</label>
          <input v-model="newGraphName" type="text" class="input" required placeholder="z.B. Licht Erdgeschoss" />
        </div>
        <div class="form-group">
          <label class="label">Beschreibung <span class="text-slate-600 font-normal">(optional)</span></label>
          <input v-model="newGraphDesc" type="text" class="input" />
        </div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showNewGraph = false" class="btn-secondary">Abbrechen</button>
          <button type="submit" class="btn-primary">Erstellen</button>
        </div>
      </form>
    </Modal>

    <!-- Rename Graph Modal -->
    <Modal v-model="showRenameGraph" title="Graph umbenennen" max-width="sm">
      <form @submit.prevent="doRenameGraph" class="flex flex-col gap-4">
        <div class="form-group">
          <label class="label">Name</label>
          <input v-model="renameGraphName" type="text" class="input" required autofocus />
        </div>
        <div class="form-group">
          <label class="label">Beschreibung <span class="text-slate-600 font-normal">(optional)</span></label>
          <input v-model="renameGraphDesc" type="text" class="input" />
        </div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showRenameGraph = false" class="btn-secondary">Abbrechen</button>
          <button type="submit" class="btn-primary" data-testid="btn-rename-confirm">Speichern</button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="showDeleteConfirm"
      title="Logic Graph löschen"
      message="Dieser Graph wird unwiderruflich gelöscht."
      confirm-label="Löschen"
      @confirm="doDeleteGraph" />
  </div>
</template>

<script setup>
import { ref, computed, onMounted, onUnmounted, watch, markRaw } from 'vue'
import { VueFlow, useVueFlow, addEdge } from '@vue-flow/core'
import { Background }           from '@vue-flow/background'
import { Controls }             from '@vue-flow/controls'
import { MiniMap }              from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'

import { useLogicStore }    from '@/stores/logic'
import { useSettingsStore } from '@/stores/settings'
import { logicApi }        from '@/api/client'
import NodePalette         from '@/components/logic/NodePalette.vue'
import NodeConfigPanel     from '@/components/logic/NodeConfigPanel.vue'
import Modal               from '@/components/ui/Modal.vue'
import ConfirmDialog       from '@/components/ui/ConfirmDialog.vue'
import Spinner             from '@/components/ui/Spinner.vue'

// Node components
import GenericNode      from '@/components/logic/nodes/GenericNode.vue'
import DatapointNode    from '@/components/logic/nodes/DatapointNode.vue'
import PythonScriptNode from '@/components/logic/nodes/PythonScriptNode.vue'
import MissingNode      from '@/components/logic/nodes/MissingNode.vue'

// ── Store ──────────────────────────────────────────────────────────────────
const store    = useLogicStore()
const settings = useSettingsStore()
// Reactive background pattern colour — recomputes when theme changes
const bgPatternColor = computed(() => {
  void settings.theme   // track reactively
  return document.documentElement.classList.contains('dark') ? '#334155' : '#94a3b8'
})

// ── Vue Flow state ─────────────────────────────────────────────────────────
const nodes = ref([])
const edges = ref([])

// ── Node type → component mapping ─────────────────────────────────────────
const _generic      = markRaw(GenericNode)
const _datapoint    = markRaw(DatapointNode)
const _pythonScript = markRaw(PythonScriptNode)
const _missing      = markRaw(MissingNode)

const nodeTypeComponents = {
  missing_node: _missing,
  // Constant
  const_value: _generic,
  // Logic
  and: _generic, or: _generic, not: _generic, xor: _generic, gate: _generic,
  compare: _generic, hysteresis: _generic,
  // Math
  math_formula: _generic, math_map: _generic,
  // Timer
  timer_delay: _generic, timer_pulse: _generic, timer_cron: _generic,
  // AI
  ai_logic: _generic,
  // Astro
  astro_sun: _generic,
  // Math extended
  clamp: _generic, statistics: _generic, avg_multi: _generic,
  heating_circuit: _generic, min_max_tracker: _generic, consumption_counter: _generic,
  // Timer extended
  operating_hours: _generic,
  // String
  string_concat: _generic,
  // Notification
  notify_pushover: _generic, notify_sms: _generic,
  // Integration
  api_client: _generic, json_extractor: _generic, xml_extractor: _generic, substring_extractor: _generic,
  // DataPoints & Script
  datapoint_read:  _datapoint,
  datapoint_write: _datapoint,
  python_script:   _pythonScript,
}

// ── Active graph ───────────────────────────────────────────────────────────
const activeGraphId = ref('')
const saving        = ref(false)
const statusMsg     = ref(null)
const canvasWrapper = ref(null)

function showStatus(ok, text, ms = 3000) {
  statusMsg.value = { ok, text }
  setTimeout(() => { statusMsg.value = null }, ms)
}

async function loadGraph() {
  if (!activeGraphId.value) { nodes.value = []; edges.value = []; return }
  const { data } = await logicApi.getGraph(activeGraphId.value)
  nodes.value = (data.flow_data.nodes || []).map(n => {
    // eslint-disable-next-line no-unused-vars
    const { _dbg, ...nodeData } = n.data ?? {}
    return { ...n, position: n.position || { x: 100, y: 100 }, data: nodeData }
  })
  edges.value = data.flow_data.edges || []
  selectedNode.value = null
}

async function saveGraph() {
  if (!activeGraphId.value) return
  saving.value = true
  try {
    const graph = store.graphs.find(g => g.id === activeGraphId.value)
    await store.saveGraph(activeGraphId.value, graph.name, graph.description, graph.enabled, {
      nodes: nodes.value.map(n => {
        // eslint-disable-next-line no-unused-vars
        const { _dbg, ...nodeData } = n.data ?? {}
        return { id: n.id, type: n.type, position: n.position, data: nodeData }
      }),
      edges: edges.value.map(e => ({
        id: e.id, source: e.source, target: e.target,
        sourceHandle: e.sourceHandle, targetHandle: e.targetHandle
      })),
    })
    showStatus(true, 'Graph gespeichert')
  } catch (err) {
    showStatus(false, err.response?.data?.detail ?? 'Fehler beim Speichern')
  } finally {
    saving.value = false
  }
}

// ── Debug mode ─────────────────────────────────────────────────────────────
const debugMode = ref(localStorage.getItem('logic_debug_mode') === '1')

function fmtDebugVal(nodeOut) {
  if (!nodeOut || typeof nodeOut !== 'object') return null

  function fv(v) {
    if (v === null || v === undefined) return '—'
    if (typeof v === 'boolean') return v ? '✓' : '✗'
    if (typeof v === 'number') return String(parseFloat(v.toPrecision(5)))
    return String(v).slice(0, 18)
  }

  // notify nodes — show message content + sent status (before generic key loop)
  if ('_message' in nodeOut) {
    const msg  = nodeOut._message !== null && nodeOut._message !== undefined
      ? `"${String(nodeOut._message).slice(0, 24)}"`
      : '—'
    const sent = 'sent' in nodeOut ? `  sent=${fv(nodeOut.sent)}` : ''
    return msg + sent
  }

  // Public keys (no leading _)
  const pairs = Object.entries(nodeOut)
    .filter(([k]) => !k.startsWith('_'))
    .map(([k, v]) => `${k}=${fv(v)}`)
  if (pairs.length) return pairs.join('   ')

  // datapoint_write outputs are all _private — show write value with → prefix
  if ('_write_value' in nodeOut) {
    return `→ ${fv(nodeOut._write_value)}`
  }

  return null
}

// Last run outputs — always kept (not just in debug mode) so that
// json_extractor / xml_extractor config panels can read _preview data.
const lastRunOutputs = ref({})

function applyDebugValues(outputs) {
  lastRunOutputs.value = outputs
  nodes.value = nodes.value.map(n => ({
    ...n,
    data: { ...n.data, _dbg: fmtDebugVal(outputs[n.id]) ?? undefined }
  }))
}

function clearDebugValues() {
  nodes.value = nodes.value.map(n => {
    // eslint-disable-next-line no-unused-vars
    const { _dbg, ...rest } = n.data
    return { ...n, data: rest }
  })
}

function toggleDebug() {
  debugMode.value = !debugMode.value
  localStorage.setItem('logic_debug_mode', debugMode.value ? '1' : '0')
  if (!debugMode.value) clearDebugValues()
}

async function runGraph() {
  try {
    const { data } = await logicApi.runGraph(activeGraphId.value)
    const evalCount = Object.keys(data.outputs || {}).length
    showStatus(true, `Graph ausgeführt — ${evalCount} Nodes evaluiert`)
    // Always update lastRunOutputs (needed for extractor config panels)
    lastRunOutputs.value = data.outputs || {}
    if (debugMode.value) applyDebugValues(data.outputs || {})
  } catch (err) {
    showStatus(false, err.response?.data?.detail ?? 'Fehler')
  }
}

// ── New graph ──────────────────────────────────────────────────────────────
const showNewGraph  = ref(false)
const newGraphName  = ref('')
const newGraphDesc  = ref('')

function newGraph() { newGraphName.value = ''; newGraphDesc.value = ''; showNewGraph.value = true }
async function doCreateGraph() {
  const g = await store.createGraph(newGraphName.value, newGraphDesc.value)
  showNewGraph.value = false
  activeGraphId.value = g.id
  nodes.value = []; edges.value = []
}

// ── Delete graph ───────────────────────────────────────────────────────────
const showDeleteConfirm = ref(false)
function confirmDeleteGraph() { showDeleteConfirm.value = true }
async function doDeleteGraph() {
  await store.deleteGraph(activeGraphId.value)
  activeGraphId.value = ''
  nodes.value = []; edges.value = []
}

// ── Duplizieren ────────────────────────────────────────────────────────────
async function doDuplicateGraph() {
  if (!activeGraphId.value) return
  try {
    const copy = await store.duplicateGraph(activeGraphId.value)
    activeGraphId.value = copy.id
    await loadGraph()
    showStatus(true, `Graph dupliziert als „${copy.name}"`)
  } catch (err) {
    showStatus(false, err.response?.data?.detail ?? 'Fehler beim Duplizieren')
  }
}

// ── Exportieren (programmatisch mit Auth-Header) ───────────────────────────
async function doExportGraph() {
  if (!activeGraphId.value) return
  try {
    const { data } = await logicApi.exportGraph(activeGraphId.value)
    const g = store.graphs.find(g => g.id === activeGraphId.value)
    const filename = g ? `${g.name.replace(/ /g, '_')}.json` : 'logic_graph.json'
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url; a.download = filename
    document.body.appendChild(a); a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (err) {
    showStatus(false, err.response?.data?.detail ?? 'Fehler beim Exportieren')
  }
}

// ── Umbenennen ─────────────────────────────────────────────────────────────
const showRenameGraph  = ref(false)
const renameGraphName  = ref('')
const renameGraphDesc  = ref('')

function openRenameGraph() {
  const g = store.graphs.find(g => g.id === activeGraphId.value)
  renameGraphName.value = g?.name ?? ''
  renameGraphDesc.value = g?.description ?? ''
  showRenameGraph.value = true
}

async function doRenameGraph() {
  if (!activeGraphId.value || !renameGraphName.value.trim()) return
  try {
    await store.renameGraph(activeGraphId.value, renameGraphName.value.trim(), renameGraphDesc.value)
    showRenameGraph.value = false
    showStatus(true, 'Graph umbenannt')
  } catch (err) {
    showStatus(false, err.response?.data?.detail ?? 'Fehler beim Umbenennen')
  }
}

// ── Importieren ────────────────────────────────────────────────────────────
async function onImportFile(event) {
  const file = event.target.files?.[0]
  if (!file) return
  event.target.value = ''   // Reset input für erneuten Import derselben Datei
  try {
    const text = await file.text()
    const payload = JSON.parse(text)
    const namesBefore = new Set(store.graphs.map(g => g.name))
    const imported = await store.importGraph(payload)
    activeGraphId.value = imported.id
    await loadGraph()
    if (namesBefore.has(imported.name)) {
      // Name bereits vergeben → Rename-Dialog sofort öffnen
      renameGraphName.value = imported.name
      renameGraphDesc.value = imported.description ?? ''
      showRenameGraph.value = true
      showStatus(true, `Graph importiert – bitte umbenennen (Name bereits vorhanden)`)
    } else {
      showStatus(true, `Graph „${imported.name}" importiert`)
    }
  } catch (err) {
    showStatus(false, err?.response?.data?.detail ?? 'Ungültige oder fehlerhafte Export-Datei')
  }
}

// ── Connect handler — REQUIRED to actually create edges ────────────────────
function onConnect(params) {
  edges.value = addEdge({
    ...params,
    type: 'smoothstep',
    animated: true,
    style: { stroke: '#475569', strokeWidth: 2 },
  }, edges.value)
}

// ── Drop node from palette ─────────────────────────────────────────────────
function onDrop(event) {
  const type = event.dataTransfer.getData('application/vueflow-node-type')
  if (!type || !activeGraphId.value) return

  const nt   = store.nodeTypes.find(t => t.type === type)
  const rect = canvasWrapper.value.getBoundingClientRect()

  // Convert screen coordinates → flow coordinates (accounts for pan/zoom)
  const { project } = useVueFlow('logic-canvas')
  const pos = project({ x: event.clientX - rect.left, y: event.clientY - rect.top })

  const newNode = {
    id:       `${type}-${Date.now()}`,
    type,
    position: pos,
    data: {
      label: nt?.label ?? type,
      ...(nt?.config_schema
        ? Object.fromEntries(
            Object.entries(nt.config_schema).map(([k, v]) => [k, v.default ?? ''])
          )
        : {}),
    },
  }
  nodes.value = [...nodes.value, newNode]
}

// ── Node selection & config ────────────────────────────────────────────────
const selectedNode = ref(null)

function onNodeClick({ node }) {
  selectedNode.value = { ...node }
}

let _autoSaveTimer = null
function onNodeDataUpdate(newData) {
  if (!selectedNode.value) return
  nodes.value = nodes.value.map(n =>
    n.id === selectedNode.value.id ? { ...n, data: { ...n.data, ...newData } } : n
  )
  selectedNode.value = { ...selectedNode.value, data: { ...selectedNode.value.data, ...newData } }
  // Auto-save after 500 ms idle
  clearTimeout(_autoSaveTimer)
  _autoSaveTimer = setTimeout(() => saveGraph(), 500)
}

// ── WebSocket — live debug updates ────────────────────────────────────────
let _ws = null
let _wsTimer = null

function _wsConnect() {
  const token = localStorage.getItem('access_token')
  if (!token) return
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const url   = `${proto}://${window.location.host}/api/v1/ws?token=${encodeURIComponent(token)}`
  try {
    _ws = new WebSocket(url)
  } catch { return }

  _ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data)
      if (
        msg.action   === 'logic_run'    &&
        msg.graph_id === activeGraphId.value &&
        debugMode.value
      ) {
        applyDebugValues(msg.outputs || {})
      }
    } catch { /* ignore parse errors */ }
  }

  _ws.onclose = () => {
    _ws = null
    _wsTimer = setTimeout(_wsConnect, 4000)   // auto-reconnect
  }
  _ws.onerror = () => { try { _ws?.close() } catch { /* ignore */ } }
}

function _wsDisconnect() {
  clearTimeout(_wsTimer)
  _wsTimer = null
  try { _ws?.close() } catch { /* ignore */ }
  _ws = null
}

// ── Persist active graph selection ────────────────────────────────────────
watch(activeGraphId, (id) => {
  if (id) localStorage.setItem('logic_active_graph', id)
  else localStorage.removeItem('logic_active_graph')
})

// ── Init ───────────────────────────────────────────────────────────────────
onMounted(async () => {
  await store.fetchNodeTypes()
  await store.fetchGraphs()
  _wsConnect()
  // Restore last active graph
  const lastId = localStorage.getItem('logic_active_graph')
  if (lastId && store.graphs.find(g => g.id === lastId)) {
    activeGraphId.value = lastId
    await loadGraph()
  }
})

onUnmounted(() => {
  _wsDisconnect()
})
</script>

<style>
.logic-canvas { background: var(--logic-canvas-bg); }
.logic-canvas .vue-flow__edge-path { stroke: #475569; }
.logic-canvas .vue-flow__handle { width: 10px; height: 10px; border-radius: 50%; }
.logic-controls { bottom: 1rem; left: 1rem; }
.logic-minimap { bottom: 1rem; right: 1rem; background: var(--logic-minimap-bg); border: 1px solid var(--node-card-border); border-radius: 6px; }

/* Edge interaction — breite unsichtbare Klickfläche */
.logic-canvas .vue-flow__edge .vue-flow__edge-interaction {
  stroke-width: 20;
  stroke: transparent;
  cursor: pointer;
}
/* Hover */
.logic-canvas .vue-flow__edge:hover .vue-flow__edge-path {
  stroke: #94a3b8 !important;
  stroke-width: 3 !important;
}
/* Selektiert → blau + dicker, dann Backspace/Delete drücken */
.logic-canvas .vue-flow__edge.selected .vue-flow__edge-path {
  stroke: #60a5fa !important;
  stroke-width: 3 !important;
}
</style>
