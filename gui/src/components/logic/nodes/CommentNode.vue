<template>
  <div class="cn-root" @mouseenter="hovered = true" @mouseleave="hovered = false">
    <NodeResizer
      :min-width="160"
      :min-height="80"
      :is-visible="selected || hovered"
      line-class-name="cn-resize-line"
      handle-class-name="cn-resize-handle"
      @resize="onResize"
    />

    <div class="cn-card logic-node-surface" :style="{ width: width + 'px', height: height + 'px', '--node-tint': cardTint }">
      <div class="cn-header">
        <NodeTitleEditor
          :value="customLabel"
          :fallback="defaultLabel"
          :editable="auth.isAdmin"
          :title-class="['cn-title', customLabel && 'cn-title--custom']"
          @rename="renameNode"
        />
        <button class="cn-del nodrag" :style="{ visibility: hovered ? 'visible' : 'hidden' }" @click.stop="remove">✕</button>
      </div>
      <div class="cn-body nowheel">
        <pre v-if="data.text" class="cn-text">{{ data.text }}</pre>
        <span v-else class="cn-placeholder">{{ $t('logic.nodeConfig.comment.empty') }}</span>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, computed } from 'vue'
import { useVueFlow } from '@vue-flow/core'
import { NodeResizer } from '@vue-flow/node-resizer'
import { useI18n } from 'vue-i18n'
import { nodeTint } from '@/utils/logicNodeSurface'
import NodeTitleEditor from '@/components/logic/NodeTitleEditor.vue'
import { useAuthStore } from '@/stores/auth'

const { updateNodeData, removeNodes } = useVueFlow()
const { t, te } = useI18n()

const props = defineProps({
  id:       { type: String, required: true },
  type:     { type: String, required: true },
  data:     { type: Object, default: () => ({}) },
  selected: { type: Boolean, default: false },
})

const hovered = ref(false)

// Comment category colour — also the card border and header accent below.
// Tinted over the opaque card surface (issue #1074).
const COMMENT_COLOR = '#ca8a04'
const cardTint = nodeTint(COMMENT_COLOR)

const defaultLabel = computed(() => (te('logic.nodeTypes.' + props.type) ? t('logic.nodeTypes.' + props.type) : props.type))

// ── User-defined block name (issue #1157) ─────────────────────────────────
// A sheet with several comment boxes shows the same "KOMMENTAR" header on all
// of them, and the config panel offers the rename field for every block type —
// so this card has to honour the name like the function-block cards do.
const customLabel = computed(() => String(props.data?.label ?? '').trim())
function renameNode(label) { updateNodeData(props.id, { label }) }
const auth = useAuthStore()
const width  = computed(() => Number(props.data?.width)  || 220)
const height = computed(() => Number(props.data?.height) || 140)

function onResize({ params }) {
  updateNodeData(props.id, { width: Math.round(params.width), height: Math.round(params.height) })
}

function remove() {
  removeNodes([props.id])
}
</script>

<style scoped>
.cn-root { position: relative; }

.cn-card {
  display: flex;
  flex-direction: column;
  border: 1px solid #ca8a04;
  border-radius: 8px;
  box-shadow: 0 4px 14px rgba(0,0,0,.3);
  overflow: hidden;
  /* background: provided by `.logic-node-surface` (opaque surface + tint). */
}

.cn-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  background: #ca8a0428;
  flex-shrink: 0;
}
.cn-title { min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--node-title-color); }
/* A user-chosen name keeps its own casing — the uppercase treatment is for
   the generated type titles. */
.cn-title--custom { text-transform: none; letter-spacing: .02em; }
.cn-del   { flex-shrink: 0; font-size: 11px; color: var(--node-del-color); background: none; border: none; cursor: pointer; padding: 0 2px; line-height: 1; }
.cn-del:hover { color: #f87171; }

.cn-body {
  flex: 1;
  min-height: 0;
  padding: 8px 10px;
  overflow-y: auto;
}

.cn-text {
  margin: 0;
  font-family: inherit;
  font-size: 12px;
  line-height: 1.4;
  color: var(--node-title-color);
  white-space: pre-wrap;
  word-break: break-word;
}

.cn-placeholder {
  font-size: 11px;
  font-style: italic;
  color: var(--node-summary-color);
}

.cn-root :deep(.cn-resize-line) { border-color: #ca8a04; }
.cn-root :deep(.cn-resize-handle) { background: #ca8a04; border-color: #713f12; }
</style>
