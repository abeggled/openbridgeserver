<template>
  <div class="gn-wrap" @mouseenter="hovered = true" @mouseleave="hovered = false">

    <template v-if="isWrite">
      <Handle type="target" id="value"   :position="Position.Left" :style="{ top: port1Top }" />
      <Handle type="target" id="trigger" :position="Position.Left" :style="{ top: port2Top }" />
    </template>

    <div class="gn-card logic-node-surface" ref="cardRef" :style="{ '--node-tint': cardTint }">
      <div class="gn-header">
        <NodeTitleEditor
          :value="customLabel"
          :fallback="defaultLabel"
          :editable="auth.isAdmin"
          :title-class="['gn-label', customLabel && 'gn-label--custom']"
          @rename="renameNode"
        />
        <span v-if="hasFilter" class="gn-filter-badge" :title="$t('logic.filterBadge')">⊘</span>
        <button v-show="hovered" class="gn-delete nodrag" @click.stop="remove" :title="$t('logic.deleteBlock')">✕</button>
      </div>
      <div class="gn-body">
        <div class="gn-sublabel">{{ $t('logic.ports.object') }}</div>
        <div class="dp-name" :class="data.datapoint_name ? 'active' : 'empty'">
          {{ data.datapoint_name || $t('logic.notSelected') }}
        </div>
      </div>
      <div class="gn-ports">
        <div v-if="isWrite" class="gn-port-col">
          <span ref="portRef1" class="gn-port-label">{{ $t('logic.ports.value') }}</span>
          <span ref="portRef2" class="gn-port-label">{{ $t('logic.ports.trigger') }}</span>
        </div>
        <div v-else class="gn-port-col" style="margin-left:auto;align-items:flex-end;">
          <span ref="portRef1" class="gn-port-label">{{ $t('logic.ports.value') }}</span>
          <span ref="portRef2" class="gn-port-label">{{ $t('logic.ports.changed') }}</span>
        </div>
      </div>
      <div v-if="data._dbg" class="gn-debug" :title="data._dbg_title || data._dbg" data-testid="debug-band">{{ data._dbg }}</div>
    </div>

    <template v-if="!isWrite">
      <Handle type="source" id="value"   :position="Position.Right" class="gn-handle-out" :style="{ top: port1Top }" />
      <Handle type="source" id="changed" :position="Position.Right" class="gn-handle-out" :style="{ top: port2Top }" />
    </template>

  </div>
</template>

<script setup>
import { ref, computed, onMounted, watch, nextTick } from 'vue'
import { Handle, Position, useVueFlow } from '@vue-flow/core'
import { useI18n } from 'vue-i18n'
import { nodeTint } from '@/utils/logicNodeSurface'
import NodeTitleEditor from '@/components/logic/NodeTitleEditor.vue'
import { useAuthStore } from '@/stores/auth'

const { t } = useI18n()

const props = defineProps({
  id:   { type: String, required: true },
  type: { type: String, required: true },
  data: { type: Object, default: () => ({}) },
})

// Category colour of the datapoint blocks — also used for the top border and
// the header accent below. Tinted over the opaque card surface (issue #1074).
const DATAPOINT_COLOR = '#0f766e'
const cardTint = nodeTint(DATAPOINT_COLOR)

const isWrite   = computed(() => props.type === 'datapoint_write')
const hovered   = ref(false)
const { removeNodes, updateNodeData } = useVueFlow()
function remove() { removeNodes([props.id]) }

// ── User-defined block name (issue #1157) ─────────────────────────────────
// Several "Read Object" blocks on one sheet are otherwise indistinguishable.
// The name lives in `data.label`, separate from the generated node id.
const defaultLabel = computed(() => t(isWrite.value ? 'logic.nodeTypes.datapoint_write' : 'logic.nodeTypes.datapoint_read'))
const customLabel  = computed(() => String(props.data?.label ?? '').trim())
function renameNode(label) { updateNodeData(props.id, { label }) }
// Only admins can save a sheet, so offering the inline field to a read-only
// viewer would silently discard whatever they typed.
const auth = useAuthStore()

// ── Handle positioning: align with port labels ────────────────────────────
// Use offsetTop (relative to offsetParent) instead of getBoundingClientRect()
// to avoid viewport-dependent values that break when VueFlow re-renders nodes.
const cardRef  = ref(null)
const portRef1 = ref(null)
const portRef2 = ref(null)
const port1Top = ref('50%')
const port2Top = ref('75%')

function updateHandlePositions() {
  nextTick(() => {
    if (!cardRef.value || !portRef1.value || !portRef2.value) return
    const cardTop = cardRef.value.offsetTop
    port1Top.value = `${cardTop + portRef1.value.offsetTop + portRef1.value.offsetHeight / 2}px`
    port2Top.value = `${cardTop + portRef2.value.offsetTop + portRef2.value.offsetHeight / 2}px`
  })
}

onMounted(updateHandlePositions)
watch(() => props.data._dbg, updateHandlePositions)

const hasFilter = computed(() => {
  const d = props.data
  return !!(
    (d.value_formula     && d.value_formula.trim())    ||
    (d.value_map         && typeof d.value_map === 'object' && Object.keys(d.value_map).length) ||
    d.trigger_on_change === 'true'                     ||
    d.only_on_change    === 'true'                     ||
    (d.min_delta        && d.min_delta    !== '')       ||
    (d.min_delta_pct    && d.min_delta_pct !== '')      ||
    (d.throttle_value   && d.throttle_value !== '')
  )
})
</script>

<style scoped>
.gn-wrap { position: relative; }

.gn-wrap :deep(.vue-flow__handle) {
  z-index: 20;
  width: 12px;
  height: 12px;
  background: var(--handle-in-bg);
  border: 2px solid var(--handle-border);
  border-radius: 50%;
  cursor: crosshair;
}
.gn-wrap :deep(.vue-flow__handle.gn-handle-out) {
  background: var(--handle-out-bg);
}
.gn-wrap :deep(.vue-flow__handle:hover) {
  background: #38bdf8;
  box-shadow: 0 0 0 4px rgba(56, 189, 248, 0.35);
}

.gn-card {
  width: 160px;
  /* background: provided by `.logic-node-surface` (opaque surface + tint). */
  border: 1px solid var(--node-card-border);
  border-top: 3px solid #0f766e;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,.25);
  position: relative;
  z-index: 1;
}
.gn-header {
  display: flex; align-items: center; gap: 4px;
  padding: 5px 10px;
  background: rgba(15,118,110,.15);
  border-radius: 5px 5px 0 0;
}
.gn-label        { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:var(--node-title-color); }
/* A user-chosen name keeps its own casing — the uppercase treatment is for
   the generated type titles. */
.gn-label--custom { text-transform:none; letter-spacing:.02em; }
.gn-filter-badge { font-size:9px; color:#fbbf24; opacity:.85; flex-shrink:0; }
.gn-delete       { font-size:11px; color:var(--node-del-color); background:none; border:none; cursor:pointer; padding:0 2px; line-height:1; transition:color .15s; margin-left:auto; flex-shrink:0; }
.gn-delete:hover { color:#f87171; }
.gn-body   { padding: 6px 10px 2px; }
.gn-sublabel { font-size:9px; color:var(--node-port-label); text-transform:uppercase; letter-spacing:.05em; margin-bottom:2px; }
.dp-name   { font-size:11px; font-weight:500; max-width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dp-name.active { color:var(--node-dp-active); }
.dp-name.empty  { color:var(--node-card-border); font-style:italic; }
.gn-ports  { padding: 2px 10px 6px; display:flex; }
.gn-port-col { display:flex; flex-direction:column; gap:2px; }
.gn-port-label { font-size:9px; color:var(--node-port-label); }
.gn-debug {
  box-sizing: border-box;
  width: 100%;
  min-width: 0;
  font-size: 9px;
  color: var(--node-debug-color);
  font-family: ui-monospace, monospace;
  padding: 2px 10px 5px;
  border-top: 1px solid var(--node-card-border);
  background: var(--node-debug-bg);
  border-radius: 0 0 6px 6px;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
}
</style>
