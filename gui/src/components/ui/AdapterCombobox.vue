<template>
  <Combobox
    :model-value="modelValue"
    :multi="true"
    :placeholder="placeholder"
    :fetch-suggestions="fetchSuggestions"
    :display-items="displayItems"
    empty-text="Keine konfigurierten Adapter"
    @update:modelValue="onUpdate"
  >
    <!-- Chip slot: flag adapter_types that are no longer configured. Remove
         (×) is rendered by the surrounding Combobox wrapper. -->
    <template #chip="{ item }">
      <span :class="['truncate', item.is_orphan ? 'line-through text-amber-500 dark:text-amber-400' : '']"
            :title="item.is_orphan ? 'Adapter ist nicht (mehr) konfiguriert — Filter trifft auf neue Einträge nicht zu.' : ''">
        {{ item.label || item.id }}
        <span v-if="item.is_orphan" class="ml-0.5">⚠</span>
      </span>
    </template>
  </Combobox>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import Combobox from '@/components/ui/Combobox.vue'
import { adapterApi } from '@/api/client'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  placeholder: { type: String, default: 'Adapter wählen …' },
})
const emit = defineEmits(['update:modelValue'])

// Adapter TYPES that have at least one configured INSTANCE. Earlier this used
// adapterApi.list() (all supported types), which let users pick adapters they
// never configured — those filters then never matched anything. We now derive
// the choice from /adapters/instances and de-dupe to one entry per adapter_type.
const configuredTypes = ref([])

async function load() {
  try {
    const { data } = await adapterApi.listInstances()
    const arr = Array.isArray(data) ? data : []
    const byType = new Map()
    for (const inst of arr) {
      const t = inst?.adapter_type
      if (!t) continue
      if (!byType.has(t)) {
        // Use the adapter_type as both id and a human label. If at some point
        // the backend ships a friendlier label per type, it can be merged in
        // here without changing the consumer contract.
        byType.set(t, { id: t, label: t })
      }
    }
    configuredTypes.value = Array.from(byType.values()).sort((a, b) => a.id.localeCompare(b.id))
  } catch {
    configuredTypes.value = []
  }
}

onMounted(() => {
  load()
})

// Make sure currently-selected adapter types that are NOT in the configured
// list (e.g. an adapter was inactivated / deleted after the filterset was
// saved) still appear in displayItems so the chip has a label. They get an
// `is_orphan: true` flag the chip slot uses to render a strike-through hint.
const displayItems = computed(() => {
  const known = new Set(configuredTypes.value.map((t) => t.id))
  const orphans = (Array.isArray(props.modelValue) ? props.modelValue : [])
    .filter((id) => id && !known.has(id))
    .map((id) => ({ id, label: id, is_orphan: true }))
  return [...configuredTypes.value, ...orphans]
})

async function fetchSuggestions(q) {
  if (!configuredTypes.value.length) await load()
  const needle = (q || '').toLowerCase()
  if (!needle) return configuredTypes.value
  return configuredTypes.value.filter(
    (t) =>
      t.id.toLowerCase().includes(needle) ||
      (t.label || '').toLowerCase().includes(needle),
  )
}

// Reload when the model-value gains new ids we haven't seen — covers the case
// where the editor opens an existing set whose adapter was just (re)configured.
watch(
  () => Array.isArray(props.modelValue) ? props.modelValue.join('|') : '',
  () => {
    if (!configuredTypes.value.length) void load()
  },
)

function onUpdate(val) {
  emit('update:modelValue', Array.isArray(val) ? val : [])
}
</script>
