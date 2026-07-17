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

async function mountPanel(type, data, configSchema) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'node1', type, data },
      nodeTypes: [{
        type,
        label: type,
        config_schema: configSchema,
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
    const wrapper = await mountPanel(type, { [key]: 1 }, {
      [key]: { type: 'number', default: 1, min: 0 },
    })
    await flushPromises()

    const input = wrapper.find('input[type="number"]')
    expect(input.attributes('min')).toBe('0')
    expect(input.attributes('step')).toBe('any')
    await input.setValue(-1)
    await input.trigger('change')
    expect(wrapper.emitted('update').at(-1)[0][key]).toBe(0)

    wrapper.unmount()
  })

  it('clamps an upper bound declared with the JSON Schema alias', async () => {
    const wrapper = await mountPanel('bounded_number', { value: 1 }, {
      value: { type: 'number', default: 1, minimum: 0, maximum: 3 },
    })
    await flushPromises()

    const input = wrapper.find('input[type="number"]')
    expect(input.attributes('min')).toBe('0')
    expect(input.attributes('max')).toBe('3')
    await input.setValue(4)
    await input.trigger('change')
    expect(wrapper.emitted('update').at(-1)[0].value).toBe(3)

    wrapper.unmount()
  })

  it('preserves a cleared numeric value', async () => {
    const wrapper = await mountPanel('timer_delay', { delay_s: 1 }, {
      delay_s: { type: 'number', default: 1, min: 0 },
    })
    await flushPromises()

    const input = wrapper.find('input[type="number"]')
    await input.setValue('')
    await input.trigger('change')
    expect(wrapper.emitted('update').at(-1)[0].delay_s).toBe('')

    wrapper.unmount()
  })

  it('emits non-numeric schema fields unchanged', async () => {
    const wrapper = await mountPanel('text_config', { label: 'before' }, {
      label: { type: 'string', default: '' },
    })
    await flushPromises()

    const input = wrapper.find('input[type="text"]')
    await input.setValue('after')
    await input.trigger('change')
    expect(wrapper.emitted('update').at(-1)[0].label).toBe('after')

    wrapper.unmount()
  })

  it('leaves non-finite numeric values unchanged', async () => {
    const wrapper = await mountPanel('invalid_number', { value: 'not-a-number' }, {
      value: { type: 'number', default: 1, min: 0 },
    })
    await flushPromises()

    await wrapper.find('input[type="number"]').trigger('change')
    expect(wrapper.emitted('update').at(-1)[0].value).toBe('not-a-number')

    wrapper.unmount()
  })

  it('emits an unbounded numeric value unchanged', async () => {
    const wrapper = await mountPanel('unbounded_number', { value: 1 }, {
      value: { type: 'number', default: 1 },
    })
    await flushPromises()

    const input = wrapper.find('input[type="number"]')
    await input.setValue(7)
    await input.trigger('change')
    expect(wrapper.emitted('update').at(-1)[0].value).toBe(7)

    wrapper.unmount()
  })
})
