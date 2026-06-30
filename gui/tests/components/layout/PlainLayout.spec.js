import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import PlainLayout from '@/components/layout/PlainLayout.vue'

describe('PlainLayout', () => {
  it('renders slot content', () => {
    const w = mount(PlainLayout, { slots: { default: '<p class="test-child">Hello</p>' } })
    expect(w.find('p.test-child').text()).toBe('Hello')
  })

  it('has a full-height centering container', () => {
    const w = mount(PlainLayout)
    expect(w.html()).toContain('min-h-screen')
  })

  it('centers content with flex layout', () => {
    const w = mount(PlainLayout)
    const html = w.html()
    expect(html).toContain('items-center')
    expect(html).toContain('justify-center')
  })
})
