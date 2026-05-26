<template>
  <div class="relative inline-block" ref="containerRef">
    <button
      type="button"
      class="btn-secondary btn-sm"
      data-testid="time-filter-trigger"
      @click="open = !open"
    >
      ⏱ {{ triggerLabel }}
    </button>

    <div
      v-if="open"
      class="absolute z-30 mt-1 left-0 w-96 bg-white dark:bg-surface-800 border border-slate-200 dark:border-slate-700 rounded-lg shadow-xl p-4 flex flex-col gap-4"
      data-testid="time-filter-popover"
    >
      <!-- Modus 1: Bereich -->
      <section
        :class="['flex flex-col gap-2', pointModeActive ? 'opacity-40' : '']"
        data-testid="range-section"
      >
        <div class="flex items-center justify-between">
          <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-500">Bereich</h4>
          <span v-if="pointModeActive" class="text-xs text-amber-500" data-testid="range-overridden">
            wird durch Zeitpunkt-Eingabe überschrieben
          </span>
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">Ab</label>
          <input
            v-model="form.fromText"
            type="text"
            class="input"
            data-testid="input-from"
            placeholder="-1h 10min, 14:30, 2026-05-11 14:30"
            :disabled="pointModeActive"
          />
          <span :class="['text-xs', fromPreview.error ? 'text-red-500' : 'text-slate-400']" data-testid="preview-from">
            {{ fromPreview.label }}
          </span>
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">Bis</label>
          <input
            v-model="form.toText"
            type="text"
            class="input"
            data-testid="input-to"
            placeholder="-5min, 14:50, leer = jetzt"
            :disabled="pointModeActive"
          />
          <span :class="['text-xs', toPreview.error ? 'text-red-500' : 'text-slate-400']" data-testid="preview-to">
            {{ toPreview.label }}
          </span>
        </div>
      </section>

      <hr class="border-slate-200 dark:border-slate-700" />

      <!-- Modus 2: Zeitpunkt ± Spanne -->
      <section class="flex flex-col gap-2" data-testid="point-section">
        <h4 class="text-xs font-semibold uppercase tracking-wider text-slate-500">Zeitpunkt ± Spanne</h4>
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">Um</label>
          <input
            v-model="form.pointText"
            type="text"
            class="input"
            data-testid="input-point"
            placeholder="-1h, 14:00, 2026-05-11 14:00"
          />
          <span :class="['text-xs', pointPreview.error ? 'text-red-500' : 'text-slate-400']" data-testid="preview-point">
            {{ pointPreview.label }}
          </span>
        </div>
        <div class="flex flex-col gap-1">
          <label class="text-xs text-slate-500">±</label>
          <input
            v-model="form.spanText"
            type="text"
            class="input"
            data-testid="input-span"
            placeholder="10min, 1h"
          />
          <span :class="['text-xs', spanPreview.error ? 'text-red-500' : 'text-slate-400']" data-testid="preview-span">
            {{ spanPreview.label }}
          </span>
        </div>
      </section>

      <div class="flex justify-between pt-2">
        <button type="button" class="btn-ghost btn-sm" data-testid="btn-reset" @click="reset">
          Filter aus
        </button>
        <button type="button" class="btn-primary btn-sm" data-testid="btn-apply" @click="apply">
          Anwenden
        </button>
      </div>
    </div>
  </div>
</template>

<script setup>
import { ref, reactive, computed, watch, onMounted, onUnmounted } from 'vue'
import {
  parseDurationToken,
  parseTimePointToken,
  formatTimeFilter,
} from '@/composables/useTimeFilterParser'

const props = defineProps({
  modelValue: { type: Object, default: null },
})
const emit = defineEmits(['update:modelValue'])

const containerRef = ref(null)
const open = ref(false)

const form = reactive({
  fromText: '',
  toText: '',
  pointText: '',
  spanText: '',
})

// Hydrate form from incoming modelValue when popover opens
function hydrate(val) {
  form.fromText = ''
  form.toText = ''
  form.pointText = ''
  form.spanText = ''
  if (!val) return
  if (val.mode === 'point') {
    form.pointText = serializeBound(val.point)
    form.spanText = serializeDuration(val.span)
  } else {
    form.fromText = serializeBound(val.from)
    form.toText = serializeBound(val.to)
  }
}

function serializeBound(b) {
  if (!b) return ''
  if (b instanceof Date) {
    const pad = (n) => String(n).padStart(2, '0')
    return `${b.getFullYear()}-${pad(b.getMonth() + 1)}-${pad(b.getDate())} ${pad(b.getHours())}:${pad(b.getMinutes())}`
  }
  if (typeof b === 'object' && Number.isFinite(b.seconds)) {
    const body = formatDurationLocal(b.seconds)
    return b.sign < 0 ? `-${body}` : body
  }
  return ''
}

function serializeDuration(d) {
  if (!d || !Number.isFinite(d.seconds)) return ''
  return formatDurationLocal(d.seconds)
}

function formatDurationLocal(seconds) {
  let total = Math.abs(seconds)
  if (total === 0) return ''
  const days = Math.floor(total / 86400)
  total -= days * 86400
  const hours = Math.floor(total / 3600)
  total -= hours * 3600
  const minutes = Math.floor(total / 60)
  const secs = total - minutes * 60
  const parts = []
  if (days) parts.push(`${days}d`)
  if (hours) parts.push(`${hours}h`)
  if (minutes) parts.push(`${minutes}min`)
  if (secs) parts.push(`${secs}s`)
  return parts.join(' ')
}

// Sync modelValue → form on open
watch(open, (val) => {
  if (val) hydrate(props.modelValue)
})
watch(() => props.modelValue, (val) => {
  if (!open.value) hydrate(val)
}, { immediate: true })

// Parse to preview struct (date or duration), with error flag
function previewBound(text) {
  const raw = String(text || '').trim()
  if (!raw) return { value: null, label: '', error: false }
  if (raw.startsWith('-') || raw.startsWith('+')) {
    const d = parseDurationToken(raw)
    if (!d) return { value: null, label: 'ungültig', error: true }
    const target = new Date(Date.now() + d.sign * d.seconds * 1000)
    return { value: d, label: `→ ${labelDate(target)}`, error: false }
  }
  const dt = parseTimePointToken(raw)
  if (!dt) return { value: null, label: 'ungültig', error: true }
  return { value: dt, label: `→ ${labelDate(dt)}`, error: false }
}

function previewDuration(text) {
  const raw = String(text || '').trim()
  if (!raw) return { value: null, label: '', error: false }
  const d = parseDurationToken(raw)
  if (!d) return { value: null, label: 'ungültig', error: true }
  return { value: { ...d, sign: 1 }, label: `→ ± ${formatDurationLocal(d.seconds)}`, error: false }
}

function labelDate(d) {
  const pad = (n) => String(n).padStart(2, '0')
  return `${pad(d.getHours())}:${pad(d.getMinutes())}:${pad(d.getSeconds())}`
}

const fromPreview = computed(() => previewBound(form.fromText))
const toPreview = computed(() => previewBound(form.toText))
const pointPreview = computed(() => previewBound(form.pointText))
const spanPreview = computed(() => previewDuration(form.spanText))

const pointModeActive = computed(() => Boolean(pointPreview.value.value) && Boolean(spanPreview.value.value))

const triggerLabel = computed(() => formatTimeFilter(props.modelValue))

function resolveToDate(b) {
  if (b instanceof Date) return b
  if (b && Number.isFinite(b.seconds)) {
    return new Date(Date.now() + b.sign * b.seconds * 1000)
  }
  return null
}

function apply() {
  if (pointModeActive.value) {
    emit('update:modelValue', {
      mode: 'point',
      point: resolveToDate(pointPreview.value.value),
      span: spanPreview.value.value,
    })
  } else if (fromPreview.value.value || toPreview.value.value) {
    emit('update:modelValue', {
      mode: 'range',
      from: fromPreview.value.value,
      to: toPreview.value.value,
    })
  } else {
    emit('update:modelValue', null)
  }
  open.value = false
}

function reset() {
  form.fromText = ''
  form.toText = ''
  form.pointText = ''
  form.spanText = ''
  emit('update:modelValue', null)
  open.value = false
}

function onClickOutside(e) {
  if (containerRef.value && !containerRef.value.contains(e.target)) {
    open.value = false
  }
}

onMounted(() => document.addEventListener('mousedown', onClickOutside))
onUnmounted(() => document.removeEventListener('mousedown', onClickOutside))
</script>
