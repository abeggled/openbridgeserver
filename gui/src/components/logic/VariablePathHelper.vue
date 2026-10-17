<script setup>
// "Insert variable" menu, resolved path and unresolved-variable hint for a
// variable-enabled text field of a logic block (issue #1301).
import { computed } from 'vue'
import { LOGIC_DATE_VARIABLES, LOGIC_STANDARD_VARIABLES, unresolvedVariables, variableToken } from '@/utils/logicVariables'

const props = defineProps({
  path: { type: String, default: '' },
  resolved: { type: String, default: '' },
  obsSlots: { type: Array, default: () => [] },
})
const emit = defineEmits(['insert'])

const unresolved = computed(() => unresolvedVariables(props.path, props.obsSlots))
const showResolved = computed(() => !!props.resolved && props.resolved !== props.path)

function onChange(event) {
  const name = event.target.value
  event.target.value = ''
  if (name) emit('insert', variableToken(name))
}
</script>

<template>
  <div class="flex flex-col gap-1">
    <select class="input text-xs" data-testid="variable-insert-select" @change="onChange">
      <option value="">{{ $t('logic.variables.insert') }}</option>
      <optgroup :label="$t('logic.variables.groupStandard')">
        <option v-for="name in LOGIC_STANDARD_VARIABLES" :key="name" :value="name">{{ variableToken(name) }}</option>
      </optgroup>
      <optgroup :label="$t('logic.variables.groupDateTime')">
        <option v-for="name in LOGIC_DATE_VARIABLES" :key="name" :value="name">{{ variableToken(name) }}</option>
      </optgroup>
      <optgroup v-if="obsSlots.length" :label="$t('logic.variables.groupObjects')">
        <option v-for="slot in obsSlots" :key="slot" :value="`OBS${slot}`">{{ variableToken(`OBS${slot}`) }}</option>
      </optgroup>
    </select>
    <p v-if="showResolved" class="text-xs text-slate-400 font-mono break-all" data-testid="variable-resolved-path">
      {{ $t('logic.variables.resolvedPath') }}: {{ resolved }}
    </p>
    <p v-if="unresolved.length" class="text-xs text-amber-400/80" data-testid="variable-unresolved">
      {{ $t('logic.variables.unresolved', { names: unresolved.map(n => variableToken(n)).join(', ') }) }}
    </p>
  </div>
</template>
