import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'

describe('RingBufferView mounts', () => {
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

  it('mounts with empty initial state', async () => {
    const { mountRingBufferView } = await import('../helpers/mountRingBufferView.js')
    const { wrapper, ringbufferApi } = await mountRingBufferView()

    // After the #438 cleanup RingBufferView fetches the data list and the
    // filterset cache (for row colouring); TopbarStats owns its own /stats
    // fetch independently and is stubbed away in this mount.
    expect(ringbufferApi.queryV2).toHaveBeenCalledTimes(1)
    expect(ringbufferApi.listFiltersets).toHaveBeenCalledTimes(1)
    expect(wrapper.find('[data-testid="ringbuffer-empty"]').exists()).toBe(true)
    // The legacy filterset admin <select> is gone — topbar chips replaced it.
    expect(wrapper.find('[data-testid="select-filterset"]').exists()).toBe(false)
    expect(wrapper.findComponent({ name: 'TopbarFilterChips' }).exists()).toBe(true)
  })
})
