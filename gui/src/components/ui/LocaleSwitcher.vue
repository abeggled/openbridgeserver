<template>
  <div class="flex items-center gap-2">
    <label class="label shrink-0">{{ $t('settings.general.language') }}</label>
    <select
      :value="locale"
      @change="onChange"
      class="input text-sm"
      data-testid="select-language"
    >
      <option v-for="l in SUPPORTED_LOCALES" :key="l.code" :value="l.code">
        {{ l.label }}
      </option>
    </select>
  </div>
</template>

<script setup>
import { useI18n } from 'vue-i18n'
import { SUPPORTED_LOCALES, setLocale } from '@/i18n'
import { useSettingsStore } from '@/stores/settings'

const { locale } = useI18n()
const settings = useSettingsStore()

async function onChange(e) {
  const language = e.target.value
  setLocale(language)
  try {
    await settings.save(settings.timezone, settings.dateFormat, settings.timeFormat, language)
  } catch {
    // The browser-local selection still remains usable while the backend is unavailable.
  }
}
</script>
