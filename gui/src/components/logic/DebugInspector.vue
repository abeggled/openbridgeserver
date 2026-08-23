<!--
  Debug values pane — rendered as the "Debug values" tab of the block config
  panel (issue #1128), so settings stay reachable while debug mode is on. The
  amber left edge keeps the visual debug-mode marker of the former standalone
  inspector; the panel header supplies the title and the close button.
-->
<template>
  <div class="flex-1 min-h-0 overflow-y-auto border-l-4 border-amber-400 p-4 space-y-5" data-testid="debug-inspector">
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
          <button v-if="input.locallyOverridden" class="ml-auto text-xs text-red-400" @click="$emit('clear-override', input.id)">{{ $t('common.delete') }}</button>
        </div>
        <ValueView :value="input.incoming" :label="$t('logic.debugInspector.incoming')" />
        <ValueView v-if="input.capturedOverridden" :value="input.effective" :label="$t('logic.debugInspector.effective')" class="mt-2" />
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
</template>

<script setup>
import { computed, ref, onUnmounted } from 'vue'
import { copyText } from '@/utils/clipboard'
import ValueView from './DebugValueView.vue'

const props = defineProps({ inputs: { type: Array, default: () => [] }, outputs: { type: Object, default: () => ({}) }, metadata: { type: Object, default: null }, hasOverrides: { type: Boolean, default: false } })
defineEmits(['set-override', 'clear-override', 'clear-all'])
const outputEntries = computed(() => Object.entries(props.outputs || {}))
const payloadCopied = ref(false)
let copiedTimer = null

async function copyPayload() {
  await copyText(JSON.stringify({ inputs: props.inputs, outputs: props.outputs, metadata: props.metadata }, null, 2))
  payloadCopied.value = true
  clearTimeout(copiedTimer)
  copiedTimer = setTimeout(() => { payloadCopied.value = false }, 1600)
}
onUnmounted(() => clearTimeout(copiedTimer))
</script>
