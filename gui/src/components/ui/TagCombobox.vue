<template>
  <Combobox
    :model-value="modelValue"
    :multi="true"
    :placeholder="effectivePlaceholder"
    :fetch-suggestions="fetchSuggestions"
    :display-items="displayItems"
    :empty-text="$t('common.noTagsFound')"
    @update:modelValue="onUpdate"
  />
</template>

<script setup>
import { computed, onMounted } from 'vue'
import { useI18n } from 'vue-i18n'
import Combobox from '@/components/ui/Combobox.vue'
import { useDatapointStore } from '@/stores/datapoints'

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  placeholder: { type: String, default: null },
})
const emit = defineEmits(['update:modelValue'])

const effectivePlaceholder = computed(() => props.placeholder ?? t('common.tagSelectPlaceholder'))

const store = useDatapointStore()

onMounted(() => {
  if (!store.allTags?.length) {
    store.loadTags().catch(() => {})
  }
})

const displayItems = computed(() =>
  (store.allTags || []).map((tag) => ({ id: tag, label: tag })),
)

async function fetchSuggestions(q) {
  const all = (store.allTags || []).map((tag) => ({ id: tag, label: tag }))
  const needle = (q || '').toLowerCase()
  if (!needle) return all
  return all.filter((tag) => tag.id.toLowerCase().includes(needle))
}

function onUpdate(val) {
  emit('update:modelValue', Array.isArray(val) ? val : [])
}
</script>
