// Vitest setup: provide a tiny localStorage shim and stub for window.matchMedia
// so views that import API client (which inspects localStorage at module load
// for auth tokens) don't crash inside happy-dom.

if (typeof globalThis.matchMedia !== 'function') {
  globalThis.matchMedia = () => ({
    matches: false,
    addEventListener: () => {},
    removeEventListener: () => {},
    addListener: () => {},
    removeListener: () => {},
  })
}

// Install vue-i18n globally for all Vue Test Utils mount() calls.
// Components that use $t() / useI18n() require the plugin to be present;
// without it they throw "Need to install with app.use".
import { config } from '@vue/test-utils'
import { createI18n } from 'vue-i18n'
import de from '@/locales/de.json'
import en from '@/locales/en.json'

config.global.plugins = config.global.plugins ?? []
config.global.plugins.push(
  createI18n({ legacy: false, locale: 'de', fallbackLocale: 'de', messages: { de, en } }),
)
