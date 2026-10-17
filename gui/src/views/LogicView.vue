<template>
  <div class="flex flex-col h-full" style="height: calc(100vh - 4rem)">
    <!-- Toolbar -->
    <div class="flex items-center gap-3 px-4 py-2 bg-surface-800 border-b border-slate-200 dark:border-slate-700/60 flex-shrink-0">
      <!-- Actions scroll horizontally on narrow/laptop viewports; the status
           bar below lives outside this container so it stays visible
           instead of scrolling off with whichever action produced it. -->
      <div class="flex items-center gap-3 overflow-x-auto min-w-0 pb-1">
        <!-- Reserved to the NodePalette column's current width below (see
             titleSpacerClass), so the dropdown lines up with the canvas
             instead of crowding the title. -->
        <h2 :class="[titleSpacerClass, 'flex-shrink-0 overflow-hidden whitespace-nowrap text-sm font-bold text-slate-800 dark:text-slate-100']">{{ $t('logic.title') }}</h2>
        <!-- Logikblatt-Auswahl: Ordner-Navigation statt flacher Liste (#1217) -->
        <button type="button" @click="showGraphPicker = true" class="btn-secondary btn-sm flex-shrink-0 max-w-[280px] truncate" data-testid="btn-open-graph-picker">
          {{ activeGraph ? activeGraph.name : $t('logic.selectGraph') }}
        </button>
        <GraphPickerModal
          v-model="showGraphPicker"
          :active-graph-id="activeGraphId"
          @select="onGraphPicked"
          @graph-deleted="onGraphDeletedFromPicker"
        />
        <button v-if="auth.isAdmin" @click="newGraph" class="btn-primary btn-sm flex-shrink-0">{{ $t('logic.newGraphBtn') }}</button>
        <button v-if="auth.isAdmin && activeGraphId" @click="saveGraph({ manual: true })" class="btn-secondary btn-sm flex-shrink-0" :disabled="saving || graphLoading" data-testid="btn-save">
          <Spinner v-if="saving" size="sm" color="white" />
          {{ $t('common.save') }}
        </button>
        <button v-if="activeGraphId" @click="requestGraphRun"
          :class="['btn-secondary btn-sm flex-shrink-0', activeGraph?.enabled ? 'text-green-400' : 'text-slate-500 opacity-50 cursor-not-allowed']"
          :disabled="!activeGraph?.enabled || runPreflightLoading"
          :title="activeGraph?.enabled ? $t('logic.runTitle') : $t('logic.runDisabledTitle')"
          data-testid="btn-run">
          <Spinner v-if="runPreflightLoading" size="sm" />
          <template v-else>&#9654;</template>
          {{ $t('logic.run') }}
        </button>
        <button v-if="auth.isAdmin && activeGraphId" @click="toggleDebug"
          :class="['btn-secondary btn-sm flex-shrink-0', debugMode ? 'text-amber-400 ring-1 ring-amber-400/50' : 'text-slate-400']"
          :title="$t('logic.debugMode')" data-testid="btn-debug">
          <svg
            aria-hidden="true"
            class="inline-block h-4 w-4 align-[-0.125em]"
            data-testid="icon-debug-bug"
            fill="none"
            stroke="currentColor"
            stroke-linecap="round"
            stroke-linejoin="round"
            stroke-width="2"
            viewBox="0 0 24 24"
          >
            <path d="m8 2 2 2" />
            <path d="m14 4 2-2" />
            <path d="M9 7V6a3 3 0 0 1 6 0v1" />
            <rect x="6" y="7" width="12" height="13" rx="6" />
            <path d="M12 11v9M6 10 3 8M6 14H2M7 18l-3 3M18 10l3-2M18 14h4M17 18l3 3" />
          </svg>
          {{ $t('logic.debugBtn') }}
        </button>
        <!-- Raster visibility is a purely local presentation preference and is
             therefore available without edit permissions (#1075); snapping and
             the grid size stay admin-only because they move blocks. -->
        <div v-if="activeGraphId" class="flex items-center gap-1 flex-shrink-0">
          <button
            type="button"
            :class="['btn-secondary btn-sm flex-shrink-0', gridVisible ? 'text-blue-400 ring-1 ring-blue-400/50' : 'text-slate-400']"
            :title="gridVisible ? $t('logic.gridHideTitle') : $t('logic.gridShowTitle')"
            :aria-pressed="gridVisible ? 'true' : 'false'"
            data-testid="btn-grid-visible"
            @click="gridVisible = !gridVisible"
          >
            ⊞ {{ $t('logic.gridVisible') }}
          </button>
          <template v-if="auth.isAdmin">
            <button
              type="button"
              :class="['btn-secondary btn-sm flex-shrink-0', snapToGrid ? 'text-blue-400 ring-1 ring-blue-400/50' : 'text-slate-400']"
              :title="$t('logic.snapToGridTitle')"
              :aria-pressed="snapToGrid ? 'true' : 'false'"
              data-testid="btn-snap-to-grid"
              @click="snapToGrid = !snapToGrid"
            >
              # {{ $t('logic.snapToGrid') }}
            </button>
            <label v-if="snapToGrid || gridVisible" class="flex items-center gap-1 flex-shrink-0 text-xs text-slate-500 dark:text-slate-400">
              <span class="sr-only">{{ $t('logic.gridSize') }}</span>
              <input
                :value="snapGridSize"
                type="number"
                min="5"
                max="100"
                step="5"
                class="input w-16 px-2 py-1 text-xs"
                data-testid="input-snap-grid-size"
                @change="updateSnapGridSize"
              />
              px
            </label>
          </template>
        </div>
        <button v-if="auth.isAdmin && activeGraphId" @click="doToggleEnabled"
          :class="['btn-secondary btn-sm flex-shrink-0', activeGraph?.enabled ? 'text-green-400' : 'text-orange-400 ring-1 ring-orange-400/50']"
          :title="activeGraph?.enabled ? $t('logic.toggleActiveTitle') : $t('logic.toggleDisabledTitle')"
          data-testid="btn-toggle-enabled">
          {{ activeGraph?.enabled ? $t('logic.toggleActive') : $t('logic.toggleDisabled') }}
        </button>
        <button v-if="auth.isAdmin && activeGraphId" @click="copySelection" class="btn-secondary btn-sm flex-shrink-0" :disabled="!hasSelection || graphLoading"
          :title="$t('logic.copySelectionTitle')" data-testid="btn-copy-nodes">
          ⧉ {{ $t('logic.copySelection') }}
        </button>
        <button v-if="auth.isAdmin && activeGraphId" @click="pasteClipboard" class="btn-secondary btn-sm flex-shrink-0" :disabled="!clipboard || graphLoading"
          :title="$t('logic.pasteSelectionTitle')" data-testid="btn-paste-nodes">
          📋 {{ $t('logic.pasteSelection') }}
        </button>
        <button v-if="auth.isAdmin && activeGraphId" @click="openRenameGraph" class="btn-secondary btn-sm flex-shrink-0" :title="$t('logic.renameGraph')" data-testid="btn-rename">
          ✏ {{ $t('logic.rename') }}
        </button>
        <button v-if="auth.isAdmin && activeGraphId" @click="openDuplicate" class="btn-secondary btn-sm flex-shrink-0" :title="$t('logic.duplicateGraph')" data-testid="btn-duplicate">
          ⧉ {{ $t('logic.duplicate') }}
        </button>
        <button v-if="activeGraphId" @click="doExportGraph" class="btn-secondary btn-sm flex-shrink-0" :title="$t('logic.exportJson')" data-testid="btn-export">
          ↓ {{ $t('logic.export') }}
        </button>
        <label v-if="auth.isAdmin" class="btn-secondary btn-sm flex-shrink-0 cursor-pointer" :title="$t('logic.importJson')" data-testid="btn-import">
          ↑ {{ $t('logic.import') }}
          <input type="file" accept=".json" class="hidden" @change="onImportFile" data-testid="input-import-file" />
        </label>
        <button v-if="auth.isAdmin && activeGraphId" @click="confirmDeleteGraph" class="btn-secondary btn-sm flex-shrink-0 text-red-400" data-testid="btn-delete">
          <svg class="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16"/>
          </svg>
          {{ $t('common.delete') }}
        </button>
      </div>
      <HelpButton help-id="logic-toolbar" class="flex-shrink-0" />
    </div>

    <!-- Main area -->
    <div class="flex flex-1 overflow-hidden">
      <!-- Node Palette -->
      <NodePalette
        v-if="auth.isAdmin"
        :node-types="store.nodeTypes"
        :collapsed="paletteCollapsed"
        @toggle="paletteCollapsed = !paletteCollapsed"
      />

      <!-- Canvas -->
      <div class="flex-1 relative" ref="canvasWrapper"
           @dragover.prevent @drop="onDrop">
        <!-- Status bar — an overlay confined to the canvas, not a row in the
             flex column: a normal-flow bar here would grow/shrink the whole
             toolbar-below area on every message, shoving the palette, canvas
             and properties panel up and down while editing. -->
        <HelpButton help-id="logic-canvas" class="absolute top-2 right-2 z-30" />
        <div v-if="statusMsg" :class="['absolute top-0 inset-x-0 z-20 px-4 py-1.5 text-xs pointer-events-none', statusMsg.ok ? 'bg-green-500/10 text-green-400' : 'bg-red-500/10 text-red-400']" data-testid="status-msg">
          {{ statusMsg.text }}
        </div>
        <div v-else-if="validationWarnings.length" class="absolute top-0 inset-x-0 z-20 px-4 py-1.5 text-xs pointer-events-none bg-amber-500/10 text-amber-500">
          {{ $t(hasDuplicateHandleWarning(validationWarnings) ? 'logic.graphValidationDuplicateHandle' : 'logic.graphValidationCycle', { count: warningCountForDisplay(validationWarnings) }) }}
        </div>

        <VueFlow
          v-if="activeGraphId"
          id="logic-canvas"
          v-model:nodes="nodes"
          v-model:edges="edges"
          :node-types="nodeTypeComponents"
          :default-edge-options="defaultEdgeOptions"
          :delete-key-code="canvasEditable ? ['Backspace', 'Delete'] : []"
          :nodes-draggable="canvasEditable"
          :nodes-connectable="canvasEditable"
          :edges-updatable="canvasEditable"
          :snap-to-grid="snapToGrid"
          :snap-grid="snapGrid"
          fit-view-on-init
          class="logic-canvas"
          @connect="onConnect"
          @edge-update="onEdgeUpdate"
          @edges-change="onElementsChange"
          @nodes-change="onNodesChange"
          @node-click="onNodeClick"
          @node-drag-start="onNodeDragStart"
          @node-drag="onNodeDrag"
          @node-drag-stop="onNodeDragStop"
        >
          <Background v-if="gridVisible" :pattern-color="bgPatternColor" :gap="snapGridSize" :offset="0.5" />
          <Controls class="logic-controls" />
          <MiniMap
            ref="minimapRef"
            class="logic-minimap"
            :class="{ 'logic-minimap--dragging': minimapDragging }"
            node-color="#475569"
            :style="minimapStyle"
          />
        </VueFlow>

        <div v-else class="absolute inset-0 flex items-center justify-center text-slate-600 flex-col gap-3">
          <svg class="w-16 h-16 opacity-30" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path stroke-linecap="round" stroke-linejoin="round" stroke-width="1" d="M13 10V3L4 14h7v7l9-11h-7z"/>
          </svg>
          <p class="text-sm">{{ $t('logic.emptyHint') }}</p>
        </div>

        <!-- Block-sized crosshair overlay while dragging (issue #1118) -->
        <div v-if="dragCrosshair" class="logic-crosshair pointer-events-none absolute inset-0" data-testid="logic-crosshair-overlay">
          <div class="logic-crosshair__bar logic-crosshair__bar--h" :style="{ height: dragCrosshair.height + 'px', transform: `translateY(${dragCrosshair.top}px)` }" />
          <div class="logic-crosshair__bar logic-crosshair__bar--v" :style="{ width: dragCrosshair.width + 'px', transform: `translateX(${dragCrosshair.left}px)` }" />
        </div>
      </div>

      <!-- Config Panel — settings and, in debug mode, the debug values of the
           selected block as a second tab (issue #1128) -->
      <NodeConfigPanel
        v-if="selectedNode && auth.isAdmin"
        :node="selectedNode"
        :node-types="store.nodeTypes"
        :node-outputs="lastRunOutputs"
        :debug-mode="debugMode"
        :debug-inputs="debugInputs"
        :debug-outputs="lastRunDebugOutputs[selectedNode.id] || {}"
        :debug-metadata="lastRunMetadata"
        :has-debug-overrides="hasDebugOverrides"
        @update="onNodeDataUpdate"
        @close="selectedNode = null"
        @set-override="setDebugOverride"
        @clear-override="clearDebugOverride"
        @clear-all="clearAllDebugOverrides"
      />
    </div>

    <!-- New Graph Modal -->
    <Modal v-model="showNewGraph" :title="$t('logic.newGraphModal')" max-width="6xl">
      <template #header-actions>
        <HelpButton help-id="logic-new-sheet" />
      </template>
      <form @submit.prevent="doCreateGraph" class="flex flex-col gap-4 min-h-[34rem]">
        <div class="form-group">
          <label class="label">{{ $t('logic.name') }}</label>
          <input v-model="newGraphName" type="text" class="input" required :placeholder="$t('logic.newGraphPlaceholder')" />
        </div>
        <div class="form-group">
          <label class="label">{{ $t('logic.description') }} <span class="text-slate-600 font-normal">{{ $t('logic.optional') }}</span></label>
          <input v-model="newGraphDesc" type="text" class="input" />
        </div>
        <div class="form-group">
          <label class="label">{{ $t('logic.hierarchyNodes') }} <span class="text-slate-600 font-normal">{{ $t('logic.optional') }}</span></label>
          <HierarchyCombobox v-model="newGraphHierarchyNodes" include-tree-roots data-testid="new-graph-hierarchy" />
          <p class="text-xs text-slate-500 mt-1">{{ $t('logic.hierarchyNodesHint') }}</p>
        </div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showNewGraph = false" class="btn-secondary">{{ $t('common.cancel') }}</button>
          <button type="submit" class="btn-primary">{{ $t('logic.create') }}</button>
        </div>
      </form>
    </Modal>

    <!-- Rename Graph Modal -->
    <Modal v-model="showRenameGraph" :title="$t('logic.renameGraph')" max-width="sm">
      <form @submit.prevent="doRenameGraph" class="flex flex-col gap-4">
        <div class="form-group">
          <label class="label">{{ $t('logic.name') }}</label>
          <input v-model="renameGraphName" type="text" class="input" required autofocus />
        </div>
        <div class="form-group">
          <label class="label">{{ $t('logic.description') }} <span class="text-slate-600 font-normal">{{ $t('logic.optional') }}</span></label>
          <input v-model="renameGraphDesc" type="text" class="input" />
        </div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showRenameGraph = false" class="btn-secondary">{{ $t('common.cancel') }}</button>
          <button type="submit" class="btn-primary" data-testid="btn-rename-confirm">{{ $t('common.save') }}</button>
        </div>
      </form>
    </Modal>

    <!-- Duplicate Graph Modal -->
    <Modal v-model="showDuplicate" :title="$t('logic.duplicateGraph')" max-width="sm">
      <form @submit.prevent="doDuplicate" class="flex flex-col gap-4">
        <div class="form-group">
          <label class="label">{{ $t('logic.name') }}</label>
          <input v-model="duplicateName" type="text" class="input" required autofocus data-testid="input-duplicate-name" />
        </div>
        <div class="flex justify-end gap-3">
          <button type="button" @click="showDuplicate = false" class="btn-secondary" data-testid="btn-duplicate-cancel">{{ $t('common.cancel') }}</button>
          <button type="submit" class="btn-primary" data-testid="btn-duplicate-confirm">{{ $t('logic.duplicate') }}</button>
        </div>
      </form>
    </Modal>

    <ConfirmDialog v-model="showDeleteConfirm"
      :title="$t('logic.deleteGraph')"
      :message="$t('logic.deleteGraphConfirm')"
      :confirm-label="$t('common.delete')"
      @confirm="doDeleteGraph" />

    <ActionPreflightDialog
      v-model="showRunPreflight"
      :title="$t('logic.preflightTitle')"
      :description="$t('logic.preflightDescription')"
      :confirm-label="$t('logic.run')"
      :items="runPreflightItems"
      :loading="runPreflightLoading"
      :error="runPreflightError"
      @confirm="confirmGraphRun"
    />
  </div>
</template>

<script setup>
import { ref, computed, nextTick, onMounted, onBeforeUnmount, onUnmounted, watch, watchEffect, markRaw } from 'vue'
import { useRoute } from 'vue-router'
import { useI18n } from 'vue-i18n'
import { VueFlow, useVueFlow, addEdge } from '@vue-flow/core'
import { Background }           from '@vue-flow/background'
import { Controls }             from '@vue-flow/controls'
import { MiniMap }              from '@vue-flow/minimap'
import '@vue-flow/core/dist/style.css'
import '@vue-flow/core/dist/theme-default.css'
import '@vue-flow/controls/dist/style.css'
import '@vue-flow/minimap/dist/style.css'
import '@vue-flow/node-resizer/dist/style.css'

import { useLogicStore }    from '@/stores/logic'
import { useSettingsStore } from '@/stores/settings'
import { useAuthStore }     from '@/stores/auth'
import { logicApi, hierarchyApi } from '@/api/client'
import { logicRunAuthzApi } from '@/api/logicAuthz'
import { cloneSelectionForClipboard, remapClipboardForPaste } from '@/utils/logicClipboard'
import { AUTH_TOKEN_REFRESHED_EVENT } from '@/utils/authEvents'
import { extractorOutputLabels, extractorRowCount, retainPreviews } from '@/utils/logicExtractorOutputs'
import { translateVariableErrors } from '@/utils/logicVariables'
import NodePalette         from '@/components/logic/NodePalette.vue'
import NodeConfigPanel     from '@/components/logic/NodeConfigPanel.vue'
import ActionPreflightDialog from '@/components/authz/ActionPreflightDialog.vue'
import Modal               from '@/components/ui/Modal.vue'
import GraphPickerModal    from '@/components/logic/GraphPickerModal.vue'
import HierarchyCombobox   from '@/components/ui/HierarchyCombobox.vue'
import { parseHierarchyCompositeId } from '@/utils/hierarchyDisplay'
import ConfirmDialog       from '@/components/ui/ConfirmDialog.vue'
import Spinner             from '@/components/ui/Spinner.vue'
import HelpButton          from '@/components/ui/HelpButton.vue'

// Node components
import GenericNode      from '@/components/logic/nodes/GenericNode.vue'
import DatapointNode    from '@/components/logic/nodes/DatapointNode.vue'
import PythonScriptNode from '@/components/logic/nodes/PythonScriptNode.vue'
import MissingNode      from '@/components/logic/nodes/MissingNode.vue'
import CommentNode      from '@/components/logic/nodes/CommentNode.vue'
import { binaryStatsInputCount } from '@/utils/binaryStatsInputCount'
import { provideCanvasEditable } from '@/components/logic/nodes/canvasEditable'
import { hemsInputPorts } from '@/utils/logicHemsOutputs'

// ── Store ──────────────────────────────────────────────────────────────────
const { t, locale } = useI18n()
const route    = useRoute()
const store    = useLogicStore()
const settings = useSettingsStore()
const auth     = useAuthStore()
// Reactive background pattern colour — recomputes when theme changes
const bgPatternColor = computed(() => {
  void settings.theme   // track reactively
  return document.documentElement.classList.contains('dark') ? '#334155' : '#94a3b8'
})

// ── Vue Flow state ─────────────────────────────────────────────────────────
const nodes = ref([])
const edges = ref([])

// ── Grid snapping ─────────────────────────────────────────────────────────
const SNAP_ENABLED_KEY = 'obs-logic-snap-to-grid'
const SNAP_SIZE_KEY = 'obs-logic-snap-grid-size'
// Raster visibility is independent of snapping (#1075) — it is a per-browser
// presentation preference and never touches graph data or node coordinates.
const GRID_VISIBLE_KEY = 'obs-logic-grid-visible'
const DEFAULT_SNAP_GRID_SIZE = 20
const MIN_SNAP_GRID_SIZE = 5
const MAX_SNAP_GRID_SIZE = 100
const savedSnapGridSize = Number(localStorage.getItem(SNAP_SIZE_KEY))
const snapToGrid = ref(localStorage.getItem(SNAP_ENABLED_KEY) === '1')
const snapGridSize = ref(
  savedSnapGridSize >= MIN_SNAP_GRID_SIZE && savedSnapGridSize <= MAX_SNAP_GRID_SIZE
    ? savedSnapGridSize
    : DEFAULT_SNAP_GRID_SIZE
)
const snapGrid = computed(() => [snapGridSize.value, snapGridSize.value])
// Shown unless it was explicitly hidden, so existing installations keep the
// raster they know until a user turns it off.
const gridVisible = ref(localStorage.getItem(GRID_VISIBLE_KEY) !== '0')

watch(snapToGrid, enabled => {
  localStorage.setItem(SNAP_ENABLED_KEY, enabled ? '1' : '0')
})

watch(gridVisible, visible => {
  localStorage.setItem(GRID_VISIBLE_KEY, visible ? '1' : '0')
})

function updateSnapGridSize(event) {
  const requestedSize = Number(event.target.value)
  snapGridSize.value = Math.min(
    MAX_SNAP_GRID_SIZE,
    Math.max(MIN_SNAP_GRID_SIZE, Number.isFinite(requestedSize) ? requestedSize : DEFAULT_SNAP_GRID_SIZE)
  )
  event.target.value = String(snapGridSize.value)
  localStorage.setItem(SNAP_SIZE_KEY, String(snapGridSize.value))
}

// ── Crosshair alignment overlay while dragging (issue #1118) ───────────────
// Block-sized cross (h-bar as tall as the dragged block, v-bar as wide as
// it) spanning the full canvas, so edges can be visually lined up against
// any other block currently in view — independent of grid snapping.
const dragCrosshair = ref(null)

function updateDragCrosshair(node) {
  if (!node?.dimensions?.width || !node?.dimensions?.height) {
    dragCrosshair.value = null
    return
  }
  const { getViewport } = useVueFlow('logic-canvas')
  const { x, y, zoom } = getViewport()
  // `node.computedPosition` is recalculated by vue-flow via a `watch()` on
  // `node.position.x/y` — a deferred effect that hasn't run yet when this
  // drag-event handler fires synchronously, so it can still hold the
  // *previous* step's coordinates on whichever axis just changed (the
  // rendered node itself doesn't show this because Vue's render flush runs
  // after that watcher). `node.position` is written synchronously in the
  // same tick as this event and is already absolute here — none of this
  // editor's blocks nest under a parent node.
  const pos = node.position
  dragCrosshair.value = {
    left:   pos.x * zoom + x,
    top:    pos.y * zoom + y,
    width:  node.dimensions.width * zoom,
    height: node.dimensions.height * zoom,
  }
}

function onNodeDragStart({ node }) { updateDragCrosshair(node) }
function onNodeDrag({ node })      { updateDragCrosshair(node) }
function onNodeDragStop()          { dragCrosshair.value = null }

// ── Node type → component mapping ─────────────────────────────────────────
const _generic      = markRaw(GenericNode)
const _datapoint    = markRaw(DatapointNode)
const _pythonScript = markRaw(PythonScriptNode)
const _missing      = markRaw(MissingNode)
const _comment      = markRaw(CommentNode)

const nodeTypeComponents = {
  missing_node: _missing,
  // Constant
  const_value: _generic,
  // Comment (issue #1043)
  comment: _comment,
  // Logic
  and: _generic, or: _generic, not: _generic, xor: _generic, binary_stats: _generic, gate: _generic, memory: _generic, merge: _generic,
  change_filter: _generic, edge_detect: _generic,
  compare: _generic, hysteresis: _generic, decision: _generic, value_mapping: _generic,
  // Math
  math_formula: _generic, math_map: _generic,
  // Timer
  timer_delay: _generic, timer_pulse: _generic, timer_cron: _generic, datetime: _generic,
  value_sequence: _generic,
  // AI
  ai_logic: _generic,
  // Astro
  astro_sun: _generic,
  // Math extended
  clamp: _generic, random_value: _generic, statistics: _generic, avg_multi: _generic,
  heating_circuit: _generic, min_max_tracker: _generic, consumption_counter: _generic,
  // Timer extended
  operating_hours: _generic, sensor_watchdog: _generic,
  // HEMS
  hems_surplus: _generic,
  // String
  string_concat: _generic, string_replace: _generic,
  // Notification
  notify_message: _generic, notify_pushover: _generic, notify_sms: _generic, message_archive: _generic, wake_on_lan: _generic, host_check: _generic,
  // Integration
  api_client: _generic, json_extractor: _generic, xml_extractor: _generic, substring_extractor: _generic,
  ical: _generic,
  // DataPoints & Script
  datapoint_read:  _datapoint,
  datapoint_write: _datapoint,
  python_script:   _pythonScript,
}

// ── Active graph ───────────────────────────────────────────────────────────
const activeGraphId = ref('')
const activeGraph   = computed(() => store.graphs.find(g => g.id === activeGraphId.value))

// Ordner-Navigation statt Dropdown (#1217): das Popup meldet die gewählte
// Logik über ein Event statt v-model auf activeGraphId, damit derselbe Weg
// wie bei der bisherigen <select @change="loadGraph"> genommen wird — nur
// der Auslöser ändert sich.
const showGraphPicker = ref(false)
async function onGraphPicked(graphId) {
  // Save the outgoing sheet before anything switches; if that fails, stay.
  if (!await leaveCanvas()) return
  activeGraphId.value = graphId
  await loadGraph()
}
// The picker's own "Löschen" (unassigned pseudo-folder) deletes the graph
// itself — if that happened to be the one currently open here, close it too.
function onGraphDeletedFromPicker(graphId) {
  if (activeGraphId.value !== graphId) return
  activeGraphId.value = ''
  // While a switch to the deleted sheet loads, the canvas still holds the
  // previous sheet — that one still exists, so its edit must be saved.
  replaceCanvas('', [], [], { discard: _canvasGraphId === graphId })
}

// ── Edge options — animated only when graph is enabled ─────────────────────
const defaultEdgeOptions = computed(() => {
  const enabled = activeGraph.value?.enabled !== false
  return {
    type: 'smoothstep',
    animated: enabled,
    interactionWidth: 20,
    style: {
      stroke: enabled ? '#475569' : '#64748b',
      strokeDasharray: enabled ? undefined : '8 5',
      strokeWidth: 2,
    },
  }
})

// Update existing edges reactively when enabled state changes
watch(() => activeGraph.value?.enabled, (enabled) => {
  const isEnabled = enabled !== false
  edges.value = edges.value.map(e => ({
    ...e,
    animated: isEnabled,
    style: {
      stroke: isEnabled ? '#475569' : '#64748b',
      strokeDasharray: isEnabled ? undefined : '8 5',
      strokeWidth: 2,
    },
  }))
})
const paletteCollapsed = ref(localStorage.getItem('logic_palette_collapsed') === '1')
watch(paletteCollapsed, v => localStorage.setItem('logic_palette_collapsed', v ? '1' : '0'))
// Matches the NodePalette column's actual current width (w-56/w-8, or absent
// entirely for non-admins) minus the toolbar's own px-4 — a fixed w-52 only
// lined up the dropdown while the palette was expanded and visible.
const titleSpacerClass = computed(() => {
  if (!auth.isAdmin) return 'w-0'
  return paletteCollapsed.value ? 'w-4' : 'w-52'
})

const saving        = ref(false)
const graphLoading  = ref(false)
// Set while create/import/duplicate swap sheets after leaveCanvas() saved
// the canvas: their server round trips happen before the swap.
const sheetSwitching = ref(false)
// Read-only while a sheet loads or is being swapped: the canvas still shows
// the previous sheet, and an edit made now would either be dropped or
// overwritten by the arriving content.
const canvasEditable = computed(() => auth.isAdmin && !graphLoading.value && !sheetSwitching.value)
provideCanvasEditable(canvasEditable)
let _loadGraphRequestId = 0
const statusMsg     = ref(null)
const canvasWrapper = ref(null)

let _statusTimer = null
// `kind: 'save'` marks a save's own result, which a later save may replace.
function showStatus(ok, text, ms = 3000, kind = null) {
  // Without cancelling the previous timer, a quick Copy → Paste → Save
  // sequence leaves multiple independent timeouts running; the earliest one
  // (e.g. from Copy) can then null out a later, still-relevant status (e.g.
  // Save's result) almost immediately instead of after its own `ms`.
  clearTimeout(_statusTimer)
  statusMsg.value = kind ? { ok, text, kind } : { ok, text }
  _statusTimer = setTimeout(() => { statusMsg.value = null }, ms)
}

const validationWarnings = computed(() => analyzeFlowWarnings(nodes.value, edges.value))

function currentFlowData() {
  return {
    nodes: nodes.value.map(n => {
      // eslint-disable-next-line no-unused-vars
      const { _dbg, _dbg_title, ...nodeData } = n.data ?? {}
      return { id: n.id, type: n.type, position: n.position, data: nodeData }
    }),
    edges: edges.value.map(e => ({
      id: e.id, source: e.source, target: e.target,
      sourceHandle: e.sourceHandle, targetHandle: e.targetHandle
    })),
  }
}

// Blocks whose output does not depend on their inputs within one run — mirrors
// TICK_BOUNDARY_NODE_TYPES in obs/logic/graph_analysis.py: a feedback loop
// through one of them is no cycle.
const TICK_BOUNDARY_NODE_TYPES = new Set(['memory', 'timer_delay', 'timer_pulse'])

function analyzeFlowWarnings(flowNodes, flowEdges) {
  const nodeMap = new Map(flowNodes.map(n => [n.id, n]))
  const inDegree = new Map(flowNodes.map(n => [n.id, 0]))
  const adj = new Map(flowNodes.map(n => [n.id, []]))

  for (const edge of flowEdges) {
    if (!adj.has(edge.source) || !inDegree.has(edge.target)) continue
    if (TICK_BOUNDARY_NODE_TYPES.has(nodeMap.get(edge.target)?.type)) continue
    adj.get(edge.source).push(edge.target)
    inDegree.set(edge.target, inDegree.get(edge.target) + 1)
  }

  const queue = [...inDegree.entries()].filter(([, deg]) => deg === 0).map(([id]) => id)
  const ordered = new Set()
  while (queue.length) {
    const id = queue.shift()
    ordered.add(id)
    for (const next of adj.get(id) || []) {
      inDegree.set(next, inDegree.get(next) - 1)
      if (inDegree.get(next) === 0) queue.push(next)
    }
  }

  const unresolved = new Set(flowNodes.map(n => n.id).filter(id => !ordered.has(id)))
  const cyclic = findCyclicNodeIds(adj, unresolved)
  const cycleList = flowNodes.filter(n => cyclic.has(n.id)).map(n => n.id)
  const cycleWarnings = flowNodes
    .filter(n => unresolved.has(n.id))
    .map(n => ({
      node_id: n.id,
      code: cyclic.has(n.id) ? 'graph_cycle' : 'graph_cycle_blocked',
      message: `${n.id}: ${cycleList.slice(0, 5).join(', ')}`,
    }))
  return [...cycleWarnings, ...findDuplicateTargetHandleWarnings(flowEdges)]
}

// Multiple edges wired to the same (target node, target handle) pair are a
// dead wire, not a merge: the executor's edge_map is a plain dict keyed by
// (target, handle), so only the last edge in array order ever actually
// reaches that input — the rest silently carry no value, permanently (#1116).
// Every source targeting one handle needs its own handle; use a `merge` node
// to combine several independent sources into one downstream path instead.
function findDuplicateTargetHandleWarnings(flowEdges) {
  const edgesByHandle = new Map()
  for (const edge of flowEdges) {
    const handle = edge.targetHandle || 'in'
    const key = `${edge.target}#${handle}`
    const list = edgesByHandle.get(key)
    if (list) list.push(edge)
    else edgesByHandle.set(key, [edge])
  }
  return [...edgesByHandle.values()]
    .filter(edgesForHandle => edgesForHandle.length > 1)
    .map(edgesForHandle => {
      const handle = edgesForHandle[0].targetHandle || 'in'
      return {
        node_id: edgesForHandle[0].target,
        code: 'duplicate_target_handle',
        message: `${edgesForHandle[0].target}.${handle} (${edgesForHandle.length})`,
      }
    })
}

function hasDuplicateHandleWarning(warnings) {
  return warnings.some(w => w.code === 'duplicate_target_handle')
}

// The two warning kinds are reported together but counted separately: mixing
// cycle counts into a duplicate-handle message (or vice versa) would show a
// number that doesn't match either sentence (#1116 review).
function warningCountForDisplay(warnings) {
  const isDuplicate = w => w.code === 'duplicate_target_handle'
  return hasDuplicateHandleWarning(warnings)
    ? warnings.filter(isDuplicate).length
    : warnings.filter(w => !isDuplicate(w)).length
}

function findCyclicNodeIds(adj, candidates) {
  let index = 0
  const stack = []
  const indices = new Map()
  const lowlinks = new Map()
  const onStack = new Set()
  const cyclic = new Set()

  function strongconnect(id) {
    indices.set(id, index)
    lowlinks.set(id, index)
    index += 1
    stack.push(id)
    onStack.add(id)

    for (const next of adj.get(id) || []) {
      if (!candidates.has(next)) continue
      if (!indices.has(next)) {
        strongconnect(next)
        lowlinks.set(id, Math.min(lowlinks.get(id), lowlinks.get(next)))
      } else if (onStack.has(next)) {
        lowlinks.set(id, Math.min(lowlinks.get(id), indices.get(next)))
      }
    }

    if (lowlinks.get(id) !== indices.get(id)) return
    const component = []
    while (stack.length) {
      const member = stack.pop()
      onStack.delete(member)
      component.push(member)
      if (member === id) break
    }
    const hasSelfLoop = component.length === 1 && (adj.get(component[0]) || []).includes(component[0])
    if (component.length > 1 || hasSelfLoop) {
      for (const member of component) cyclic.add(member)
    }
  }

  for (const id of candidates) {
    if (!indices.has(id)) strongconnect(id)
  }
  return cyclic
}

async function loadGraph() {
  if (!activeGraphId.value) { replaceCanvas(''); return }
  // Tag this request so overlapping sheet switches don't let an earlier
  // response's `finally` clear graphLoading (or apply its stale data) after a
  // newer request has already taken over.
  const requestId = ++_loadGraphRequestId
  const graphId = activeGraphId.value
  // A newer load took over, or the sheet was closed/replaced without one
  // (delete, create) while this load was waiting.
  const superseded = () => requestId !== _loadGraphRequestId || activeGraphId.value !== graphId
  graphLoading.value = true
  // The config panel would keep editing a block of the outgoing canvas,
  // whose edits the read-only load drops.
  selectedNode.value = null
  try {
    // Re-picking the open sheet: persist its edit first, or the GET returns
    // the state before it. Saves of the sheet being loaded settle first.
    const saved = await persistPendingChanges(graphId)
    if (superseded()) return
    // A save this view did not start (the previous instance's unmount flush)
    // failed: its own error message went with it, so report it here.
    if (!saved && _canvasGraphId !== graphId) showStatus(false, t('logic.errorSave'), 3000, 'save')
    const { data } = await logicApi.getGraph(graphId)
    if (superseded()) return
    replaceCanvas(graphId, (data.flow_data.nodes || []).map(n => {
      // eslint-disable-next-line no-unused-vars
      const { _dbg, _dbg_title, ...nodeData } = n.data ?? {}
      return { ...n, position: n.position || { x: 100, y: 100 }, data: nodeData }
    }), data.flow_data.edges || [])
  } catch (err) {
    if (superseded()) return
    // A failed load must not leave an editable (but never actually loaded)
    // sheet around: with only nodes/edges cleared, activeGraphId still
    // named this sheet and graphLoading still clears in `finally` below, so
    // Save stays enabled and would submit those empty arrays — overwriting
    // the real, unrelated graph on the server with nothing. Revert the
    // selection entirely instead of presenting a stale or blank editor.
    activeGraphId.value = ''
    replaceCanvas('')
    showStatus(false, err.response?.data?.detail ?? t('logic.errorLoad'))
  } finally {
    if (requestId === _loadGraphRequestId) graphLoading.value = false
  }
}

// ── Canvas ownership & unsaved edits ───────────────────────────────────────
// `nodes`/`edges` always hold exactly one sheet's content, _canvasGraphId —
// which lags behind activeGraphId while a sheet switch is loading, so edits
// and saves in that window still belong to the sheet actually shown. Every
// edit bumps _editRev; _sentRev and _savedRev record how far saves got. A
// canvas replacement resets all three and bumps _canvasEpoch, so the late
// result of a save for an earlier canvas cannot touch them.
let _canvasGraphId = ''
let _canvasEpoch = 0
let _editRev = 0
let _sentRev = 0
let _savedRev = 0

// Edits the server has not confirmed: pending, in flight, failed or refused.
function hasUnsavedEdits() {
  return !!_canvasGraphId && _editRev > _savedRev
}

// Edits no save is underway for: pending, failed or refused.
function hasUnsentEdits() {
  return !!_canvasGraphId && _editRev > _sentRev
}

// The only way to swap the canvas to another sheet's content (or clear it).
// An unsent edit of the outgoing sheet is saved first, from the snapshot
// saveGraph takes right now — unless that sheet is gone (`discard`).
function replaceCanvas(graphId, flowNodes = [], flowEdges = [], { discard = false } = {}) {
  clearTimeout(_autoSaveTimer)
  if (!discard && hasUnsentEdits()) saveGraph()
  _canvasGraphId = graphId
  _canvasEpoch += 1
  _editRev = 0
  _sentRev = 0
  _savedRev = 0
  nodes.value = flowNodes
  edges.value = flowEdges
  // New content, not an edit — the content watcher must not count it, nor
  // Vue Flow's own normalisation of it during the next ticks.
  _persistedSignature = persistedSignature()
  _contentSettling = true
  const epoch = _canvasEpoch
  nextTick().then(nextTick).then(() => {
    if (epoch !== _canvasEpoch) return
    _persistedSignature = persistedSignature()
    _contentSettling = false
  })
  // The config panel would otherwise keep editing a block of the old canvas.
  selectedNode.value = null
}

// Saves the canvas into the sheet it belongs to. Resolves to true when
// saved, false when the save failed, and null when it was refused for good:
// by the graph validation or by a 4xx from the server. A refused edit stays
// unsaved but does not block run/export/duplicate — retrying cannot help, so
// those act on the server's state as they did before auto-save.
// Server answers that retrying the same payload cannot change; anything else
// (5xx, network, 408/409/429) is a failure to retry.
// A 401 only reaches here once the token refresh failed and the API client
// is already redirecting to /login — prompting for a retry would block that.
const PERMANENT_SAVE_REJECTIONS = new Set([400, 401, 403, 404, 413, 422])

// `manual`: the Save button — its confirmation always shows, while an
// auto-save's "saved" must not replace an error the user still has to read.
async function saveGraph({ manual = false } = {}) {
  const graphId = _canvasGraphId
  if (!auth.isAdmin || !graphId) return false
  // Any save carries the latest canvas, so a pending auto-save is redundant.
  clearTimeout(_autoSaveTimer)
  const graphWarnings = analyzeFlowWarnings(nodes.value, edges.value)
  if (graphWarnings.length) {
    // Always say that nothing was saved — an edit must never look saved.
    // Only the Save button also marks the blocks: an auto-save overwriting
    // the debug bands on every move of a legacy sheet's blocks would get in
    // the way.
    const saveBlockedKey = hasDuplicateHandleWarning(graphWarnings) ? 'logic.graphValidationSaveBlockedDuplicateHandle' : 'logic.graphValidationSaveBlocked'
    showStatus(false, t(saveBlockedKey, { count: warningCountForDisplay(graphWarnings) }), 6000, 'save')
    if (manual) {
      applyDebugValues(Object.fromEntries(
        graphWarnings.map(w => [w.node_id, {
          __error__: t(w.code === 'duplicate_target_handle' ? 'logic.graphValidationNodeErrorDuplicateHandle' : 'logic.graphValidationNodeError'),
          __diagnostic__: w.code,
        }])
      ))
    }
    // Refused for good: counts as sent, so neither a flush, a canvas swap
    // nor the reload prompt keeps re-trying it.
    _sentRev = Math.max(_sentRev, _editRev)
    return null
  }
  // Snapshot synchronously: by the time a queued save runs, the canvas may
  // already show another sheet.
  const flowData = currentFlowData()
  const epoch = _canvasEpoch
  const rev = _editRev
  const sameCanvas = () => epoch === _canvasEpoch
  _sentRev = rev
  // Queued in the store, shared with the graph picker and with the next
  // instance of this view: a remount must not load before this save lands.
  return store.enqueueWrite(async () => {
    saving.value = true
    let result = false
    try {
      const graph = store.graphs.find(g => g.id === graphId)
      if (graph) {
        await store.saveGraph(graphId, graph.name, graph.description, graph.enabled, flowData)
        if (sameCanvas()) _savedRev = Math.max(_savedRev, rev)
        // An auto-save confirms itself only over nothing or an earlier
        // save's result (clearing a past failure), never over other news.
        if (manual || !statusMsg.value || statusMsg.value.kind === 'save') showStatus(true, t('logic.saved'), 3000, 'save')
        result = true
      } else {
        // Deleted meanwhile (e.g. from the graph picker) — nothing to save
        // into, ever, and nothing to report: the user deleted it.
        result = null
      }
    } catch (err) {
      showStatus(false, err.response?.data?.detail ?? t('logic.errorSave'), 3000, 'save')
      if (PERMANENT_SAVE_REJECTIONS.has(err.response?.status)) result = null
    } finally {
      saving.value = false
      // Failed (not refused): the edit counts as unsent again, so the next
      // flush, the reload prompt and run/export/duplicate retry or guard
      // it. A newer save already underway keeps its claim.
      if (result === false && sameCanvas() && _sentRev === rev) _sentRev = _savedRev
    }
    return result
  }, graphId)
}

// ── Auto-save ──────────────────────────────────────────────────────────────
// Structural edits (block config, connections created/removed/re-wired,
// blocks dropped/pasted/removed) are persisted after a short idle period,
// so a burst of edits costs one PUT.
const AUTO_SAVE_DELAY_MS = 500
let _autoSaveTimer = null

function scheduleAutoSave(signature = persistedSignature()) {
  if (!auth.isAdmin || !_canvasGraphId) return
  // Counted here — the content watcher must not count (and re-debounce) the
  // same change again when it runs.
  _persistedSignature = signature
  _editRev += 1
  clearTimeout(_autoSaveTimer)
  _autoSaveTimer = setTimeout(saveGraph, AUTO_SAVE_DELAY_MS)
}

// Every change to what a save would persist counts as an edit, wherever it
// comes from: edits on the block cards themselves (inline rename, negation
// toggle, comment resize — Vue Flow's updateNodeData emits no change event)
// and moved blocks included. The handlers above still schedule directly, so
// a run/export right after them already sees the edit synchronously.
let _persistedSignature = ''
function persistedSignature() {
  return JSON.stringify(currentFlowData())
}
// Right after a canvas swap, Vue Flow normalises the new content itself
// (snapping positions to the grid, dropping edges to missing blocks); that is
// not a user edit and must not be saved. The baseline is taken once it is
// done.
let _contentSettling = false
watch(persistedSignature, (signature) => {
  if (!_contentSettling && signature !== _persistedSignature) scheduleAutoSave(signature)
})

// Saves an unsent edit right away instead of waiting for the debounce.
function flushUnsentEdits() {
  if (hasUnsentEdits()) saveGraph()
}

// Saves the canvas before it is left (another sheet, a new or imported
// sheet). Resolves to false when that save failed — the caller then stays,
// because switching would discard the edit with nothing left to retry it
// from. Edits made while the save was in flight are saved too; a refused
// edit can never be saved and does not hold the switch up.
async function leaveCanvas() {
  while (hasUnsavedEdits()) {
    const rev = _editRev
    if (!await persistPendingChanges(_canvasGraphId)) return false
    if (_editRev === rev) break
  }
  return true
}

// Actions that read the sheet back from the server (run, export, duplicate,
// reload) must see the last edit, not the state before the debounce elapsed.
// Resolves to false when that edit failed to save, so the caller stops
// instead of acting on the server's older state.
async function persistPendingChanges(graphId) {
  if (_canvasGraphId === graphId) flushUnsentEdits()
  // The flushed save queues behind any earlier one, so it settles last.
  return (await store.pendingSave(graphId)) !== false
}

// ── Node selection & config ────────────────────────────────────────────────
// One selected block feeds both the settings and the debug values of the
// config panel, so switching tabs never targets a different block.
const selectedNode = ref(null)

// ── Debug mode ─────────────────────────────────────────────────────────────
const debugMode = ref(false)
const debugOverrides = ref({})
const lastRunMetadata = ref(null)
const lastRunInputs = ref({})
const lastRunDebugOutputs = ref({})
let debugStateGeneration = 0
const DEBUG_TOOLTIP_MAX_CHARS = 1000

function fmtDebugVal(nodeOut, { full = false, maxChars = null, portLabels = {} } = {}) {
  if (!nodeOut || typeof nodeOut !== 'object') return null

  function maybeClip(text) {
    return maxChars !== null && text.length > maxChars ? `${text.slice(0, maxChars)}…` : text
  }

  function fv(value) {
    if (value === null || value === undefined) return '—'
    if (typeof value === 'boolean') return value ? '✓' : '✗'
    if (typeof value === 'number') return String(parseFloat(value.toPrecision(5)))
    const text = String(value)
    return full ? maybeClip(text) : text.slice(0, 18)
  }

  function clipped(value, limit) {
    if (value === null || value === undefined) return '—'
    const text = String(value)
    if (full) return maybeClip(text)
    return text.length <= limit ? text : `${text.slice(0, limit)}…`
  }

  if ('__error__' in nodeOut) return `${t('logic.nodeError')}: ${clipped(translateVariableErrors(nodeOut.__error__, t), 50)}`
  if ('_message' in nodeOut) {
    const message = nodeOut._message !== null && nodeOut._message !== undefined
      ? `"${String(nodeOut._message).slice(0, 24)}"`
      : '—'
    const sent = 'sent' in nodeOut ? `  sent=${fv(nodeOut.sent)}` : ''
    return message + sent
  }
  if ('value' in nodeOut && 'changed' in nodeOut) return `= ${fv(nodeOut.value)}`
  if ('_write_value' in nodeOut) return `→ ${fv(nodeOut._write_value)}`
  if ('response' in nodeOut && 'status' in nodeOut && 'success' in nodeOut) {
    return `response=${clipped(nodeOut.response, 80)}   status=${fv(nodeOut.status)}   success=${fv(nodeOut.success)}`
  }
  // Configured output names are free text — keep the band compact and the
  // tooltip within its cap regardless of how long a user made them.
  const pairs = Object.entries(nodeOut)
    .filter(([key]) => !key.startsWith('_'))
    .map(([key, value]) => `${clipped(portLabels[key] ?? key, 24)}=${fv(value)}`)
  if (!pairs.length) return null
  const line = pairs.join('   ')
  return full ? maybeClip(line) : line
}

// Last run outputs — always kept (not just in debug mode) so that
// json_extractor / xml_extractor config panels can read _preview data.
const lastRunOutputs = ref({})
const showRunPreflight = ref(false)
const runPreflightLoading = ref(false)
const runPreflightError = ref('')
const runPreflightItems = ref([])
const preflightApproved = ref(false)
const preflightGraphId = ref('')
let preflightRequestId = 0

// Extractor blocks keep the last received payload (`_preview`) across runs
// that carry none, so their path picker survives an untriggered re-execution
// (issue #1104).
function setLastRunOutputs(outputs) {
  lastRunOutputs.value = retainPreviews(lastRunOutputs.value, outputs)
}

// Outputs the canvas debug bands were last rendered from — the band text is
// baked into node.data, so a locale change re-renders it from here.
let _debugBandOutputs = null

function applyDebugValues(outputs, captureDebugOutputs = debugMode.value) {
  setLastRunOutputs(outputs)
  if (captureDebugOutputs) lastRunDebugOutputs.value = outputs
  _debugBandOutputs = outputs
  renderDebugBands(outputs)
}

function renderDebugBands(outputs) {
  nodes.value = nodes.value.map(node => {
    // Debug band shows the configured output names, not `out_N` (issue #1104)
    const portLabels = extractorOutputLabels(node, t)
    return {
      ...node,
      data: {
        ...node.data,
        _dbg: fmtDebugVal(outputs[node.id], { portLabels }) ?? undefined,
        _dbg_title: fmtDebugVal(outputs[node.id], { full: true, maxChars: DEBUG_TOOLTIP_MAX_CHARS, portLabels }) ?? undefined,
      },
    }
  })
}

function clearDebugValues() {
  _debugBandOutputs = null
  nodes.value = nodes.value.map(node => {
    // eslint-disable-next-line no-unused-vars
    const { _dbg, _dbg_title, ...data } = node.data
    return { ...node, data }
  })
}

// Translated parts of the band (fallback output names, error texts) follow
// the active locale without waiting for the next execution.
watch(locale, () => {
  if (_debugBandOutputs) renderDebugBands(_debugBandOutputs)
})

function countGraphDiagnostics(outputs) {
  return Object.values(outputs || {}).filter(out =>
    out &&
    typeof out === 'object' &&
    typeof out.__diagnostic__ === 'string' &&
    out.__diagnostic__.startsWith('graph_cycle')
  ).length
}

function toggleDebug() {
  if (!auth.isAdmin) return
  debugMode.value = !debugMode.value
  debugStateGeneration += 1
  sendDebugSubscription(activeGraphId.value, debugMode.value)
  clearDebugValues()
  if (!debugMode.value) {
    clearAllDebugOverrides()
    lastRunMetadata.value = null
    lastRunInputs.value = {}
    lastRunDebugOutputs.value = {}
  }
}

function preflightLabel(check) {
  if (check.target_type === 'logic_graph') return t('logic.preflightGraph', { id: check.target_id })
  if (check.target_type === 'logic_graph_state') return t('logic.preflightGraphState')
  if (check.target_type === 'logic_capability') return t('logic.preflightCapability', { capability: check.target_id })
  if (check.target_type === 'datapoint') return t('logic.preflightDatapoint', { id: check.target_id })
  return `${check.target_type}: ${check.target_id}`
}

function preflightReason(check) {
  const key = `logic.preflightReasons.${check.reason}`
  const translated = t(key)
  return translated === key ? check.reason : translated
}

function normalizeRunPreflight(data) {
  return (data.checks || []).map((check, index) => ({
    id: `${check.target_type}:${check.target_id}:${index}`,
    label: preflightLabel(check),
    detail: check.node_ids?.length ? t('logic.preflightNodes', { nodes: check.node_ids.join(', ') }) : '',
    allowed: check.allowed,
    reason: check.allowed ? '' : preflightReason(check),
  }))
}

async function runApprovedGraph(graphId) {
  preflightApproved.value = true
  try {
    await runGraph(graphId)
  } finally {
    preflightApproved.value = false
    preflightGraphId.value = ''
  }
}

async function requestGraphRun() {
  if (!activeGraphId.value || !activeGraph.value?.enabled) return
  const graphId = activeGraphId.value
  // Bail out if the sheet was switched while the save was in flight.
  if (!await persistPendingChanges(graphId) || activeGraphId.value !== graphId) return
  const requestId = ++preflightRequestId
  runPreflightLoading.value = true
  runPreflightError.value = ''
  runPreflightItems.value = []
  preflightGraphId.value = ''
  try {
    const { data } = await logicRunAuthzApi.preflight(graphId)
    if (requestId !== preflightRequestId || activeGraphId.value !== graphId || data.graph_id !== graphId) return
    preflightGraphId.value = graphId
    const items = normalizeRunPreflight(data)
    runPreflightItems.value = items
    // Only interrupt with the confirmation dialog when a check is actually
    // denied — a fully-allowed run (the common case for admins, who have no
    // grant restrictions) proceeds immediately without the popup.
    if (items.every(item => item.allowed !== false)) {
      runPreflightLoading.value = false
      await runApprovedGraph(graphId)
      return
    }
    showRunPreflight.value = true
  } catch (err) {
    if (requestId !== preflightRequestId) return
    runPreflightError.value = err.response?.data?.detail ?? t('logic.preflightError')
    showRunPreflight.value = true
  } finally {
    if (requestId === preflightRequestId) runPreflightLoading.value = false
  }
}

async function confirmGraphRun() {
  const graphId = preflightGraphId.value
  if (!graphId || activeGraphId.value !== graphId || runPreflightItems.value.some(item => !item.allowed)) return
  showRunPreflight.value = false
  await runApprovedGraph(graphId)
}

function parseOverride(text) {
  if (!text.trim()) return undefined
  try { return JSON.parse(text) } catch { return text }
}

function setDebugOverride(inputId, text) {
  if (!auth.isAdmin || !selectedNode.value) return
  const nodeValues = { ...(debugOverrides.value[selectedNode.value.id] || {}) }
  if (!text.trim()) delete nodeValues[inputId]
  else nodeValues[inputId] = text
  debugOverrides.value = { ...debugOverrides.value, [selectedNode.value.id]: nodeValues }
}

function clearDebugOverride(inputId) { setDebugOverride(inputId, '') }
function clearAllDebugOverrides() { debugOverrides.value = {} }
const hasDebugOverrides = computed(() => Object.values(debugOverrides.value).some(values => Object.keys(values).length > 0))

const debugInputs = computed(() => {
  if (!selectedNode.value) return []
  const definition = store.nodeTypes.find(type => type.type === selectedNode.value.type)
  let ports = definition?.inputs || []
  const count = Number(selectedNode.value.data?.input_count) || 2
  if (['and', 'or', 'xor', 'binary_stats'].includes(selectedNode.value.type)) {
    const gateCount = selectedNode.value.type === 'binary_stats'
      ? binaryStatsInputCount(selectedNode.value.data?.input_count)
      : Math.max(2, Math.min(30, count))
    ports = Array.from({ length: gateCount }, (_, i) => ({ id: `in${i + 1}`, label: `${i + 1}` }))
  } else if (selectedNode.value.type === 'avg_multi') {
    ports = Array.from({ length: Math.max(2, Math.min(20, count)) }, (_, i) => ({ id: `in_${i + 1}`, label: `${i + 1}` }))
  } else if (selectedNode.value.type === 'string_concat') {
    const stringCount = Number(selectedNode.value.data?.count) || 2
    ports = Array.from({ length: Math.max(2, Math.min(20, stringCount)) }, (_, i) => ({ id: `in_${i + 1}`, label: `${i + 1}` }))
  } else if (selectedNode.value.type === 'python_script') {
    ports = ['a', 'b', 'c'].map(id => ({ id, label: id }))
  } else if (selectedNode.value.type === 'hems_surplus') {
    ports = hemsInputPorts(selectedNode.value.data, t)
  }
  const known = new Set(ports.map(port => port.id))
  for (const edge of edges.value.filter(item => item.target === selectedNode.value.id)) {
    const id = edge.targetHandle || 'in'
    if (!known.has(id)) {
      ports = [...ports, { id, label: id }]
      known.add(id)
    }
  }
  for (const id of Object.keys(lastRunInputs.value[selectedNode.value.id] || {})) {
    if (!known.has(id)) {
      ports = [...ports, { id, label: id }]
      known.add(id)
    }
  }
  return ports.map(port => {
    const edge = edges.value.find(item => item.target === selectedNode.value.id && (item.targetHandle || 'in') === port.id)
    const captured = lastRunInputs.value[selectedNode.value.id]?.[port.id]
    const hasCapturedInput = captured && Object.prototype.hasOwnProperty.call(captured, 'incoming')
    const incoming = hasCapturedInput ? captured.incoming : (edge ? lastRunDebugOutputs.value[edge.source]?.[edge.sourceHandle || 'out'] : undefined)
    const overrideText = debugOverrides.value[selectedNode.value.id]?.[port.id]
    const locallyOverridden = overrideText !== undefined
    const capturedOverridden = captured?.overridden === true
    return {
      id: port.id,
      label: port.label || port.id,
      incoming,
      effective: captured?.effective,
      locallyOverridden,
      capturedOverridden,
      overridden: locallyOverridden || capturedOverridden,
      overrideText: overrideText ?? '',
    }
  })
})

async function runGraph(graphId = activeGraphId.value) {
  if ((!auth.isAdmin && !preflightApproved.value) || !graphId) return
  const requestGraphId = graphId
  const requestDebugGeneration = debugStateGeneration
  const requestedDebugState = debugMode.value
  try {
    const parsedOverrides = Object.fromEntries(Object.entries(debugOverrides.value).map(([nodeId, values]) => [
      nodeId,
      Object.fromEntries(Object.entries(values).map(([port, value]) => [port, parseOverride(value)])),
    ]).filter(([, values]) => Object.keys(values).length))
    const { data } = requestedDebugState || Object.keys(parsedOverrides).length
      ? await logicApi.runGraph(requestGraphId, { debug: requestedDebugState, input_overrides: parsedOverrides })
      : await logicApi.runGraph(requestGraphId)
    if (activeGraphId.value !== requestGraphId) return
    const outputs = data.outputs || {}
    const evalCount = Object.keys(outputs).length
    const diagnosticCount = Array.isArray(data.warnings) ? data.warnings.length : countGraphDiagnostics(outputs)
    const acceptsDebugResponse = (
      requestedDebugState &&
      debugMode.value &&
      debugStateGeneration === requestDebugGeneration
    )
    showStatus(
      diagnosticCount === 0,
      diagnosticCount > 0
        ? t('logic.runResultWithWarnings', { count: evalCount, warnings: diagnosticCount })
        : t('logic.runResult', { count: evalCount }),
      diagnosticCount > 0 ? 6000 : 3000
    )
    // Always update lastRunOutputs (needed for extractor config panels)
    if (debugMode.value || diagnosticCount > 0) applyDebugValues(outputs, acceptsDebugResponse)
    else {
      setLastRunOutputs(outputs)
      clearDebugValues()
    }
    if (acceptsDebugResponse) {
      lastRunMetadata.value = data.debug || { timestamp: new Date().toISOString(), used_overrides: false }
      lastRunInputs.value = data.debug?.inputs || {}
    }
  } catch (err) {
    showStatus(false, err.response?.data?.detail ?? t('common.error'))
  }
}

watch(activeGraphId, () => {
  preflightRequestId += 1
  showRunPreflight.value = false
  runPreflightLoading.value = false
  runPreflightItems.value = []
  preflightGraphId.value = ''
})

// ── New graph ──────────────────────────────────────────────────────────────
const showNewGraph  = ref(false)
const newGraphName  = ref('')
const newGraphDesc  = ref('')
const newGraphHierarchyNodes = ref([]) // composite "tree_id:node_id" strings — optional, unassigned when empty

function newGraph() {
  if (!auth.isAdmin) return
  newGraphName.value = ''
  newGraphDesc.value = ''
  newGraphHierarchyNodes.value = []
  showNewGraph.value = true
}
async function doCreateGraph() {
  if (!auth.isAdmin) return
  if (!await leaveCanvas()) return
  sheetSwitching.value = true
  try {
    const g = await store.createGraph(newGraphName.value, newGraphDesc.value)
    // Optional: link the new graph into the picked hierarchy node(s) right away —
    // matches the many-to-many model #1217 already established (0, 1 or several
    // nodes). Best-effort: a link failure must not block the graph having been
    // created, it would just stay reachable via "Nicht zugeordnet" instead.
    await Promise.all(
      newGraphHierarchyNodes.value.map((compositeId) => {
        const parsed = parseHierarchyCompositeId(compositeId)
        if (!parsed) return null
        return hierarchyApi.createLogicGraphLink({ node_id: parsed.node_id, graph_id: g.id }).catch(() => {})
      }),
    )
    showNewGraph.value = false
    activeGraphId.value = g.id
    replaceCanvas(g.id)
  } finally {
    sheetSwitching.value = false
  }
}

// ── Toggle enabled ─────────────────────────────────────────────────────────
async function doToggleEnabled() {
  if (!auth.isAdmin || !activeGraphId.value) return
  try {
    // Captured now: the queued task may run after the user switched sheets.
    const graphId = activeGraphId.value
    const updated = await store.enqueueWrite(() => store.toggleEnabled(graphId))
    showStatus(true, updated.enabled ? t('logic.activated') : t('logic.deactivated'))
  } catch (err) {
    showStatus(false, err.response?.data?.detail ?? t('logic.errorToggle'))
  }
}

// ── Delete graph ───────────────────────────────────────────────────────────
const showDeleteConfirm = ref(false)
function confirmDeleteGraph() {
  if (!auth.isAdmin || !activeGraphId.value) return
  showDeleteConfirm.value = true
}
async function doDeleteGraph() {
  if (!auth.isAdmin || !activeGraphId.value) return
  const graphId = activeGraphId.value
  // Persist a pending edit first: should the delete fail, the sheet stays
  // and so must its edit. Every queued write (save, toggle, rename) has to
  // land before the DELETE, or it fails afterwards with a spurious error.
  await persistPendingChanges(graphId)
  await store.enqueueWrite(() => store.deleteGraph(graphId))
  // The user may have opened another sheet meanwhile — leave that one open.
  if (activeGraphId.value !== graphId) return
  activeGraphId.value = ''
  replaceCanvas('', [], [], { discard: _canvasGraphId === graphId })
}

// ── Duplizieren ────────────────────────────────────────────────────────────
const showDuplicate = ref(false)
const duplicateName = ref('')

function openDuplicate() {
  if (!auth.isAdmin || !activeGraphId.value) return
  const g = store.graphs.find(g => g.id === activeGraphId.value)
  duplicateName.value = `${g?.name ?? ''}${t('logic.duplicateNameSuffix')}`
  showDuplicate.value = true
}

async function doDuplicate() {
  if (!auth.isAdmin || !activeGraphId.value || !duplicateName.value.trim()) return
  const graphId = activeGraphId.value
  const name = duplicateName.value.trim()
  try {
    // Persists the canvas, and waits for any save of the source sheet still
    // queued (e.g. from a just-unmounted editor), before it is copied.
    if (!await leaveCanvas() || !await persistPendingChanges(graphId)) return
    sheetSwitching.value = true
    const copy = await store.duplicateGraph(graphId, name)
    showDuplicate.value = false
    activeGraphId.value = copy.id
    await loadGraph()
    showStatus(true, t('logic.duplicated', { name: copy.name }))
  } catch (err) {
    showStatus(false, err.response?.data?.detail ?? t('logic.errorDuplicate'))
  } finally {
    sheetSwitching.value = false
  }
}

// ── Exportieren (programmatisch mit Auth-Header) ───────────────────────────
async function doExportGraph() {
  if (!activeGraphId.value) return
  const graphId = activeGraphId.value
  try {
    if (!await persistPendingChanges(graphId)) return
    const { data } = await logicApi.exportGraph(graphId)
    const g = store.graphs.find(g => g.id === graphId)
    const filename = g ? `${g.name.replace(/ /g, '_')}.json` : 'logic_graph.json'
    const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' })
    const url  = URL.createObjectURL(blob)
    const a    = document.createElement('a')
    a.href = url; a.download = filename
    document.body.appendChild(a); a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  } catch (err) {
    showStatus(false, err.response?.data?.detail ?? t('logic.errorExport'))
  }
}

// ── Umbenennen ─────────────────────────────────────────────────────────────
const showRenameGraph  = ref(false)
const renameGraphName  = ref('')
const renameGraphDesc  = ref('')

function openRenameGraph() {
  if (!auth.isAdmin || !activeGraphId.value) return
  const g = store.graphs.find(g => g.id === activeGraphId.value)
  renameGraphName.value = g?.name ?? ''
  renameGraphDesc.value = g?.description ?? ''
  showRenameGraph.value = true
}

async function doRenameGraph() {
  if (!auth.isAdmin || !activeGraphId.value || !renameGraphName.value.trim()) return
  try {
    // Captured now: the queued task may run after the user switched sheets.
    const graphId = activeGraphId.value
    const name = renameGraphName.value.trim()
    const description = renameGraphDesc.value
    await store.enqueueWrite(() => store.renameGraph(graphId, name, description))
    showRenameGraph.value = false
    showStatus(true, t('logic.renamed'))
  } catch (err) {
    showStatus(false, err.response?.data?.detail ?? t('logic.errorRename'))
  }
}

// ── Importieren ────────────────────────────────────────────────────────────
async function onImportFile(event) {
  if (!auth.isAdmin) return
  const file = event.target.files?.[0]
  if (!file) return
  event.target.value = ''   // Reset input für erneuten Import derselben Datei
  try {
    const text = await file.text()
    const payload = JSON.parse(text)
    const namesBefore = new Set(store.graphs.map(g => g.name))
    if (!await leaveCanvas()) return
    sheetSwitching.value = true
    const imported = await store.importGraph(payload)
    activeGraphId.value = imported.id
    await loadGraph()
    if (namesBefore.has(imported.name)) {
      // Name bereits vergeben → Rename-Dialog sofort öffnen
      renameGraphName.value = imported.name
      renameGraphDesc.value = imported.description ?? ''
      showRenameGraph.value = true
      showStatus(true, t('logic.importedRename'))
    } else {
      showStatus(true, t('logic.imported', { name: imported.name }))
    }
  } catch (err) {
    showStatus(false, err?.response?.data?.detail ?? t('logic.errorImport'))
  } finally {
    sheetSwitching.value = false
  }
}

// Rejects edge sets the graph validation refuses and reports why; only an
// accepted set reaches the canvas and the auto-save.
function applyValidatedEdges(nextEdges) {
  const graphWarnings = analyzeFlowWarnings(nodes.value, nextEdges)
  if (graphWarnings.length) {
    const connectBlockedKey = hasDuplicateHandleWarning(graphWarnings) ? 'logic.graphValidationConnectBlockedDuplicateHandle' : 'logic.graphValidationConnectBlocked'
    showStatus(false, t(connectBlockedKey, { count: warningCountForDisplay(graphWarnings) }), 6000)
    return
  }
  edges.value = nextEdges
  scheduleAutoSave()
}

// ── Connect handler — REQUIRED to actually create edges ────────────────────
function onConnect(params) {
  if (!canvasEditable.value) return
  if (edges.value.some(e => sameConnection(e, params))) return
  const opts = defaultEdgeOptions.value
  // addEdge pushes into the array it is given — hand it a copy, or a
  // connection the validation then rejects would already sit in `edges`.
  applyValidatedEdges(addEdge({
    ...params,
    type: opts.type,
    animated: opts.animated,
    style: opts.style,
  }, [...edges.value]))
}

// A handle stored as `undefined` (dropped from JSON), '' and Vue Flow's
// `null` for "no handle" name the same port — as in Vue Flow's own
// duplicate check.
function sameConnection(a, b) {
  return a.source === b.source && a.target === b.target
    && (a.sourceHandle || null) === (b.sourceHandle || null)
    && (a.targetHandle || null) === (b.targetHandle || null)
}

// ── Re-wire an existing edge by dragging one of its ends ───────────────────
// Vue Flow only reports the drop; without this handler the edge snaps back.
function onEdgeUpdate({ edge, connection }) {
  if (!canvasEditable.value) return
  if (!connection?.source || !connection?.target) return
  // Dropped back onto the same handles — nothing changed, nothing to save.
  if (sameConnection(edge, connection)) return
  applyValidatedEdges(edges.value.map(e => e.id === edge.id
    ? {
        ...e,
        // Same id scheme as Vue Flow's own updateEdge: an id still naming the
        // old connection would collide once that connection is drawn again.
        id: `vueflow__edge-${connection.source}${connection.sourceHandle ?? ''}-${connection.target}${connection.targetHandle ?? ''}`,
        source: connection.source,
        target: connection.target,
        sourceHandle: connection.sourceHandle,
        targetHandle: connection.targetHandle,
      }
    : e))
}

// Vue Flow applies removals itself (Delete key, or removing a block together
// with its edges); only the persistence is left to do here. Selection and
// drag changes arrive through the same events and must not trigger a save.
function onElementsChange(changes) {
  if (changes.some(change => change.type === 'remove')) scheduleAutoSave()
}

// The properties panel works on its own copy of the selected block, so it
// would stay open for a deleted block and its edits would go nowhere while
// still triggering a save. Debug overrides of deleted blocks go too (#1159).
function onNodesChange(changes) {
  const removedIds = new Set(changes.filter(change => change.type === 'remove').map(change => change.id))
  if (removedIds.size) {
    if (removedIds.has(selectedNode.value?.id)) selectedNode.value = null
    if (Object.keys(debugOverrides.value).some(id => removedIds.has(id))) {
      debugOverrides.value = Object.fromEntries(Object.entries(debugOverrides.value).filter(([id]) => !removedIds.has(id)))
    }
  }
  onElementsChange(changes)
}

// ── Drop node from palette ─────────────────────────────────────────────────
function onDrop(event) {
  if (!canvasEditable.value) return
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
      ...(nt?.config_schema
        ? Object.fromEntries(
            Object.entries(nt.config_schema).map(([k, v]) => [k, v.default ?? ''])
          )
        : {}),
    },
  }
  nodes.value = [...nodes.value, newNode]
  scheduleAutoSave()
}

// ── Node selection ─────────────────────────────────────────────────────────
function onNodeClick({ node }) {
  // During a load the panel would edit a block of the outgoing canvas,
  // whose edits the read-only canvas drops.
  if (!canvasEditable.value) return
  selectedNode.value = { ...node }
}

// The properties panel holds its own copy of the selected block, so a rename
// typed directly on the card (issue #1157) would otherwise leave the panel
// heading showing the previous name until the block is reselected.
watch(
  () => {
    const node = nodes.value.find(n => n.id === selectedNode.value?.id)
    // `null` marks "no such block on the canvas" — distinct from a block
    // that simply carries no name, so removing the selected block does not
    // push a bogus update into the panel and make it re-read the block.
    return node ? String(node.data?.label ?? '') : null
  },
  (label) => {
    const current = selectedNode.value
    if (label === null || !current) return
    if (String(current.data?.label ?? '') === label) return
    selectedNode.value = { ...current, data: { ...current.data, label } }
  },
)

// ── Copy/Paste selected nodes (issue #1084) ────────────────────────────────
const clipboard  = ref(null)
const hasSelection = computed(() => nodes.value.some(n => n.selected))
let pasteCount = 0

function copySelection() {
  // graphLoading guards against a slow getGraph() from an earlier sheet
  // switch resolving after this copy — the selector may already name the
  // target sheet while `nodes` still holds the previous sheet's blocks and
  // selection, which would silently overwrite the clipboard with stale data.
  if (!auth.isAdmin || graphLoading.value) return
  const copied = cloneSelectionForClipboard(nodes.value, edges.value)
  if (!copied) {
    showStatus(false, t('logic.copySelectionEmpty'))
    return
  }
  clipboard.value = { ...copied, sourceGraphId: activeGraphId.value }
  pasteCount = 0
  showStatus(true, t('logic.copiedNodes', { count: copied.nodes.length }))
}

function pasteClipboard() {
  // graphLoading guards against a slow getGraph() from an earlier sheet
  // switch resolving after this paste and overwriting nodes/edges with the
  // just-loaded (pre-paste) sheet, silently dropping the pasted blocks.
  if (!canvasEditable.value || !clipboard.value || !activeGraphId.value) return
  // Anchor the pasted group at the center of the currently visible canvas
  // (converted to flow coordinates, same technique as onDrop) only when
  // pasting into a *different* sheet than the one it was copied from —
  // otherwise pasting into a sheet whose viewport is fitted around a
  // different graph-coordinate region than the source would leave the
  // pasted blocks outside the destination's visible viewport. A same-sheet
  // paste instead keeps remapClipboardForPaste's small relative offset, so
  // the copy lands right next to its source instead of jumping to wherever
  // the canvas happens to be centered.
  let targetCenter = null
  if (clipboard.value.sourceGraphId !== activeGraphId.value) {
    const rect = canvasWrapper.value?.getBoundingClientRect()
    if (rect && rect.width && rect.height) {
      const { project } = useVueFlow('logic-canvas')
      targetCenter = project({ x: rect.width / 2, y: rect.height / 2 })
    }
  }
  const pasted = remapClipboardForPaste(clipboard.value, pasteCount, targetCenter)
  pasteCount += 1
  // Only the freshly pasted nodes/edges should stay selected, so they can be
  // dragged as a group right away instead of also moving (or deleting) the
  // still-selected originals.
  nodes.value = [...nodes.value.map(n => ({ ...n, selected: false })), ...pasted.nodes]
  edges.value = [...edges.value.map(e => ({ ...e, selected: false })), ...pasted.edges]
  // Clear any single-node config-panel target — it may point at a node that
  // was just deselected (or isn't part of the new paste), so editing it now
  // would silently update the wrong node instead of the pasted selection.
  selectedNode.value = null
  showStatus(true, t('logic.pastedNodes', { count: pasted.nodes.length }))
  scheduleAutoSave()
}

function _isEditableTarget(el) {
  if (!el) return false
  const tag = el.tagName
  // SELECT is intentionally excluded: it doesn't accept free-text paste, and
  // the graph-select dropdown is the documented way to switch sheets before
  // pasting — blocking the shortcut there defeats that workflow.
  return tag === 'INPUT' || tag === 'TEXTAREA' || el.isContentEditable
}

function _onClipboardKeydown(event) {
  if (!auth.isAdmin || !activeGraphId.value) return
  if (!(event.ctrlKey || event.metaKey)) return
  if (showNewGraph.value || showRenameGraph.value || showDeleteConfirm.value) return
  if (_isEditableTarget(document.activeElement)) return
  if (event.key === 'c' || event.key === 'C') {
    copySelection()
  } else if (event.key === 'v' || event.key === 'V') {
    pasteClipboard()
  }
}

function onNodeDataUpdate(newData) {
  if (!canvasEditable.value || !selectedNode.value) return
  const rowsBefore = extractorRowCount(selectedNode.value)
  nodes.value = nodes.value.map(n =>
    n.id === selectedNode.value.id ? { ...n, data: { ...n.data, ...newData } } : n
  )
  selectedNode.value = { ...selectedNode.value, data: { ...selectedNode.value.data, ...newData } }
  // Configured output names feed the debug band text — re-render it so a
  // renamed extractor output shows up without another execution. Adding or
  // removing a row shifts the out_N numbering, so the cached per-port
  // values would be shown under the wrong names: drop that block's band
  // until the next execution delivers values for the new layout.
  if (extractorRowCount(selectedNode.value) !== rowsBefore) {
    const id = selectedNode.value.id
    if (_debugBandOutputs) {
      const { [id]: _staleBand, ...rest } = _debugBandOutputs
      _debugBandOutputs = rest
    }
    // The Debug values tab reads the same per-port values — they would be
    // listed under the shifted row names just as wrongly.
    if (id in lastRunDebugOutputs.value) {
      const { [id]: _staleInspector, ...rest } = lastRunDebugOutputs.value
      lastRunDebugOutputs.value = rest
    }
  }
  if (_debugBandOutputs) renderDebugBands(_debugBandOutputs)
  scheduleAutoSave()
}

// ── WebSocket — live debug updates ────────────────────────────────────────
let _ws = null
let _wsTimer = null
let _wsShouldReconnect = true

function _wsConnect() {
  const token = localStorage.getItem('access_token')
  if (!token) return
  _wsShouldReconnect = true
  const proto = window.location.protocol === 'https:' ? 'wss' : 'ws'
  const url   = `${proto}://${window.location.host}/api/v1/ws`
  try {
    _ws = new WebSocket(url, [`obs.jwt.${token}`])
  } catch { return }

  _ws.onopen = () => sendDebugSubscription(activeGraphId.value, debugMode.value)

  _ws.onmessage = (ev) => {
    try {
      const msg = JSON.parse(ev.data)
      if (
        msg.action   === 'logic_run'    &&
        msg.graph_id === activeGraphId.value &&
        debugMode.value
      ) {
        applyDebugValues(msg.outputs || {})
        lastRunInputs.value = msg.inputs || {}
        lastRunMetadata.value = msg.debug || { timestamp: new Date().toISOString(), used_overrides: false }
      }
    } catch { /* ignore parse errors */ }
  }

  _ws.onclose = (ev) => {
    _ws = null
    if (ev?.code === 4001) _wsShouldReconnect = false
    if (!_wsShouldReconnect) return
    _wsTimer = setTimeout(_wsConnect, 4000)   // auto-reconnect
  }
  _ws.onerror = () => { try { _ws?.close() } catch { /* ignore */ } }
}

function sendDebugSubscription(graphId, enabled) {
  if (!graphId || !_ws || typeof _ws.send !== 'function' || _ws.readyState !== WebSocket.OPEN) return
  _ws.send(JSON.stringify({ action: 'logic_debug', graph_id: graphId, enabled }))
}

function _wsDisconnect() {
  _wsShouldReconnect = false
  clearTimeout(_wsTimer)
  _wsTimer = null
  const ws = _ws
  _ws = null
  if (ws) {
    ws.onclose = null
    try { ws.close() } catch { /* ignore */ }
  }
}

function _wsReconnectAfterTokenRefresh() {
  clearTimeout(_wsTimer)
  _wsTimer = null
  const staleWs = _ws
  _ws = null
  if (staleWs) {
    staleWs.onclose = null
    try { staleWs.close() } catch { /* ignore */ }
  }
  _wsShouldReconnect = true
  _wsConnect()
}

// ── Persist active graph selection ────────────────────────────────────────
watch(activeGraphId, (id, previousId) => {
  if (previousId && id !== previousId) {
    debugStateGeneration += 1
    sendDebugSubscription(previousId, false)
    debugMode.value = false
    debugOverrides.value = {}
    lastRunOutputs.value = {}
    lastRunInputs.value = {}
    lastRunDebugOutputs.value = {}
    lastRunMetadata.value = null
    _debugBandOutputs = null
  }
  if (id) localStorage.setItem('logic_active_graph', id)
  else localStorage.removeItem('logic_active_graph')
})

// ── Init ───────────────────────────────────────────────────────────────────
onMounted(async () => {
  window.addEventListener('keydown', _onClipboardKeydown)
  window.addEventListener('beforeunload', _onBeforeUnload)
  window.addEventListener(AUTH_TOKEN_REFRESHED_EVENT, _wsReconnectAfterTokenRefresh)
  await store.fetchNodeTypes()
  await store.fetchGraphs()
  _wsConnect()
  // Query-Parameter ?graph=<id> hat Vorrang vor dem gespeicherten letzten Graph
  const queryId = route.query.graph
  const lastId  = localStorage.getItem('logic_active_graph')
  const targetId = queryId || lastId
  if (targetId && store.graphs.find(g => g.id === targetId)) {
    activeGraphId.value = targetId
    await loadGraph()
  }
})

onBeforeUnmount(() => {
  // Leaving the editor within the debounce window must not drop the edit.
  flushUnsentEdits()
})

// A reload or tab close never unmounts the view: start the pending save and
// ask the browser to confirm, which gives that save time to complete. A save
// already queued counts as underway and is not prompted for; behind a slow
// earlier save it may still be cut off by the unload. A save
// already sent is left alone — prompting for it would also block the API
// client's forced redirect to /login when that very save got a 401.
function _onBeforeUnload(event) {
  if (!hasUnsentEdits()) return
  flushUnsentEdits()
  event.preventDefault()
  event.returnValue = ''
}
onUnmounted(() => {
  _wsDisconnect()
  window.removeEventListener('keydown', _onClipboardKeydown)
  window.removeEventListener('beforeunload', _onBeforeUnload)
  window.removeEventListener(AUTH_TOKEN_REFRESHED_EVENT, _wsReconnectAfterTokenRefresh)
  window.removeEventListener('mousemove', _onMinimapMouseMove, { capture: true })
  window.removeEventListener('mouseup',   _onMinimapMouseUp,   { capture: true })
})

// ── Draggable minimap ─────────────────────────────────────────────────────
// MiniMap uses inheritAttrs:false and D3 captures mousedown internally,
// so we attach directly to the DOM element in capture phase and use a
// movement threshold to distinguish a drag from a normal click-to-pan.
const MINIMAP_POS_KEY = 'obs-logic-minimap-pos'
const DRAG_THRESHOLD  = 5

const minimapRef     = ref(null)
const minimapPos     = ref((() => {
  try { return JSON.parse(localStorage.getItem(MINIMAP_POS_KEY)) } catch { return null }
})())
const minimapDragging = ref(false)

const minimapStyle = computed(() => {
  if (!minimapPos.value) return {}
  return { left: minimapPos.value.x + 'px', top: minimapPos.value.y + 'px', right: 'auto', bottom: 'auto' }
})

let _mmDragStart  = null
let _mmIsDragging = false

watchEffect((onCleanup) => {
  const el = minimapRef.value?.$el
  if (!el) return
  el.addEventListener('mousedown', _onMinimapMouseDown, { capture: true })
  onCleanup(() => el.removeEventListener('mousedown', _onMinimapMouseDown, { capture: true }))
})

function _defaultMinimapPos() {
  const rect = canvasWrapper.value?.getBoundingClientRect()
  const w = rect ? rect.width  : window.innerWidth
  const h = rect ? rect.height : window.innerHeight
  return { x: w - 208, y: h - 152 }
}

function _onMinimapMouseDown(e) {
  if (e.button !== 0) return
  _mmIsDragging = false
  const pos = minimapPos.value ?? _defaultMinimapPos()
  _mmDragStart = { mouseX: e.clientX, mouseY: e.clientY, posX: pos.x, posY: pos.y }
  window.addEventListener('mousemove', _onMinimapMouseMove, { capture: true })
  window.addEventListener('mouseup',   _onMinimapMouseUp,   { capture: true })
}

function _onMinimapMouseMove(e) {
  const dx = e.clientX - _mmDragStart.mouseX
  const dy = e.clientY - _mmDragStart.mouseY
  if (!_mmIsDragging) {
    if (Math.hypot(dx, dy) < DRAG_THRESHOLD) return
    _mmIsDragging = true
    minimapDragging.value = true
  }
  e.stopImmediatePropagation()
  const rawX = _mmDragStart.posX + dx
  const rawY = _mmDragStart.posY + dy
  const rect = canvasWrapper.value?.getBoundingClientRect()
  const maxX = rect ? rect.width  - 208 : rawX
  const maxY = rect ? rect.height - 152 : rawY
  minimapPos.value = { x: Math.max(0, Math.min(rawX, maxX)), y: Math.max(0, Math.min(rawY, maxY)) }
}

function _onMinimapMouseUp(e) {
  if (_mmIsDragging) {
    e.stopImmediatePropagation()
    localStorage.setItem(MINIMAP_POS_KEY, JSON.stringify(minimapPos.value))
  }
  _mmIsDragging = false
  minimapDragging.value = false
  window.removeEventListener('mousemove', _onMinimapMouseMove, { capture: true })
  window.removeEventListener('mouseup',   _onMinimapMouseUp,   { capture: true })
}
</script>

<style>
.logic-canvas { background: var(--logic-canvas-bg); }
.logic-canvas .vue-flow__edge-path { stroke: #475569; }
.logic-canvas .vue-flow__handle { width: 10px; height: 10px; border-radius: 50%; }
.logic-controls { bottom: 1rem; left: 1rem; }
.logic-minimap { bottom: 1rem; right: 1rem; background: var(--logic-minimap-bg); border: 1px solid var(--node-card-border); border-radius: 6px; cursor: grab; user-select: none; }
.logic-minimap--dragging { cursor: grabbing; }

/* Crosshair alignment overlay while dragging a block (issue #1118). Position
   via `transform` (set inline, updated every drag event) rather than
   top/left: vue-flow itself moves the dragged node with a transform, which
   the compositor can repaint on its own thread; a top/left-animated overlay
   forces a synchronous layout on every mousemove and visibly falls a step
   behind the node once grid-snapping raises the update rate. */
.logic-crosshair { z-index: 5; }
.logic-crosshair__bar { position: absolute; background: rgba(59, 130, 246, 0.18); will-change: transform; }
.logic-crosshair__bar--h { top: 0; left: 0; right: 0; }
.logic-crosshair__bar--v { top: 0; left: 0; bottom: 0; }

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
