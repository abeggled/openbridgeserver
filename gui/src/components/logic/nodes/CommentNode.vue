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

    <div class="cn-card" :style="{ width: width + 'px', height: height + 'px' }">
      <div class="cn-header">
        <span class="cn-title">{{ label }}</span>
        <button class="cn-del nodrag" :style="{ visibility: hovered ? 'visible' : 'hidden' }" @click.stop="remove">✕</button>
      </div>
      <div class="cn-body">
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

const { updateNodeData, removeNodes } = useVueFlow()
const { t, te } = useI18n()

const props = defineProps({
  id:       { type: String, required: true },
  type:     { type: String, required: true },
  data:     { type: Object, default: () => ({}) },
  selected: { type: Boolean, default: false },
})

const hovered = ref(false)

const label = computed(() => (te('logic.nodeTypes.' + props.type) ? t('logic.nodeTypes.' + props.type) : props.type))
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
  background: #ca8a0412;
  overflow: hidden;
}

.cn-header {
  display: flex;
  align-items: center;
  justify-content: space-between;
  padding: 4px 10px;
  background: #ca8a0428;
  flex-shrink: 0;
}
.cn-title { font-size: 11px; font-weight: 700; text-transform: uppercase; letter-spacing: .06em; color: var(--node-title-color); }
.cn-del   { font-size: 11px; color: var(--node-del-color); background: none; border: none; cursor: pointer; padding: 0 2px; line-height: 1; }
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
