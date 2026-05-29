<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import DataPointPicker from '@/components/DataPointPicker.vue'

interface RTRConfig {
  label:             string
  gradient_colors:   string[]
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
  variant:           'heating' | 'ac'
}

const HEATING_MODES = [
  { value: 0, label: 'widgets.rtr.modeAuto'        },
  { value: 1, label: 'widgets.rtr.modeKomfort'     },
  { value: 2, label: 'widgets.rtr.modeStandby'     },
  { value: 3, label: 'widgets.rtr.modeEconomy'     },
  { value: 4, label: 'widgets.rtr.modeFrostschutz' },
]

const AC_MODES = [
  { value:  0, label: 'widgets.rtr.modeAutomatik'   },
  { value:  1, label: 'widgets.rtr.modeHeizen'      },
  { value:  3, label: 'widgets.rtr.modeKuehlen'     },
  { value:  6, label: 'widgets.rtr.modeAus'         },
  { value:  9, label: 'widgets.rtr.modeNurLuefter'  },
  { value: 14, label: 'widgets.rtr.modeEntfeuchten' },
]

const VARIANT_DEFAULT_MODES: Record<'heating' | 'ac', number[]> = {
  heating: HEATING_MODES.map(m => m.value),
  ac:      AC_MODES.map(m => m.value),
}

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive<RTRConfig>({
  label:             (props.modelValue.label             as string         | undefined) ?? '',
  gradient_colors:   (() => {
    const gc = props.modelValue.gradient_colors as string[] | undefined
    if (gc && gc.length > 0) return gc
    return [(props.modelValue.color as string | undefined) ?? '#ef4444']
  })(),
  min_temp:          (props.modelValue.min_temp          as number         | undefined) ?? 5,
  max_temp:          (props.modelValue.max_temp          as number         | undefined) ?? 35,
  step:              (props.modelValue.step              as number         | undefined) ?? 0.5,
  decimals:          (props.modelValue.decimals          as number         | undefined) ?? 1,
  setpoint_offset:   (props.modelValue.setpoint_offset   as number         | undefined) ?? 0,
  actual_offset:     (props.modelValue.actual_offset     as number         | undefined) ?? 0,
  actual_temp_dp_id: (props.modelValue.actual_temp_dp_id as string | null  | undefined) ?? null,
  mode_dp_id:        (props.modelValue.mode_dp_id        as string | null  | undefined) ?? null,
  show_modes:        (props.modelValue.show_modes        as boolean        | undefined) ?? true,
  supported_modes:   (props.modelValue.supported_modes   as number[]       | undefined) ?? [0, 1, 2, 3, 4],
  variant:           (props.modelValue.variant           as 'heating' | 'ac' | undefined) ?? 'heating',
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })

watch(() => cfg.variant, (newVariant) => {
  cfg.supported_modes = [...VARIANT_DEFAULT_MODES[newVariant]]
})

const currentAllModes = computed(() => cfg.variant === 'ac' ? AC_MODES : HEATING_MODES)

const dptNote = computed(() =>
  cfg.variant === 'ac'
    ? 'KNX DPT 20.105 · Automatik=0, Heizen=1, Kühlen=3, Aus=6, Nur Lüfter=9, Entfeuchten=14'
    : 'KNX DPT 20.102 · Auto=0, Komfort=1, Standby=2, Economy=3, Frostschutz=4',
)

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
        {{ $t('widgets.common.label') }} <span class="text-gray-600 font-normal ml-1">{{ $t('widgets.rtr.optional') }}</span>
      </label>
      <input
        v-model="cfg.label"
        type="text"
        :placeholder="$t('widgets.rtr.labelPlaceholder')"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Farbverlauf -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.rtr.gradientColors') }}</label>
      <div class="flex items-center gap-2 flex-wrap">
        <input
          v-for="(_, i) in cfg.gradient_colors"
          :key="i"
          v-model="cfg.gradient_colors[i]"
          type="color"
          class="w-8 h-8 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
          :title="$t('widgets.rtr.colorIndex', { n: i + 1 })"
        />
        <button
          v-if="cfg.gradient_colors.length < 4"
          type="button"
          class="text-xs text-blue-400 hover:text-blue-300 px-2 py-1 border border-gray-700 rounded"
          @click="cfg.gradient_colors.push('#6b7280')"
        >+</button>
        <button
          v-if="cfg.gradient_colors.length > 1"
          type="button"
          class="text-xs text-red-400 hover:text-red-300 px-2 py-1 border border-gray-700 rounded"
          @click="cfg.gradient_colors.splice(cfg.gradient_colors.length - 1, 1)"
        >−</button>
      </div>
    </div>

    <!-- Temperaturbereich -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.rtr.tempRange') }}</label>
      <div class="grid grid-cols-4 gap-2">
        <div>
          <label class="block text-xs text-gray-500 mb-1">{{ $t('widgets.rtr.min') }}</label>
          <input
            v-model.number="cfg.min_temp"
            type="number"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">{{ $t('widgets.rtr.max') }}</label>
          <input
            v-model.number="cfg.max_temp"
            type="number"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">{{ $t('widgets.rtr.step') }}</label>
          <input
            v-model.number="cfg.step"
            type="number"
            min="0.1"
            step="0.1"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">{{ $t('widgets.rtr.dez') }}</label>
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
        {{ $t('widgets.rtr.displayCorrection') }}
        <span class="text-gray-600 font-normal ml-1">{{ $t('widgets.rtr.displayCorrectionHint') }}</span>
      </label>
      <div class="grid grid-cols-2 gap-2">
        <div>
          <label class="block text-xs text-gray-500 mb-1">{{ $t('widgets.rtr.setpointCorrection') }}</label>
          <input
            v-model.number="cfg.setpoint_offset"
            type="number"
            step="0.1"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div>
          <label class="block text-xs text-gray-500 mb-1">{{ $t('widgets.rtr.actualCorrection') }}</label>
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
        :label="$t('widgets.rtr.dpActualTemp')"
        :compatible-types="['FLOAT', 'INTEGER']"
        @update:model-value="(id) => (cfg.actual_temp_dp_id = id)"
      />
    </div>

    <!-- Steuerungsvariante -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.rtr.variant') }}</label>
      <div class="flex gap-2">
        <button
          type="button"
          :class="[
            'flex-1 text-xs px-2 py-1.5 rounded border transition-colors',
            cfg.variant === 'heating'
              ? 'border-blue-500 bg-blue-500/20 text-blue-300'
              : 'border-gray-700 text-gray-400 hover:border-gray-500',
          ]"
          @click="cfg.variant = 'heating'"
        >{{ $t('widgets.rtr.variantHeating') }}</button>
        <button
          type="button"
          :class="[
            'flex-1 text-xs px-2 py-1.5 rounded border transition-colors',
            cfg.variant === 'ac'
              ? 'border-blue-500 bg-blue-500/20 text-blue-300'
              : 'border-gray-700 text-gray-400 hover:border-gray-500',
          ]"
          @click="cfg.variant = 'ac'"
        >{{ $t('widgets.rtr.variantAc') }}</button>
      </div>
    </div>

    <!-- Betriebsart-Datenpunkt -->
    <div>
      <DataPointPicker
        :model-value="cfg.mode_dp_id"
        :label="$t('widgets.rtr.dpMode')"
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
        <span class="text-xs text-gray-300">{{ $t('widgets.rtr.showModes') }}</span>
      </label>

      <div v-if="cfg.show_modes">
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.rtr.supportedModes') }}</label>
        <div class="flex flex-wrap gap-1">
          <button
            v-for="m in currentAllModes"
            :key="m.value"
            type="button"
            :class="[
              'text-xs px-2 py-1 rounded border transition-colors',
              cfg.supported_modes.includes(m.value)
                ? 'border-blue-500 bg-blue-500/20 text-blue-300'
                : 'border-gray-700 text-gray-400 hover:border-gray-500',
            ]"
            @click="toggleMode(m.value)"
          >{{ $t(m.label) }}</button>
        </div>
        <p class="text-xs text-gray-600 mt-1">{{ dptNote }}</p>
      </div>
    </div>

  </div>
</template>
