/**
 * Integrated help drawer wiring on the Monitor/RingBufferView view.
 *
 * The toolbar and the live-table card each got a HelpButton pointing at a
 * help_id documented in help/ringbuffer/overview.md. This spec checks the
 * buttons are present with the right help_id and that clicking one opens
 * the real help store.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { vi } from 'vitest'

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

function helpButton(wrapper, helpId) {
  return wrapper.find(`[data-testid="help-button-${helpId}"]`)
}

describe('RingBufferView — help buttons', () => {
  it.each(['ringbuffer-toolbar', 'ringbuffer-table'])('renders a help button for %s', async (helpId) => {
    const { mountRingBufferView } = await import('../helpers/mountRingBufferView.js')
    const { wrapper } = await mountRingBufferView()
    expect(helpButton(wrapper, helpId).exists()).toBe(true)
  })

  it.each(['ringbuffer-toolbar', 'ringbuffer-table'])(
    'opens the help store with %s when its button is clicked',
    async (helpId) => {
      const { mountRingBufferView } = await import('../helpers/mountRingBufferView.js')
      const { wrapper } = await mountRingBufferView()
      const { useHelpStore } = await import('@/stores/help')
      const helpStore = useHelpStore()

      await helpButton(wrapper, helpId).trigger('click')

      expect(helpStore.isOpen).toBe(true)
      expect(helpStore.currentHelpId).toBe(helpId)
    }
  )
})
