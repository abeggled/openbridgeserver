<template>
  <div class="flex flex-col gap-2">
    <div v-if="entries.length === 0" class="text-xs text-slate-500">
      {{ $t('adapters.allowlist.emptyMeansAny') }}
    </div>

    <div v-for="(entry, index) in entries" :key="index" class="flex items-center gap-2">
      <input
        :value="entry"
        class="input flex-1 font-mono text-sm"
        :class="rowClass(entry)"
        :placeholder="$t('adapters.allowlist.placeholder')"
        :data-testid="`allowlist-entry-${index}`"
        @input="update(index, $event.target.value)"
        @keydown.enter.prevent="addRow"
      />
      <button
        type="button"
        class="btn-secondary px-2 text-sm"
        :title="$t('adapters.allowlist.removeEntry')"
        :aria-label="$t('adapters.allowlist.removeEntry')"
        :data-testid="`allowlist-remove-${index}`"
        @click="removeRow(index)"
      >
        ✕
      </button>
    </div>

    <div class="flex items-center gap-3">
      <button type="button" class="btn-secondary btn-sm" data-testid="allowlist-add" @click="addRow">
        {{ $t('adapters.allowlist.addEntry') }}
      </button>
      <p v-if="invalidCount > 0" class="text-xs text-red-400" data-testid="allowlist-invalid">
        {{ $t('adapters.allowlist.invalidEntries', { n: invalidCount }) }}
      </p>
    </div>

    <p class="hint">{{ $t('adapters.allowlist.hint') }}</p>
  </div>
</template>

<script setup>
import { computed } from 'vue'
import { classifyEntry, normalizeEntries } from '@/utils/ipAllowlist'

const props = defineProps({
  modelValue: { type: [Array, String, null], default: () => [] },
})

const emit = defineEmits(['update:modelValue'])

// A configuration stored before this field became a list still arrives as a
// comma-separated string, so the editor accepts both shapes and always emits
// a list — the same tolerance the backend schema has.
const entries = computed(() => normalizeEntries(props.modelValue))

const invalidCount = computed(
  () => entries.value.filter(entry => entry.trim() !== '' && classifyEntry(entry) === 'invalid').length,
)

function rowClass(entry) {
  if (entry.trim() === '') return ''
  return classifyEntry(entry) === 'invalid' ? 'border-red-400 dark:border-red-500' : ''
}

function commit(next) {
  emit('update:modelValue', next)
}

function update(index, value) {
  const next = [...entries.value]
  next[index] = value
  commit(next)
}

function addRow() {
  commit([...entries.value, ''])
}

function removeRow(index) {
  commit(entries.value.filter((_, i) => i !== index))
}
</script>
