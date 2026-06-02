<script setup lang="ts">
import { reactive, watch, computed } from 'vue'
import { useI18n } from 'vue-i18n'
import DataPointPicker from '@/components/DataPointPicker.vue'
import IconPicker from '@/components/IconPicker.vue'

type FlowDirection = 'to_house' | 'from_house' | 'bidirectional'

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



const { t } = useI18n()

const DIRECTION_OPTIONS = computed(() => [
  { value: 'to_house'      as FlowDirection, label: '→ 🏠',  title: t('widgets.energiefluss.dirToHouse') },
  { value: 'bidirectional' as FlowDirection, label: '⇄',      title: t('widgets.energiefluss.dirBidir') },
  { value: 'from_house'   as FlowDirection, label: '🏠 →',  title: t('widgets.energiefluss.dirFromHouse') },
])

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
  house_icon:      (props.modelValue.house_icon      as string) ?? '🏠',
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
      house_icon:     cfg.house_icon     || undefined,
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
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.energiefluss.titleLabel') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        placeholder="z.B. Energiefluss"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
      />
    </div>

    <!-- Hinweis -->
    <p class="text-xs text-gray-500 leading-relaxed">
      {{ $t('widgets.energiefluss.hint') }}
    </p>

    <!-- Hausverbrauch (Zentrum) -->
    <div class="border border-gray-700 rounded p-2 space-y-2">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider">
        {{ $t('widgets.energiefluss.center') }}
      </p>
      <!-- Haus-Icon -->
      <div>
        <label class="block text-xs text-gray-400 mb-1">Icon</label>
        <IconPicker v-model="cfg.house_icon" :dark="true" />
      </div>
      <DataPointPicker
        :model-value="cfg.house_dp || null"
        :compatible-types="['FLOAT', 'INTEGER']"
        @update:model-value="(id) => (cfg.house_dp = id ?? '')"
      />
      <template v-if="cfg.house_dp">
        <div class="flex gap-2">
          <div class="flex-1">
            <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.energiefluss.unitFromObject') }}</label>
            <input
              v-model="cfg.house_unit"
              type="text"
              placeholder="W"
              class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
            />
          </div>
          <div class="w-20">
            <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.energiefluss.decimalsShort') }}</label>
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
        {{ $t('widgets.energiefluss.nodes', { max: MAX_ENTITIES }) }}
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
          <p class="text-xs text-gray-500">{{ $t('widgets.energiefluss.nodeN', { n: i + 1 }) }}</p>
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
              <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.energiefluss.color') }}</label>
              <input
                v-model="entity.color"
                type="color"
                class="w-8 h-8 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5"
              />
            </div>

            <!-- Flussrichtung -->
            <div class="flex-1">
              <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.energiefluss.flowDirection') }}</label>
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
            <IconPicker v-model="entity.icon" :dark="true" />
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
              <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.energiefluss.unitFromObject') }}</label>
              <input
                v-model="entity.unit"
                type="text"
                placeholder="W"
                class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
              />
            </div>
            <div class="w-20">
              <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.energiefluss.decimalsShort') }}</label>
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
            <span class="text-xs text-gray-300">{{ $t('widgets.energiefluss.invertSign') }}</span>
          </label>
        </template>
      </div>
    </div>
  </div>
</template>
