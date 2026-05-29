<script setup lang="ts">
import { reactive, computed, watch } from 'vue'
import DataPointPicker from '@/components/DataPointPicker.vue'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive({
  label:                (props.modelValue.label                as string)  ?? '',
  mode:                 (props.modelValue.mode                 as string)  ?? 'rolladen',
  invert:               (props.modelValue.invert               as boolean) ?? false,
  invert_move_up:       (props.modelValue.invert_move_up       as boolean) ?? false,
  invert_move_down:     (props.modelValue.invert_move_down     as boolean) ?? false,
  dp_move_up:           (props.modelValue.dp_move_up           as string)  ?? '',
  dp_move_down:         (props.modelValue.dp_move_down         as string)  ?? '',
  dp_stop:              (props.modelValue.dp_stop              as string)  ?? '',
  dp_position:          (props.modelValue.dp_position          as string)  ?? '',
  dp_position_status:   (props.modelValue.dp_position_status   as string)  ?? '',
  dp_slat:              (props.modelValue.dp_slat              as string)  ?? '',
  dp_slat_status:       (props.modelValue.dp_slat_status       as string)  ?? '',
  // Sperre & Statusindikatoren
  dp_lock:              (props.modelValue.dp_lock              as string)  ?? '',
  dp_status_1:          (props.modelValue.dp_status_1          as string)  ?? '',
  dp_status_2:          (props.modelValue.dp_status_2          as string)  ?? '',
  dp_status_3:          (props.modelValue.dp_status_3          as string)  ?? '',
  dp_status_4:          (props.modelValue.dp_status_4          as string)  ?? '',
  label_status_1:       (props.modelValue.label_status_1       as string)  ?? '',
  label_status_2:       (props.modelValue.label_status_2       as string)  ?? '',
  label_status_3:       (props.modelValue.label_status_3       as string)  ?? '',
  label_status_4:       (props.modelValue.label_status_4       as string)  ?? '',
})

const isJalousie = computed(() => cfg.mode === 'jalousie')

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-3">
    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ $t('widgets.common.label') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Schlafzimmer Süd"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Modus -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{{ $t('widgets.common.type') }}</label>
      <select
        v-model="cfg.mode"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="rolladen">{{ $t('widgets.rolladen.typeRollo') }}</option>
        <option value="jalousie">{{ $t('widgets.rolladen.typeJalousie') }}</option>
      </select>
    </div>

    <!-- Invertierung -->
    <div class="flex items-center gap-2">
      <input
        id="cfg-invert"
        v-model="cfg.invert"
        type="checkbox"
        class="rounded accent-blue-500"
      />
      <label for="cfg-invert" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
        {{ $t('widgets.rolladen.invertPos') }}
      </label>
    </div>

    <hr class="border-gray-200 dark:border-gray-700" />

    <!-- Steuer-Datenpunkte -->
    <p class="text-xs font-medium text-gray-600 dark:text-gray-400">{{ $t('widgets.rolladen.commands') }}</p>

    <DataPointPicker
      v-model="cfg.dp_move_up"
      :label="$t('widgets.rolladen.dpUp')"
      :compatible-types="['BOOLEAN']"
    />
    <div class="flex items-center gap-2 pl-1">
      <input id="cfg-inv-up" v-model="cfg.invert_move_up" type="checkbox" class="rounded accent-blue-500" />
      <label for="cfg-inv-up" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
        {{ $t('widgets.rolladen.invertDirWarn') }}
      </label>
    </div>

    <DataPointPicker
      v-model="cfg.dp_move_down"
      :label="$t('widgets.rolladen.dpDown')"
      :compatible-types="['BOOLEAN']"
    />
    <div class="flex items-center gap-2 pl-1">
      <input id="cfg-inv-down" v-model="cfg.invert_move_down" type="checkbox" class="rounded accent-blue-500" />
      <label for="cfg-inv-down" class="text-xs text-gray-500 dark:text-gray-400 cursor-pointer">
        {{ $t('widgets.rolladen.invertDirWarn') }}
      </label>
    </div>

    <DataPointPicker
      v-model="cfg.dp_stop"
      :label="$t('widgets.rolladen.dpStop')"
      :compatible-types="['BOOLEAN']"
    />

    <hr class="border-gray-200 dark:border-gray-700" />

    <!-- Positions-Datenpunkte -->
    <p class="text-xs font-medium text-gray-600 dark:text-gray-400">{{ $t('widgets.rolladen.positionSection') }}</p>

    <DataPointPicker
      v-model="cfg.dp_position"
      :label="$t('widgets.rolladen.dpPosSend')"
      :compatible-types="['FLOAT', 'INTEGER']"
    />
    <DataPointPicker
      v-model="cfg.dp_position_status"
      :label="$t('widgets.rolladen.dpPosStatus')"
      :compatible-types="['FLOAT', 'INTEGER']"
    />

    <!-- Lamellen (nur Jalousie) -->
    <template v-if="isJalousie">
      <hr class="border-gray-200 dark:border-gray-700" />
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">{{ $t('widgets.rolladen.slatSection') }}</p>

      <DataPointPicker
        v-model="cfg.dp_slat"
        :label="$t('widgets.rolladen.dpSlatSend')"
        :compatible-types="['FLOAT', 'INTEGER']"
      />
      <DataPointPicker
        v-model="cfg.dp_slat_status"
        :label="$t('widgets.rolladen.dpSlatStatus')"
        :compatible-types="['FLOAT', 'INTEGER']"
      />
    </template>

    <hr class="border-gray-200 dark:border-gray-700" />

    <!-- Sperre & Statusindikatoren -->
    <p class="text-xs font-medium text-gray-600 dark:text-gray-400">{{ $t('widgets.rolladen.lockStatus') }}</p>

    <!-- Sperre (Ausgang) -->
    <DataPointPicker
      v-model="cfg.dp_lock"
      :label="$t('widgets.rolladen.dpLock')"
      :compatible-types="['BOOLEAN']"
    />

    <!-- Status 1 -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{ $t('widgets.rolladen.indicatorLabel', { n: 1 }) }</label>
      <input
        v-model="cfg.label_status_1"
        type="text"
        placeholder="Manuelle Sperre"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>
    <DataPointPicker
      v-model="cfg.dp_status_1"
      :label="$t('widgets.rolladen.dpIndicatorRo', { n: 1 })"
      :compatible-types="['BOOLEAN']"
    />

    <!-- Status 2 -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{ $t('widgets.rolladen.indicatorLabel', { n: 2 }) }</label>
      <input
        v-model="cfg.label_status_2"
        type="text"
        placeholder="z.B. Windalarm"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>
    <DataPointPicker
      v-model="cfg.dp_status_2"
      :label="$t('widgets.rolladen.dpIndicatorRo', { n: 2 })"
      :compatible-types="['BOOLEAN']"
    />

    <!-- Status 3 -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{ $t('widgets.rolladen.indicatorLabel', { n: 3 }) }</label>
      <input
        v-model="cfg.label_status_3"
        type="text"
        placeholder="z.B. Regenalarm"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>
    <DataPointPicker
      v-model="cfg.dp_status_3"
      :label="$t('widgets.rolladen.dpIndicatorRo', { n: 3 })"
      :compatible-types="['BOOLEAN']"
    />

    <!-- Status 4 -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">{ $t('widgets.rolladen.indicatorLabel', { n: 4 }) }</label>
      <input
        v-model="cfg.label_status_4"
        type="text"
        placeholder="z.B. Automatik aktiv"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>
    <DataPointPicker
      v-model="cfg.dp_status_4"
      :label="$t('widgets.rolladen.dpIndicatorRo', { n: 4 })"
      :compatible-types="['BOOLEAN']"
    />
  </div>
</template>
