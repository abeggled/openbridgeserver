<script setup lang="ts">
import { reactive, watch } from 'vue'
import DataPointPicker from '@/components/DataPointPicker.vue'
import { TIME_RANGE_PRESETS, DEFAULT_TIME_RANGE } from './timeRangePresets'
import { COLORS } from './seriesDefs'

type Axis      = 'left' | 'right'
type ChartType = 'line' | 'bar'

interface Series {
  dp_id: string
  label: string
  color: string
  axis: Axis
}

interface Cfg {
  label: string
  time_range: string
  chart_type: ChartType
  primary_color: string
  primary_axis: Axis
  primary_label: string
  series: Series[]
}

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

function normalizeSeries(raw: unknown): Series[] {
  if (!Array.isArray(raw)) return []
  return raw.map(s => ({
    dp_id:  (s as Record<string, unknown>).dp_id  as string ?? '',
    label:  (s as Record<string, unknown>).label  as string ?? '',
    color:  (s as Record<string, unknown>).color  as string ?? COLORS[1],
    axis:   ((s as Record<string, unknown>).axis  as Axis)  ?? 'left',
  }))
}

function resolveTimeRange(raw: Record<string, unknown>): string {
  if (raw.time_range && typeof raw.time_range === 'string') return raw.time_range as string
  return DEFAULT_TIME_RANGE
}

const cfg = reactive<Cfg>({
  label:         (props.modelValue.label         as string)    ?? '',
  time_range:    resolveTimeRange(props.modelValue),
  chart_type:    (props.modelValue.chart_type    as ChartType) ?? 'line',
  primary_color: (props.modelValue.primary_color as string)    ?? COLORS[0],
  primary_axis:  (props.modelValue.primary_axis  as Axis)      ?? 'left',
  primary_label: (props.modelValue.primary_label as string)    ?? '',
  series:        normalizeSeries(props.modelValue.series),
})

watch(cfg, () => emit('update:modelValue', {
  label:         cfg.label,
  time_range:    cfg.time_range,
  chart_type:    cfg.chart_type,
  primary_color: cfg.primary_color,
  primary_axis:  cfg.primary_axis,
  primary_label: cfg.primary_label,
  series:        cfg.series.map(s => ({ ...s })),
}), { deep: true })

function addSeries() {
  // Index 0 gehört der Primärreihe — gleiche Zuordnung wie der Fallback in buildSeriesDefs()
  const color = COLORS[(cfg.series.length + 1) % COLORS.length]
  cfg.series.push({ dp_id: '', label: '', color, axis: 'left' })
}

function removeSeries(i: number) {
  cfg.series.splice(i, 1)
}
</script>

<template>
  <div class="space-y-3">

    <!-- Diagrammtitel -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.chart.chartTitle') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        :placeholder="$t('widgets.chart.labelPlaceholder')"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Diagramm-Typ -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.chart.chartType') }}</label>
      <div class="flex gap-1">
        <button
          v-for="opt in [{ v: 'line', l: $t('widgets.chart.chartTypeLine') }, { v: 'bar', l: $t('widgets.chart.chartTypeBar') }]"
          :key="opt.v"
          type="button"
          :class="[
            'flex-1 py-1 text-xs rounded border',
            cfg.chart_type === opt.v
              ? 'border-blue-500 bg-blue-500/20 text-blue-300'
              : 'border-gray-700 text-gray-400 hover:border-gray-500',
          ]"
          @click="cfg.chart_type = opt.v as ChartType"
        >{{ opt.l }}</button>
      </div>
    </div>

    <!-- Zeitraum -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.chart.defaultTimeRange') }}</label>
      <select
        v-model="cfg.time_range"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      >
        <option v-for="p in TIME_RANGE_PRESETS" :key="p.value" :value="p.value">{{ $t(p.label) }}</option>
      </select>
    </div>

    <!-- Primäre Reihe -->
    <div class="border-t border-gray-700 pt-3">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">{{ $t('widgets.chart.primarySeries') }}</p>
      <input
        v-model="cfg.primary_label"
        type="text"
        :placeholder="$t('widgets.chart.primaryLabel')"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
      <p class="mt-1 mb-2 text-xs text-gray-500">{{ $t('widgets.chart.primaryLabelHint') }}</p>
      <div class="flex gap-2 items-center">
        <input
          v-model="cfg.primary_color"
          type="color"
          class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
          :title="$t('widgets.chart.color')"
        />
        <!-- Achsen-Toggle -->
        <div class="flex gap-1 flex-1">
          <button
            v-for="opt in [{ v: 'left', l: $t('widgets.chart.axisLeft') }, { v: 'right', l: $t('widgets.chart.axisRight') }]"
            :key="opt.v"
            type="button"
            :class="[
              'flex-1 py-1 text-xs rounded border',
              cfg.primary_axis === opt.v
                ? 'border-blue-500 bg-blue-500/20 text-blue-300'
                : 'border-gray-700 text-gray-400 hover:border-gray-500',
            ]"
            @click="cfg.primary_axis = opt.v as Axis"
          >{{ opt.l }}</button>
        </div>
      </div>
    </div>

    <!-- Weitere Reihen -->
    <div class="border-t border-gray-700 pt-3">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">{{ $t('widgets.chart.additionalSeries') }}</p>

      <div class="space-y-3">
        <div
          v-for="(s, i) in cfg.series"
          :key="i"
          class="border border-gray-700 rounded p-2 space-y-2"
        >
          <!-- Objekt-Picker -->
          <DataPointPicker
            :model-value="s.dp_id || null"
            :compatible-types="['FLOAT', 'INTEGER']"
            @update:model-value="id => (s.dp_id = id ?? '')"
          />

          <!-- Bezeichnung + Farbe + Löschen -->
          <div class="flex gap-2 items-center">
            <input
              v-model="s.label"
              type="text"
              :placeholder="$t('widgets.chart.seriesLabel')"
              class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
            />
            <input
              v-model="s.color"
              type="color"
              class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
              :title="$t('widgets.chart.color')"
            />
            <button
              type="button"
              class="text-gray-500 hover:text-red-400 shrink-0 text-sm px-1"
              :title="$t('widgets.chart.removeSeriesTitle')"
              @click="removeSeries(i)"
            >🗑</button>
          </div>

          <!-- Achsen-Toggle -->
          <div class="flex gap-1">
            <button
              v-for="opt in [{ v: 'left', l: $t('widgets.chart.axisLeft') }, { v: 'right', l: $t('widgets.chart.axisRight') }]"
              :key="opt.v"
              type="button"
              :class="[
                'flex-1 py-1 text-xs rounded border',
                s.axis === opt.v
                  ? 'border-blue-500 bg-blue-500/20 text-blue-300'
                  : 'border-gray-700 text-gray-400 hover:border-gray-500',
              ]"
              @click="s.axis = opt.v as Axis"
            >{{ opt.l }}</button>
          </div>
        </div>
      </div>

      <button
        type="button"
        class="mt-2 text-xs text-blue-400 hover:text-blue-300"
        @click="addSeries"
      >{{ $t('widgets.chart.addSeries') }}</button>
    </div>

  </div>
</template>
