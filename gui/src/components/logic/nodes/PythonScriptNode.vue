<template>
  <div class="gn-wrap" @mouseenter="hovered = true" @mouseleave="hovered = false">

    <Handle type="target" id="a" :position="Position.Left" :style="{ top: '33%' }" />
    <Handle type="target" id="b" :position="Position.Left" :style="{ top: '52%' }" />
    <Handle type="target" id="c" :position="Position.Left" :style="{ top: '71%' }" />

    <div class="gn-card logic-node-surface" :style="{ '--node-tint': cardTint }">
      <div class="gn-header">
        <NodeTitleEditor
          :value="customLabel"
          :fallback="$t('logic.nodeTypes.python_script')"
          :editable="auth.isAdmin"
          :title-class="['gn-label', customLabel && 'gn-label--custom']"
          @rename="renameNode"
        />
        <button v-show="hovered" class="gn-delete nodrag" @click.stop="remove" :title="$t('logic.deleteBlock')">✕</button>
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
        <span class="gn-port-label" style="margin-left:auto;align-self:center;">{{ $t('logic.ports.result') }}</span>
      </div>
      <div v-if="data._dbg" class="gn-debug" :title="data._dbg_title || data._dbg" data-testid="debug-band">{{ data._dbg }}</div>
    </div>

    <Handle type="source" id="result" :position="Position.Right" class="gn-handle-out" :style="{ top: '52%' }" />

  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { Handle, Position, useVueFlow } from '@vue-flow/core'
import { nodeTint } from '@/utils/logicNodeSurface'
import NodeTitleEditor from '@/components/logic/NodeTitleEditor.vue'
import { useAuthStore } from '@/stores/auth'

const props = defineProps({
  id:   { type: String, required: true },
  type: { type: String, required: true },
  data: { type: Object, default: () => ({}) },
})

// Script category colour — also the top border and header accent below.
// Tinted over the opaque card surface (issue #1074).
const SCRIPT_COLOR = '#be185d'
const cardTint = nodeTint(SCRIPT_COLOR)

const shortScript = computed(() => {
  const s = props.data.script || '# script'
  return s.length > 80 ? s.slice(0, 77) + '…' : s
})

const hovered = ref(false)
const { removeNodes, updateNodeData } = useVueFlow()
function remove() { removeNodes([props.id]) }

// ── User-defined block name (issue #1157) ─────────────────────────────────
const customLabel = computed(() => String(props.data?.label ?? '').trim())
function renameNode(label) { updateNodeData(props.id, { label }) }
// Only admins can save a sheet, so offering the inline field to a read-only
// viewer would silently discard whatever they typed.
const auth = useAuthStore()
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
  width: 180px;
  /* background: provided by `.logic-node-surface` (opaque surface + tint). */
  border: 1px solid var(--node-card-border);
  border-top: 3px solid #be185d;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0,0,0,.4);
  position: relative;
  z-index: 1;
}
.gn-header  { display:flex; align-items:center; justify-content:space-between; padding:5px 10px; background:rgba(190,24,93,.18); border-radius:5px 5px 0 0; }
.gn-label   { min-width:0; overflow:hidden; text-overflow:ellipsis; white-space:nowrap; font-size:11px; font-weight:700; text-transform:uppercase; letter-spacing:.06em; color:var(--node-title-color); }
/* A user-chosen name keeps its own casing — the uppercase treatment is for
   the generated type titles. */
.gn-label--custom { text-transform:none; letter-spacing:.02em; }
.gn-delete  { flex-shrink:0; font-size:11px; color:var(--node-del-color); background:none; border:none; cursor:pointer; padding:0 2px; line-height:1; transition:color .15s; }
.gn-delete:hover { color:#f87171; }
.gn-body    { padding: 6px 10px 4px; }
.script-preview { font-size:10px; color:var(--node-script-color); font-family:ui-monospace,monospace; white-space:pre-wrap; max-height:55px; overflow:hidden; margin:0; }
.gn-ports   { padding: 2px 10px 6px; display:flex; align-items:center; }
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
