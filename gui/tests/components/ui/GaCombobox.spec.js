import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountGaCombobox(api = {}) {
  const knxprojApi = {
    listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }),
    ...api,
  }
  vi.doMock('@/api/client', () => ({ knxprojApi }))
  const mod = await import('@/components/ui/GaCombobox.vue')
  const wrapper = mount(mod.default, { attachTo: document.body })
  return { wrapper, knxprojApi }
}

describe('GaCombobox', () => {
  it('uses translated default placeholder and empty-state text', async () => {
    const { wrapper } = await mountGaCombobox()
    const input = wrapper.find('input')

    expect(input.attributes('placeholder')).toBe('z.B. 1/2/3 oder Name suchen …')

    await input.trigger('focus')
    input.element.value = '1/2'
    await input.trigger('input')
    await new Promise((resolve) => setTimeout(resolve, 260))
    await flushPromises()

    expect(wrapper.text()).toContain('Keine Gruppenadressen gefunden')
    expect(wrapper.text()).toContain('(.knxproj noch nicht importiert)')
  })
})
