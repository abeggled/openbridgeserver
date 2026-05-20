<template>
  <Combobox
    :model-value="modelValue"
    :multi="true"
    :placeholder="placeholder"
    :fetch-suggestions="fetchSuggestions"
    :display-items="displayItems"
    empty-text="Keine Tags gefunden"
    @update:modelValue="onUpdate"
  />
</template>

<script setup>
import { computed, onMounted } from 'vue'
import Combobox from '@/components/ui/Combobox.vue'
import { useDatapointStore } from '@/stores/datapoints'

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  placeholder: { type: String, default: 'Tag wählen …' },
})
const emit = defineEmits(['update:modelValue'])

const store = useDatapointStore()

onMounted(() => {
  if (!store.allTags?.length) {
    store.loadTags().catch(() => {})
  }
})

const displayItems = computed(() =>
  (store.allTags || []).map((t) => ({ id: t, label: t })),
)

async function fetchSuggestions(q) {
  const all = (store.allTags || []).map((t) => ({ id: t, label: t }))
  const needle = (q || '').toLowerCase()
  if (!needle) return all
  return all.filter((t) => t.id.toLowerCase().includes(needle))
}

function onUpdate(val) {
  emit('update:modelValue', Array.isArray(val) ? val : [])
}
</script>
