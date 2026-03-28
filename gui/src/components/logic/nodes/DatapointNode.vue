<template>
  <div class="gn-wrap" @mouseenter="hovered = true" @mouseleave="hovered = false">

    <template v-if="isWrite">
      <Handle type="target" id="value"   :position="Position.Left" :style="{ top: '45%' }" />
      <Handle type="target" id="trigger" :position="Position.Left" :style="{ top: '68%' }" />
    </template>

    <div class="gn-card">
      <div class="gn-header">
        <span class="gn-label">{{ isWrite ? 'DP Schreiben' : 'DP Lesen' }}</span>
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

const isWrite = computed(() => props.type === 'datapoint_write')
const hovered = ref(false)
const { removeNodes } = useVueFlow()
function remove() { removeNodes([props.id]) }
</script>

<style scoped>
.gn-wrap { position: relative; }

.gn-wrap :deep(.vue-flow__handle) {
  z-index: 20;
  width: 12px;
  height: 12px;
  background: #94a3b8;
  border: 2px solid #0f172a;
  border-radius: 50%;
  cursor: crosshair;
}
.gn-wrap :deep(.vue-flow__handle.gn-handle-out) {
  background: #60a5fa;
}
.gn-wrap :deep(.vue-flow__handle:hover) {
  background: #38bdf8;
  transform: translate(-50%, -50%) scale(1.3);
}

.gn-card {
  min-width: 160px;
  background: #1e293b;
  border: 1px solid #334155;
  border-top: 3px solid #0f766e;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,.4);
  position: relative;
  z-index: 1;
}
.gn-header {
  display: flex; align-items: center; justify-content: space-between;
  padding: 5px 10px;
  background: rgba(15,118,110,.18);
  border-radius: 5px 5px 0 0;
}
.gn-label  { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:#f1f5f9; }
.gn-delete { font-size:11px; color:#64748b; background:none; border:none; cursor:pointer; padding:0 2px; line-height:1; transition:color .15s; }
.gn-delete:hover { color:#f87171; }
.gn-body   { padding: 6px 10px 2px; }
.gn-sublabel { font-size:9px; color:#64748b; text-transform:uppercase; letter-spacing:.05em; margin-bottom:2px; }
.dp-name   { font-size:11px; font-weight:500; max-width:150px; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.dp-name.active { color:#5eead4; }
.dp-name.empty  { color:#334155; font-style:italic; }
.gn-ports  { padding: 2px 10px 6px; display:flex; }
.gn-port-col { display:flex; flex-direction:column; gap:2px; }
.gn-port-label { font-size:9px; color:#64748b; }
</style>
