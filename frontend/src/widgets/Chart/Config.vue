<script setup lang="ts">
import { reactive, watch } from 'vue'
import DataPointPicker from '@/components/DataPointPicker.vue'

type Axis = 'left' | 'right'

interface Series {
  dp_id: string
  label: string
  color: string
  axis: Axis
}

interface Cfg {
  label: string
  hours: number
  primary_color: string
  primary_axis: Axis
  series: Series[]
}

const SERIES_COLORS = ['#ef4444', '#10b981', '#f59e0b', '#8b5cf6', '#ec4899', '#06b6d4', '#f97316']

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

function normalizeSeries(raw: unknown): Series[] {
  if (!Array.isArray(raw)) return []
  return raw.map(s => ({
    dp_id:  (s as Record<string, unknown>).dp_id  as string ?? '',
    label:  (s as Record<string, unknown>).label  as string ?? '',
    color:  (s as Record<string, unknown>).color  as string ?? SERIES_COLORS[0],
    axis:   ((s as Record<string, unknown>).axis  as Axis)  ?? 'left',
  }))
}

const cfg = reactive<Cfg>({
  label:         (props.modelValue.label         as string) ?? '',
  hours:         (props.modelValue.hours         as number) ?? 24,
  primary_color: (props.modelValue.primary_color as string) ?? '#3b82f6',
  primary_axis:  (props.modelValue.primary_axis  as Axis)   ?? 'left',
  series:        normalizeSeries(props.modelValue.series),
})

watch(cfg, () => emit('update:modelValue', {
  label:         cfg.label,
  hours:         cfg.hours,
  primary_color: cfg.primary_color,
  primary_axis:  cfg.primary_axis,
  series:        cfg.series.map(s => ({ ...s })),
}), { deep: true })

function addSeries() {
  const color = SERIES_COLORS[cfg.series.length % SERIES_COLORS.length]
  cfg.series.push({ dp_id: '', label: '', color, axis: 'left' })
}

function removeSeries(i: number) {
  cfg.series.splice(i, 1)
}
</script>

<template>
  <div class="space-y-3">

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Beschriftung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Temperaturverlauf"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Zeitraum -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Zeitraum (Stunden)</label>
      <input
        v-model.number="cfg.hours"
        type="number"
        min="1"
        max="720"
        class="w-32 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Primäre Reihe -->
    <div class="border-t border-gray-700 pt-3">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Primäre Reihe</p>
      <div class="flex gap-2 items-center">
        <input
          v-model="cfg.primary_color"
          type="color"
          class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
          title="Farbe"
        />
        <!-- Achsen-Toggle -->
        <div class="flex gap-1 flex-1">
          <button
            v-for="opt in [{ v: 'left', l: '◀ Links' }, { v: 'right', l: 'Rechts ▶' }]"
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
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">Weitere Reihen</p>

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
              placeholder="Bezeichnung (optional)"
              class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
            />
            <input
              v-model="s.color"
              type="color"
              class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
              title="Farbe"
            />
            <button
              type="button"
              class="text-gray-500 hover:text-red-400 shrink-0 text-sm px-1"
              title="Reihe entfernen"
              @click="removeSeries(i)"
            >🗑</button>
          </div>

          <!-- Achsen-Toggle -->
          <div class="flex gap-1">
            <button
              v-for="opt in [{ v: 'left', l: '◀ Links' }, { v: 'right', l: 'Rechts ▶' }]"
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
      >+ Weitere Reihe hinzufügen</button>
    </div>

  </div>
</template>
