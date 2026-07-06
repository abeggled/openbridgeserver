<template>
  <Combobox
    :model-value="modelValue"
    :multi="true"
    :placeholder="effectivePlaceholder"
    :fetch-suggestions="fetchSuggestions"
    :display-items="displayItems"
    :empty-text="$t('knxDevices.noDeviceMatches')"
    @update:modelValue="onUpdate"
  >
    <template #chip="{ item, index, remove }">
      <slot name="chip" :item="item" :index="index" :remove="remove">
        <span class="truncate">{{ item.label }}</span>
      </slot>
    </template>

    <template #item="{ item, active, selected }">
      <span class="font-mono text-xs text-blue-700 dark:text-blue-300 shrink-0 w-14">{{ item.id }}</span>
      <span class="flex-1 min-w-0 truncate">{{ item.label }}</span>
      <span v-if="item.manufacturer" class="text-xs text-slate-500 shrink-0">{{ item.manufacturer }}</span>
      <span v-if="selected" class="sr-only">{{ $t('common.selected') }}</span>
      <span v-if="active" class="sr-only">{{ $t('common.active') }}</span>
    </template>
  </Combobox>
</template>

<script setup>
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Combobox from '@/components/ui/Combobox.vue'
import { knxprojApi } from '@/api/client'

const { t } = useI18n()

const props = defineProps({
  modelValue: { type: Array, default: () => [] },
  placeholder: { type: String, default: null },
})
const emit = defineEmits(['update:modelValue'])

const effectivePlaceholder = computed(() => props.placeholder ?? t('knxDevices.deviceSelectPlaceholder'))
const knownItems = ref(new Map())

function normalizeDevice(device) {
  const pa = String(device?.pa ?? device?.physical_address ?? '').trim()
  if (!pa) return null
  const name = String(device?.name ?? '').trim()
  const manufacturer = String(device?.manufacturer ?? '').trim()
  const orderNumber = String(device?.order_number ?? '').trim()
  const detail = [manufacturer, orderNumber].filter(Boolean).join(' ')
  return {
    ...device,
    id: pa,
    label: name || detail || pa,
    manufacturer,
    order_number: orderNumber,
  }
}

function rememberDevice(device) {
  const normalized = normalizeDevice(device)
  if (!normalized) return null
  knownItems.value.set(normalized.id, normalized)
  return normalized
}

async function hydrateUnknownIds(ids) {
  const unknown = (ids || []).filter((id) => id && !knownItems.value.has(id))
  if (!unknown.length) return
  await Promise.all(
    unknown.map(async (id) => {
      try {
        const { data } = await knxprojApi.getDevice(id)
        rememberDevice(data)
      } catch {
        knownItems.value.set(id, { id, label: id })
      }
    }),
  )
}

onMounted(() => {
  void hydrateUnknownIds(props.modelValue)
})

watch(
  () => Array.isArray(props.modelValue) ? props.modelValue.join('|') : '',
  () => {
    void hydrateUnknownIds(props.modelValue)
  },
)

const displayItems = computed(() => Array.from(knownItems.value.values()))

async function fetchSuggestions(q) {
  try {
    const { data } = await knxprojApi.listDevices({
      q: q || '',
      page: 0,
      size: 50,
    })
    const devices = Array.isArray(data?.items) ? data.items : []
    return devices.map(rememberDevice).filter(Boolean)
  } catch {
    return []
  }
}

function onUpdate(val) {
  emit('update:modelValue', Array.isArray(val) ? val : [])
}
</script>
