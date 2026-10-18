// @vitest-environment jsdom
import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import UhrConfig from './Config.vue'

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))

function mountConfig(modelValue: Record<string, unknown>) {
  return mount(UhrConfig, {
    props: { modelValue },
    global: { mocks: { $t: (key: string) => key } },
  })
}

function lastEmitted(w: ReturnType<typeof mountConfig>): Record<string, unknown> {
  const events = w.emitted('update:modelValue') ?? []
  return events[events.length - 1][0] as Record<string, unknown>
}

describe('Uhr Config.vue — minute dots', () => {
  it('offers the option only in word clock mode', () => {
    expect(mountConfig({ mode: 'digital' }).find('[data-testid="uhr-config-minute-dots"]').exists()).toBe(false)
    expect(mountConfig({ mode: 'analog' }).find('[data-testid="uhr-config-minute-dots"]').exists()).toBe(false)

    const w = mountConfig({ mode: 'wortuhr' })
    expect(w.find('[data-testid="uhr-config-minute-dots"]').exists()).toBe(true)
    expect(w.text()).toContain('widgets.uhr.minuteDots')
    expect(w.text()).toContain('widgets.uhr.minuteDotsHint')
  })

  it('defaults to off for widgets saved without the key', () => {
    const w = mountConfig({ mode: 'wortuhr' })

    expect((w.get('[data-testid="uhr-config-minute-dots"]').element as HTMLInputElement).checked).toBe(false)
  })

  it('loads a stored value and emits the toggled one', async () => {
    const w = mountConfig({ mode: 'wortuhr', minuteDots: true })
    const box = w.get('[data-testid="uhr-config-minute-dots"]')
    expect((box.element as HTMLInputElement).checked).toBe(true)

    await box.setValue(false)
    expect(lastEmitted(w)).toMatchObject({ mode: 'wortuhr', minuteDots: false })

    await box.setValue(true)
    expect(lastEmitted(w)).toMatchObject({ minuteDots: true })
  })
})

describe('Uhr Config.vue — translated texts', () => {
  it('renders placeholders and hints through i18n keys', () => {
    const w = mountConfig({ mode: 'analog' })

    expect(w.get('input[type="color"]').attributes('title')).toBe('widgets.uhr.accentColor')
    expect(w.get('input[list="tz-vorschlaege"]').attributes('placeholder')).toBe('widgets.uhr.timezonePlaceholder')
    expect(w.text()).toContain('widgets.uhr.timezoneFormat')
    expect(w.text()).toContain('widgets.uhr.optional')
    const textInputs = w.findAll('input[type="text"]')
    const labelInput = textInputs[textInputs.length - 1]
    expect(labelInput.attributes('placeholder')).toBe('widgets.uhr.labelPlaceholder')
  })
})
