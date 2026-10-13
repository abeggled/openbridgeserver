import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({ knxprojApi: { listGA: vi.fn().mockResolvedValue({ data: { total: 0, items: [] } }) } }))
  vi.doMock('@/components/layout/Sidebar.vue', () => ({
    default: {
      name: 'Sidebar',
      template: '<aside class="sidebar-stub" :data-collapsed="collapsed" @click="$emit(\'toggle\')" />',
      props: ['collapsed'],
      emits: ['toggle'],
    },
  }))
  vi.doMock('@/components/layout/TopBar.vue', () => ({
    default: {
      name: 'TopBar',
      template: '<header class="topbar-stub" @click="$emit(\'toggle-sidebar\')" />',
      emits: ['toggle-sidebar'],
    },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/components/layout/Sidebar.vue')
  vi.doUnmock('@/components/layout/TopBar.vue')
})

async function mountAppLayout(slot = '<p class="main">Content</p>') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { default: AppLayout } = await import('@/components/layout/AppLayout.vue')
  const w = mount(AppLayout, {
    slots: { default: slot },
    global: { plugins: [pinia] },
  })
  await flushPromises()
  return w
}

describe('AppLayout', () => {
  it('renders the Sidebar stub', async () => {
    const w = await mountAppLayout()
    expect(w.find('.sidebar-stub').exists()).toBe(true)
  })

  it('renders the TopBar stub', async () => {
    const w = await mountAppLayout()
    expect(w.find('.topbar-stub').exists()).toBe(true)
  })

  it('renders slot content in the main area', async () => {
    const w = await mountAppLayout('<p class="main">Main content</p>')
    expect(w.find('p.main').text()).toBe('Main content')
  })

  it('sidebar starts uncollapsed', async () => {
    const w = await mountAppLayout()
    expect(w.find('.sidebar-stub').attributes('data-collapsed')).toBe('false')
  })

  it('toggles sidebar collapsed state when Sidebar emits toggle', async () => {
    const w = await mountAppLayout()
    expect(w.find('.sidebar-stub').attributes('data-collapsed')).toBe('false')
    await w.find('.sidebar-stub').trigger('click')
    expect(w.find('.sidebar-stub').attributes('data-collapsed')).toBe('true')
    await w.find('.sidebar-stub').trigger('click')
    expect(w.find('.sidebar-stub').attributes('data-collapsed')).toBe('false')
  })

  it('toggles sidebar collapsed state when TopBar emits toggle-sidebar', async () => {
    const w = await mountAppLayout()
    await w.find('.topbar-stub').trigger('click')
    expect(w.find('.sidebar-stub').attributes('data-collapsed')).toBe('true')
  })
})

describe('AppLayout loads the KNX project style early (#1296)', () => {
  it('asks for it once on mount, before any view needs it', async () => {
    const listGA = vi.fn().mockResolvedValue({ data: { total: 0, items: [], group_address_style: 'TwoLevel' } })
    vi.doMock('@/api/client', () => ({ knxprojApi: { listGA } }))
    localStorage.setItem('access_token', 'token')
    try {
      await mountAppLayout()
      expect(listGA).toHaveBeenCalledWith({ size: 1 })
      const { useKnxProjectStore } = await import('@/stores/knxProject')
      expect(useKnxProjectStore().groupAddressStyle).toBe('TwoLevel')
    } finally {
      localStorage.removeItem('access_token')
      vi.doUnmock('@/api/client')
    }
  })

  it('does not ask before login: a 401 would send the login page into a reload loop', async () => {
    const listGA = vi.fn().mockResolvedValue({ data: { total: 0, items: [] } })
    vi.doMock('@/api/client', () => ({ knxprojApi: { listGA } }))
    localStorage.removeItem('access_token')
    try {
      await mountAppLayout()
      expect(listGA).not.toHaveBeenCalled()
    } finally {
      vi.doUnmock('@/api/client')
    }
  })
})
