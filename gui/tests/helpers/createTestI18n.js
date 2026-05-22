import { createI18n } from 'vue-i18n'
import de from '@/locales/de.json'
import en from '@/locales/en.json'

/**
 * Returns a vue-i18n instance suitable for use in unit tests.
 * Always uses 'de' locale so snapshots stay predictable.
 */
export function createTestI18n() {
  return createI18n({
    legacy: false,
    locale: 'de',
    fallbackLocale: 'de',
    messages: { de, en },
  })
}
