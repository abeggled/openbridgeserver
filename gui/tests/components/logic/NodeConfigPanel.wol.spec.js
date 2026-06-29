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

async function mountWolPanel(data = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: {
        id: 'wol1',
        type: 'wake_on_lan',
        data: { mac_address: '', broadcast_ip: '255.255.255.255', port: 9, ...data },
      },
      nodeTypes: [{ type: 'wake_on_lan', label: 'Wake on LAN', description: 'Sends a WoL packet.' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

describe('NodeConfigPanel wake_on_lan — MAC address validation', () => {
  it('shows no error when MAC is empty', async () => {
    const wrapper = await mountWolPanel({ mac_address: '' })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-mac-address"]').exists()).toBe(true)
    expect(wrapper.find('[data-testid="wol-mac-address"]').classes()).not.toContain('border-red-500')
    wrapper.unmount()
  })

  it('shows no error for a valid MAC address', async () => {
    const wrapper = await mountWolPanel({ mac_address: 'AA:BB:CC:DD:EE:FF' })
    await flushPromises()
    const input = wrapper.find('[data-testid="wol-mac-address"]')
    expect(input.classes()).not.toContain('border-red-500')
    wrapper.unmount()
  })

  it('shows an error for an invalid MAC address', async () => {
    const wrapper = await mountWolPanel({ mac_address: 'ZZ:ZZ:ZZ:ZZ:ZZ:ZZ' })
    await flushPromises()
    const input = wrapper.find('[data-testid="wol-mac-address"]')
    expect(input.classes()).toContain('border-red-500')
    wrapper.unmount()
  })

  it('shows an error for a MAC with wrong separator', async () => {
    const wrapper = await mountWolPanel({ mac_address: 'AA-BB-CC-DD-EE-FF' })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-mac-address"]').classes()).toContain('border-red-500')
    wrapper.unmount()
  })

  it('shows an error for a truncated MAC', async () => {
    const wrapper = await mountWolPanel({ mac_address: 'AA:BB:CC:DD:EE' })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-mac-address"]').classes()).toContain('border-red-500')
    wrapper.unmount()
  })
})

describe('NodeConfigPanel wake_on_lan — broadcast IP validation', () => {
  it('shows no error when broadcast IP is empty', async () => {
    const wrapper = await mountWolPanel({ broadcast_ip: '' })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-broadcast-ip"]').classes()).not.toContain('border-red-500')
    wrapper.unmount()
  })

  it('shows no error for a valid broadcast IP', async () => {
    const wrapper = await mountWolPanel({ broadcast_ip: '192.168.1.255' })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-broadcast-ip"]').classes()).not.toContain('border-red-500')
    wrapper.unmount()
  })

  it('shows an error for an invalid IP', async () => {
    const wrapper = await mountWolPanel({ broadcast_ip: '999.999.999.999' })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-broadcast-ip"]').classes()).toContain('border-red-500')
    wrapper.unmount()
  })

  it('shows an error for a non-IP string', async () => {
    const wrapper = await mountWolPanel({ broadcast_ip: 'not-an-ip' })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-broadcast-ip"]').classes()).toContain('border-red-500')
    wrapper.unmount()
  })
})

describe('NodeConfigPanel wake_on_lan — port validation', () => {
  it('shows no error for a valid port', async () => {
    const wrapper = await mountWolPanel({ port: 9 })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-port"]').classes()).not.toContain('border-red-500')
    wrapper.unmount()
  })

  it('shows an error for port 0', async () => {
    const wrapper = await mountWolPanel({ port: 0 })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-port"]').classes()).toContain('border-red-500')
    wrapper.unmount()
  })

  it('shows an error for port above 65535', async () => {
    const wrapper = await mountWolPanel({ port: 65536 })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-port"]').classes()).toContain('border-red-500')
    wrapper.unmount()
  })

  it('shows no error for port 65535', async () => {
    const wrapper = await mountWolPanel({ port: 65535 })
    await flushPromises()
    expect(wrapper.find('[data-testid="wol-port"]').classes()).not.toContain('border-red-500')
    wrapper.unmount()
  })
})
