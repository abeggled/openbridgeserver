<script setup lang="ts">
import { computed, reactive, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import DataPointPicker from '@/components/DataPointPicker.vue'
import { clampDays, MAX_DAYS, MIN_DAYS } from './calendarEvents'
import {
  DEFAULT_DATE_PATTERN,
  MAX_SOURCES,
  SEPARATORS,
  SOURCE_COLORS,
  type CalendarSource,
  type SeparatorKey,
} from './config'

const props = defineProps<{
  modelValue: Record<string, unknown>
}>()

const emit = defineEmits<{
  (e: 'update:modelValue', val: Record<string, unknown>): void
}>()

const { t } = useI18n()

function makeSource(src: Partial<CalendarSource> | undefined, index: number): CalendarSource {
  return {
    datapoint_id: src?.datapoint_id ?? '',
    label: src?.label ?? '',
    color: src?.color || SOURCE_COLORS[index % SOURCE_COLORS.length],
  }
}

const storedSources = (props.modelValue.sources as Partial<CalendarSource>[] | undefined) ?? []
const storedSeparator = props.modelValue.separator as SeparatorKey | undefined

const cfg = reactive({
  label: (props.modelValue.label as string | undefined) ?? '',
  sources: storedSources.slice(0, MAX_SOURCES).map(makeSource),
  days: clampDays(props.modelValue.days),
  separator: (storedSeparator && storedSeparator in SEPARATORS ? storedSeparator : 'semicolon') as SeparatorKey,
  show_time: (props.modelValue.show_time as boolean | undefined) ?? true,
  show_location: (props.modelValue.show_location as boolean | undefined) ?? false,
  show_empty_days: (props.modelValue.show_empty_days as boolean | undefined) ?? false,
  date_pattern: (props.modelValue.date_pattern as string | undefined) || DEFAULT_DATE_PATTERN,
})

const separatorOptions = computed(() => [
  { value: 'semicolon', label: t('widgets.terminliste.separatorSemicolon') },
  { value: 'star', label: t('widgets.terminliste.separatorStar') },
  { value: 'newline', label: t('widgets.terminliste.separatorNewline') },
])

// The add button is hidden once MAX_SOURCES is reached.
function addSource(): void {
  cfg.sources.push(makeSource(undefined, cfg.sources.length))
}

function removeSource(index: number): void {
  cfg.sources.splice(index, 1)
}

watch(
  cfg,
  () => {
    emit('update:modelValue', {
      label: cfg.label,
      sources: cfg.sources.filter((s) => !!s.datapoint_id).map((s) => ({ ...s })),
      days: clampDays(cfg.days),
      separator: cfg.separator,
      show_time: cfg.show_time,
      show_location: cfg.show_location,
      show_empty_days: cfg.show_empty_days,
      date_pattern: cfg.date_pattern.trim() || DEFAULT_DATE_PATTERN,
    })
  },
  { deep: true },
)
</script>

<template>
  <div class="space-y-3">
    <div>
      <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.common.label') }}</label>
      <input
        v-model="cfg.label"
        type="text"
        :placeholder="$t('widgets.terminliste.titlePlaceholder')"
        class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
        data-testid="terminliste-config-label"
      />
    </div>

    <!-- Kalenderquellen -->
    <div class="pt-1">
      <p class="text-xs font-semibold text-gray-400 uppercase tracking-wider mb-1">
        {{ $t('widgets.terminliste.sources', { max: MAX_SOURCES }) }}
      </p>
      <p class="text-xs text-gray-500 mb-2">{{ $t('widgets.terminliste.sourcesHint') }}</p>
      <div
        v-for="(source, i) in cfg.sources"
        :key="i"
        class="border border-gray-700 rounded p-2 space-y-2 mb-2"
        data-testid="terminliste-config-source"
      >
        <div class="flex items-center justify-between">
          <p class="text-xs text-gray-500">{{ $t('widgets.terminliste.sourceN', { n: i + 1 }) }}</p>
          <button
            type="button"
            class="text-xs text-red-400 hover:text-red-300"
            :title="$t('widgets.terminliste.removeSource')"
            data-testid="terminliste-config-remove-source"
            @click="removeSource(i)"
          >✕</button>
        </div>
        <DataPointPicker
          :model-value="source.datapoint_id || null"
          :compatible-types="['*']"
          @update:model-value="(id) => (source.datapoint_id = id ?? '')"
        />
        <div class="flex gap-2 items-center">
          <input
            v-model="source.color"
            type="color"
            class="w-7 h-7 rounded cursor-pointer border border-gray-700 bg-transparent p-0.5 shrink-0"
            :title="$t('widgets.terminliste.color')"
            data-testid="terminliste-config-source-color"
          />
          <input
            v-model="source.label"
            type="text"
            :placeholder="$t('widgets.terminliste.sourceLabelPlaceholder')"
            class="flex-1 min-w-0 bg-gray-800 border border-gray-700 rounded px-2 py-1 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
            data-testid="terminliste-config-source-label"
          />
        </div>
      </div>
      <button
        v-if="cfg.sources.length < MAX_SOURCES"
        type="button"
        class="w-full py-1 text-xs rounded border border-dashed border-gray-600 text-gray-400 hover:border-gray-400"
        data-testid="terminliste-config-add-source"
        @click="addSource"
      >{{ $t('widgets.terminliste.addSource') }}</button>
    </div>

    <!-- Darstellung -->
    <div class="border-t border-gray-700 pt-3 space-y-3">
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.terminliste.days') }}</label>
        <input
          v-model.number="cfg.days"
          type="number"
          :min="MIN_DAYS"
          :max="MAX_DAYS"
          class="w-24 bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          data-testid="terminliste-config-days"
        />
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.terminliste.separator') }}</label>
        <select
          v-model="cfg.separator"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          data-testid="terminliste-config-separator"
        >
          <option v-for="opt in separatorOptions" :key="opt.value" :value="opt.value">{{ opt.label }}</option>
        </select>
      </div>
      <div>
        <label class="block text-xs text-gray-400 mb-1">{{ $t('widgets.terminliste.datePattern') }}</label>
        <input
          v-model="cfg.date_pattern"
          type="text"
          :placeholder="DEFAULT_DATE_PATTERN"
          class="w-full bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm text-gray-100 focus:outline-none focus:border-blue-500"
          data-testid="terminliste-config-date-pattern"
        />
        <p class="mt-1 text-xs text-gray-500">{{ $t('widgets.terminliste.datePatternHint') }}</p>
      </div>
      <label class="flex items-center gap-2 text-sm text-gray-300">
        <input v-model="cfg.show_time" type="checkbox" data-testid="terminliste-config-show-time" />
        {{ $t('widgets.terminliste.showTime') }}
      </label>
      <label class="flex items-center gap-2 text-sm text-gray-300">
        <input v-model="cfg.show_location" type="checkbox" data-testid="terminliste-config-show-location" />
        {{ $t('widgets.terminliste.showLocation') }}
      </label>
      <label class="flex items-center gap-2 text-sm text-gray-300">
        <input v-model="cfg.show_empty_days" type="checkbox" data-testid="terminliste-config-show-empty-days" />
        {{ $t('widgets.terminliste.showEmptyDays') }}
      </label>
    </div>
  </div>
</template>
