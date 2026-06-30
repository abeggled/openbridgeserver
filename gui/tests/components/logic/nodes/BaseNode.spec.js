import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import BaseNode from '@/components/logic/nodes/BaseNode.vue'

function mk(props = {}) {
  return mount(BaseNode, {
    props: { label: 'Test', color: '#1d4ed8', ...props },
    slots: { default: '<span class="slot-content">slot text</span>' },
  })
}

describe('BaseNode', () => {
  it('renders label text', () => {
    expect(mk({ label: 'AND' }).text()).toContain('AND')
  })

  it('applies border-top color from color prop', () => {
    const w = mk({ color: '#ef4444' })
    expect(w.find('.logic-node').attributes('style')).toContain('#ef4444')
  })

  it('applies custom borderClass', () => {
    const w = mk({ borderClass: 'border-red-500' })
    expect(w.find('.logic-node').classes()).toContain('border-red-500')
  })

  it('renders default slot content', () => {
    expect(mk().find('.slot-content').exists()).toBe(true)
  })

  it('uses default color #475569 when not provided', () => {
    const w = mount(BaseNode, { props: { label: 'X' } })
    expect(w.find('.logic-node').attributes('style')).toContain('#475569')
  })
})
