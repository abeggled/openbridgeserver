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

async function mountHcPanel(data = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: {
        id: 'hc1',
        type: 'host_check',
        data: { host: '', timeout_s: 2, count: 1, ...data },
      },
      nodeTypes: [{ type: 'host_check', label: 'Host Check', description: 'Pings a host.' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

describe('NodeConfigPanel host_check — renders inputs', () => {
  it('renders the host input field', async () => {
    const wrapper = await mountHcPanel()
    await flushPromises()
    expect(wrapper.find('[data-testid="hc-host"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('renders the timeout input field', async () => {
    const wrapper = await mountHcPanel()
    await flushPromises()
    expect(wrapper.find('[data-testid="hc-timeout"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('renders the count input field', async () => {
    const wrapper = await mountHcPanel()
    await flushPromises()
    expect(wrapper.find('[data-testid="hc-count"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('prefills host from node data', async () => {
    const wrapper = await mountHcPanel({ host: '192.168.1.1' })
    await flushPromises()
    expect(wrapper.find('[data-testid="hc-host"]').element.value).toBe('192.168.1.1')
    wrapper.unmount()
  })

  it('emits update when host changes', async () => {
    const wrapper = await mountHcPanel({ host: '' })
    await flushPromises()
    const input = wrapper.find('[data-testid="hc-host"]')
    await input.setValue('10.0.0.1')
    await input.trigger('change')
    expect(wrapper.emitted('update')).toBeTruthy()
    wrapper.unmount()
  })

  it('emits update when timeout changes', async () => {
    const wrapper = await mountHcPanel()
    await flushPromises()
    const input = wrapper.find('[data-testid="hc-timeout"]')
    await input.setValue(5)
    await input.trigger('change')
    expect(wrapper.emitted('update')).toBeTruthy()
    wrapper.unmount()
  })

  it('emits update when count changes', async () => {
    const wrapper = await mountHcPanel()
    await flushPromises()
    const input = wrapper.find('[data-testid="hc-count"]')
    await input.setValue(3)
    await input.trigger('change')
    expect(wrapper.emitted('update')).toBeTruthy()
    wrapper.unmount()
  })
})
