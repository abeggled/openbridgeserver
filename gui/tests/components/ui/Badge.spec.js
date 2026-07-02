import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import Badge from '@/components/ui/Badge.vue'

function mk(props = {}, slot = 'Label') {
  return mount(Badge, { props, slots: { default: slot } })
}

describe('Badge', () => {
  it('renders slot content', () => {
    expect(mk({}, 'Active').text()).toBe('Active')
  })

  it('applies default variant classes', () => {
    const html = mk({ variant: 'default' }).html()
    expect(html).toContain('bg-slate-200/60')
    expect(html).toContain('text-slate-600')
  })

  it('applies success variant classes', () => {
    expect(mk({ variant: 'success' }).html()).toContain('bg-green-500/15')
  })

  it('applies warning variant classes', () => {
    expect(mk({ variant: 'warning' }).html()).toContain('bg-amber-500/15')
  })

  it('applies danger variant classes', () => {
    expect(mk({ variant: 'danger' }).html()).toContain('bg-red-500/15')
  })

  it('applies info variant classes', () => {
    expect(mk({ variant: 'info' }).html()).toContain('bg-blue-500/15')
  })

  it('applies muted variant classes', () => {
    expect(mk({ variant: 'muted' }).html()).toContain('bg-slate-100')
  })

  it('falls back to default classes for unknown variant', () => {
    // hits the ?? map.default branch
    const html = mk({ variant: 'unknown-xyz' }).html()
    expect(html).toContain('bg-slate-200/60')
  })

  it('renders dot element when dot=true', () => {
    const w = mk({ dot: true })
    expect(w.find('span.rounded-full').exists()).toBe(true)
  })

  it('dot has correct color class for success variant', () => {
    // Root span also has rounded-full so check HTML directly
    const w = mk({ dot: true, variant: 'success' })
    expect(w.html()).toContain('bg-green-400')
  })

  it('dot falls back to default color for unknown variant', () => {
    // hits the ?? dotMap.default branch
    const w = mk({ dot: true, variant: 'unknown-xyz' })
    expect(w.html()).toContain('bg-slate-400')
  })

  it('size xs uses compact padding', () => {
    expect(mk({ size: 'xs' }).html()).toContain('px-2 py-0.5')
  })

  it('size sm (default) uses standard padding', () => {
    expect(mk({ size: 'sm' }).html()).toContain('px-2.5 py-0.5')
  })

  it('dot not rendered when dot=false (default)', () => {
    // Only the root span is rendered; no nested dot span
    expect(mk({}).findAll('span').length).toBe(1)
  })
})
