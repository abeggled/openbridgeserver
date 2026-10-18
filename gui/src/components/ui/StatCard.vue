<template>
  <div class="card pt-4 pb-4 pl-4 flex items-center gap-4 relative" :class="helpId ? 'pr-10' : 'pr-4'">
    <div :class="['w-10 h-10 rounded-xl flex items-center justify-center text-xl shrink-0', bgClass]">
      {{ icon }}
    </div>
    <div class="min-w-0">
      <div class="text-xs text-slate-500 font-medium uppercase tracking-wide">{{ label }}</div>
      <div class="text-xl font-bold text-slate-900 dark:text-slate-100 mt-0.5">{{ value }}</div>
    </div>
    <HelpButton v-if="helpId" :help-id="helpId" class="absolute top-1 right-1" />
  </div>
</template>

<script setup>
import { computed } from 'vue'
import HelpButton from '@/components/ui/HelpButton.vue'
const props = defineProps({
  label: String,
  value: [String, Number],
  icon: String,
  color: { type: String, default: 'blue' },
  helpId: { type: String, default: null },
})
const bgs = { blue: 'bg-blue-500/15', green: 'bg-green-500/15', red: 'bg-red-500/15', amber: 'bg-amber-500/15' }
const bgClass = computed(() => bgs[props.color] ?? bgs.blue)
</script>
