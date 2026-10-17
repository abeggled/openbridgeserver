<template>
  <!-- Styled with utilities: the .section-header/.hint helpers are only defined
       by BindingForm, and this component is also used in the logic editor. -->
  <div class="flex flex-col gap-4" data-testid="pushover-options">
    <div class="flex items-center justify-between">
      <div class="section-header grow text-xs font-semibold uppercase tracking-wider text-blue-500 dark:text-blue-400 border-b border-slate-200 dark:border-slate-700 pb-1">{{ $t('adapters.pushoverOptions.section') }}</div>
      <HelpButton help-id="adapters-message-pushover" compact />
    </div>
    <p class="hint text-xs text-slate-500 mt-0.5">{{ $t('adapters.pushoverOptions.intro') }}</p>

    <div class="form-group">
      <label class="label">{{ $t('adapters.pushoverOptions.imageUrl') }}</label>
      <input v-model="options.image_url" class="input text-sm font-mono" data-testid="pushover-image-url" @change="changed" />
      <p v-if="errors.image_url" class="text-xs text-red-500" data-testid="pushover-error-image_url">{{ $t(errors.image_url, { max: PUSHOVER_MAX_LENGTHS.image_url }) }}</p>
      <p class="hint text-xs text-slate-500 mt-0.5">{{ $t('adapters.pushoverOptions.imageUrlHint') }}</p>
    </div>

    <div class="grid grid-cols-2 gap-4">
      <div class="form-group">
        <label class="label">{{ $t('adapters.pushoverOptions.url') }}</label>
        <input v-model="options.url" class="input text-sm font-mono" data-testid="pushover-url" @change="changed" />
        <p v-if="errors.url" class="text-xs text-red-500" data-testid="pushover-error-url">{{ $t(errors.url, { max: PUSHOVER_MAX_LENGTHS.url }) }}</p>
      </div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.pushoverOptions.urlTitle') }}</label>
        <input v-model="options.url_title" class="input text-sm" data-testid="pushover-url-title" @change="changed" />
        <p v-if="errors.url_title" class="text-xs text-red-500" data-testid="pushover-error-url_title">{{ $t(errors.url_title, { max: PUSHOVER_MAX_LENGTHS.url_title }) }}</p>
      </div>
    </div>
    <p v-if="hint" class="hint text-xs text-slate-500 mt-0.5">{{ hint }}</p>

    <div class="grid grid-cols-2 gap-4">
      <div class="form-group">
        <label class="label">{{ $t('adapters.pushoverOptions.priority') }}</label>
        <select :value="priorityValue" class="input text-sm" data-testid="pushover-priority" @change="onPriorityChange">
          <option value="">{{ $t('adapters.pushoverOptions.priorityDefault') }}</option>
          <option v-for="option in priorityOptions" :key="option.value" :value="String(option.value)">{{ option.label }}</option>
        </select>
      </div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.pushoverOptions.format') }}</label>
        <select :value="formatValue" class="input text-sm" data-testid="pushover-format" @change="onFormatChange">
          <option v-for="option in formatOptions" :key="option.value" :value="option.value">{{ option.label }}</option>
        </select>
      </div>
    </div>

    <div v-if="isEmergency" class="grid grid-cols-2 gap-4">
      <div class="form-group">
        <label class="label">{{ $t('adapters.pushoverOptions.retry') }}</label>
        <input v-model.number="options.retry" type="number" :min="PUSHOVER_MIN_RETRY_SECONDS" step="1" class="input text-sm" data-testid="pushover-retry" @change="changed" />
        <p v-if="errors.retry" class="text-xs text-red-500" data-testid="pushover-error-retry">{{ $t(errors.retry) }}</p>
      </div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.pushoverOptions.expire') }}</label>
        <input v-model.number="options.expire" type="number" min="1" :max="PUSHOVER_MAX_EXPIRE_SECONDS" step="1" class="input text-sm" data-testid="pushover-expire" @change="changed" />
        <p v-if="errors.expire" class="text-xs text-red-500" data-testid="pushover-error-expire">{{ $t(errors.expire) }}</p>
      </div>
    </div>

    <div class="grid grid-cols-3 gap-4">
      <div class="form-group">
        <label class="label">{{ $t('adapters.pushoverOptions.sound') }}</label>
        <input v-model="options.sound" :list="soundListId" class="input text-sm" :placeholder="$t('adapters.pushoverOptions.soundDefault')" data-testid="pushover-sound" @change="changed" />
        <datalist :id="soundListId">
          <option v-for="sound in PUSHOVER_SOUNDS" :key="sound" :value="sound" />
        </datalist>
        <p v-if="errors.sound" class="text-xs text-red-500" data-testid="pushover-error-sound">{{ $t(errors.sound, { max: PUSHOVER_MAX_LENGTHS.sound }) }}</p>
      </div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.pushoverOptions.ttl') }}</label>
        <input v-model.number="options.ttl" type="number" min="1" step="1" class="input text-sm" data-testid="pushover-ttl" @change="changed" />
        <p v-if="errors.ttl" class="text-xs text-red-500" data-testid="pushover-error-ttl">{{ $t(errors.ttl) }}</p>
      </div>
      <div class="form-group">
        <label class="label">{{ $t('adapters.pushoverOptions.timestamp') }}</label>
        <input v-model.number="options.timestamp" type="number" min="1" step="1" class="input text-sm" data-testid="pushover-timestamp" @change="changed" />
        <p v-if="errors.timestamp" class="text-xs text-red-500" data-testid="pushover-error-timestamp">{{ $t(errors.timestamp) }}</p>
      </div>
    </div>
  </div>
</template>

<script>
let instanceCounter = 0
</script>

<script setup>
import { computed } from 'vue'
import { useI18n } from 'vue-i18n'
import HelpButton from '@/components/ui/HelpButton.vue'
import {
  PUSHOVER_EMERGENCY_PRIORITY,
  PUSHOVER_FORMATS,
  PUSHOVER_MAX_EXPIRE_SECONDS,
  PUSHOVER_MAX_LENGTHS,
  PUSHOVER_MIN_RETRY_SECONDS,
  PUSHOVER_PRIORITIES,
  PUSHOVER_SOUNDS,
  normalizeFormat,
  normalizePriority,
  pushoverOptionErrors,
} from '@/utils/pushoverOptions'

// `options` is mutated in place, like the binding sub-forms do with `cfg`.
const props = defineProps({
  options: { type: Object, required: true },
  hint: { type: String, default: '' },
  // The notification block can receive its URL through a connected input.
  urlFromInput: { type: Boolean, default: false },
})
const priority = defineModel('priority', { type: [Number, String, null], default: null })
const emit = defineEmits(['change'])
const { t } = useI18n()

const soundListId = `pushover-sounds-${++instanceCounter}`

const priorityValue = computed(() => {
  const value = normalizePriority(priority.value)
  return value == null ? '' : String(value)
})
const isEmergency = computed(() => normalizePriority(priority.value) === PUSHOVER_EMERGENCY_PRIORITY)
const errors = computed(() => pushoverOptionErrors(props.options, priority.value, { urlFromInput: props.urlFromInput }))

const priorityOptions = computed(() => PUSHOVER_PRIORITIES.map((value) => ({
  value,
  label: t(`adapters.pushoverOptions.priorities.p${value < 0 ? `m${-value}` : value}`),
})))
const formatOptions = computed(() => PUSHOVER_FORMATS.map((value) => ({
  value,
  label: t(`adapters.pushoverOptions.formats.${value}`),
})))

// Unset or unknown formats show — and behave — as plain text.
const formatValue = computed(() => normalizeFormat(props.options.format))

function onFormatChange(event) {
  props.options.format = event.target.value
  changed()
}

function changed() {
  emit('change')
}

function onPriorityChange(event) {
  priority.value = normalizePriority(event.target.value)
  changed()
}
</script>
