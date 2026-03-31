<template>
  <div class="gn-wrap" @mouseenter="hovered = true" @mouseleave="hovered = false">

    <template v-if="isWrite">
      <Handle type="target" id="value"   :position="Position.Left" :style="{ top: '45%' }" />
      <Handle type="target" id="trigger" :position="Position.Left" :style="{ top: '68%' }" />
    </template>

    <div class="gn-card">
      <div class="gn-header">
        <span class="gn-label">{{ isWrite ? 'DP Schreiben' : 'DP Lesen' }}</span>
        <span v-if="hasFilter" class="gn-filter-badge" title="Filter / Transformation aktiv">⊘</span>
        <button v-show="hovered" class="gn-delete nodrag" @click.stop="remove" title="Block löschen">✕</button>
      </div>
      <div class="gn-body">
        <div class="gn-sublabel">DataPoint</div>
        <div class="dp-name" :class="data.datapoint_name ? 'active' : 'empty'">
          {{ data.datapoint_name || '— nicht gewählt —' }}
        </div>
      </div>
      <div class="gn-ports">
        <div v-if="isWrite" class="gn-port-col">
          <span class="gn-port-label">Wert</span>
          <span class="gn-port-label">Trigger</span>
        </div>
        <div v-else class="gn-port-col" style="margin-left:auto;align-items:flex-end;">
          <span class="gn-port-label">Wert</span>
          <span class="gn-port-label">Geändert</span>
        </div>
      </div>
      <div v-if="data._dbg" class="gn-debug">{{ data._dbg }}</div>
    </div>

    <template v-if="!isWrite">
      <Handle type="source" id="value"   :position="Position.Right" class="gn-handle-out" :style="{ top: '45%' }" />
      <Handle type="source" id="changed" :position="Position.Right" class="gn-handle-out" :style="{ top: '68%' }" />
    </template>

  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Handle, Position, useVueFlow } from '@vue-flow/core'

const props = defineProps({
  id:   { type: String, required: true },
  type: { type: String, required: true },
  data: { type: Object, default: () => ({}) },
})

const isWrite   = computed(() => props.type === 'datapoint_write')
const hovered   = ref(false)
const { removeNodes } = useVueFlow()
function remove() { removeNodes([props.id]) }

const hasFilter = computed(() => {
  const d = props.data
  return !!(
    (d.value_formula     && d.value_formula.trim())    ||
    d.trigger_on_change === 'true'                     ||
    d.only_on_change    === 'true'                     ||
    (d.min_delta        && d.min_delta    !== '')       ||
    (d.min_delta_pct    && d.min_delta_pct !== '')      ||
    (d.throttle_ms      && d.throttle_ms  !== '')
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
  min-width: 160px;
  background: var(--node-card-bg);
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
.gn-label        { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:var(--node-title-color); }
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
