import { beforeEach, describe, expect, it, vi } from 'vitest'
import { useLocalizedText } from './useLocalizedText'

const messages = vi.hoisted(() => ({
  values: new Map<string, string>(),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    locale: { value: 'en' },
    te: (key: string) => messages.values.has(key),
    t: (key: string) => messages.values.get(key) ?? key,
  }),
}))

beforeEach(() => {
  messages.values = new Map([
    ['widgets.buttongroup.title', 'Button group'],
    ['widgetGroups.Steuerung', 'Control'],
  ])
})

describe('useLocalizedText', () => {
  it('translates known widget labels and keeps plain labels unchanged', () => {
    const { widgetLabel } = useLocalizedText()

    expect(widgetLabel('widgets.buttongroup.title')).toBe('Button group')
    expect(widgetLabel('Plain legacy label')).toBe('Plain legacy label')
  })

  it('translates known widget groups and falls back to the raw group name', () => {
    const { widgetGroupLabel } = useLocalizedText()

    expect(widgetGroupLabel('Steuerung')).toBe('Control')
    expect(widgetGroupLabel('Custom group')).toBe('Custom group')
  })
})
