// @vitest-environment jsdom
/**
 * Start-page routing: `/visu/` opens the start page of this device's profile,
 * `?device=` on any Visu URL picks the profile.
 */
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h } from 'vue'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VisuDevice } from '@/api/client'

const list = vi.fn<() => Promise<VisuDevice[]>>()
const jwt = vi.fn<() => string | null>(() => null)
const admin = vi.fn(() => false)

vi.mock('@/api/client', () => ({
  getJwt: () => jwt(),
  getIsAdmin: () => admin(),
  visuDevices: { list: () => list() },
}))

const Stub = defineComponent({ render: () => h('div') })
for (const view of ['VisuTree', 'VisuViewer', 'VisuEditor', 'LoginView', 'PinAuth', 'TreeManager']) {
  vi.doMock(`@/views/${view}.vue`, () => ({ default: Stub }))
}

const DEVICES: VisuDevice[] = [
  { slug: 'panel', name: 'Panel', start_node_id: 'page-1', return_after_s: 60 },
  { slug: 'handy', name: 'Handy', start_node_id: null, return_after_s: 0 },
]

async function freshRouter() {
  vi.resetModules()
  const { default: router } = await import('./index')
  const { useDeviceStore } = await import('@/stores/device')
  return { router, device: useDeviceStore() }
}

beforeEach(() => {
  localStorage.clear()
  list.mockReset()
  list.mockResolvedValue(DEVICES)
  jwt.mockReturnValue(null)
  admin.mockReturnValue(false)
  setActivePinia(createPinia())
})

describe('Visu router — device start page', () => {
  it('opens the overview without a device profile', async () => {
    const { router } = await freshRouter()

    await router.push('/')

    expect(router.currentRoute.value.name).toBe('tree')
  })

  it('does not wait longer than the start-load timeout for the profiles', async () => {
    vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout'] })
    try {
      localStorage.setItem('visu_device', 'panel')
      list.mockReturnValue(new Promise(() => {}))  // OBS behind a proxy that never answers
      const { router } = await freshRouter()

      const navigation = router.push('/')
      await vi.advanceTimersByTimeAsync(3_000)
      await navigation

      expect(router.currentRoute.value.name).toBe('tree')
    } finally {
      vi.useRealTimers()
    }
  })

  it('opens the start page of the stored profile', async () => {
    localStorage.setItem('visu_device', 'panel')
    const { router } = await freshRouter()

    await router.push('/')

    expect(router.currentRoute.value.fullPath).toBe('/page-1')
  })

  it('picks the profile from the link and remembers it', async () => {
    const { router, device } = await freshRouter()

    await router.push('/?device=panel')

    expect(router.currentRoute.value.fullPath).toBe('/page-1')
    expect(device.slug).toBe('panel')
    expect(localStorage.getItem('visu_device')).toBe('panel')
  })

  it('uses the overview for a profile without a start page', async () => {
    const { router } = await freshRouter()

    await router.push('/?device=handy')

    expect(router.currentRoute.value.name).toBe('tree')
  })

  it('accepts ?device= on any page and releases the profile with ?device=none', async () => {
    localStorage.setItem('visu_device', 'panel')
    const { router, device } = await freshRouter()

    await router.push('/page-7?device=none')

    expect(router.currentRoute.value.path).toBe('/page-7')
    expect(device.slug).toBeNull()
    expect(localStorage.getItem('visu_device')).toBeNull()
  })

  it('still sends protected routes to the login', async () => {
    const { router } = await freshRouter()

    await router.push('/manage')

    expect(router.currentRoute.value.name).toBe('login')
  })
})
