/**
 * Integrated help drawer wiring on the Dashboard's RingBuffer/Retention card
 * (help_id documented in help/dashboard/overview.md).
 */
import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
  vi.doUnmock('@/stores/auth')
})

async function mountCard() {
  vi.doMock('@/api/client', () => ({
    ringbufferApi: { stats: vi.fn().mockResolvedValue({ data: { enabled: false } }), config: vi.fn() },
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))
  vi.doMock('@/stores/auth', () => ({
    useAuthStore: () => ({ isAdmin: true }),
  }))
  const { default: RingBufferCard } = await import('@/components/dashboard/RingBufferCard.vue')
  const wrapper = mount(RingBufferCard, {
    global: {
      stubs: {
        RouterLink: { template: '<a href="#"><slot /></a>' },
        Spinner: { template: '<span class="spinner" />' },
        Modal: { template: '<div v-if="modelValue"><slot /></div>', props: ['modelValue', 'title', 'maxWidth'] },
        MonitorConfigModal: true,
        LegacyMigrationBanner: true,
        LegacyMigrationWizard: true,
      },
    },
  })
  await flushPromises()
  return wrapper
}

describe('RingBufferCard — help button (#896)', () => {
  it('renders a help button pointing at dashboard-ringbuffer', async () => {
    const wrapper = await mountCard()
    expect(wrapper.find('[data-testid="help-button-dashboard-ringbuffer"]').exists()).toBe(true)
  })

  it('opens the help store with dashboard-ringbuffer when clicked', async () => {
    const wrapper = await mountCard()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await wrapper.find('[data-testid="help-button-dashboard-ringbuffer"]').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('dashboard-ringbuffer')
  })
})
