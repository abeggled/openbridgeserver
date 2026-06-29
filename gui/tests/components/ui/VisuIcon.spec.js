import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const getSvgMock = vi.fn()

beforeEach(() => {
  vi.resetModules()
  getSvgMock.mockResolvedValue('<svg><path d="M0 0"/></svg>')
  vi.doMock('@/composables/useIcons', () => ({
    useIcons: () => ({
      isSvgIcon:   (icon) => typeof icon === 'string' && icon.startsWith('svg:'),
      svgIconName: (icon) => icon.replace('svg:', ''),
      getSvg:      getSvgMock,
    }),
  }))
})

afterEach(() => {
  vi.doUnmock('@/composables/useIcons')
})

describe('VisuIcon', () => {
  it('renders an emoji icon as plain text span', async () => {
    const { default: VisuIcon } = await import('@/components/ui/VisuIcon.vue')
    const w = mount(VisuIcon, { props: { icon: '🔗' } })
    await flushPromises()
    expect(w.text()).toBe('🔗')
    expect(getSvgMock).not.toHaveBeenCalled()
  })

  it('loads and injects SVG for svg:{name} icon', async () => {
    const { default: VisuIcon } = await import('@/components/ui/VisuIcon.vue')
    const w = mount(VisuIcon, { props: { icon: 'svg:home' } })
    await flushPromises()

    expect(getSvgMock).toHaveBeenCalledWith('home')
    expect(w.html()).toContain('<svg>')
  })

  it('shows placeholder span while SVG resolves as empty', async () => {
    getSvgMock.mockResolvedValue('')
    const { default: VisuIcon } = await import('@/components/ui/VisuIcon.vue')
    const w = mount(VisuIcon, { props: { icon: 'svg:missing' } })
    // Before resolving
    expect(w.find('span.inline-block').exists()).toBe(true)
  })

  it('re-loads SVG when icon prop changes', async () => {
    const { default: VisuIcon } = await import('@/components/ui/VisuIcon.vue')
    const w = mount(VisuIcon, { props: { icon: 'svg:home' } })
    await flushPromises()
    expect(getSvgMock).toHaveBeenCalledWith('home')

    await w.setProps({ icon: 'svg:settings' })
    await flushPromises()
    expect(getSvgMock).toHaveBeenCalledWith('settings')
  })

  it('clears svgContent when switching from svg to emoji', async () => {
    const { default: VisuIcon } = await import('@/components/ui/VisuIcon.vue')
    const w = mount(VisuIcon, { props: { icon: 'svg:home' } })
    await flushPromises()

    await w.setProps({ icon: '🏠' })
    await flushPromises()
    expect(w.text()).toBe('🏠')
    expect(w.html()).not.toContain('<svg>')
  })

  it('renders placeholder for empty icon prop', async () => {
    const { default: VisuIcon } = await import('@/components/ui/VisuIcon.vue')
    const w = mount(VisuIcon, { props: { icon: '' } })
    await flushPromises()
    // empty string is not an svg: icon → renders as emoji span (empty text)
    expect(w.find('span').exists()).toBe(true)
  })
})
