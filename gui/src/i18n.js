import { createI18n } from 'vue-i18n'
import de from './locales/de.json'
import en from './locales/en.json'

/**
 * Supported locales.
 * To add a new language: add its JSON file to src/locales/ and import it here.
 * The JSON files are the Weblate source/target resources — en.json is the
 * authoritative source language (what Weblate volunteers translate from) and
 * must be complete; other locales are managed via Weblate and pulled with `wlc pull`.
 */
export const SUPPORTED_LOCALES = [
  { code: 'de', label: 'Deutsch' },
  { code: 'en', label: 'English' },
]

function detectLocale() {
  const stored = localStorage.getItem('obs-locale')
  if (stored && SUPPORTED_LOCALES.some(l => l.code === stored)) return stored
  const browser = navigator.language.split('-')[0]
  if (SUPPORTED_LOCALES.some(l => l.code === browser)) return browser
  return 'de'
}

const i18n = createI18n({
  legacy: false,          // use Composition API mode
  locale: detectLocale(),
  fallbackLocale: 'en',  // en.json is the authoritative source; fall back to it for missing keys
  messages: { de, en },
})

export function setLocale(code) {
  i18n.global.locale.value = code
  localStorage.setItem('obs-locale', code)
  document.documentElement.lang = code
}

export default i18n

