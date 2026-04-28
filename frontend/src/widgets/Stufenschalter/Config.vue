<script setup lang="ts">
import { reactive, watch } from 'vue'
import IconPicker from '@/components/IconPicker.vue'

interface Step {
  label: string
  value: number
  icon: string
  color: string
}

interface Cfg {
  label: string
  steps: Step[]
}

const MIN_STEPS = 3
const MAX_STEPS = 10

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

function parseSteps(raw: unknown): Step[] {
  const arr = raw as Partial<Step>[] | undefined
  if (!Array.isArray(arr) || arr.length < MIN_STEPS) {
    return [
      { label: 'Stufe 1', value: 0, icon: '', color: '#6b7280' },
      { label: 'Stufe 2', value: 1, icon: '', color: '#3b82f6' },
      { label: 'Stufe 3', value: 2, icon: '', color: '#10b981' },
    ]
  }
  return arr.map((s) => ({
    label: s.label ?? '',
    value: s.value ?? 0,
    icon:  s.icon  ?? '',
    color: s.color ?? '#6b7280',
  }))
}

const cfg = reactive<Cfg>({
  label: (props.modelValue.label as string) ?? '',
  steps: parseSteps(props.modelValue.steps),
})

watch(cfg, () => emit('update:modelValue', { ...cfg, steps: [...cfg.steps] }), { deep: true })

function addStep() {
  if (cfg.steps.length >= MAX_STEPS) return
  const next = cfg.steps.length
  cfg.steps.push({ label: `Stufe ${next + 1}`, value: next, icon: '', color: '#6b7280' })
}

function removeStep(i: number) {
  if (cfg.steps.length <= MIN_STEPS) return
  cfg.steps.splice(i, 1)
}

function moveUp(i: number) {
  if (i === 0) return
  const tmp = cfg.steps[i - 1]
  cfg.steps[i - 1] = cfg.steps[i]
  cfg.steps[i] = tmp
}

function moveDown(i: number) {
  if (i === cfg.steps.length - 1) return
  const tmp = cfg.steps[i + 1]
  cfg.steps[i + 1] = cfg.steps[i]
  cfg.steps[i] = tmp
}
</script>

<template>
  <div class="space-y-4 text-sm">

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Beschriftung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Lüfterstufe"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Stufen -->
    <div>
      <div class="flex items-center justify-between mb-2">
        <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">
          Stufen ({{ cfg.steps.length }}/{{ MAX_STEPS }})
        </p>
        <button
          type="button"
          :disabled="cfg.steps.length >= MAX_STEPS"
          class="text-xs px-2 py-1 rounded border border-dashed border-gray-600 text-gray-400 hover:border-blue-500 hover:text-blue-400 disabled:opacity-30 disabled:cursor-not-allowed transition-colors"
          @click="addStep"
        >+ Stufe</button>
      </div>

      <div class="space-y-2">
        <div
          v-for="(step, i) in cfg.steps"
          :key="i"
          class="border border-gray-700 rounded p-2 space-y-2"
        >
          <!-- Stufen-Kopfzeile mit Nummerierung + Reihenfolge-Buttons -->
          <div class="flex items-center gap-1">
            <span class="text-xs font-semibold text-gray-500 w-4 shrink-0">{{ i + 1 }}</span>
            <div class="flex gap-0.5 ml-auto">
              <button
                type="button"
                :disabled="i === 0"
                class="w-5 h-5 flex items-center justify-center rounded text-gray-500 hover:text-gray-300 disabled:opacity-20"
                title="Nach oben"
                @click="moveUp(i)"
              >▲</button>
              <button
                type="button"
                :disabled="i === cfg.steps.length - 1"
                class="w-5 h-5 flex items-center justify-center rounded text-gray-500 hover:text-gray-300 disabled:opacity-20"
                title="Nach unten"
                @click="moveDown(i)"
              >▼</button>
              <button
                type="button"
                :disabled="cfg.steps.length <= MIN_STEPS"
                class="w-5 h-5 flex items-center justify-center rounded text-red-600 hover:text-red-400 disabled:opacity-20"
                title="Entfernen"
                @click="removeStep(i)"
              >✕</button>
            </div>
          </div>

          <!-- Icon + Farbe -->
          <div class="flex gap-2 items-center">
            <span class="text-xs text-gray-500 w-8 shrink-0">Icon</span>
            <IconPicker v-model="step.icon" :dark="true" />
            <input
              v-model="step.color"
              type="color"
              class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
              title="Farbe"
            />
          </div>

          <!-- Beschriftung + Wert -->
          <div class="flex gap-2">
            <div class="flex-1">
              <label class="block text-xs text-gray-500 mb-0.5">Beschriftung</label>
              <input
                v-model="step.label"
                type="text"
                :placeholder="`Stufe ${i + 1}`"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
              />
            </div>
            <div class="w-20">
              <label class="block text-xs text-gray-500 mb-0.5">Wert</label>
              <input
                v-model.number="step.value"
                type="number"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>
        </div>
      </div>
    </div>

  </div>
</template>
