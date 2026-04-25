<script setup lang="ts">
import { reactive, watch } from 'vue'

type UhrModus = 'digital' | 'analog' | 'wortuhr'

interface UhrConfig {
  mode:        UhrModus
  showSeconds: boolean
  showDate:    boolean
  color:       string
  label:       string
}

const MODI: { value: UhrModus; label: string }[] = [
  { value: 'digital', label: 'Digital' },
  { value: 'analog',  label: 'Analog'  },
  { value: 'wortuhr', label: 'Wortuhr' },
]

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive<UhrConfig>({
  mode:        (props.modelValue.mode        as UhrModus | undefined) ?? 'digital',
  showSeconds: (props.modelValue.showSeconds as boolean  | undefined) ?? false,
  showDate:    (props.modelValue.showDate    as boolean  | undefined) ?? false,
  color:       (props.modelValue.color       as string   | undefined) ?? '#3b82f6',
  label:       (props.modelValue.label       as string   | undefined) ?? '',
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-4 text-sm">

    <!-- Modus -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Modus</label>
      <div class="flex gap-1">
        <button
          v-for="m in MODI"
          :key="m.value"
          type="button"
          :class="[
            'flex-1 py-1.5 text-xs rounded border transition-colors',
            cfg.mode === m.value
              ? 'border-blue-500 bg-blue-500/20 text-blue-300'
              : 'border-gray-700 text-gray-400 hover:border-gray-500',
          ]"
          @click="cfg.mode = m.value"
        >{{ m.label }}</button>
      </div>
    </div>

    <!-- Farbe -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Akzentfarbe</label>
      <div class="flex items-center gap-2">
        <input
          v-model="cfg.color"
          type="color"
          class="w-8 h-8 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
          title="Akzentfarbe"
        />
        <span class="text-xs text-gray-500 font-mono">{{ cfg.color }}</span>
      </div>
    </div>

    <!-- Sekunden anzeigen (analog + digital) -->
    <div v-if="cfg.mode !== 'wortuhr'">
      <label class="flex items-center gap-2 cursor-pointer select-none">
        <input
          v-model="cfg.showSeconds"
          type="checkbox"
          class="w-4 h-4 rounded accent-blue-500"
        />
        <span class="text-xs text-gray-300">Sekunden anzeigen</span>
      </label>
    </div>

    <!-- Datum anzeigen (nur digital) -->
    <div v-if="cfg.mode === 'digital'">
      <label class="flex items-center gap-2 cursor-pointer select-none">
        <input
          v-model="cfg.showDate"
          type="checkbox"
          class="w-4 h-4 rounded accent-blue-500"
        />
        <span class="text-xs text-gray-300">Datum anzeigen</span>
      </label>
    </div>

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">
        Beschriftung
        <span class="text-gray-600 font-normal ml-1">(optional)</span>
      </label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Wohnzimmer"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

  </div>
</template>
