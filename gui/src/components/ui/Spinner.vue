<template>
  <svg
    :class="['animate-spin', sizeClass, colorClass]"
    xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24"
  >
    <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"/>
    <path  class="opacity-75" fill="currentColor"
      d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4z"/>
  </svg>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({
  size:  { type: String, default: 'md' },   // xs | sm | md | lg
  color: { type: String, default: 'blue' }, // blue | white | slate
})
const sizes  = { xs: 'w-3 h-3', sm: 'w-4 h-4', md: 'w-5 h-5', lg: 'w-8 h-8' }
const colors = { blue: 'text-blue-500', white: 'text-white', slate: 'text-slate-400' }
const sizeClass  = computed(() => sizes[props.size]  ?? sizes.md)
const colorClass = computed(() => colors[props.color] ?? colors.blue)
</script>
