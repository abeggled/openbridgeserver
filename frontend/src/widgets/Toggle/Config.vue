<script setup lang="ts">
import { reactive, watch } from 'vue'
import IconPicker from '@/components/IconPicker.vue'

type DisplayMode = 'switch' | 'icon_only' | 'icon_text'

interface StateRule {
  icon: string
  color: string
  text: string
}

interface Cfg {
  label: string
  mode: DisplayMode
  on: StateRule
  off: StateRule
}

const MODES: { value: DisplayMode; label: string }[] = [
  { value: 'switch',    label: 'Schalter' },
  { value: 'icon_only', label: 'Nur Icon' },
  { value: 'icon_text', label: 'Icon + Text' },
]

const props = defineProps<{ modelValue: Record<string, unknown> }>()
const emit  = defineEmits<{ (e: 'update:modelValue', val: Record<string, unknown>): void }>()

function parseRule(raw: unknown, defaults: StateRule): StateRule {
  const r = raw as Partial<StateRule> | undefined
  return {
    icon:  r?.icon  ?? defaults.icon,
    color: r?.color ?? defaults.color,
    text:  r?.text  ?? defaults.text,
  }
}

const cfg = reactive<Cfg>({
  label: (props.modelValue.label as string) ?? '',
  mode:  (props.modelValue.mode  as DisplayMode) ?? 'switch',
  on:  parseRule(props.modelValue.on,  { icon: '', color: '#3b82f6', text: 'EIN' }),
  off: parseRule(props.modelValue.off, { icon: '', color: '#6b7280', text: 'AUS' }),
})

watch(cfg, () => emit('update:modelValue', { ...cfg }), { deep: true })
</script>

<template>
  <div class="space-y-4 text-sm">

    <!-- Beschriftung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Beschriftung</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Pumpe HK1"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Modus -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Modus</label>
      <div class="flex gap-1">
        <button
          v-for="m in MODES"
          :key="m.value"
          type="button"
          :class="[
            'flex-1 py-1.5 text-xs rounded border',
            cfg.mode === m.value
              ? 'border-blue-500 bg-blue-500/20 text-blue-300'
              : 'border-gray-700 text-gray-400 hover:border-gray-500',
          ]"
          @click="cfg.mode = m.value"
        >{{ m.label }}</button>
      </div>
    </div>

    <!-- ── Zustandsregeln ─────────────────────────────────────────────────── -->
    <div class="space-y-2">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">Zustände</p>

      <!-- EIN (true) -->
      <div class="border border-gray-700 rounded p-2 space-y-2">
        <p class="text-xs font-semibold text-blue-400">EIN (true)</p>

        <!-- Icon + Farbe -->
        <div class="flex gap-2 items-center">
          <span class="text-xs text-gray-500 w-8 shrink-0">Icon</span>
          <IconPicker v-model="cfg.on.icon" :dark="true" />
          <input
            v-model="cfg.on.color"
            type="color"
            class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
            title="Farbe"
          />
        </div>

        <!-- Text (nicht im Nur-Icon-Modus) -->
        <div v-if="cfg.mode !== 'icon_only'" class="flex gap-2 items-center">
          <span class="text-xs text-gray-500 w-8 shrink-0">Text</span>
          <input
            v-model="cfg.on.text"
            type="text"
            placeholder="EIN"
            class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>

      <!-- AUS (false / default) -->
      <div class="border border-gray-700 rounded p-2 space-y-2">
        <p class="text-xs font-semibold text-gray-400">AUS (false)</p>

        <!-- Icon + Farbe -->
        <div class="flex gap-2 items-center">
          <span class="text-xs text-gray-500 w-8 shrink-0">Icon</span>
          <IconPicker v-model="cfg.off.icon" :dark="true" />
          <input
            v-model="cfg.off.color"
            type="color"
            class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
            title="Farbe"
          />
        </div>

        <!-- Text (nicht im Nur-Icon-Modus) -->
        <div v-if="cfg.mode !== 'icon_only'" class="flex gap-2 items-center">
          <span class="text-xs text-gray-500 w-8 shrink-0">Text</span>
          <input
            v-model="cfg.off.text"
            type="text"
            placeholder="AUS"
            class="flex-1 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-xs text-gray-100 focus:outline-none focus:border-blue-500"
          />
        </div>
      </div>
    </div>

  </div>
</template>
