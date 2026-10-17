<template>
  <!-- The category tint rides on top of the opaque theme surface via
       `--node-tint` (see `.logic-node-surface` in style.css) so the canvas
       raster cannot show through the block body (issue #1074). -->
  <div :class="['logic-node logic-node-surface rounded-lg border shadow-lg min-w-[140px]', borderClass]"
       :style="{ borderTopColor: color, borderTopWidth: '3px', '--node-tint': cardTint }">
    <!-- Header -->
    <div class="px-3 py-1.5 flex items-center gap-2"
         :style="{ background: color + '22' }">
      <span class="text-xs font-bold uppercase tracking-wide" :style="{ color: 'var(--node-title-color)' }">{{ label }}</span>
    </div>
    <!-- Slots for handles and content injected by parent -->
    <div class="px-3 py-2 flex flex-col gap-1 text-xs" :style="{ color: 'var(--node-port-label)' }">
      <slot />
    </div>
  </div>
</template>
<script setup>
import { computed } from 'vue'
import { nodeTint } from '@/utils/logicNodeSurface'

const props = defineProps({
  label: { type: String, required: true },
  color: { type: String, default: '#475569' },
  borderClass: { type: String, default: 'border-slate-600' },
})

const cardTint = computed(() => nodeTint(props.color))
</script>
