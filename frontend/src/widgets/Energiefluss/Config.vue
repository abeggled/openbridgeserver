<script setup lang="ts">
import { reactive, watch } from 'vue'
import DataPointPicker from '@/components/DataPointPicker.vue'

type FlowDirection = 'to_house' | 'from_house' | 'bidirectional'

interface HouseConfig {
  dp: string
  unit: string
  decimals: number
}

interface EntityConfig {
  id: string
  label: string
  icon: string
  color: string
  direction: FlowDirection
  unit: string
  decimals: number
  invert: boolean
}

const props = defineProps<{
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, unknown>): void
}>()

const MAX_ENTITIES = 8

const ICON_PRESETS = [
  { icon: '☀️', label: 'PV / Solar' },
  { icon: '🏭', label: 'Verbrauch' },
  { icon: '🔋', label: 'Batterie' },
  { icon: '⚡', label: 'Netz' },
  { icon: '🚗', label: 'Wallbox / E-Auto' },
  { icon: '🌬️', label: 'Wind' },
  { icon: '🔌', label: 'Steckdose' },
  { icon: '💧', label: 'Wasser / Wärmepumpe' },
]

const DIRECTION_OPTIONS: { value: FlowDirection; label: string; title: string }[] = [
  { value: 'to_house',      label: '→ 🏠',  title: 'Nur vom Knoten zum Haus' },
  { value: 'bidirectional', label: '⇄',      title: 'Bidirektional (Vorzeichen entscheidet)' },
  { value: 'from_house',    label: '🏠 →',  title: 'Nur vom Haus zum Knoten' },
]

function makeEntity(src?: Partial<EntityConfig>): EntityConfig {
  return {
    id: src?.id ?? '',
    label: src?.label ?? '',
    icon: src?.icon ?? '⚡',
    color: src?.color ?? '#60a5fa',
    direction: src?.direction ?? 'bidirectional',
    unit: src?.unit ?? '',
    decimals: src?.decimals ?? 1,
    invert: src?.invert ?? false,
  }
}

const existingEntities = (props.modelValue.entities as EntityConfig[] | undefined) ?? []

const cfg = reactive({
  label:           (props.modelValue.label           as string) ?? '',
  house_dp:        (props.modelValue.house_dp        as string) ?? '',
  house_unit:      (props.modelValue.house_unit      as string) ?? '',
  house_decimals:  (props.modelValue.house_decimals  as number) ?? 1,
  entities: Array.from({ length: MAX_ENTITIES }, (_, i) => makeEntity(existingEntities[i])),
})

watch(
  cfg,
  () => {
    emit('update:modelValue', {
      label:          cfg.label,
      house_dp:       cfg.house_dp       || undefined,
      house_unit:     cfg.house_unit     || undefined,
      house_decimals: cfg.house_decimals,
      entities: cfg.entities.filter((e) => !!e.id),
    })
  },
  { deep: true },
)
</script>

<template>
  <div class="space-y-3">
    <!-- Widget-Beschriftung -->
    <div>
      <label class="block text-xs text-gray-400 mb-1">Titel (optional)</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Energiefluss"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Hinweis -->
    <p class="text-xs text-gray-500 leading-relaxed">
      Farbe und Flussrichtung werden pro Knoten konfiguriert.<br />
      Bei «Bidirektional» bestimmt das Vorzeichen die Richtung (positiv → Haus, negativ → weg).
      «Vorzeichen umkehren» dreht diese Logik um.
    </p>

    <!-- Hausverbrauch (Zentrum) -->
    <div class="border border-gray-700 rounded p-2 space-y-2">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">
        🏠 Hausverbrauch (Zentrum, optional)
      </p>
      <DataPointPicker
        :model-value="cfg.house_dp || null"
        :compatible-types="['FLOAT', 'INTEGER']"
        @update:model-value="(id) => (cfg.house_dp = id ?? '')"
      />
      <template v-if="cfg.house_dp">
        <div class="flex gap-2">
          <div class="flex-1">
            <label class="block text-xs text-gray-400 mb-1">Einheit (leer = vom Objekt)</label>
            <input
              v-model="cfg.house_unit"
              type="text"
              placeholder="W"
              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div class="w-20">
            <label class="block text-xs text-gray-400 mb-1">Dezimalst.</label>
            <input
              v-model.number="cfg.house_decimals"
              type="number"
              min="0"
              max="4"
              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
            />
          </div>
        </div>
      </template>
    </div>

    <!-- Energieknoten -->
    <div class="pt-1">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-2">
        Energieknoten (max. {{ MAX_ENTITIES }})
      </p>

      <div
        v-for="(entity, i) in cfg.entities"
        :key="i"
        class="border border-gray-700 rounded p-2 space-y-2 mb-2"
        :style="entity.id ? `border-color: ${entity.color}40` : ''"
      >
        <!-- Header mit Farb-Swatch -->
        <div class="flex items-center gap-2">
          <span
            class="w-2.5 h-2.5 rounded-full shrink-0"
            :style="entity.id ? `background: ${entity.color}` : 'background: #4b5563'"
          />
          <p class="text-xs text-gray-500">Energieknoten {{ i + 1 }}</p>
        </div>

        <DataPointPicker
          :model-value="entity.id || null"
          :compatible-types="['FLOAT', 'INTEGER']"
          @update:model-value="(id) => (entity.id = id ?? '')"
        />

        <template v-if="entity.id">
          <!-- Farbe -->
          <div class="flex items-center gap-3">
            <div>
              <label class="block text-xs text-gray-400 mb-1">Farbe</label>
              <input
                v-model="entity.color"
                type="color"
                class="w-8 h-8 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5"
              />
            </div>

            <!-- Flussrichtung -->
            <div class="flex-1">
              <label class="block text-xs text-gray-400 mb-1">Flussrichtung</label>
              <div class="flex gap-1">
                <button
                  v-for="opt in DIRECTION_OPTIONS"
                  :key="opt.value"
                  type="button"
                  :title="opt.title"
                  :class="[
                    'flex-1 py-1 text-xs rounded border font-mono',
                    entity.direction === opt.value
                      ? 'border-blue-500 bg-blue-500/20 text-blue-300'
                      : 'border-gray-700 text-gray-400 hover:border-gray-500',
                  ]"
                  @click="entity.direction = opt.value"
                >{{ opt.label }}</button>
              </div>
            </div>
          </div>

          <!-- Icon-Auswahl -->
          <div>
            <label class="block text-xs text-gray-400 mb-1">Icon</label>
            <div class="flex flex-wrap gap-1 mb-1">
              <button
                v-for="preset in ICON_PRESETS"
                :key="preset.icon"
                type="button"
                :title="preset.label"
                :class="[
                  'px-1.5 py-0.5 rounded text-base leading-none border',
                  entity.icon === preset.icon
                    ? 'border-blue-500 bg-blue-500/20'
                    : 'border-gray-700 hover:border-gray-500',
                ]"
                @click="entity.icon = preset.icon"
              >{{ preset.icon }}</button>
            </div>
            <input
              v-model="entity.icon"
              type="text"
              placeholder="Emoji (z.B. ☀️)"
              class="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
            />
          </div>

          <!-- Bezeichnung -->
          <input
            v-model="entity.label"
            type="text"
            placeholder="Bezeichnung (z.B. PV-Anlage)"
            class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          />

          <!-- Einheit + Dezimalstellen -->
          <div class="flex gap-2">
            <div class="flex-1">
              <label class="block text-xs text-gray-400 mb-1">Einheit (leer = vom Objekt)</label>
              <input
                v-model="entity.unit"
                type="text"
                placeholder="W"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
              />
            </div>
            <div class="w-20">
              <label class="block text-xs text-gray-400 mb-1">Dezimalst.</label>
              <input
                v-model.number="entity.decimals"
                type="number"
                min="0"
                max="4"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
              />
            </div>
          </div>

          <!-- Vorzeichen umkehren -->
          <label class="flex items-center gap-2 cursor-pointer select-none">
            <input
              v-model="entity.invert"
              type="checkbox"
              class="rounded border-gray-600 text-blue-500 focus:ring-blue-500 focus:ring-offset-gray-900"
            />
            <span class="text-xs text-gray-300">Vorzeichen umkehren</span>
          </label>
        </template>
      </div>
    </div>
  </div>
</template>
