<template>
  <div class="gn-wrap" @mouseenter="hovered = true" @mouseleave="hovered = false">

    <Handle type="target" id="a" :position="Position.Left" :style="{ top: '33%' }" />
    <Handle type="target" id="b" :position="Position.Left" :style="{ top: '52%' }" />
    <Handle type="target" id="c" :position="Position.Left" :style="{ top: '71%' }" />

    <div class="gn-card">
      <div class="gn-header">
        <span class="gn-label">Python Script</span>
        <button v-show="hovered" class="gn-delete nodrag" @click.stop="remove" title="Block löschen">✕</button>
      </div>
      <div class="gn-body">
        <pre class="script-preview">{{ shortScript }}</pre>
      </div>
      <div class="gn-ports">
        <div class="gn-port-col">
          <span class="gn-port-label">a</span>
          <span class="gn-port-label">b</span>
          <span class="gn-port-label">c</span>
        </div>
        <span class="gn-port-label" style="margin-left:auto;align-self:center;">Ergebnis</span>
      </div>
    </div>

    <Handle type="source" id="result" :position="Position.Right" class="gn-handle-out" :style="{ top: '52%' }" />

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

const shortScript = computed(() => {
  const s = props.data.script || '# script'
  return s.length > 80 ? s.slice(0, 77) + '…' : s
})

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
  min-width: 180px;
  background: #1e293b;
  border: 1px solid #334155;
  border-top: 3px solid #be185d;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,.4);
  position: relative;
  z-index: 1;
}
.gn-header  { display:flex; align-items:center; justify-content:space-between; padding:5px 10px; background:rgba(190,24,93,.18); border-radius:5px 5px 0 0; }
.gn-label   { font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:#f1f5f9; }
.gn-delete  { font-size:11px; color:#64748b; background:none; border:none; cursor:pointer; padding:0 2px; line-height:1; transition:color .15s; }
.gn-delete:hover { color:#f87171; }
.gn-body    { padding: 6px 10px 4px; }
.script-preview { font-size:10px; color:#f9a8d4; font-family:ui-monospace,monospace; white-space:pre-wrap; max-height:55px; overflow:hidden; margin:0; }
.gn-ports   { padding: 2px 10px 6px; display:flex; align-items:center; }
.gn-port-col { display:flex; flex-direction:column; gap:2px; }
.gn-port-label { font-size:9px; color:#64748b; }
</style>
