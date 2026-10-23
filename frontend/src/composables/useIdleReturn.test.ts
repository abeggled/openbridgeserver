// @vitest-environment jsdom
import { mount, type VueWrapper } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { useDeviceStore } from '@/stores/device'
import { useIdleReturn } from './useIdleReturn'

vi.mock('@/api/client', () => ({ visuDevices: { list: vi.fn() } }))

const Empty = defineComponent({ render: () => h('div') })

let router: Router
let wrapper: VueWrapper | null = null

async function setup(profile: { start_node_id: string | null; return_after_s: number } | null, path = '/page-2') {
  const pinia = createPinia()
  setActivePinia(pinia)
  const device = useDeviceStore()
  if (profile) {
    device.devices = [{ slug: 'panel', name: 'Panel', ...profile }]
    device.select('panel')
  }
  router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/tree', name: 'tree', component: Empty },
      { path: '/editor/:id', name: 'editor', component: Empty },
      { path: '/:id', name: 'viewer', component: Empty },
    ],
  })
  await router.push(path)
  const Host = defineComponent({ setup() { useIdleReturn(); return () => h('div') } })
  wrapper = mount(Host, { global: { plugins: [pinia, router] } })
  return device
}

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
  localStorage.clear()
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.useRealTimers()
})

async function settle() {
  vi.useRealTimers()
  await new Promise(resolve => setTimeout(resolve, 0))
  await nextTick()
}

describe('useIdleReturn', () => {
  it('waits the full idle time', async () => {
    await setup({ start_node_id: 'page-1', return_after_s: 60 })

    vi.advanceTimersByTime(59_999)
    await settle()

    expect(router.currentRoute.value.fullPath).toBe('/page-2')
  })

  it('returns to the start page after the configured idle time', async () => {
    await setup({ start_node_id: 'page-1', return_after_s: 60 })

    vi.advanceTimersByTime(60_000)
    await settle()

    expect(router.currentRoute.value.fullPath).toBe('/page-1')
  })

  it('restarts the wait on every interaction', async () => {
    await setup({ start_node_id: 'page-1', return_after_s: 10 })

    vi.advanceTimersByTime(9_000)
    window.dispatchEvent(new Event('pointerdown'))
    vi.advanceTimersByTime(9_000)
    window.dispatchEvent(new Event('keydown'))
    vi.advanceTimersByTime(9_000)
    await settle()

    expect(router.currentRoute.value.fullPath).toBe('/page-2')
  })

  it('returns to the overview for a profile without a start page', async () => {
    await setup({ start_node_id: null, return_after_s: 5 })

    vi.advanceTimersByTime(5_000)
    await settle()

    expect(router.currentRoute.value.name).toBe('tree')
  })

  it('does nothing without a timeout or without a profile', async () => {
    await setup({ start_node_id: 'page-1', return_after_s: 0 })
    vi.advanceTimersByTime(86_400_000)
    await settle()
    expect(router.currentRoute.value.fullPath).toBe('/page-2')

    wrapper?.unmount()
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    await setup(null)
    vi.advanceTimersByTime(86_400_000)
    await settle()
    expect(router.currentRoute.value.fullPath).toBe('/page-2')
  })

  it('never leaves the editor', async () => {
    await setup({ start_node_id: 'page-1', return_after_s: 5 }, '/editor/page-2')

    vi.advanceTimersByTime(5_000)
    await settle()

    expect(router.currentRoute.value.fullPath).toBe('/editor/page-2')
  })

  it('stays put when already on the start page', async () => {
    await setup({ start_node_id: 'page-1', return_after_s: 5 }, '/page-1')
    const push = vi.spyOn(router, 'push')

    vi.advanceTimersByTime(5_000)
    await settle()

    expect(push).not.toHaveBeenCalled()
  })

  it('applies a changed timeout right away', async () => {
    const device = await setup({ start_node_id: 'page-1', return_after_s: 0 })

    device.devices = [{ slug: 'panel', name: 'Panel', start_node_id: 'page-1', return_after_s: 3 }]
    await nextTick()
    vi.advanceTimersByTime(3_000)
    await settle()

    expect(router.currentRoute.value.fullPath).toBe('/page-1')
  })

  it('stops listening once unmounted', async () => {
    await setup({ start_node_id: 'page-1', return_after_s: 5 })
    const remove = vi.spyOn(window, 'removeEventListener')
    const push = vi.spyOn(router, 'push')

    wrapper?.unmount()
    wrapper = null
    vi.advanceTimersByTime(5_000)
    await settle()

    expect(remove).toHaveBeenCalledWith('pointerdown', expect.any(Function))
    expect(push).not.toHaveBeenCalled()
  })
})
