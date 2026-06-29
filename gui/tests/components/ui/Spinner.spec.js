import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import Spinner from '@/components/ui/Spinner.vue'

function mk(props = {}) {
  return mount(Spinner, { props })
}

describe('Spinner', () => {
  it('is an SVG element', () => {
    expect(mk().find('svg').exists()).toBe(true)
  })

  it('has animate-spin class', () => {
    expect(mk().find('svg').classes()).toContain('animate-spin')
  })

  it('applies xs size class', () => {
    expect(mk({ size: 'xs' }).find('svg').classes()).toContain('w-3')
  })

  it('applies sm size class', () => {
    expect(mk({ size: 'sm' }).find('svg').classes()).toContain('w-4')
  })

  it('applies md size class (default)', () => {
    expect(mk({ size: 'md' }).find('svg').classes()).toContain('w-5')
  })

  it('applies lg size class', () => {
    expect(mk({ size: 'lg' }).find('svg').classes()).toContain('w-8')
  })

  it('falls back to md for unknown size', () => {
    // hits the ?? sizes.md branch
    expect(mk({ size: 'xxl' }).find('svg').classes()).toContain('w-5')
  })

  it('applies blue color class (default)', () => {
    expect(mk({ color: 'blue' }).find('svg').classes()).toContain('text-blue-500')
  })

  it('applies white color class', () => {
    expect(mk({ color: 'white' }).find('svg').classes()).toContain('text-white')
  })

  it('applies slate color class', () => {
    expect(mk({ color: 'slate' }).find('svg').classes()).toContain('text-slate-400')
  })

  it('falls back to blue for unknown color', () => {
    // hits the ?? colors.blue branch
    expect(mk({ color: 'magenta' }).find('svg').classes()).toContain('text-blue-500')
  })
})
