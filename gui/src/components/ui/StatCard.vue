<template>
  <div class="card p-4 flex items-center gap-4">
    <div :class="['w-10 h-10 rounded-xl flex items-center justify-center text-xl shrink-0', bgClass]">
      {{ icon }}
    </div>
    <div class="min-w-0">
      <div class="text-xs text-slate-500 font-medium uppercase tracking-wide">{{ label }}</div>
      <div class="text-xl font-bold text-slate-900 dark:text-slate-100 mt-0.5">{{ value }}</div>
    </div>
  </div>
</template>

<script setup>
import { computed } from 'vue'
const props = defineProps({ label: String, value: [String, Number], icon: String, color: { type: String, default: 'blue' } })
const bgs = { blue: 'bg-blue-500/15', green: 'bg-green-500/15', red: 'bg-red-500/15', amber: 'bg-amber-500/15' }
const bgClass = computed(() => bgs[props.color] ?? bgs.blue)
</script>
