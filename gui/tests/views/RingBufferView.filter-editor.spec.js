/**
 * Tests for the FilterEditor wiring in RingBufferView (issue #436).
 *
 * Verifies:
 *  - TopbarFilterChips `edit-set` payload opens the editor with that id
 *  - TopbarFilterChips `new-set` payload opens the editor with null id
 *  - On the editor's `saved` event, the topbar chips and the legacy
 *    filterset selector are refreshed
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

describe('RingBufferView FilterEditor wiring (#436)', () => {
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

  it('opens the editor with the set id when topbar emits edit-set', async () => {
    const { mountRingBufferView, flushPromises } = await import('../helpers/mountRingBufferView.js')
    const { wrapper } = await mountRingBufferView()

    const chips = wrapper.findComponent({ name: 'TopbarFilterChips' })
    await chips.vm.$emit('edit-set', 'fs-42')
    await flushPromises()

    const editor = wrapper.findComponent({ name: 'FilterEditor' })
    expect(editor.exists()).toBe(true)
    expect(editor.props('setId')).toBe('fs-42')
    expect(editor.props('modelValue')).toBe(true)
  })

  it('opens the editor with null setId when topbar emits new-set', async () => {
    const { mountRingBufferView, flushPromises } = await import('../helpers/mountRingBufferView.js')
    const { wrapper } = await mountRingBufferView()

    const chips = wrapper.findComponent({ name: 'TopbarFilterChips' })
    await chips.vm.$emit('new-set')
    await flushPromises()

    const editor = wrapper.findComponent({ name: 'FilterEditor' })
    expect(editor.props('setId')).toBe(null)
    expect(editor.props('modelValue')).toBe(true)
  })

  it('reloads filtersets after the editor emits saved', async () => {
    const { mountRingBufferView, makeRingbufferApiMock, flushPromises } = await import('../helpers/mountRingBufferView.js')
    const ringbufferApi = makeRingbufferApiMock()
    const { wrapper } = await mountRingBufferView({ ringbufferApi })

    // Reset to count calls from this point
    ringbufferApi.listFiltersets.mockClear()

    const editor = wrapper.findComponent({ name: 'FilterEditor' })
    await editor.vm.$emit('saved', { id: 'fs-new' })
    await flushPromises()

    expect(ringbufferApi.listFiltersets).toHaveBeenCalledTimes(1)
  })
})
