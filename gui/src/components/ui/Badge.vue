<template>
  <span :class="classes">
    <span v-if="dot" class="w-1.5 h-1.5 rounded-full" :class="dotColor" />
    <slot />
  </span>
</template>

<script setup>
import { computed } from 'vue'

const props = defineProps({
  variant: { type: String, default: 'default' },  // default | success | warning | danger | info | muted
  dot:     { type: Boolean, default: false },
  size:    { type: String, default: 'sm' },
})

const map = {
  default: 'bg-slate-700/60 text-slate-300 border-slate-600/50',
  success: 'bg-green-500/15 text-green-400 border-green-500/30',
  warning: 'bg-amber-500/15 text-amber-400 border-amber-500/30',
  danger:  'bg-red-500/15 text-red-400 border-red-500/30',
  info:    'bg-blue-500/15 text-blue-400 border-blue-500/30',
  muted:   'bg-slate-800 text-slate-500 border-slate-700/40',
}
const dotMap = {
  default: 'bg-slate-400',
  success: 'bg-green-400',
  warning: 'bg-amber-400',
  danger:  'bg-red-400',
  info:    'bg-blue-400',
  muted:   'bg-slate-500',
}

const classes  = computed(() => `inline-flex items-center gap-1 border rounded-full font-medium ${props.size === 'xs' ? 'text-xs px-2 py-0.5' : 'text-xs px-2.5 py-0.5'} ${map[props.variant] ?? map.default}`)
const dotColor = computed(() => dotMap[props.variant] ?? dotMap.default)
</script>
