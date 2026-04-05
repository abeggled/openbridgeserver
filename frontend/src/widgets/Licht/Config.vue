<script setup lang="ts">
import { reactive, computed, watch } from 'vue'
import DataPointPicker from '@/components/DataPointPicker.vue'

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const cfg = reactive({
  label:            (props.modelValue.label            as string) ?? '',
  mode:             (props.modelValue.mode             as string) ?? 'dimm',
  dp_switch:        (props.modelValue.dp_switch        as string) ?? '',
  dp_switch_status: (props.modelValue.dp_switch_status as string) ?? '',
  dp_dim:           (props.modelValue.dp_dim           as string) ?? '',
  dp_dim_status:    (props.modelValue.dp_dim_status    as string) ?? '',
  dp_tw:            (props.modelValue.dp_tw            as string) ?? '',
  dp_tw_status:     (props.modelValue.dp_tw_status     as string) ?? '',
  tw_warm_k:        (props.modelValue.tw_warm_k        as number) ?? 2700,
  tw_cold_k:        (props.modelValue.tw_cold_k        as number) ?? 6500,
  dp_r:             (props.modelValue.dp_r             as string) ?? '',
  dp_g:             (props.modelValue.dp_g             as string) ?? '',
  dp_b:             (props.modelValue.dp_b             as string) ?? '',
  dp_r_status:      (props.modelValue.dp_r_status      as string) ?? '',
  dp_g_status:      (props.modelValue.dp_g_status      as string) ?? '',
  dp_b_status:      (props.modelValue.dp_b_status      as string) ?? '',
  dp_w:             (props.modelValue.dp_w             as string) ?? '',
  dp_w_status:      (props.modelValue.dp_w_status      as string) ?? '',
})

const hasDim   = computed(() => ['dimm', 'tw', 'rgb', 'rgbw'].includes(cfg.mode))
const hasTw    = computed(() => cfg.mode === 'tw')
const hasColor = computed(() => ['rgb', 'rgbw'].includes(cfg.mode))
const hasWhite = computed(() => cfg.mode === 'rgbw')

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-3">

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Beschriftung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Wohnzimmer"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Modus -->
    <div>
      <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Typ</label>
      <select
        v-model="cfg.mode"
        class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option value="on_off">Ein/Aus</option>
        <option value="dimm">Ein/Aus + Dimmen</option>
        <option value="tw">Ein/Aus + Dimmen + Tunable White</option>
        <option value="rgb">Ein/Aus + Dimmen + RGB</option>
        <option value="rgbw">Ein/Aus + Dimmen + RGBW</option>
      </select>
    </div>

    <hr class="border-gray-200 dark:border-gray-700" />

    <!-- Schalten -->
    <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Schalten (BOOLEAN)</p>

    <DataPointPicker
      v-model="cfg.dp_switch"
      label="Ein/Aus schreiben"
      :compatible-types="['BOOLEAN']"
    />
    <DataPointPicker
      v-model="cfg.dp_switch_status"
      label="Ein/Aus lesen (Status, optional)"
      :compatible-types="['BOOLEAN']"
    />

    <!-- Dimmen -->
    <template v-if="hasDim">
      <hr class="border-gray-200 dark:border-gray-700" />
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Helligkeit (0–100 %)</p>

      <DataPointPicker
        v-model="cfg.dp_dim"
        label="Helligkeit schreiben"
        :compatible-types="['FLOAT', 'INTEGER']"
      />
      <DataPointPicker
        v-model="cfg.dp_dim_status"
        label="Helligkeit lesen (Status, optional)"
        :compatible-types="['FLOAT', 'INTEGER']"
      />
    </template>

    <!-- Tunable White -->
    <template v-if="hasTw">
      <hr class="border-gray-200 dark:border-gray-700" />
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Farbtemperatur / Tunable White (Kelvin)</p>

      <div class="flex gap-2">
        <div class="flex-1">
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Warm (K)</label>
          <input
            v-model.number="cfg.tw_warm_k"
            type="number" min="1000" max="10000" step="100"
            placeholder="2700"
            class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
        <div class="flex-1">
          <label class="block text-xs text-gray-500 dark:text-gray-400 mb-1">Kalt (K)</label>
          <input
            v-model.number="cfg.tw_cold_k"
            type="number" min="1000" max="10000" step="100"
            placeholder="6500"
            class="w-full bg-gray-50 dark:bg-gray-800 border border-gray-300 dark:border-gray-700 rounded px-2 py-1.5 text-sm text-gray-900 dark:text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      <DataPointPicker
        v-model="cfg.dp_tw"
        label="Farbtemperatur schreiben (DPT 7.600 = Kelvin)"
        :compatible-types="['FLOAT', 'INTEGER']"
      />
      <DataPointPicker
        v-model="cfg.dp_tw_status"
        label="Farbtemperatur lesen (Status, optional)"
        :compatible-types="['FLOAT', 'INTEGER']"
      />
    </template>

    <!-- RGB -->
    <template v-if="hasColor">
      <hr class="border-gray-200 dark:border-gray-700" />
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">RGB (je 0–255)</p>

      <DataPointPicker v-model="cfg.dp_r" label="Rot schreiben"  :compatible-types="['FLOAT', 'INTEGER']" />
      <DataPointPicker v-model="cfg.dp_g" label="Grün schreiben" :compatible-types="['FLOAT', 'INTEGER']" />
      <DataPointPicker v-model="cfg.dp_b" label="Blau schreiben" :compatible-types="['FLOAT', 'INTEGER']" />

      <DataPointPicker v-model="cfg.dp_r_status" label="Rot lesen (Status, optional)"  :compatible-types="['FLOAT', 'INTEGER']" />
      <DataPointPicker v-model="cfg.dp_g_status" label="Grün lesen (Status, optional)" :compatible-types="['FLOAT', 'INTEGER']" />
      <DataPointPicker v-model="cfg.dp_b_status" label="Blau lesen (Status, optional)" :compatible-types="['FLOAT', 'INTEGER']" />
    </template>

    <!-- White channel (RGBW) -->
    <template v-if="hasWhite">
      <hr class="border-gray-200 dark:border-gray-700" />
      <p class="text-xs font-medium text-gray-600 dark:text-gray-400">Weisskanal (0–255)</p>

      <DataPointPicker
        v-model="cfg.dp_w"
        label="Weiss schreiben"
        :compatible-types="['FLOAT', 'INTEGER']"
      />
      <DataPointPicker
        v-model="cfg.dp_w_status"
        label="Weiss lesen (Status, optional)"
        :compatible-types="['FLOAT', 'INTEGER']"
      />
    </template>

  </div>
</template>
