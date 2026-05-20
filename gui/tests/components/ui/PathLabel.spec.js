/**
 * Tests for PathLabel.vue.
 *
 * PathLabel renders a hierarchical path (e.g. ['Gebäude', 'EG', 'Küche']) with
 * - segment-based collapsing via ResizeObserver,
 * - tooltip rendering via @floating-ui/vue on hover,
 * - optional hideRoot to drop the first segment.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import PathLabel from '@/components/ui/PathLabel.vue'

/** Capture ResizeObserver callbacks so tests can simulate width changes. */
class FakeResizeObserver {
  constructor(cb) {
    this.cb = cb
    FakeResizeObserver.instances.push(this)
    this.observed = []
    this.disconnected = false
  }
  observe(el) { this.observed.push(el) }
  unobserve() {}
  disconnect() { this.disconnected = true }
}
FakeResizeObserver.instances = []

beforeEach(() => {
  FakeResizeObserver.instances = []
  globalThis.ResizeObserver = FakeResizeObserver
})

afterEach(() => {
  // restore
})

function setWidth(el, w) {
  Object.defineProperty(el, 'clientWidth', { value: w, configurable: true })
  Object.defineProperty(el, 'offsetWidth', { value: w, configurable: true })
}

async function fireResize(width) {
  const ro = FakeResizeObserver.instances[FakeResizeObserver.instances.length - 1]
  if (!ro) return
  for (const el of ro.observed) {
    setWidth(el, width)
  }
  ro.cb([{ contentRect: { width } }])
  await nextTick()
  await flushPromises()
}

describe('PathLabel rendering', () => {
  it('renders all segments when the path is short', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['Gebäude', 'EG', 'Küche'] },
    })
    await fireResize(800)
    const html = wrapper.text()
    expect(html).toContain('Gebäude')
    expect(html).toContain('EG')
    expect(html).toContain('Küche')
  })

  it('uses the configured separator', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['A', 'B'], separator: '/' },
    })
    await fireResize(800)
    expect(wrapper.text()).toContain('/')
  })

  it('hides the root segment when hideRoot=true', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['Gebäude', 'EG', 'Küche'], hideRoot: true },
    })
    await fireResize(800)
    expect(wrapper.text()).not.toContain('Gebäude')
    expect(wrapper.text()).toContain('EG')
    expect(wrapper.text()).toContain('Küche')
  })

  it('collapses long paths in the middle when width is constrained', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['A', 'B', 'C', 'D', 'E'] },
    })
    // Force a narrow container
    await fireResize(80)
    const text = wrapper.text()
    expect(text).toContain('A') // first
    expect(text).toContain('E') // last
    expect(text).toContain('…')
  })

  it('shows leaf-only with ellipsis prefix when very narrow', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['A', 'B', 'C', 'D', 'E'] },
    })
    await fireResize(20)
    const text = wrapper.text()
    expect(text).toContain('E')
    expect(text).toContain('…')
  })

  it('renders nothing when segments is empty', () => {
    const wrapper = mount(PathLabel, { props: { segments: [] } })
    expect(wrapper.text()).toBe('')
  })
})

describe('PathLabel tooltip', () => {
  it('renders a full-path tooltip element with the joined segments', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['Gebäude', 'EG', 'Küche'] },
      attachTo: document.body,
    })
    await fireResize(50)
    // Trigger pointer enter to show the tooltip
    await wrapper.find('[data-testid="pathlabel-root"]').trigger('pointerenter')
    await flushPromises()
    const tooltip = wrapper.find('[data-testid="pathlabel-tooltip"]')
    expect(tooltip.exists()).toBe(true)
    expect(tooltip.text()).toContain('Gebäude')
    expect(tooltip.text()).toContain('EG')
    expect(tooltip.text()).toContain('Küche')
    wrapper.unmount()
  })

  it('hides the tooltip on pointer leave', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['A', 'B'] },
      attachTo: document.body,
    })
    await fireResize(50)
    await wrapper.find('[data-testid="pathlabel-root"]').trigger('pointerenter')
    await flushPromises()
    expect(wrapper.find('[data-testid="pathlabel-tooltip"]').exists()).toBe(true)
    await wrapper.find('[data-testid="pathlabel-root"]').trigger('pointerleave')
    await flushPromises()
    expect(wrapper.find('[data-testid="pathlabel-tooltip"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('PathLabel cleanup', () => {
  it('disconnects the ResizeObserver on unmount', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['A', 'B'] },
    })
    await fireResize(200)
    const ro = FakeResizeObserver.instances[FakeResizeObserver.instances.length - 1]
    expect(ro.disconnected).toBe(false)
    wrapper.unmount()
    expect(ro.disconnected).toBe(true)
  })

  it('removes the floating-UI tooltip from the DOM when unmounted while open', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['Gebäude', 'EG', 'Küche'] },
      attachTo: document.body,
    })
    await fireResize(50)
    await wrapper.find('[data-testid="pathlabel-root"]').trigger('pointerenter')
    await flushPromises()
    expect(document.querySelector('[data-testid="pathlabel-tooltip"]')).not.toBeNull()
    wrapper.unmount()
    // After unmount the tooltip node must be gone — no detached overlay leaks.
    expect(document.querySelector('[data-testid="pathlabel-tooltip"]')).toBeNull()
  })

  it('survives an unmount that happens immediately after pointer-leave', async () => {
    // Regression: pointer-leave triggers stopAutoUpdateFn which must be safe to
    // call again from onBeforeUnmount.
    const wrapper = mount(PathLabel, {
      props: { segments: ['A', 'B'] },
      attachTo: document.body,
    })
    await fireResize(80)
    await wrapper.find('[data-testid="pathlabel-root"]').trigger('pointerenter')
    await wrapper.find('[data-testid="pathlabel-root"]').trigger('pointerleave')
    await flushPromises()
    expect(() => wrapper.unmount()).not.toThrow()
  })
})

describe('PathLabel edge-case path sizes (#439)', () => {
  it('renders a 1-segment path without any truncation marker', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['OnlyOne'] },
    })
    await fireResize(800)
    const text = wrapper.text()
    expect(text).toContain('OnlyOne')
    expect(text).not.toContain('…')
  })

  it('renders a 1-segment path even on a very narrow container', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: ['SoleLeaf'] },
    })
    // 1 segment <= capacity is always true (capacity floors to >= 1)
    await fireResize(10)
    expect(wrapper.text()).toContain('SoleLeaf')
    // No ellipsis needed because we already fit
    expect(wrapper.text()).not.toContain('…')
  })

  it('collapses a 100-segment path cleanly into first + ellipsis + last', async () => {
    const segs = Array.from({ length: 100 }, (_, i) => `S${i}`)
    const wrapper = mount(PathLabel, { props: { segments: segs } })
    // 100 segments / 40px each = needs ~4000px → 200px → collapse to 3 visible parts
    await fireResize(200)
    const text = wrapper.text()
    expect(text).toContain('S0')
    expect(text).toContain('S99')
    expect(text).toContain('…')
    // Random middle segment must be absent
    expect(text).not.toContain('S42')
  })

  it('renders the tooltip with the full 100-segment path joined by the separator', async () => {
    const segs = Array.from({ length: 100 }, (_, i) => `Node${i}`)
    const wrapper = mount(PathLabel, {
      props: { segments: segs },
      attachTo: document.body,
    })
    await fireResize(50)
    await wrapper.find('[data-testid="pathlabel-root"]').trigger('pointerenter')
    await flushPromises()
    const tooltip = wrapper.find('[data-testid="pathlabel-tooltip"]')
    expect(tooltip.exists()).toBe(true)
    // Tooltip contains the joined full text — verify a sampled middle segment is in it
    expect(tooltip.text()).toContain('Node0')
    expect(tooltip.text()).toContain('Node50')
    expect(tooltip.text()).toContain('Node99')
    wrapper.unmount()
  })

  it('handles an empty segments array without showing a tooltip on hover', async () => {
    const wrapper = mount(PathLabel, {
      props: { segments: [] },
      attachTo: document.body,
    })
    await fireResize(800)
    expect(wrapper.text()).toBe('')
    await wrapper.find('[data-testid="pathlabel-root"]').trigger('pointerenter')
    await flushPromises()
    // fullText is empty, so showTooltip early-returns and no tooltip node renders.
    expect(wrapper.find('[data-testid="pathlabel-tooltip"]').exists()).toBe(false)
    wrapper.unmount()
  })
})
