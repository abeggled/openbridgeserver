import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

let pushMock
let logoutMock
let disconnectMock

beforeEach(() => {
  vi.resetModules()
  pushMock       = vi.fn()
  logoutMock     = vi.fn()
  disconnectMock = vi.fn()
})

afterEach(() => {
  vi.doUnmock('vue-router')
  vi.doUnmock('@/stores/auth')
  vi.doUnmock('@/stores/websocket')
})

async function mountTopBar({ routeName = 'Dashboard', username = 'admin' } = {}) {
  vi.doMock('vue-router', () => ({
    useRoute:  () => ({ name: routeName, path: `/${routeName.toLowerCase()}` }),
    useRouter: () => ({ push: pushMock }),
  }))
  vi.doMock('@/stores/auth', () => ({
    useAuthStore: () => ({ username, isLoggedIn: true, logout: logoutMock }),
  }))
  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({ connected: true, disconnect: disconnectMock }),
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

describe('TopBar — page title', () => {
  it('shows German title for Dashboard route', async () => {
    const w = await mountTopBar({ routeName: 'Dashboard' })
    expect(w.text()).toContain('Übersicht')
  })

  it('shows German title for DataPoints route', async () => {
    const w = await mountTopBar({ routeName: 'DataPoints' })
    expect(w.text()).toContain('Objekte')
  })

  it('shows German title for Adapters route', async () => {
    const w = await mountTopBar({ routeName: 'Adapters' })
    expect(w.text()).toContain('Adapter')
  })

  it('shows German title for Settings route', async () => {
    const w = await mountTopBar({ routeName: 'Settings' })
    expect(w.text()).toContain('Einstellungen')
  })

  it('falls back to "open bridge server" for unknown route name', async () => {
    const w = await mountTopBar({ routeName: 'Unknown' })
    expect(w.text()).toContain('open bridge server')
  })
})

describe('TopBar — user menu', () => {
  it('shows the first letter of username in avatar', async () => {
    const w = await mountTopBar({ username: 'yves' })
    expect(w.text()).toContain('Y')
  })

  it('shows version string', async () => {
    const w = await mountTopBar()
    expect(w.text()).toContain('test') // __APP_VERSION__ is "test" in vitest.config.js
  })

  it('menu dropdown is hidden by default', async () => {
    const w = await mountTopBar()
    // Logout button is inside the dropdown
    expect(w.findAll('button').filter(b => b.text().includes('Abmelden')).length).toBe(0)
  })

  it('opens the dropdown menu on button click', async () => {
    const w = await mountTopBar()
    // Click the user menu toggle button (last button in header, not logout)
    await w.findAll('button').at(-1).trigger('click')
    await flushPromises()
    expect(w.findAll('button').find(b => b.text().includes('Abmelden'))).toBeTruthy()
  })

  it('emits toggle-sidebar when mobile hamburger button is clicked', async () => {
    const w = await mountTopBar()
    await w.find('button').trigger('click')
    expect(w.emitted('toggle-sidebar')).toBeTruthy()
  })
})

describe('TopBar — logout', () => {
  it('logout calls ws.disconnect, auth.logout, and redirects to /login', async () => {
    const w = await mountTopBar()
    // Open menu first
    await w.findAll('button').at(-1).trigger('click')
    await flushPromises()
    // Click logout
    const logoutBtn = w.findAll('button').find(b => b.text().includes('Abmelden'))
    await logoutBtn.trigger('click')

    expect(disconnectMock).toHaveBeenCalled()
    expect(logoutMock).toHaveBeenCalled()
    expect(pushMock).toHaveBeenCalledWith('/login')
  })
})

describe('TopBar — click-outside', () => {
  it('closes the menu when clicking outside the menu element', async () => {
    const w = await mountTopBar()
    // Open the menu
    await w.findAll('button').at(-1).trigger('click')
    await flushPromises()

    // Simulate a click outside (on document body)
    document.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()

    expect(w.findAll('button').filter(b => b.text().includes('Abmelden')).length).toBe(0)
  })
})
