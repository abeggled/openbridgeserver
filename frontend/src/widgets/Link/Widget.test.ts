// @vitest-environment jsdom
import { flushPromises, mount, type VueWrapper } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import LinkWidget from './Widget.vue'

vi.mock('vue-router', () => ({
  useRouter: () => ({ push: vi.fn() }),
  useRoute: () => ({ params: {} }),
}))

vi.mock('@/stores/visu', () => ({
  useVisuStore: () => ({
    getNode: vi.fn(),
    getChildren: vi.fn().mockReturnValue([]),
    hasSessionToken: vi.fn().mockReturnValue(false),
    isLoggedIn: false,
  }),
}))

vi.mock('@/composables/useIcons', () => ({
  useIcons: () => ({
    getSvg: vi.fn().mockResolvedValue('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 393 207"></svg>'),
    isSvgIcon: (icon: string) => icon.startsWith('svg:'),
    svgIconName: (icon: string) => icon.slice(4),
  }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({ t: (key: string) => key }),
}))

let wrapper: VueWrapper | null = null

function mountWidget(config: Record<string, unknown>) {
  wrapper = mount(LinkWidget, {
    props: {
      config: { label: 'Garage', target_node_id: 'page-1', ...config },
      datapointId: null,
      value: null,
      statusValue: null,
      editorMode: false,
    },
    global: { mocks: { $t: (key: string) => key } },
  })
  return wrapper
}

beforeEach(() => {
  vi.stubGlobal('URL', { ...URL, createObjectURL: vi.fn(() => 'blob:icon'), revokeObjectURL: vi.fn() })
})

afterEach(() => {
  wrapper?.unmount()
  wrapper = null
  vi.unstubAllGlobals()
})

describe('Link widget icon size', () => {
  it('keeps the default text-4xl icon size when icon_size is missing', () => {
    const icon = mountWidget({ icon: '🔗' }).get('[data-testid="link-icon"]')
    expect(icon.classes()).toContain('text-4xl')
    expect(icon.attributes('style')).toBeUndefined()
  })

  it.each([0, -10, 'abc', null])('falls back to the default size for icon_size=%s', (size) => {
    const icon = mountWidget({ icon: '🔗', icon_size: size }).get('[data-testid="link-icon"]')
    expect(icon.classes()).toContain('text-4xl')
    expect(icon.attributes('style')).toBeUndefined()
  })

  it('applies a custom icon size as font-size in px', () => {
    const icon = mountWidget({ icon: '🔗', icon_size: 96 }).get('[data-testid="link-icon"]')
    expect(icon.classes()).not.toContain('text-4xl')
    expect(icon.classes()).toEqual(expect.arrayContaining(['block', 'w-full', 'text-center']))
    expect(icon.attributes('style')).toContain('font-size: 96px')
  })

  it('accepts a numeric string as icon size', () => {
    const icon = mountWidget({ icon: '🔗', icon_size: '48' }).get('[data-testid="link-icon"]')
    expect(icon.attributes('style')).toContain('font-size: 48px')
  })

  it('renders SVG icons as a 1em square by default', async () => {
    const w = mountWidget({ icon: 'svg:car' })
    await flushPromises()
    const img = w.get('[data-testid="link-icon"] img')
    expect(img.classes()).toContain('w-[1em]')
    expect(img.classes()).not.toContain('w-auto')
  })

  it('keeps the aspect ratio of SVG icons when an icon size is set', async () => {
    const w = mountWidget({ icon: 'svg:car', icon_size: 120 })
    await flushPromises()
    const img = w.get('[data-testid="link-icon"] img')
    expect(img.classes()).toEqual(expect.arrayContaining(['h-[1em]', 'w-auto', 'max-w-full']))
    expect(img.classes()).not.toContain('w-[1em]')
  })

  it('renders no icon when show_icon is false', () => {
    expect(mountWidget({ icon: '🔗', icon_size: 96, show_icon: false }).find('[data-testid="link-icon"]').exists()).toBe(false)
  })
})
