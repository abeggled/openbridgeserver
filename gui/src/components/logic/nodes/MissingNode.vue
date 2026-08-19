<template>
  <div class="missing-node logic-node-surface" :style="{ '--node-tint': cardTint }">
    <Handle v-for="h in inputs" :key="h.id" type="target" :id="h.id" :position="Position.Left" />
    <div class="missing-node__body">
      <span class="missing-node__badge" :aria-label="$t('logic.missingNode.ariaLabel')">!</span>
      <div>
        <div class="missing-node__title">{{ $t('logic.missingNode.title') }}</div>
        <div class="missing-node__type">{{ data.original_type ?? data.label }}</div>
      </div>
    </div>
    <Handle v-for="h in outputs" :key="h.id" type="source" :id="h.id" :position="Position.Right" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'
import { nodeTint } from '@/utils/logicNodeSurface'

const props = defineProps({ data: { type: Object, default: () => ({}) } })

// Error category colour — also the dashed border and badge below.
// Tinted over the opaque card surface (issue #1074).
const MISSING_COLOR = '#ef4444'
const cardTint = nodeTint(MISSING_COLOR)

const inputs  = computed(() => [{ id: 'in' }])
const outputs = computed(() => [{ id: 'out' }])
</script>

<style scoped>
.missing-node {
  position: relative;
  min-width: 180px;
  border: 2px dashed #ef4444;
  border-radius: 8px;
  padding: 10px 14px;
  /* background: provided by `.logic-node-surface` (opaque surface + tint). */
}
.missing-node__body {
  display: flex;
  align-items: center;
  gap: 8px;
}
.missing-node__badge {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  width: 22px;
  height: 22px;
  border-radius: 50%;
  background: #ef4444;
  color: #fff;
  font-weight: 700;
  font-size: 0.85rem;
  flex-shrink: 0;
}
.missing-node__title {
  font-size: 0.7rem;
  font-weight: 600;
  color: #ef4444;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.missing-node__type {
  font-size: 0.75rem;
  color: var(--node-summary-color);
  margin-top: 1px;
  word-break: break-all;
}
</style>
