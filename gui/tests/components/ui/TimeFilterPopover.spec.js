import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import TimeFilterPopover from '@/components/ui/TimeFilterPopover.vue'

describe('TimeFilterPopover', () => {
  beforeEach(() => {
    // Pin "now" so relative-offset previews are deterministic
    vi.useFakeTimers()
    vi.setSystemTime(new Date('2026-05-12T14:00:00'))
  })

  afterEach(() => {
    vi.useRealTimers()
  })

  function open(wrapper) {
    return wrapper.find('[data-testid="time-filter-trigger"]').trigger('click')
  }

  it('renders trigger with "aus" label when modelValue is null', () => {
    const wrapper = mount(TimeFilterPopover, { props: { modelValue: null } })
    expect(wrapper.find('[data-testid="time-filter-trigger"]').text()).toContain('aus')
  })

  it('opens the popover on trigger click', async () => {
    const wrapper = mount(TimeFilterPopover, { props: { modelValue: null }, attachTo: document.body })
    await open(wrapper)
    expect(wrapper.find('[data-testid="time-filter-popover"]').exists()).toBe(true)
    wrapper.unmount()
  })

  it('shows live preview when Ab field has a valid relative input', async () => {
    const wrapper = mount(TimeFilterPopover, { props: { modelValue: null }, attachTo: document.body })
    await open(wrapper)
    const ab = wrapper.find('[data-testid="input-from"]')
    await ab.setValue('-1h')
    const preview = wrapper.find('[data-testid="preview-from"]')
    // 14:00 - 1h = 13:00:00
    expect(preview.text()).toContain('13:00:00')
    expect(preview.classes().some((c) => c.includes('red'))).toBe(false)
    wrapper.unmount()
  })

  it('shows red error label for invalid input', async () => {
    const wrapper = mount(TimeFilterPopover, { props: { modelValue: null }, attachTo: document.body })
    await open(wrapper)
    await wrapper.find('[data-testid="input-from"]').setValue('abc')
    const preview = wrapper.find('[data-testid="preview-from"]')
    expect(preview.text()).toContain('ungültig')
    expect(preview.classes().some((c) => c.includes('red'))).toBe(true)
    wrapper.unmount()
  })

  it('greys out range section when both point and span are filled', async () => {
    const wrapper = mount(TimeFilterPopover, { props: { modelValue: null }, attachTo: document.body })
    await open(wrapper)
    await wrapper.find('[data-testid="input-point"]').setValue('-1h')
    await wrapper.find('[data-testid="input-span"]').setValue('10min')
    expect(wrapper.find('[data-testid="range-overridden"]').exists()).toBe(true)
    const rangeSection = wrapper.find('[data-testid="range-section"]')
    expect(rangeSection.classes().some((c) => c.includes('opacity'))).toBe(true)
    expect(wrapper.find('[data-testid="input-from"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('emits update:modelValue with point/span on apply when point mode is active', async () => {
    const wrapper = mount(TimeFilterPopover, { props: { modelValue: null }, attachTo: document.body })
    await open(wrapper)
    await wrapper.find('[data-testid="input-point"]').setValue('-1h')
    await wrapper.find('[data-testid="input-span"]').setValue('10min')
    await wrapper.find('[data-testid="btn-apply"]').trigger('click')
    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    expect(emitted[0][0].mode).toBe('point')
    expect(emitted[0][0].point).toBeInstanceOf(Date)
    expect(emitted[0][0].span.seconds).toBe(600)
    wrapper.unmount()
  })

  it('emits update:modelValue with range from/to on apply when only range is filled', async () => {
    const wrapper = mount(TimeFilterPopover, { props: { modelValue: null }, attachTo: document.body })
    await open(wrapper)
    await wrapper.find('[data-testid="input-from"]').setValue('-1h')
    await wrapper.find('[data-testid="input-to"]').setValue('-5min')
    await wrapper.find('[data-testid="btn-apply"]').trigger('click')
    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    expect(emitted[0][0].mode).toBe('range')
    expect(emitted[0][0].from.seconds).toBe(3600)
    expect(emitted[0][0].from.sign).toBe(-1)
    expect(emitted[0][0].to.seconds).toBe(300)
    wrapper.unmount()
  })

  it('reset button clears form and emits null', async () => {
    const wrapper = mount(TimeFilterPopover, { props: { modelValue: null }, attachTo: document.body })
    await open(wrapper)
    await wrapper.find('[data-testid="input-from"]').setValue('-1h')
    await wrapper.find('[data-testid="btn-reset"]').trigger('click')
    const emitted = wrapper.emitted('update:modelValue')
    expect(emitted).toBeTruthy()
    expect(emitted[emitted.length - 1][0]).toBeNull()
    wrapper.unmount()
  })

  it('hydrates form from existing modelValue on open (range mode)', async () => {
    const wrapper = mount(TimeFilterPopover, {
      props: {
        modelValue: { mode: 'range', from: { seconds: 3600, sign: -1 }, to: { seconds: 300, sign: -1 } },
      },
      attachTo: document.body,
    })
    await open(wrapper)
    expect(wrapper.find('[data-testid="input-from"]').element.value).toBe('-1h')
    expect(wrapper.find('[data-testid="input-to"]').element.value).toBe('-5min')
    wrapper.unmount()
  })

  it('hydrates form from existing modelValue on open (point mode)', async () => {
    const wrapper = mount(TimeFilterPopover, {
      props: {
        modelValue: { mode: 'point', point: { seconds: 3600, sign: -1 }, span: { seconds: 600, sign: 1 } },
      },
      attachTo: document.body,
    })
    await open(wrapper)
    expect(wrapper.find('[data-testid="input-point"]').element.value).toBe('-1h')
    expect(wrapper.find('[data-testid="input-span"]').element.value).toBe('10min')
    wrapper.unmount()
  })

  it('trigger label reflects current modelValue', () => {
    const wrapper = mount(TimeFilterPopover, {
      props: { modelValue: { mode: 'range', from: { seconds: 3600, sign: -1 } } },
    })
    expect(wrapper.find('[data-testid="time-filter-trigger"]').text()).toContain('Letzte 1h')
  })
})
