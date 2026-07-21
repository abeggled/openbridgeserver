<template>
  <aside class="w-[28rem] max-w-[45vw] flex-shrink-0 border-l-4 border-amber-400 bg-surface-800 flex flex-col shadow-[-6px_0_16px_rgba(245,158,11,0.12)]" data-testid="debug-inspector">
    <header class="flex items-start gap-3 p-4 border-b border-slate-200 dark:border-slate-700">
      <div class="min-w-0 flex-1">
        <h3 class="font-semibold truncate">{{ node.data?.label || node.id }}</h3>
        <p class="text-xs text-slate-500">{{ $t('logic.debugInspector.title') }}</p>
      </div>
      <button class="btn-icon" :title="$t('logic.debugInspector.close')" @click="$emit('close')">×</button>
    </header>

    <div class="flex-1 overflow-y-auto p-4 space-y-5">
      <section>
        <div class="flex justify-between items-center mb-2">
          <h4 class="text-xs font-bold uppercase tracking-wide text-slate-500">{{ $t('logic.debugInspector.inputs') }}</h4>
          <button v-if="hasOverrides" class="text-xs text-red-400" @click="$emit('clear-all')">{{ $t('logic.debugInspector.clearAll') }}</button>
        </div>
        <p v-if="!inputs.length" class="text-sm text-slate-500">{{ $t('logic.debugInspector.noValues') }}</p>
        <div v-for="input in inputs" :key="input.id" class="mb-3 rounded border border-slate-200 dark:border-slate-700 p-3">
          <div class="flex items-center gap-2 mb-2">
            <span class="text-sm font-medium">{{ input.label }}</span>
            <span v-if="input.overridden" class="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-500">{{ $t('logic.debugInspector.overridden') }}</span>
            <button v-if="input.overridden" class="ml-auto text-xs text-red-400" @click="$emit('clear-override', input.id)">{{ $t('common.delete') }}</button>
          </div>
          <ValueView :value="input.incoming" :label="$t('logic.debugInspector.incoming')" />
          <label class="block text-[11px] text-slate-500 mt-2 mb-1">{{ $t('logic.debugInspector.override') }}</label>
          <textarea :value="input.overrideText" class="input w-full min-h-20 font-mono text-xs" :placeholder="$t('logic.debugInspector.overridePlaceholder')" @input="$emit('set-override', input.id, $event.target.value)" />
        </div>
      </section>

      <section>
        <div class="flex justify-between items-center mb-2">
          <h4 class="text-xs font-bold uppercase tracking-wide text-slate-500">{{ $t('logic.debugInspector.outputs') }}</h4>
          <button v-if="outputEntries.length" class="text-xs text-blue-400" @click="copyPayload">{{ payloadCopied ? $t('logic.debugInspector.copied') : $t('logic.debugInspector.copyAll') }}</button>
        </div>
        <p v-if="!outputEntries.length" class="text-sm text-slate-500">{{ $t('logic.debugInspector.noExecution') }}</p>
        <ValueView v-for="([key, value]) in outputEntries" :key="key" :value="value" :label="key" class="mb-3" />
      </section>

      <section v-if="metadata" class="text-xs text-slate-500 border-t border-slate-200 dark:border-slate-700 pt-3 space-y-1">
        <div>{{ $t('logic.debugInspector.timestamp') }}: {{ metadata.timestamp || '—' }}</div>
        <div>{{ $t('logic.debugInspector.duration') }}: {{ metadata.duration_ms ?? '—' }} ms</div>
        <div v-if="metadata.used_overrides" class="text-amber-500">{{ $t('logic.debugInspector.overrideExecution') }}</div>
      </section>
    </div>
  </aside>
</template>

<script setup>
import { computed, ref, onUnmounted } from 'vue'
import ValueView from './DebugValueView.vue'

const props = defineProps({ node: { type: Object, required: true }, inputs: { type: Array, default: () => [] }, outputs: { type: Object, default: () => ({}) }, metadata: { type: Object, default: null } })
defineEmits(['close', 'set-override', 'clear-override', 'clear-all'])
const outputEntries = computed(() => Object.entries(props.outputs || {}))
const hasOverrides = computed(() => props.inputs.some(input => input.overridden))
const payloadCopied = ref(false)
let copiedTimer = null

async function copyPayload() {
  await navigator.clipboard.writeText(JSON.stringify({ inputs: props.inputs, outputs: props.outputs, metadata: props.metadata }, null, 2))
  payloadCopied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { payloadCopied.value = false }, 1600)
}
onUnmounted(() => clearTimeout(copiedTimer))
</script>
