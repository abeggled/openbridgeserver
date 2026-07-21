<template>
  <div class="rounded bg-slate-100 dark:bg-slate-900/60 p-2 min-w-0">
    <div class="flex items-center gap-2 mb-1">
      <span class="text-[11px] font-semibold text-slate-500 truncate">{{ label }}</span>
      <span class="ml-auto text-[10px] text-slate-500">{{ type }} · {{ size }}</span>
      <button class="text-xs text-blue-400 min-w-12 text-right" :title="$t('logic.debugInspector.copy')" @click="copy">{{ copied ? $t('logic.debugInspector.copied') : '⧉' }}</button>
    </div>
    <pre class="font-mono text-xs whitespace-pre-wrap break-words max-h-64 overflow-auto">{{ formatted }}</pre>
  </div>
</template>

<script setup>
import { computed, ref, onUnmounted } from 'vue'
const props = defineProps({ value: { default: null }, label: { type: String, required: true } })
const type = computed(() => Array.isArray(props.value) ? 'array' : props.value === null ? 'null' : typeof props.value)
const formatted = computed(() => typeof props.value === 'string' ? props.value : JSON.stringify(props.value, null, 2) ?? String(props.value))
const size = computed(() => `${new Blob([formatted.value]).size} B`)
const copied = ref(false)
let copiedTimer = null
async function copy() {
  await navigator.clipboard.writeText(formatted.value)
  copied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { copied.value = false }, 1600)
}
onUnmounted(() => clearTimeout(copiedTimer))
</script>
