import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi:      { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:  { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountGenericPanel(data = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: {
        id: 'clamp1',
        type: 'clamp',
        data: { min: 0, max: 100, ...data },
      },
      nodeTypes: [{
        type: 'clamp',
        label: 'Limiter',
        description: 'Clamps the input value to [Min, Max].',
        config_schema: {
          min: { type: 'number', default: 0, label: 'Minimum' },
          max: { type: 'number', default: 100, label: 'Maximum' },
        },
      }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

// clamp has no dedicated NodeConfigPanel block — this exercises the generic
// "all other node types" fallback (config_schema-driven form rendering),
// which no other spec covers.
describe('NodeConfigPanel generic fallback (no dedicated block)', () => {
  it('renders the node description', async () => {
    const wrapper = await mountGenericPanel()
    await flushPromises()
    // Real i18n instance (locale 'de') resolves logic.nodeDescriptions.clamp,
    // taking precedence over the fake nodeTypes prop's description.
    expect(wrapper.text()).toContain('Begrenzt den Eingangswert auf [Min, Max]')
    wrapper.unmount()
  })

  it('renders a labeled input per config_schema field, bound to node data', async () => {
    const wrapper = await mountGenericPanel({ min: 5, max: 50 })
    await flushPromises()
    const labels = wrapper.findAll('.label').map(l => l.text())
    expect(labels).toContain('Minimum')
    expect(labels).toContain('Maximum')
    const inputs = wrapper.findAll('input[type="number"]')
    expect(inputs.map(i => i.element.value)).toEqual(['5', '50'])
    wrapper.unmount()
  })

  it('emits update when a generic field changes', async () => {
    const wrapper = await mountGenericPanel()
    await flushPromises()
    const maxInput = wrapper.findAll('input[type="number"]')[1]
    await maxInput.setValue(75)
    await maxInput.trigger('change')
    expect(wrapper.emitted('update')[0][0]).toMatchObject({ max: 75 })
    wrapper.unmount()
  })
})
