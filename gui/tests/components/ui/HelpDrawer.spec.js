import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { reactive } from 'vue'

let helpStoreMock

vi.mock('@/stores/help', () => ({ useHelpStore: () => helpStoreMock }))

beforeEach(() => {
  helpStoreMock = reactive({
    isOpen: false, currentUrl: null, drawerWidth: 0, loadError: false,
    close: vi.fn(), setDrawerWidth: vi.fn((w) => { helpStoreMock.drawerWidth = w }),
  })
  document.body.innerHTML = ''
})

afterEach(() => {
  document.body.innerHTML = ''
})

describe('HelpDrawer — closed', () => {
  it('renders nothing when the store is closed', async () => {
    const { default: HelpDrawer } = await import('@/components/ui/HelpDrawer.vue')
    mount(HelpDrawer, { attachTo: document.body })
    expect(document.querySelector('[data-testid="help-drawer-panel"]')).toBeFalsy()
  })
})

describe('HelpDrawer — open with a resolved URL', () => {
  async function mountOpen(currentUrl = '/help/datapoints/overview.html#datapoints-overview') {
    helpStoreMock.isOpen = true
    helpStoreMock.currentUrl = currentUrl
    const { default: HelpDrawer } = await import('@/components/ui/HelpDrawer.vue')
    const w = mount(HelpDrawer, { attachTo: document.body })
    await flushPromises()
    return w
  }

  it('renders the panel and an iframe pointing at currentUrl', async () => {
    await mountOpen('/help/datapoints/overview.html#datapoints-overview')
    const panel = document.querySelector('[data-testid="help-drawer-panel"]')
    expect(panel).toBeTruthy()
    const iframe = document.querySelector('[data-testid="help-drawer-iframe"]')
    expect(iframe).toBeTruthy()
    expect(iframe.getAttribute('src')).toBe(
      '/help/datapoints/overview.html?appearance=light#datapoints-overview'
    )
  })

  it("carries the Admin-GUI's current dark mode into the iframe src so VitePress doesn't fall back to its own detection", async () => {
    const { useSettingsStore } = await import('@/stores/settings')
    useSettingsStore().setTheme('dark')
    await mountOpen('/help/datapoints/overview.html#datapoints-overview')
    const iframe = document.querySelector('[data-testid="help-drawer-iframe"]')
    expect(iframe.getAttribute('src')).toBe(
      '/help/datapoints/overview.html?appearance=dark#datapoints-overview'
    )
    document.documentElement.classList.remove('dark')
  })

  it('updates the iframe when the OS-level color scheme changes while theme is "system" (issue feedback: applyTheme() runs without setTheme() ever touching settings.theme)', async () => {
    const { useSettingsStore } = await import('@/stores/settings')
    const settings = useSettingsStore()
    settings.setTheme('system')
    await mountOpen('/help/datapoints/overview.html#datapoints-overview')
    const iframe = document.querySelector('[data-testid="help-drawer-iframe"]')
    expect(iframe.getAttribute('src')).toContain('appearance=light')

    // Mirrors App.vue's prefers-color-scheme listener: it calls applyTheme()
    // directly without ever assigning settings.theme.value.
    vi.spyOn(window, 'matchMedia').mockReturnValue({ matches: true })
    settings.applyTheme()
    await flushPromises()

    expect(iframe.getAttribute('src')).toContain('appearance=dark')
    document.documentElement.classList.remove('dark')
    vi.restoreAllMocks()
  })

  it("updates the iframe's appearance when the theme changes while the drawer stays open (issue feedback: reactive dependency, not just a one-time DOM read at mount)", async () => {
    const { useSettingsStore } = await import('@/stores/settings')
    const settings = useSettingsStore()
    await mountOpen('/help/datapoints/overview.html#datapoints-overview')
    const iframe = document.querySelector('[data-testid="help-drawer-iframe"]')
    expect(iframe.getAttribute('src')).toContain('appearance=light')

    settings.setTheme('dark')
    await flushPromises()

    expect(iframe.getAttribute('src')).toContain('appearance=dark')
    document.documentElement.classList.remove('dark')
  })

  it('appends appearance with & when the URL already carries a query string', async () => {
    await mountOpen('/help/datapoints/overview.html?foo=bar#datapoints-overview')
    const iframe = document.querySelector('[data-testid="help-drawer-iframe"]')
    expect(iframe.getAttribute('src')).toBe(
      '/help/datapoints/overview.html?foo=bar&appearance=light#datapoints-overview'
    )
  })

  it('handles a currentUrl with no #hash fragment', async () => {
    await mountOpen('/help/settings/')
    const iframe = document.querySelector('[data-testid="help-drawer-iframe"]')
    expect(iframe.getAttribute('src')).toBe('/help/settings/?appearance=light')
  })

  it('does not block the rest of the page — no full-viewport overlay element (regression, feedback after first release)', async () => {
    // A prior version wrapped the panel in a `fixed inset-0` backdrop div that
    // swallowed clicks everywhere, defeating the point of a slide-in drawer
    // ("keep working while help is open"). The panel itself must only cover
    // its own width, anchored to the right edge, not the whole viewport.
    await mountOpen()
    expect(document.querySelector('.fixed.inset-0')).toBeFalsy()
    const panel = document.querySelector('[data-testid="help-drawer-panel"]')
    expect(panel.className).toContain('fixed')
    expect(panel.className).toContain('right-0')
    expect(panel.className).not.toContain('inset-0')
  })

  it('leaves elements outside the drawer clickable while open', async () => {
    const outside = document.createElement('button')
    outside.setAttribute('data-testid', 'page-behind-drawer')
    const onClick = vi.fn()
    outside.addEventListener('click', onClick)
    document.body.appendChild(outside)

    await mountOpen()
    outside.dispatchEvent(new MouseEvent('click', { bubbles: true }))

    expect(onClick).toHaveBeenCalledTimes(1)
  })

  it('clamps the initial width to its configured minimum on a narrow viewport with no persisted width (issue feedback: defaultWidth = 40% of viewport could go below the 320px minimum)', async () => {
    const originalWidth = window.innerWidth
    Object.defineProperty(window, 'innerWidth', { configurable: true, value: 390 })
    localStorage.removeItem('obs-help-drawer-width')

    await mountOpen()
    const panel = document.querySelector('[data-testid="help-drawer-panel"]')
    expect(parseInt(panel.style.width, 10)).toBeGreaterThanOrEqual(320)

    Object.defineProperty(window, 'innerWidth', { configurable: true, value: originalWidth })
  })

  it('renders the resize handle', async () => {
    await mountOpen()
    expect(document.querySelector('[data-testid="help-drawer-resize-handle"]')).toBeTruthy()
  })

  it('resizes the panel by dragging the handle (delegates to useResizablePanel)', async () => {
    await mountOpen()
    const panel = document.querySelector('[data-testid="help-drawer-panel"]')
    const startWidth = parseInt(panel.style.width, 10)
    const handle = document.querySelector('[data-testid="help-drawer-resize-handle"]')

    handle.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, clientX: 500 }))
    document.dispatchEvent(new PointerEvent('pointermove', { bubbles: true, clientX: 300 }))
    document.dispatchEvent(new PointerEvent('pointerup', { bubbles: true }))
    await flushPromises()

    // Dragging the left-edge handle leftwards widens a right-anchored panel.
    expect(parseInt(panel.style.width, 10)).toBeGreaterThan(startWidth)
  })

  it('mirrors its width into the store so the main layout can reserve space for it', async () => {
    // The layout can't float the drawer on top of page content (issue
    // feedback: fields near the right edge disappeared behind it) — it needs
    // to know the current drawer width to reserve that much space instead.
    await mountOpen()
    expect(helpStoreMock.setDrawerWidth).toHaveBeenCalled()
    const lastCallWidth = helpStoreMock.setDrawerWidth.mock.calls.at(-1)[0]
    expect(helpStoreMock.drawerWidth).toBe(lastCallWidth)
    expect(lastCallWidth).toBeGreaterThan(0)
  })

  it('calls store.close() when the close button is clicked', async () => {
    await mountOpen()
    document.querySelector('[data-testid="help-drawer-close"]').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()
    expect(helpStoreMock.close).toHaveBeenCalledTimes(1)
  })

  it('opens currentUrl in a new tab and closes the drawer (narrow-monitor escape hatch)', async () => {
    const openSpy = vi.spyOn(window, 'open').mockImplementation(() => {})
    await mountOpen('/help/datapoints/overview.html#datapoints-overview')

    document.querySelector('[data-testid="help-drawer-open-new-tab"]').dispatchEvent(new MouseEvent('click', { bubbles: true }))
    await flushPromises()

    expect(openSpy).toHaveBeenCalledWith(
      '/help/datapoints/overview.html?appearance=light#datapoints-overview', '_blank', 'noopener,noreferrer'
    )
    expect(helpStoreMock.close).toHaveBeenCalledTimes(1)
    openSpy.mockRestore()
  })

  it('calls store.close() on Escape', async () => {
    await mountOpen()
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(helpStoreMock.close).toHaveBeenCalledTimes(1)
  })

  it('does not close on Escape when already closed', async () => {
    helpStoreMock.isOpen = false
    const { default: HelpDrawer } = await import('@/components/ui/HelpDrawer.vue')
    mount(HelpDrawer, { attachTo: document.body })
    document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }))
    await flushPromises()
    expect(helpStoreMock.close).not.toHaveBeenCalled()
  })
})

describe('HelpDrawer — open with no resolvable URL', () => {
  it('shows the unavailable message instead of an iframe', async () => {
    helpStoreMock.isOpen = true
    helpStoreMock.currentUrl = null
    const { default: HelpDrawer } = await import('@/components/ui/HelpDrawer.vue')
    mount(HelpDrawer, { attachTo: document.body })
    await flushPromises()

    expect(document.querySelector('[data-testid="help-drawer-iframe"]')).toBeFalsy()
    const body = document.querySelector('.card-body')
    expect(body).toBeTruthy()
    expect(body.textContent.length).toBeGreaterThan(0)
  })

  it('does not render the open-in-new-tab button — nothing to open', async () => {
    helpStoreMock.isOpen = true
    helpStoreMock.currentUrl = null
    const { default: HelpDrawer } = await import('@/components/ui/HelpDrawer.vue')
    mount(HelpDrawer, { attachTo: document.body })
    await flushPromises()

    expect(document.querySelector('[data-testid="help-drawer-open-new-tab"]')).toBeFalsy()
  })

  it('shows a distinct message when the help index itself failed to load, instead of the per-topic "not available" text (issue feedback: both cases looked identical, masking a missing/unbuilt help_dist as "no content for this area")', async () => {
    helpStoreMock.isOpen = true
    helpStoreMock.currentUrl = null
    helpStoreMock.loadError = true
    const { default: HelpDrawer } = await import('@/components/ui/HelpDrawer.vue')
    mount(HelpDrawer, { attachTo: document.body })
    await flushPromises()

    const body = document.querySelector('.card-body')
    expect(body.textContent).toContain('Hilfe-Inhalte sind auf diesem Server aktuell nicht verfügbar')
  })

  it('shows the per-topic message (not the system-unavailable one) when the index loaded fine but this help_id just has no mapping', async () => {
    helpStoreMock.isOpen = true
    helpStoreMock.currentUrl = null
    helpStoreMock.loadError = false
    const { default: HelpDrawer } = await import('@/components/ui/HelpDrawer.vue')
    mount(HelpDrawer, { attachTo: document.body })
    await flushPromises()

    const body = document.querySelector('.card-body')
    expect(body.textContent).toContain('Für diesen Bereich ist noch keine Hilfe verfügbar')
  })
})
