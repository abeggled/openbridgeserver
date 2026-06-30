import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { ref } from 'vue'

let iconNamesMock
let loadListMock

beforeEach(() => {
  vi.resetModules()
  iconNamesMock = ref([])
  loadListMock = vi.fn()

  vi.doMock('@/composables/useIcons', () => ({
    useIcons: () => ({
      iconNames: iconNamesMock,
      loadList: loadListMock,
      isSvgIcon: (icon) => typeof icon === 'string' && icon.startsWith('svg:'),
      svgIconName: (icon) => icon.replace('svg:', ''),
      getSvg: vi.fn(),
    }),
  }))
  vi.doMock('@/components/ui/VisuIcon.vue', () => ({
    default: { template: '<span class="visu-icon" />' },
  }))
})

afterEach(() => {
  vi.doUnmock('@/composables/useIcons')
  vi.doUnmock('@/components/ui/VisuIcon.vue')
})

async function mountPicker(props = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { default: IconPicker } = await import('@/components/ui/IconPicker.vue')
  const w = mount(IconPicker, { props: { modelValue: '', ...props }, global: { plugins: [pinia] } })
  await flushPromises()
  return w
}

describe('IconPicker — emoji tab', () => {
  it('starts on the emoji tab', async () => {
    const w = await mountPicker()
    expect(w.text()).toContain('Emoji')
    // Emoji quick-pick buttons are rendered
    expect(w.findAll('button').filter(b => b.text().match(/\p{Emoji}/u)).length).toBeGreaterThan(0)
  })

  it('calls loadList on mount', async () => {
    await mountPicker()
    expect(loadListMock).toHaveBeenCalled()
  })

  it('emits update:modelValue with emoji on quick-pick click', async () => {
    const w = await mountPicker()
    // Find first emoji button that is not a tab (tabs say "Emoji" or "Icons")
    const emojiBtn = w.findAll('button').find(b => b.text().match(/\p{Emoji}/u))
    await emojiBtn.trigger('click')
    expect(w.emitted('update:modelValue')).toBeTruthy()
    expect(typeof w.emitted('update:modelValue')[0][0]).toBe('string')
  })

  it('emits update:modelValue from custom text input', async () => {
    const w = await mountPicker()
    const input = w.find('input[type="text"]')
    // setValue already triggers the input event — don't fire it again
    await input.setValue('🌈')
    expect(w.emitted('update:modelValue')).toEqual([['🌈']])
  })

  it('hides SVG tab button when iconNames is empty', async () => {
    const w = await mountPicker()
    const svgTabBtn = w.findAll('button').find(b => b.text().includes('Icons'))
    expect(svgTabBtn).toBeUndefined()
  })

  it('current emoji has ring class when it matches modelValue', async () => {
    const w = await mountPicker({ modelValue: '🏠' })
    const homeBtn = w.findAll('button').find(b => b.text() === '🏠')
    expect(homeBtn.classes().join(' ')).toContain('ring-2')
  })
})

describe('IconPicker — SVG tab', () => {
  it('shows SVG tab button when iconNames has entries', async () => {
    iconNamesMock.value = ['home', 'settings']
    const w = await mountPicker()
    const svgTabBtn = w.findAll('button').find(b => b.text().includes('Icons'))
    expect(svgTabBtn).toBeDefined()
    expect(svgTabBtn.text()).toContain('2')
  })

  it('switches to SVG tab and shows icon buttons', async () => {
    iconNamesMock.value = ['home', 'settings']
    const w = await mountPicker()
    await w.findAll('button').find(b => b.text().includes('Icons')).trigger('click')
    // svg icon buttons are rendered for each icon name
    expect(w.text()).toContain('home')
    expect(w.text()).toContain('settings')
  })

  it('emits svg:name when SVG icon is clicked', async () => {
    iconNamesMock.value = ['home']
    const w = await mountPicker()
    await w.findAll('button').find(b => b.text().includes('Icons')).trigger('click')
    const homeBtn = w.findAll('button').find(b => b.attributes('title') === 'home')
    await homeBtn.trigger('click')
    expect(w.emitted('update:modelValue')).toEqual([['svg:home']])
  })

  it('filters SVG icons by search query', async () => {
    iconNamesMock.value = ['home', 'settings', 'home-alt']
    const w = await mountPicker()
    await w.findAll('button').find(b => b.text().includes('Icons')).trigger('click')
    await w.find('input[type="text"]').setValue('settings')
    await flushPromises()
    expect(w.text()).toContain('settings')
    expect(w.text()).not.toContain('home-alt')
  })

  it('shows no-match message when search has no results', async () => {
    iconNamesMock.value = ['home']
    const w = await mountPicker()
    await w.findAll('button').find(b => b.text().includes('Icons')).trigger('click')
    await w.find('input[type="text"]').setValue('zzznomatch')
    await flushPromises()
    // The no-match message paragraph is rendered
    expect(w.find('p').exists()).toBe(true)
  })

  it('auto-switches to SVG tab when modelValue starts with svg:', async () => {
    iconNamesMock.value = ['home']
    const w = await mountPicker({ modelValue: 'svg:home' })
    // Watcher switches tab immediately; emoji tab content is not shown
    expect(w.find('input[maxlength="4"]').exists()).toBe(false)
  })
})
