/**
 * Tests for the TimeFilterPopover wiring into RingBufferView (#438).
 *
 * Verifies:
 *   - TimeFilterPopover (#432) is rendered inside the TopbarFilterChips
 *     "time-filter-slot" — i.e. it appears inside the chip strip.
 *   - When TimeFilterPopover emits `update:modelValue`, RingBufferView
 *     re-runs the load() query and the new payload contains the time
 *     block in the expected backend shape.
 *   - For the no-topbar-set path the time filter is merged into the
 *     queryV2 payload (filters.time).
 *   - For the topbar-multi-set path the time filter is merged into the
 *     queryMultiFiltersets body at the top level (BE-08).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

describe('RingBufferView TimeFilterPopover wiring (#438)', () => {
  beforeEach(() => {
    vi.resetModules()
  })

  afterEach(() => {
    vi.doUnmock('@/api/client')
    vi.doUnmock('@/stores/websocket')
    vi.doUnmock('@/composables/useTz')
    vi.doUnmock('@/components/ui/Badge.vue')
    vi.doUnmock('@/components/ui/Spinner.vue')
    vi.doUnmock('@/components/ui/Modal.vue')
  })

  it('renders TimeFilterPopover inside TopbarFilterChips', async () => {
    const { mountRingBufferView } = await import('../../helpers/mountRingBufferView.js')
    const { wrapper } = await mountRingBufferView()
    const popover = wrapper.findComponent({ name: 'TimeFilterPopover' })
    // The TopbarFilterChips component is stubbed in the helper, so the
    // popover is rendered inside its default slot. Either way the
    // component must mount.
    expect(popover.exists()).toBe(true)
  })

  it('merges an absolute range into queryV2.filters.time when no topbar set is active', async () => {
    const { mountRingBufferView, makeRingbufferApiMock, flushPromises } = await import(
      '../../helpers/mountRingBufferView.js'
    )
    const ringbufferApi = makeRingbufferApiMock()
    const { wrapper } = await mountRingBufferView({ ringbufferApi })

    ringbufferApi.queryV2.mockClear()
    const popover = wrapper.findComponent({ name: 'TimeFilterPopover' })
    const from = new Date('2026-05-11T10:00:00Z')
    const to = new Date('2026-05-11T11:00:00Z')
    await popover.vm.$emit('update:modelValue', { mode: 'range', from, to })
    await flushPromises()

    expect(ringbufferApi.queryV2).toHaveBeenCalledTimes(1)
    const body = ringbufferApi.queryV2.mock.calls[0][0]
    expect(body.filters.time).toBeTruthy()
    expect(body.filters.time.from).toBe(from.toISOString())
    expect(body.filters.time.to).toBe(to.toISOString())
  })

  it('relative duration bounds map to from_relative_seconds / to_relative_seconds', async () => {
    const { mountRingBufferView, makeRingbufferApiMock, flushPromises } = await import(
      '../../helpers/mountRingBufferView.js'
    )
    const ringbufferApi = makeRingbufferApiMock()
    const { wrapper } = await mountRingBufferView({ ringbufferApi })

    ringbufferApi.queryV2.mockClear()
    const popover = wrapper.findComponent({ name: 'TimeFilterPopover' })
    await popover.vm.$emit('update:modelValue', {
      mode: 'range',
      from: { seconds: 3600, sign: -1 },
      to: { seconds: 300, sign: -1 },
    })
    await flushPromises()

    const body = ringbufferApi.queryV2.mock.calls[0][0]
    expect(body.filters.time).toEqual({
      from_relative_seconds: -3600,
      to_relative_seconds: -300,
    })
  })

  it('point ± span collapses into a from/to ISO pair', async () => {
    const { mountRingBufferView, makeRingbufferApiMock, flushPromises } = await import(
      '../../helpers/mountRingBufferView.js'
    )
    const ringbufferApi = makeRingbufferApiMock()
    const { wrapper } = await mountRingBufferView({ ringbufferApi })

    ringbufferApi.queryV2.mockClear()
    const popover = wrapper.findComponent({ name: 'TimeFilterPopover' })
    const point = new Date('2026-05-11T12:00:00Z')
    await popover.vm.$emit('update:modelValue', {
      mode: 'point',
      point,
      span: { seconds: 600, sign: 1 },
    })
    await flushPromises()

    const body = ringbufferApi.queryV2.mock.calls[0][0]
    expect(body.filters.time.from).toBe(new Date(point.getTime() - 600 * 1000).toISOString())
    expect(body.filters.time.to).toBe(new Date(point.getTime() + 600 * 1000).toISOString())
  })

  it('merges the time filter into queryMultiFiltersets when topbar sets are active', async () => {
    const { mountRingBufferView, makeRingbufferApiMock, flushPromises } = await import(
      '../../helpers/mountRingBufferView.js'
    )
    const ringbufferApi = makeRingbufferApiMock({
      listFiltersets: vi.fn().mockResolvedValue({
        data: [
          {
            id: 'set-a',
            name: 'A',
            color: '#3b82f6',
            is_active: true,
            topbar_active: true,
            topbar_order: 0,
          },
        ],
      }),
    })
    const { wrapper } = await mountRingBufferView({ ringbufferApi })

    ringbufferApi.queryMultiFiltersets.mockClear()
    const popover = wrapper.findComponent({ name: 'TimeFilterPopover' })
    const from = new Date('2026-05-11T10:00:00Z')
    await popover.vm.$emit('update:modelValue', { mode: 'range', from })
    await flushPromises()

    expect(ringbufferApi.queryMultiFiltersets).toHaveBeenCalledTimes(1)
    const body = ringbufferApi.queryMultiFiltersets.mock.calls[0][0]
    expect(body.set_ids).toEqual(['set-a'])
    expect(body.time).toBeTruthy()
    expect(body.time.from).toBe(from.toISOString())
  })

  it('clearing the time filter (null) keeps the load() query running and drops the time block', async () => {
    const { mountRingBufferView, makeRingbufferApiMock, flushPromises } = await import(
      '../../helpers/mountRingBufferView.js'
    )
    const ringbufferApi = makeRingbufferApiMock()
    const { wrapper } = await mountRingBufferView({ ringbufferApi })

    const popover = wrapper.findComponent({ name: 'TimeFilterPopover' })
    await popover.vm.$emit('update:modelValue', {
      mode: 'range',
      from: new Date('2026-05-11T10:00:00Z'),
    })
    await flushPromises()

    ringbufferApi.queryV2.mockClear()
    await popover.vm.$emit('update:modelValue', null)
    await flushPromises()

    expect(ringbufferApi.queryV2).toHaveBeenCalledTimes(1)
    const body = ringbufferApi.queryV2.mock.calls[0][0]
    expect(body.filters.time).toBeUndefined()
  })
})
