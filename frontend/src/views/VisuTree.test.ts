// @vitest-environment jsdom
/**
 * Overview header: device profile selector (start page + return-to-start).
 */
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { VisuDevice } from '@/api/client'
import { useDeviceStore } from '@/stores/device'
import VisuTree from './VisuTree.vue'

vi.mock('vue-i18n', () => ({ useI18n: () => ({ t: (key: string) => key }) }))
vi.mock('vue-router', () => ({ useRouter: () => ({ push: vi.fn() }) }))
vi.mock('@/stores/theme', () => ({ useThemeStore: () => ({ isDark: false, toggle: vi.fn() }) }))
vi.mock('@/api/client', () => ({ visuDevices: { list: vi.fn() } }))
vi.mock('@/stores/visu', async () => {
  const { ref } = await import('vue')
  const state = { rootNodes: ref([]), isAdmin: ref(false), loadTree: vi.fn().mockResolvedValue(undefined) }
  return { useVisuStore: () => state }
})
vi.mock('pinia', async (importOriginal) => {
  const actual = await importOriginal<typeof import('pinia')>()
  return { ...actual, storeToRefs: (store: Record<string, unknown>) => store }
})

const DEVICES: VisuDevice[] = [
  { slug: 'panel', name: 'Wandpanel', start_node_id: 'page-1', return_after_s: 60 },
  { slug: 'handy', name: 'Handy', start_node_id: null, return_after_s: 0 },
]

function mountTree() {
  return mount(VisuTree, {
    global: {
      mocks: { $t: (key: string, params?: Record<string, unknown>) => (params ? `${key}:${JSON.stringify(params)}` : key) },
      stubs: { AuthButton: true, VisuIcon: true, RouterLink: true },
    },
  })
}

beforeEach(() => {
  localStorage.clear()
  setActivePinia(createPinia())
})

describe('VisuTree — device selector', () => {
  it('is hidden while no device profiles exist', async () => {
    const wrapper = mountTree()
    await flushPromises()

    expect(wrapper.find('[data-testid="device-select"]').exists()).toBe(false)
  })

  it('lists the profiles and stores the choice for this device', async () => {
    useDeviceStore().devices = DEVICES
    const wrapper = mountTree()
    await flushPromises()

    const select = wrapper.get('[data-testid="device-select"]')
    expect(select.findAll('option').map(o => o.text())).toEqual(['tree.deviceNone', 'Wandpanel', 'Handy'])
    expect((select.element as HTMLSelectElement).value).toBe('')

    await select.setValue('panel')
    expect(useDeviceStore().slug).toBe('panel')
    expect(localStorage.getItem('visu_device')).toBe('panel')

    await select.setValue('')
    expect(useDeviceStore().slug).toBeNull()
  })

  it('shows a stored profile that no longer exists', async () => {
    const device = useDeviceStore()
    device.devices = DEVICES
    device.select('weg')
    const wrapper = mountTree()
    await flushPromises()

    const select = wrapper.get('[data-testid="device-select"]')
    expect((select.element as HTMLSelectElement).value).toBe('weg')
    expect(select.findAll('option')[1].text()).toBe('tree.deviceUnknown:{"slug":"weg"}')
  })

  it('preselects the profile in use', async () => {
    const device = useDeviceStore()
    device.devices = DEVICES
    device.select('handy')
    const wrapper = mountTree()
    await flushPromises()

    const select = wrapper.get('[data-testid="device-select"]')
    expect((select.element as HTMLSelectElement).value).toBe('handy')
    expect(select.findAll('option').map(o => o.text())).not.toContain('tree.deviceUnknown:{"slug":"handy"}')
  })
})

describe('VisuTree — header', () => {
  it('renders the product name through i18n', () => {
    expect(mountTree().text()).toContain('tree.productName')
  })
})
