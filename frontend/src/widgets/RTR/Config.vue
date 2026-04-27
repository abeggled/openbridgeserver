<script setup lang="ts">
import { reactive, watch } from 'vue'
import DataPointPicker from '@/components/DataPointPicker.vue'

interface RTRConfig {
  label:             string
  color:             string
  min_temp:          number
  max_temp:          number
  step:              number
  decimals:          number
  setpoint_offset:   number
  actual_offset:     number
  actual_temp_dp_id: string | null
  mode_dp_id:        string | null
  show_modes:        boolean
  supported_modes:   number[]
}

const ALL_MODES = [
  { value: 0, label: 'Auto'   },
  { value: 1, label: 'Heizen' },
  { value: 3, label: 'Kühlen' },
  { value: 6, label: 'Aus'    },
]

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive<RTRConfig>({
  label:             (props.modelValue.label             as string   | undefined) ?? '',
  color:             (props.modelValue.color             as string   | undefined) ?? '#ef4444',
  min_temp:          (props.modelValue.min_temp          as number   | undefined) ?? 5,
  max_temp:          (props.modelValue.max_temp          as number   | undefined) ?? 35,
  step:              (props.modelValue.step              as number   | undefined) ?? 0.5,
  decimals:          (props.modelValue.decimals          as number   | undefined) ?? 1,
  setpoint_offset:   (props.modelValue.setpoint_offset   as number   | undefined) ?? 0,
  actual_offset:     (props.modelValue.actual_offset     as number   | undefined) ?? 0,
  actual_temp_dp_id: (props.modelValue.actual_temp_dp_id as string   | null | undefined) ?? null,
  mode_dp_id:        (props.modelValue.mode_dp_id        as string   | null | undefined) ?? null,
  show_modes:        (props.modelValue.show_modes        as boolean  | undefined) ?? true,
  supported_modes:   (props.modelValue.supported_modes   as number[] | undefined) ?? [0, 1, 3, 6],
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })

function toggleMode(value: number) {
  if (cfg.supported_modes.includes(value)) {
    cfg.supported_modes = cfg.supported_modes.filter(v => v !== value)
  } else {
    cfg.supported_modes = [...cfg.supported_modes, value].sort((a, b) => a - b)
  }
}
</script>

<template>
  <div class="space-y-4 text-sm">

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">
        Beschriftung <span class="text-gray-600 font-normal ml-1">(optional)</span>
      </label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Wohnzimmer"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Akzentfarbe -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Akzentfarbe</label>
      <div class="flex items-center gap-2">
        <input
          v-model="cfg.color"
          type="color"
          class="w-8 h-8 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
        />
        <span class="text-xs text-gray-500 font-mono">{{ cfg.color }}</span>
      </div>
    </div>

    <!-- Temperaturbereich -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Temperaturbereich</label>
      <div class="grid grid-cols-4 gap-2">
        <div>
          <label class="block text-xs text-gray-500 mb-1">Min</label>
          <input
            v-model.number="cfg.min_temp"
            type="number"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Max</label>
          <input
            v-model.number="cfg.max_temp"
            type="number"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Schritt</label>
          <input
            v-model.number="cfg.step"
            type="number"
            min="0.1"
            step="0.1"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Dez.</label>
          <input
            v-model.number="cfg.decimals"
            type="number"
            min="0"
            max="3"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
    </div>

    <!-- Korrekturen (Offset für Anzeige) -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">
        Anzeigekorrektur
        <span class="text-gray-600 font-normal ml-1">(addiert auf den angezeigten Wert)</span>
      </label>
      <div class="grid grid-cols-2 gap-2">
        <div>
          <label class="block text-xs text-gray-500 mb-1">Soll-Korrektur</label>
          <input
            v-model.number="cfg.setpoint_offset"
            type="number"
            step="0.1"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">Ist-Korrektur</label>
          <input
            v-model.number="cfg.actual_offset"
            type="number"
            step="0.1"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
    </div>

    <!-- Isttemperatur-Datenpunkt -->
    <div>
      <DataPointPicker
        :model-value="cfg.actual_temp_dp_id"
        label="Isttemperatur-Objekt (optional)"
        :compatible-types="['FLOAT', 'INTEGER']"
        @update:model-value="(id) => (cfg.actual_temp_dp_id = id)"
      />
    </div>

    <!-- Betriebsart-Datenpunkt -->
    <div>
      <DataPointPicker
        :model-value="cfg.mode_dp_id"
        label="Betriebsart-Objekt (optional)"
        :compatible-types="['INTEGER']"
        @update:model-value="(id) => (cfg.mode_dp_id = id)"
      />
    </div>

    <!-- Betriebsart-Buttons anzeigen -->
    <div v-if="cfg.mode_dp_id">
      <label class="flex items-center gap-2 cursor-pointer select-none mb-2">
        <input
          v-model="cfg.show_modes"
          type="checkbox"
          class="w-4 h-4 rounded accent-blue-500"
        />
        <span class="text-xs text-gray-300">Betriebsart-Buttons anzeigen</span>
      </label>

      <div v-if="cfg.show_modes">
        <label class="block text-xs text-gray-400 mb-1">Unterstützte Modi</label>
        <div class="flex flex-wrap gap-1">
          <button
            v-for="m in ALL_MODES"
            :key="m.value"
            type="button"
            :class="[
              'text-xs px-2 py-1 rounded border transition-colors',
              cfg.supported_modes.includes(m.value)
                ? 'border-blue-500 bg-blue-500/20 text-blue-300'
                : 'border-gray-700 text-gray-400 hover:border-gray-500',
            ]"
            @click="toggleMode(m.value)"
          >{{ m.label }}</button>
        </div>
        <p class="text-xs text-gray-600 mt-1">KNX DPT 20.102 · Auto=0, Heizen=1, Kühlen=3, Aus=6</p>
      </div>
    </div>

  </div>
</template>
