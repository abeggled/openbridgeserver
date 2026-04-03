<script setup lang="ts">
import { reactive, watch } from 'vue'
import DataPointPicker from '@/components/DataPointPicker.vue'

interface ExtraDatapoint {
  id: string
  label: string
  unit: string
  decimals: number
}

const props = defineProps<{
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, unknown>): void
}>()

const MAX_EXTRA = 4

function makeExtra(src?: Partial<ExtraDatapoint>): ExtraDatapoint {
  return {
    id: src?.id ?? '',
    label: src?.label ?? '',
    unit: src?.unit ?? '',
    decimals: src?.decimals ?? 1,
  }
}

const existingExtras = (props.modelValue.extra_datapoints as ExtraDatapoint[] | undefined) ?? []

const cfg = reactive({
  label: (props.modelValue.label as string) ?? '',
  unit: (props.modelValue.unit as string) ?? '',
  decimals: (props.modelValue.decimals as number) ?? 1,
  extra_datapoints: Array.from({ length: MAX_EXTRA }, (_, i) => makeExtra(existingExtras[i])),
})

watch(
  cfg,
  () => {
    emit('update:modelValue', {
      label: cfg.label,
      unit: cfg.unit,
      decimals: cfg.decimals,
      extra_datapoints: cfg.extra_datapoints.filter((e) => !!e.id),
    })
  },
  { deep: true },
)
</script>

<template>
  <div class="space-y-3">
    <!-- Hauptwert -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Beschriftung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Vorlauftemperatur"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>
    <div>
      <label class="block text-xs text-gray-400 mb-1">Einheit (überschreibt Objekt-Einheit)</label>
      <input
        v-model="cfg.unit"
        type="text"
        placeholder="z.B. °C"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>
    <div>
      <label class="block text-xs text-gray-400 mb-1">Dezimalstellen</label>
      <input
        v-model.number="cfg.decimals"
        type="number"
        min="0"
        max="6"
        class="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Extra-Objekte -->
    <div class="pt-1">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
        Zusätzliche Werte (max. {{ MAX_EXTRA }})
      </p>
      <div
        v-for="(extra, i) in cfg.extra_datapoints"
        :key="i"
        class="border border-gray-700 rounded p-2 space-y-2 mb-2"
      >
        <p class="text-xs text-gray-500">Wert {{ i + 1 }}</p>
        <DataPointPicker
          :model-value="extra.id || null"
          :compatible-types="['*']"
          @update:model-value="(id) => (extra.id = id ?? '')"
        />
        <div v-if="extra.id" class="space-y-1.5">
          <input
            v-model="extra.label"
            type="text"
            placeholder="Beschriftung"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
          <input
            v-model="extra.unit"
            type="text"
            placeholder="Einheit"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
          <input
            v-model.number="extra.decimals"
            type="number"
            min="0"
            max="6"
            placeholder="Dezimalstellen"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
    </div>
  </div>
</template>
