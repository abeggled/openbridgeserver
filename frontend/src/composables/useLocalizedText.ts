import { useI18n } from 'vue-i18n'

export function useLocalizedText() {
  const { t, te, locale } = useI18n()

  function optionalKey(value: string): string {
    return te(value) ? t(value) : value
  }

  function widgetLabel(label: string): string {
    return optionalKey(label)
  }

  function widgetGroupLabel(group: string): string {
    const key = `widgetGroups.${group}`
    return te(key) ? t(key) : group
  }

  return {
    locale,
    optionalKey,
    widgetLabel,
    widgetGroupLabel,
  }
}
