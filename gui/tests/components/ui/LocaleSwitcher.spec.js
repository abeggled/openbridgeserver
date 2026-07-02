import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let setLocaleMock

beforeEach(() => {
  vi.resetModules()
  setLocaleMock = vi.fn()
  vi.doMock('@/i18n', () => ({
    SUPPORTED_LOCALES: [
      { code: 'de', label: 'Deutsch' },
      { code: 'en', label: 'English' },
    ],
    setLocale: setLocaleMock,
  }))
})

afterEach(() => {
  vi.doUnmock('@/i18n')
})

async function mountSwitcher() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { default: LocaleSwitcher } = await import('@/components/ui/LocaleSwitcher.vue')
  return mount(LocaleSwitcher, { global: { plugins: [pinia] } })
}

describe('LocaleSwitcher', () => {
  it('renders the language select element', async () => {
    const w = await mountSwitcher()
    expect(w.find('[data-testid="select-language"]').exists()).toBe(true)
  })

  it('renders an option for each supported locale', async () => {
    const w = await mountSwitcher()
    const options = w.findAll('option')
    expect(options.length).toBe(2)
    expect(options[0].text()).toBe('Deutsch')
    expect(options[1].text()).toBe('English')
  })

  it('option values match locale codes', async () => {
    const w = await mountSwitcher()
    const options = w.findAll('option')
    expect(options[0].attributes('value')).toBe('de')
    expect(options[1].attributes('value')).toBe('en')
  })

  it('calls setLocale with the selected code when changed', async () => {
    const w = await mountSwitcher()
    const select = w.find('[data-testid="select-language"]')
    await select.setValue('en')
    expect(setLocaleMock).toHaveBeenCalledWith('en')
  })

  it('calls setLocale once per change', async () => {
    const w = await mountSwitcher()
    await w.find('[data-testid="select-language"]').setValue('en')
    await w.find('[data-testid="select-language"]').setValue('de')
    expect(setLocaleMock).toHaveBeenCalledTimes(2)
  })
})
