import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let loadIndexMock

beforeEach(() => {
  vi.resetModules()
  loadIndexMock = vi.fn()
})

afterEach(() => {
  vi.doUnmock('vue-router')
  vi.doUnmock('@/stores/auth')
  vi.doUnmock('@/stores/websocket')
  vi.doUnmock('@/stores/settings')
  vi.doUnmock('@/stores/help')
})

async function mountApp({ isLoggedIn = false, helpIsOpen = false, helpDrawerWidth = 0 } = {}) {
  vi.doMock('vue-router', () => ({
    useRoute: () => ({ meta: {}, name: 'Dashboard' }),
  }))
  vi.doMock('@/stores/auth', () => ({
    useAuthStore: () => ({ isLoggedIn, loadMe: vi.fn() }),
  }))
  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({ connect: vi.fn() }),
  }))
  vi.doMock('@/stores/settings', () => ({
    useSettingsStore: () => ({ theme: 'system', load: vi.fn(), applyTheme: vi.fn() }),
  }))
  vi.doMock('@/stores/help', () => ({
    useHelpStore: () => ({
      loadIndex: loadIndexMock,
      isOpen: helpIsOpen,
      drawerWidth: helpDrawerWidth,
      reservedRight: helpIsOpen ? `min(${helpDrawerWidth}px, 90vw)` : '0px',
    }),
  }))

  const pinia = createPinia()
  setActivePinia(pinia)

  const { default: App } = await import('@/App.vue')
  const w = mount(App, {
    global: {
      plugins: [pinia],
      stubs: {
        AppLayout: { template: '<div data-testid="app-layout"><slot /></div>' },
        PlainLayout: { template: '<div data-testid="plain-layout"><slot /></div>' },
        RouterView: { template: '<div data-testid="router-view" />' },
        HelpDrawer: { template: '<div data-testid="help-drawer-stub" />' },
      },
    },
  })
  await flushPromises()
  return w
}

describe('App — help drawer wiring (#896)', () => {
  it('renders the HelpDrawer alongside the layout', async () => {
    const w = await mountApp()
    expect(w.find('[data-testid="help-drawer-stub"]').exists()).toBe(true)
  })

  it('prefetches the help index on mount regardless of login state', async () => {
    await mountApp({ isLoggedIn: false })
    expect(loadIndexMock).toHaveBeenCalledTimes(1)
  })

  it('still prefetches the help index when logged in', async () => {
    await mountApp({ isLoggedIn: true })
    expect(loadIndexMock).toHaveBeenCalledTimes(1)
  })
})

describe('App — reserves layout space for the open drawer (issue feedback: it must not cover content)', () => {
  it('applies no right margin to the layout while the drawer is closed', async () => {
    const w = await mountApp({ helpIsOpen: false, helpDrawerWidth: 480 })
    const layout = w.find('[data-testid="app-layout"]')
    expect(layout.attributes('style')).toContain('margin-right: 0px')
  })

  it('applies a right margin matching the drawer width while it is open', async () => {
    // Asserted against the component's own computed value rather than the
    // rendered style attribute: happy-dom's CSSStyleDeclaration doesn't
    // recognize the CSS min() function and silently drops the declaration
    // from the serialized style string, even though real browsers apply it
    // correctly (broadly supported since ~2020).
    const w = await mountApp({ helpIsOpen: true, helpDrawerWidth: 480 })
    expect(w.vm.contentAreaStyle.marginRight).toBe('min(480px, 90vw)')
  })

  it("caps the reserved margin at 90vw via CSS min(), matching HelpDrawer.vue's own maxWidth (issue feedback: a persisted wide drawerWidth on a narrower viewport must not reserve more margin than the drawer actually renders at)", async () => {
    const w = await mountApp({ helpIsOpen: true, helpDrawerWidth: 960 })
    expect(w.vm.contentAreaStyle.marginRight).toBe('min(960px, 90vw)')
  })
})
