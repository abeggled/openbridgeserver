import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi: { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi: { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountTimer(type, key) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'timer1', type, data: { [key]: 1 } },
      nodeTypes: [{
        type,
        label: type,
        config_schema: { [key]: { type: 'number', default: 1, min: 0 } },
      }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

describe('NodeConfigPanel timer durations', () => {
  it.each([
    ['timer_delay', 'delay_s'],
    ['timer_pulse', 'duration_s'],
  ])('disallows negative values for %s', async (type, key) => {
    const wrapper = await mountTimer(type, key)
    await flushPromises()

    const input = wrapper.find('input[type="number"]')
    expect(input.attributes('min')).toBe('0')
    expect(input.attributes('step')).toBe('any')
    await input.setValue(-1)
    await input.trigger('change')
    expect(wrapper.emitted('update').at(-1)[0][key]).toBe(0)

    wrapper.unmount()
  })
})
