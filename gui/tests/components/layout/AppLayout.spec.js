import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
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
