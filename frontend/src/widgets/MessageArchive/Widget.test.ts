// @vitest-environment jsdom
import { flushPromises, mount } from '@vue/test-utils'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { messageArchives, type MessageArchiveEntry } from '@/api/client'
import MessageArchiveWidget from './Widget.vue'

const wsHandlers = vi.hoisted(() => ({
  current: [] as Array<(data: Record<string, unknown>) => void>,
}))

const apiState = vi.hoisted(() => ({
  jwt: 'jwt-1' as string | null,
  writeContext: {} as { pageId?: string; sessionToken?: string; definingId?: string },
}))

vi.mock('@/api/client', () => ({
  getJwt: vi.fn(() => apiState.jwt),
  getWriteContext: vi.fn(() => apiState.writeContext),
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

const olderEntry: MessageArchiveEntry = {
  ...entry,
  id: 'entry-older',
  created_at: '2025-12-31T10:00:00+00:00',
  updated_at: '2025-12-31T10:00:00+00:00',
  title: 'Older',
  message: entry.message,
}

afterEach(() => {
  wsHandlers.current = []
  apiState.jwt = 'jwt-1'
  apiState.writeContext = {}
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

  it('hides read action for anonymous page viewers', async () => {
    apiState.jwt = null
    apiState.writeContext = { pageId: 'page-1' }
    vi.mocked(messageArchives.entries).mockResolvedValue({ items: [entry], total: 1, limit: 25, offset: 0 })

    const wrapper = mount(MessageArchiveWidget, {
      props: {
        config: { allow_read: true, allow_acknowledge: true },
        editorMode: false,
        readonly: false,
      },
      global: {
        mocks: { $t: (key: string) => key },
      },
    })
    await flushPromises()

    expect(wrapper.text()).not.toContain('widgets.messageArchive.markRead')
    expect(wrapper.text()).toContain('widgets.messageArchive.acknowledge')
    wrapper.unmount()
  })

  it('refreshes when cached live entries no longer match filters', async () => {
    vi.mocked(messageArchives.entries)
      .mockResolvedValueOnce({ items: [entry], total: 1, limit: 25, offset: 0 })
      .mockResolvedValueOnce({ items: [olderEntry], total: 1, limit: 25, offset: 0 })

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

    expect(messageArchives.entries).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).not.toContain('Startup')
    expect(wrapper.text()).toContain('Older')
  })

  it('honors plural filter keys when loading and matching live entries', async () => {
    vi.mocked(messageArchives.entries).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 })

    const wrapper = mount(MessageArchiveWidget, {
      props: {
        config: {
          archive_ids: ['system'],
          severity: [],
          severities: ['warning'],
          status: [],
          statuses: ['new'],
          type: [],
          types: ['system'],
          source: [],
          sources: ['core'],
        },
        editorMode: false,
        readonly: false,
      },
      global: {
        mocks: { $t: (key: string) => key },
      },
    })
    await flushPromises()

    expect(messageArchives.entries).toHaveBeenCalledWith({
      archive_id: 'system',
      limit: 25,
      severity: 'warning',
      sort: 'desc',
      source: 'core',
      status: 'new',
      type: 'system',
    })

    wsHandlers.current[0]({ action: 'message_archive_entry', entry: { ...entry, severity: 'info' } })
    wsHandlers.current[0]({ action: 'message_archive_entry', entry: { ...entry, severity: 'warning' } })
    await flushPromises()

    expect(wrapper.text()).toContain('Startup')
    wrapper.unmount()
  })

  it('normalizes archive filters for loading and live entries', async () => {
    vi.mocked(messageArchives.entries).mockResolvedValue({ items: [], total: 0, limit: 25, offset: 0 })

    const wrapper = mount(MessageArchiveWidget, {
      props: {
        config: { archive_ids: ['SYSTEM'] },
        editorMode: false,
        readonly: false,
      },
      global: {
        mocks: { $t: (key: string) => key },
      },
    })
    await flushPromises()

    expect(messageArchives.entries).toHaveBeenCalledWith({
      archive_id: 'system',
      limit: 25,
      sort: 'desc',
    })

    wsHandlers.current[0]({ action: 'message_archive_entry', entry })
    await flushPromises()

    expect(wrapper.text()).toContain('Startup')
    wrapper.unmount()
  })

  it('keeps live updates sorted and preserves local read state', async () => {
    const readEntry = { ...entry, is_read: true, read_at: '2026-01-01T10:05:00+00:00' }
    vi.mocked(messageArchives.entries).mockResolvedValue({ items: [readEntry, olderEntry], total: 2, limit: 25, offset: 0 })

    const wrapper = mount(MessageArchiveWidget, {
      props: {
        config: {},
        editorMode: false,
        readonly: false,
      },
      global: {
        mocks: { $t: (key: string) => key },
      },
    })
    await flushPromises()

    wsHandlers.current[0]({
      action: 'message_archive_entry',
      entry: {
        ...olderEntry,
        status: 'acknowledged',
        acknowledged_at: '2026-01-01T10:06:00+00:00',
        acknowledged_by: 'admin',
      },
    })
    wsHandlers.current[0]({
      action: 'message_archive_entry',
      entry: {
        ...readEntry,
        status: 'acknowledged',
        acknowledged_at: '2026-01-01T10:07:00+00:00',
        acknowledged_by: 'other-user',
        is_read: false,
        read_at: null,
      },
    })
    await flushPromises()

    const articles = wrapper.findAll('article')
    expect(articles[0].text()).toContain('Startup')
    expect(articles[1].text()).toContain('Older')
    expect(articles[0].text()).not.toContain('widgets.messageArchive.unread')
    wrapper.unmount()
  })

  it('refreshes live entries when the loaded archive can be retention-pruned', async () => {
    const retainedEntry = { ...entry, id: 'entry-retained', title: 'Retained' }
    const newEntry = {
      ...entry,
      id: 'entry-new',
      title: 'New',
      created_at: '2026-01-01T10:10:00+00:00',
      updated_at: '2026-01-01T10:10:00+00:00',
    }
    vi.mocked(messageArchives.entries)
      .mockResolvedValueOnce({ items: [retainedEntry, olderEntry], total: 2, limit: 25, offset: 0 })
      .mockResolvedValueOnce({ items: [newEntry, retainedEntry], total: 2, limit: 25, offset: 0 })

    const wrapper = mount(MessageArchiveWidget, {
      props: {
        config: {},
        editorMode: false,
        readonly: false,
      },
      global: {
        mocks: { $t: (key: string) => key },
      },
    })
    await flushPromises()

    expect(wrapper.text()).toContain('Older')
    wsHandlers.current[0]({ action: 'message_archive_entry', entry: newEntry })
    await flushPromises()

    expect(messageArchives.entries).toHaveBeenCalledTimes(2)
    expect(wrapper.text()).toContain('New')
    expect(wrapper.text()).toContain('Retained')
    expect(wrapper.text()).not.toContain('Older')
    wrapper.unmount()
  })
})
