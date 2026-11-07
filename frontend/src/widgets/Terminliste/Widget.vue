<script setup lang="ts">
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import { useDatapointsStore } from '@/stores/datapoints'
import { useFormatStore } from '@/stores/format'
import { formatPattern } from '@/utils/datePattern'
import type { DataPointValue } from '@/types'
import {
  buildDays,
  clampDays,
  isoDateIn,
  parseEvents,
  type DayEvent,
  type DayGroup,
} from './calendarEvents'
import { DEFAULT_DATE_PATTERN, SEPARATORS, type CalendarSource, type SeparatorKey } from './config'

const props = defineProps<{
  config: Record<string, unknown>
  datapointId: string | null
  value: DataPointValue | null
  editorMode: boolean
  h?: number
}>()

const { t } = useI18n()
const dpStore = useDatapointsStore()
const format = useFormatStore()

// Re-evaluated every minute so "Heute"/"Morgen" move on at midnight.
const now = ref(new Date())
let timer: ReturnType<typeof setInterval> | undefined
onMounted(() => {
  timer = setInterval(() => { now.value = new Date() }, 60_000)
})
onBeforeUnmount(() => clearInterval(timer))

const title = computed(() => (props.config.label as string | undefined) ?? '')
const days = computed(() => clampDays(props.config.days))
const showTime = computed(() => (props.config.show_time as boolean | undefined) ?? true)
const showLocation = computed(() => (props.config.show_location as boolean | undefined) ?? false)
const showEmptyDays = computed(() => (props.config.show_empty_days as boolean | undefined) ?? false)
const datePattern = computed(() => (props.config.date_pattern as string | undefined) || DEFAULT_DATE_PATTERN)
const separatorKey = computed<SeparatorKey>(() => {
  const key = props.config.separator as SeparatorKey | undefined
  return key && key in SEPARATORS ? key : 'semicolon'
})
const inline = computed(() => separatorKey.value !== 'newline')

const sources = computed<CalendarSource[]>(() =>
  ((props.config.sources as CalendarSource[] | undefined) ?? []).filter((s) => !!s?.datapoint_id),
)

const groups = computed<DayGroup[]>(() => {
  const today = isoDateIn(now.value, format.timezone)
  const entries = sources.value.map((source) => ({
    events: parseEvents(dpStore.getValue(source.datapoint_id)?.v),
    color: source.color ?? '',
    label: source.label ?? '',
  }))
  return buildDays(entries, today, days.value, showEmptyDays.value)
})

function dayLabel(group: DayGroup): string {
  if (group.kind === 'today') return t('widgets.terminliste.today')
  if (group.kind === 'tomorrow') return t('widgets.terminliste.tomorrow')
  // The date carries no time of day; noon UTC keeps every weekday stable.
  return formatPattern(new Date(`${group.date}T12:00:00Z`), datePattern.value, 'UTC', format.localeNames())
}

function eventText(event: DayEvent): string {
  const parts: string[] = []
  if (showTime.value && event.start) parts.push(event.start)
  parts.push(event.summary)
  if (showLocation.value && event.location) parts.push(`(${event.location})`)
  return parts.join(' ')
}
</script>

<template>
  <div class="flex flex-col h-full p-3 select-none gap-1 min-h-0" data-testid="terminliste">
    <span v-if="title" class="text-xs text-gray-500 dark:text-gray-400 truncate">{{ title }}</span>

    <p v-if="sources.length === 0" class="text-sm text-gray-400 dark:text-gray-500" data-testid="terminliste-no-sources">
      {{ $t('widgets.terminliste.noSources') }}
    </p>
    <p v-else-if="groups.length === 0" class="text-sm text-gray-400 dark:text-gray-500" data-testid="terminliste-empty">
      {{ $t('widgets.terminliste.noEvents', { n: days }) }}
    </p>

    <div v-else class="flex flex-col gap-1 overflow-y-auto min-h-0">
      <div
        v-for="group in groups"
        :key="group.date"
        class="flex gap-2 text-sm leading-snug"
        data-testid="terminliste-day"
      >
        <span
          :class="[
            'w-24 shrink-0',
            group.kind === 'today' ? 'font-semibold text-gray-900 dark:text-gray-100' : 'text-gray-500 dark:text-gray-400',
          ]"
          data-testid="terminliste-day-label"
        >{{ dayLabel(group) }}</span>
        <span v-if="group.events.length === 0" class="text-gray-400 dark:text-gray-500">{{ $t('widgets.terminliste.noEventsOnDay') }}</span>
        <span v-else-if="inline" class="min-w-0 text-gray-800 dark:text-gray-200" data-testid="terminliste-events">
          <template v-for="(event, i) in group.events" :key="i">
            <span v-if="i !== 0" class="text-gray-400 dark:text-gray-500">{{ SEPARATORS[separatorKey] }}</span>
            <span :style="event.color ? { color: event.color } : undefined" :title="event.sourceLabel || undefined">{{ eventText(event) }}</span>
          </template>
        </span>
        <span v-else class="flex flex-col min-w-0 text-gray-800 dark:text-gray-200" data-testid="terminliste-events">
          <span
            v-for="(event, i) in group.events"
            :key="i"
            :style="event.color ? { color: event.color } : undefined"
            :title="event.sourceLabel || undefined"
          >{{ eventText(event) }}</span>
        </span>
      </div>
    </div>
  </div>
</template>
