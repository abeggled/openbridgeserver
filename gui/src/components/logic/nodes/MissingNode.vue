<template>
  <div class="missing-node">
    <Handle v-for="h in inputs" :key="h.id" type="target" :id="h.id" :position="Position.Left" />
    <div class="missing-node__body">
      <span class="missing-node__icon">⚠️</span>
      <div>
        <div class="missing-node__title">Fehlender Block</div>
        <div class="missing-node__type">{{ data.original_type ?? data.label }}</div>
      </div>
    </div>
    <Handle v-for="h in outputs" :key="h.id" type="source" :id="h.id" :position="Position.Right" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { Handle, Position } from '@vue-flow/core'

const props = defineProps({ data: { type: Object, default: () => ({}) } })

// Fehlende Blöcke zeigen je einen Eingang und einen Ausgang
const inputs  = computed(() => [{ id: 'in' }])
const outputs = computed(() => [{ id: 'out' }])
</script>

<style scoped>
.missing-node {
  position: relative;
  min-width: 180px;
  border: 2px dashed #f59e0b;
  border-radius: 8px;
  background: rgba(245, 158, 11, 0.08);
  padding: 10px 14px;
}
.missing-node__body {
  display: flex;
  align-items: center;
  gap: 8px;
}
.missing-node__icon { font-size: 1.2rem; }
.missing-node__title {
  font-size: 0.7rem;
  font-weight: 600;
  color: #f59e0b;
  text-transform: uppercase;
  letter-spacing: 0.04em;
}
.missing-node__type {
  font-size: 0.75rem;
  color: #94a3b8;
  margin-top: 1px;
  word-break: break-all;
}
</style>
