/**
 * Integrated help drawer wiring on the Meldungsarchive/MessageArchivesView view.
 *
 * The header, the archive detail panel, and the entries table each got a
 * HelpButton pointing at a help_id documented in help/messagearchives/list.md.
 * This spec checks the buttons are present with the right help_id and that
 * clicking one opens the real help store.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const archive = {
  id: 'system',
  name: 'System',
  description: 'Systemmeldungen',
  default_type: 'system',
  color: '#123456',
  retention_max_entries: 100,
  retention_max_age_days: 30,
  entry_count: 2,
  db_path: '/data/archives/messages.sqlite3',
}

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

async function mountMessageArchivesView() {
  vi.doMock('@/api/client', () => ({
    messageArchivesApi: {
      list: vi.fn().mockResolvedValue({ data: [archive] }),
      entries: vi.fn().mockResolvedValue({ data: { items: [], total: 0 } }),
      integrityCheck: vi.fn(),
      create: vi.fn(),
      update: vi.fn(),
      delete: vi.fn(),
      clear: vi.fn(),
      export: vi.fn(),
    },
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))

  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const { default: MessageArchivesView } = await import('@/views/MessageArchivesView.vue')
  const wrapper = mount(MessageArchivesView, { attachTo: document.body })
  await flushPromises()
  return wrapper
}

function helpButton(wrapper, helpId) {
  return wrapper.find(`[data-testid="help-button-${helpId}"]`)
}

describe('MessageArchivesView — help buttons', () => {
  const helpIds = ['messagearchives-list', 'messagearchives-detail', 'messagearchives-entries']

  it.each(helpIds)('renders a help button for %s', async (helpId) => {
    const wrapper = await mountMessageArchivesView()
    expect(helpButton(wrapper, helpId).exists()).toBe(true)
  })

  it.each(helpIds)('opens the help store with %s when its button is clicked', async (helpId) => {
    const wrapper = await mountMessageArchivesView()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, helpId).trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe(helpId)
  })
})
