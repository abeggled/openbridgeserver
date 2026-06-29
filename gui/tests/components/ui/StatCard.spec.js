import { describe, it, expect } from 'vitest'
import { mount } from '@vue/test-utils'
import StatCard from '@/components/ui/StatCard.vue'

function mountCard(props = {}) {
  return mount(StatCard, { props })
}

describe('StatCard', () => {
  it('renders label and value', () => {
    const w = mountCard({ label: 'Datapoints', value: 42, icon: '📊' })
    expect(w.text()).toContain('Datapoints')
    expect(w.text()).toContain('42')
    expect(w.text()).toContain('📊')
  })

  it('applies blue background class by default', () => {
    const w = mountCard({ label: 'L', value: '0' })
    expect(w.html()).toContain('bg-blue-500/15')
  })

  it('applies green background class when color="green"', () => {
    const w = mountCard({ label: 'L', value: '0', color: 'green' })
    expect(w.html()).toContain('bg-green-500/15')
  })

  it('applies red background class when color="red"', () => {
    const w = mountCard({ label: 'L', value: '0', color: 'red' })
    expect(w.html()).toContain('bg-red-500/15')
  })

  it('applies amber background class when color="amber"', () => {
    const w = mountCard({ label: 'L', value: '0', color: 'amber' })
    expect(w.html()).toContain('bg-amber-500/15')
  })

  it('falls back to blue for an unknown color', () => {
    const w = mountCard({ label: 'L', value: '0', color: 'magenta' })
    expect(w.html()).toContain('bg-blue-500/15')
  })

  it('renders a numeric value prop', () => {
    const w = mountCard({ label: 'Count', value: 99 })
    expect(w.text()).toContain('99')
  })
})
