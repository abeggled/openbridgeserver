// Vitest setup: provide a tiny localStorage shim and stub for window.matchMedia
// so views that import API client (which inspects localStorage at module load
// for auth tokens) don't crash inside happy-dom.

// localStorage shim: happy-dom does not reliably expose a working Storage in
// this environment, so views/stores that read localStorage at init (settings
// store, API client auth token) would crash. Guarded → no-op where the runtime
// already provides a real Storage.
if (typeof globalThis.localStorage === 'undefined' || typeof globalThis.localStorage.getItem !== 'function') {
  const store = new Map()
  globalThis.localStorage = {
    getItem: (k) => (store.has(k) ? store.get(k) : null),
    setItem: (k, v) => { store.set(k, String(v)) },
    removeItem: (k) => { store.delete(k) },
    clear: () => { store.clear() },
    key: (i) => [...store.keys()][i] ?? null,
    get length() { return store.size },
  }
}

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
import { beforeEach } from 'vitest'
import { createPinia, setActivePinia } from 'pinia'
import { createI18n } from 'vue-i18n'
import de from '@/locales/de.json'
import en from '@/locales/en.json'

const i18n = createI18n({ legacy: false, locale: 'de', fallbackLocale: 'de', messages: { de, en } })

beforeEach(() => {
  const pinia = createPinia()
  setActivePinia(pinia)
  config.global.plugins = [i18n, pinia]
})
