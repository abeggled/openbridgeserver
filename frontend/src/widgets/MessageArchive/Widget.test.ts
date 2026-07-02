// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { messageArchives, type MessageArchiveEntry } from '@/api/client'
import MessageArchiveWidget from './Widget.vue'

const wsHandlers = vi.hoisted(() => ({
  current: [] as Array<(data: Record<string, unknown>) => void>,
}))

vi.mock('@/api/client', () => ({
  messageArchives: {
    entries: vi.fn(),
    markRead: vi.fn(),
    acknowledge: vi.fn(),
  },
}))

vi.mock('@/composables/useWebSocket', () => ({
  useWebSocket: () => ({
    onMessage: vi.fn((handler: (data: Record<string, unknown>) => void) => {
      wsHandlers.current.push(handler)
      return () => {
        wsHandlers.current = wsHandlers.current.filter(item => item !== handler)
      }
    }),
  }),
}))

vi.mock('vue-i18n', () => ({
  useI18n: () => ({
    t: (key: string) => key,
  }),
}))

const entry: MessageArchiveEntry = {
  id: 'entry-1',
  archive_id: 'system',
  archive_name: 'System',
  archive_color: '#3b82f6',
  created_at: '2026-01-01T10:00:00+00:00',
  updated_at: '2026-01-01T10:00:00+00:00',
  type: 'system',
  severity: 'info',
  status: 'new',
  source: 'core',
  title: 'Startup',
  message: 'Ready',
  payload: {},
  acknowledged_at: null,
  acknowledged_by: null,
  read_at: null,
  is_read: false,
}

afterEach(() => {
  wsHandlers.current = []
  vi.clearAllMocks()
})

describe('MessageArchive Widget.vue', () => {
  it('hides archive actions on readonly pages', async () => {
    vi.mocked(messageArchives.entries).mockResolvedValue({ items: [entry], total: 1, limit: 25, offset: 0 })

    const wrapper = mount(MessageArchiveWidget, {
      props: {
        config: { allow_read: true, allow_acknowledge: true },
        editorMode: false,
        readonly: true,
      },
      global: {
        mocks: { $t: (key: string) => key },
      },
    })
    await flushPromises()

    expect(wrapper.text()).not.toContain('widgets.messageArchive.markRead')
    expect(wrapper.text()).not.toContain('widgets.messageArchive.acknowledge')
  })

  it('removes cached live entries that no longer match filters', async () => {
    vi.mocked(messageArchives.entries).mockResolvedValue({ items: [entry], total: 1, limit: 25, offset: 0 })

    const wrapper = mount(MessageArchiveWidget, {
      props: {
        config: { status: ['new'] },
        editorMode: false,
        readonly: false,
      },
      global: {
        mocks: { $t: (key: string) => key },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Startup')
    wsHandlers.current[0]({
      action: 'message_archive_entry',
      entry: { ...entry, status: 'acknowledged', acknowledged_at: '2026-01-01T10:01:00+00:00', acknowledged_by: 'admin' },
    })
    await flushPromises()

    expect(wrapper.text()).not.toContain('Startup')
  })
})
