/**
 * Page-level help entry point in the header (#1183).
 *
 * Every named route declares a `meta.helpId` (gui/src/router/index.js) and
 * tools/check_help_contract.py fails CI when one is missing or does not
 * resolve. TopBar is what makes that declaration live: it renders the
 * page-level HelpButton from the current route's meta, next to the page
 * title and independent of the section-level buttons inside the views.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
})

afterEach(() => {
  vi.doUnmock('vue-router')
  vi.doUnmock('@/stores/auth')
  vi.doUnmock('@/stores/websocket')
  vi.doUnmock('@/api/client')
})

async function mountTopBar({ routeName = 'Dashboard', meta = { helpId: 'dashboard' } } = {}) {
  vi.doMock('vue-router', () => ({
    useRoute:  () => ({ name: routeName, path: '/', meta }),
    useRouter: () => ({ push: vi.fn() }),
  }))
  vi.doMock('@/stores/auth', () => ({
    useAuthStore: () => ({ username: 'admin', isLoggedIn: true, logout: vi.fn() }),
  }))
  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({ connected: true, disconnect: vi.fn() }),
  }))
  vi.doMock('@/api/client', () => ({
    helpApi: { index: vi.fn().mockResolvedValue({ data: { helpIds: {} } }) },
  }))

  const pinia = createPinia()
  setActivePinia(pinia)

  const { default: TopBar } = await import('@/components/layout/TopBar.vue')
  const w = mount(TopBar, {
    global: {
      plugins: [pinia],
      stubs: { RouterLink: { template: '<a href="#"><slot /></a>' } },
    },
  })
  await flushPromises()
  return w
}

describe('TopBar — page-level help button', () => {
  it('renders the help button for the current route help_id', async () => {
    const w = await mountTopBar({ routeName: 'Logs', meta: { helpId: 'logs' } })

    expect(w.find('[data-testid="help-button-logs"]').exists()).toBe(true)
  })

  it('opens the help store with the route help_id when clicked', async () => {
    const w = await mountTopBar({ routeName: 'Settings', meta: { helpId: 'settings' } })
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await w.find('[data-testid="help-button-settings"]').trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe('settings')
  })

  it('renders no help button on a route without a help_id (Login)', async () => {
    const w = await mountTopBar({ routeName: 'Login', meta: { public: true } })

    expect(w.find('button[aria-label]').exists()).toBe(false)
  })

  // `route.meta` is always an object under vue-router, but TopBar guards it
  // anyway — the guard is a branch, so it gets a test.
  it('renders no help button when the route carries no meta', async () => {
    const w = await mountTopBar({ routeName: 'Unknown', meta: null })

    expect(w.find('button[aria-label]').exists()).toBe(false)
  })
})
