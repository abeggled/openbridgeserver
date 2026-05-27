<script setup lang="ts">
import { reactive, watch } from 'vue'
import DataPointPicker from '@/components/DataPointPicker.vue'

interface BarConfig {
  label: string
  dp_id: string
  min: number
  max: number
  decimals: number
  prefix: string
  postfix: string
}

interface Cfg {
  label: string
  bars: BarConfig[]
  colors: string[]
  show_value: boolean
}

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

const MAX_BARS = 12

function makeBar(src?: Partial<BarConfig>): BarConfig {
  return {
    label:    src?.label    ?? '',
    dp_id:    src?.dp_id    ?? '',
    min:      src?.min      ?? 0,
    max:      src?.max      ?? 100,
    decimals: src?.decimals ?? 1,
    prefix:   src?.prefix   ?? '',
    postfix:  src?.postfix  ?? '',
  }
}

const existingBars = (props.modelValue.bars as BarConfig[] | undefined) ?? []

const cfg = reactive<Cfg>({
  label:      (props.modelValue.label      as string)   ?? '',
  bars:       existingBars.map(b => makeBar(b)),
  colors:     ((props.modelValue.colors    as string[] | undefined) ?? ['#22c55e', '#f59e0b', '#ef4444']).slice(),
  show_value: (props.modelValue.show_value as boolean)  ?? true,
})

watch(cfg, () => {
  emit('update:modelValue', {
    label:      cfg.label,
    bars:       cfg.bars,
    colors:     cfg.colors,
    show_value: cfg.show_value,
  })
}, { deep: true })

function addBar() {
  if (cfg.bars.length < MAX_BARS) cfg.bars.push(makeBar())
}

function removeBar(i: number) {
  cfg.bars.splice(i, 1)
}

function moveUp(i: number) {
  if (i > 0) {
    const tmp = cfg.bars[i - 1]
    cfg.bars[i - 1] = cfg.bars[i]
    cfg.bars[i] = tmp
  }
}

function moveDown(i: number) {
  if (i < cfg.bars.length - 1) {
    const tmp = cfg.bars[i + 1]
    cfg.bars[i + 1] = cfg.bars[i]
    cfg.bars[i] = tmp
  }
}

const gradientPreview = (c: string[]) => {
  if (c.length === 0) return '#374151'
  if (c.length === 1) return c[0]
  const stops = c.map((col, i) => `${col} ${(i / (c.length - 1)) * 100}%`).join(', ')
  return `linear-gradient(to right, ${stops})`
}
</script>

<template>
  <div class="space-y-4 text-sm">

    <!-- Widget-Titel -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Beschriftung (Widget-Titel)</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Raumtemperaturen"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Wert anzeigen -->
    <div class="flex items-center gap-3">
      <span class="text-xs text-gray-400">Wert anzeigen</span>
      <button
        type="button"
        :class="[
          'relative inline-flex h-5 w-9 shrink-0 rounded-full border-2 border-transparent cursor-pointer transition-colors',
          cfg.show_value ? 'bg-blue-500' : 'bg-gray-700',
        ]"
        @click="cfg.show_value = !cfg.show_value"
      >
        <span
          :class="[
            'pointer-events-none inline-block h-4 w-4 rounded-full bg-white shadow transform transition-transform',
            cfg.show_value ? 'translate-x-4' : 'translate-x-0',
          ]"
        />
      </button>
    </div>

    <!-- Farbverlauf -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Farbverlauf (niedrig → hoch, 1–4 Farben)</label>
      <div class="flex items-center gap-2 flex-wrap">
        <input
          v-for="(_, i) in cfg.colors"
          :key="i"
          v-model="cfg.colors[i]"
          type="color"
          class="w-8 h-8 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
          :title="`Farbe ${i + 1}`"
        />
        <button
          v-if="cfg.colors.length < 4"
          type="button"
          class="text-xs text-blue-400 hover:text-blue-300 px-2 py-1 border border-gray-700 rounded"
          @click="cfg.colors.push('#6b7280')"
        >+</button>
        <button
          v-if="cfg.colors.length > 1"
          type="button"
          class="text-xs text-red-400 hover:text-red-300 px-2 py-1 border border-gray-700 rounded"
          @click="cfg.colors.splice(cfg.colors.length - 1, 1)"
        >−</button>
        <!-- Vorschau -->
        <div
          class="h-4 flex-1 min-w-16 rounded"
          :style="{ background: gradientPreview(cfg.colors) }"
        />
      </div>
    </div>

    <!-- Balken-Liste -->
    <div>
      <div class="flex items-center justify-between mb-2">
        <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Balken ({{ cfg.bars.length }}/{{ MAX_BARS }})
        </p>
      </div>

      <div class="space-y-2">
        <div
          v-for="(bar, i) in cfg.bars"
          :key="i"
          class="border border-gray-700 rounded overflow-hidden"
        >
          <!-- Kopfzeile -->
          <div class="flex items-center gap-1 px-2 py-1.5 bg-gray-800/50">
            <span class="text-xs text-gray-500 w-4 shrink-0">{{ i + 1 }}</span>
            <input
              v-model="bar.label"
              type="text"
              placeholder="Bezeichnung"
              class="flex-1 min-w-0 bg-transparent border-b border-gray-700 px-1 py-0.5 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
            />
            <div class="flex gap-1 ml-1 shrink-0">
              <button
                type="button"
                class="text-gray-200 hover:text-white px-1 text-sm leading-none disabled:opacity-30"
                title="Nach oben"
                :disabled="i === 0"
                @click="moveUp(i)"
              >↑</button>
              <button
                type="button"
                class="text-gray-200 hover:text-white px-1 text-sm leading-none disabled:opacity-30"
                title="Nach unten"
                :disabled="i === cfg.bars.length - 1"
                @click="moveDown(i)"
              >↓</button>
              <button
                type="button"
                class="text-red-400 hover:text-red-200 px-1 text-sm leading-none"
                title="Entfernen"
                @click="removeBar(i)"
              >×</button>
            </div>
          </div>

          <!-- Details -->
          <div class="px-2 py-2 space-y-2">
            <DataPointPicker
              :model-value="bar.dp_id || null"
              :compatible-types="['FLOAT', 'INTEGER']"
              @update:model-value="(id) => (bar.dp_id = id ?? '')"
            />

            <!-- Min / Max / Dezimal -->
            <div class="flex gap-2">
              <div class="flex-1">
                <label class="block text-xs text-gray-500 mb-0.5">Min</label>
                <input
                  v-model.number="bar.min"
                  type="number"
                  class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div class="flex-1">
                <label class="block text-xs text-gray-500 mb-0.5">Max</label>
                <input
                  v-model.number="bar.max"
                  type="number"
                  class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div class="w-16">
                <label class="block text-xs text-gray-500 mb-0.5">Dezimal</label>
                <input
                  v-model.number="bar.decimals"
                  type="number"
                  min="0"
                  max="4"
                  class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>

            <!-- Präfix / Einheit -->
            <div class="flex gap-2">
              <div class="flex-1">
                <label class="block text-xs text-gray-500 mb-0.5">Präfix</label>
                <input
                  v-model="bar.prefix"
                  type="text"
                  class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
              <div class="flex-1">
                <label class="block text-xs text-gray-500 mb-0.5">Einheit / Postfix</label>
                <input
                  v-model="bar.postfix"
                  type="text"
                  placeholder="z.B. °C"
                  class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
                />
              </div>
            </div>
          </div>
        </div>
      </div>

      <button
        v-if="cfg.bars.length < MAX_BARS"
        type="button"
        class="mt-2 text-xs text-blue-400 hover:text-blue-300"
        @click="addBar"
      >+ Balken hinzufügen</button>
    </div>

  </div>
</template>
