import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/components/ui/VisuIcon.vue', () => ({
    default: { template: '<span class="visu-icon" />' },
  }))
})

afterEach(() => {
  vi.doUnmock('vue-router')
  vi.doUnmock('@/stores/websocket')
  vi.doUnmock('@/stores/navLinks')
  vi.doUnmock('@/stores/auth')
  vi.doUnmock('@/stores/adapters')
  vi.doUnmock('@/components/ui/VisuIcon.vue')
})

const ROUTER_LINK_STUB = {
  template: '<a :href="to" v-bind="$attrs"><slot /></a>',
  props: ['to'],
}

async function mountSidebar({
  collapsed       = false,
  routePath       = '/',
  wsConnected     = true,
  isLoggedIn      = true,
  adapterInstances = [],
  navLinks        = [],
} = {}) {
  const fetchAdaptersMock = vi.fn().mockResolvedValue([])
  const navLoadMock       = vi.fn().mockResolvedValue([])

  vi.doMock('vue-router', () => ({
    useRoute: () => ({ path: routePath, name: 'Dashboard' }),
  }))
  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({ connected: wsConnected }),
  }))
  vi.doMock('@/stores/navLinks', () => ({
    useNavLinksStore: () => ({ links: navLinks, load: navLoadMock }),
  }))
  vi.doMock('@/stores/auth', () => ({
    useAuthStore: () => ({ isLoggedIn, username: 'admin' }),
  }))
  vi.doMock('@/stores/adapters', () => ({
    useAdapterStore: () => ({
      instances: adapterInstances,
      fetchAdapters: fetchAdaptersMock,
    }),
  }))

  const pinia = createPinia()
  setActivePinia(pinia)

  const { default: Sidebar } = await import('@/components/layout/Sidebar.vue')
  const wrapper = mount(Sidebar, {
    props: { collapsed },
    global: {
      plugins: [pinia],
      stubs: { RouterLink: ROUTER_LINK_STUB },
    },
  })
  await flushPromises()
  return { wrapper, fetchAdaptersMock, navLoadMock }
}

// ─── Nav links ───────────────────────────────────────────────────────────────

describe('Sidebar — nav links', () => {
  it('renders the home nav link', async () => {
    const { wrapper } = await mountSidebar()
    expect(wrapper.find('[data-testid="nav-home"]').exists()).toBe(true)
  })

  it('renders the datapoints nav link', async () => {
    const { wrapper } = await mountSidebar()
    expect(wrapper.find('[data-testid="nav-datapoints"]').exists()).toBe(true)
  })

  it('renders the adapters nav link', async () => {
    const { wrapper } = await mountSidebar()
    expect(wrapper.find('[data-testid="nav-adapters"]').exists()).toBe(true)
  })

  it('active route "/" is highlighted on home link', async () => {
    const { wrapper } = await mountSidebar({ routePath: '/' })
    const homeLink = wrapper.find('[data-testid="nav-home"]')
    expect(homeLink.classes().join(' ')).toContain('bg-blue-600/20')
  })

  it('non-active links do not have active class', async () => {
    const { wrapper } = await mountSidebar({ routePath: '/' })
    const dpLink = wrapper.find('[data-testid="nav-datapoints"]')
    expect(dpLink.classes().join(' ')).not.toContain('bg-blue-600/20')
  })

  it('nav link /datapoints is highlighted when route starts with /datapoints', async () => {
    const { wrapper } = await mountSidebar({ routePath: '/datapoints' })
    expect(wrapper.find('[data-testid="nav-datapoints"]').classes().join(' ')).toContain('bg-blue-600/20')
  })

  it('home link is NOT highlighted when route is /datapoints (exact match only)', async () => {
    const { wrapper } = await mountSidebar({ routePath: '/datapoints' })
    expect(wrapper.find('[data-testid="nav-home"]').classes().join(' ')).not.toContain('bg-blue-600/20')
  })
})

// ─── Collapsed state ─────────────────────────────────────────────────────────

describe('Sidebar — collapsed state', () => {
  it('shows "open bridge server" label when not collapsed', async () => {
    const { wrapper } = await mountSidebar({ collapsed: false })
    expect(wrapper.text()).toContain('open bridge server')
  })

  it('hides label text when collapsed', async () => {
    const { wrapper } = await mountSidebar({ collapsed: true })
    // The v-if="!collapsed" span hides the label
    expect(wrapper.findAll('span').filter(s => s.text() === 'open bridge server').length).toBe(0)
  })

  it('emits toggle when collapse button is clicked', async () => {
    const { wrapper } = await mountSidebar()
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('toggle')).toBeTruthy()
  })

  it('sidebar has narrow width class when collapsed', async () => {
    const { wrapper } = await mountSidebar({ collapsed: true })
    expect(wrapper.find('aside').classes()).toContain('w-16')
  })

  it('sidebar has wide width class when not collapsed', async () => {
    const { wrapper } = await mountSidebar({ collapsed: false })
    expect(wrapper.find('aside').classes()).toContain('w-56')
  })
})

// ─── WS status ───────────────────────────────────────────────────────────────

describe('Sidebar — WS status', () => {
  it('shows green dot when ws is connected', async () => {
    const { wrapper } = await mountSidebar({ wsConnected: true })
    const dot = wrapper.find('span.rounded-full.w-2')
    expect(dot.classes()).toContain('bg-green-400')
  })

  it('shows red dot when ws is disconnected', async () => {
    const { wrapper } = await mountSidebar({ wsConnected: false })
    const dot = wrapper.find('span.rounded-full.w-2')
    expect(dot.classes()).toContain('bg-red-500')
  })
})

// ─── Adapter warning badge ────────────────────────────────────────────────────

describe('Sidebar — adapter warning badge', () => {
  it('hides warning badge when all adapters are ok', async () => {
    const { wrapper } = await mountSidebar({
      adapterInstances: [{ severity: 'ok' }],
    })
    expect(wrapper.find('[data-testid="nav-adapter-warning-count"]').exists()).toBe(false)
  })

  it('shows warning count when adapters have non-ok severity', async () => {
    const { wrapper } = await mountSidebar({
      adapterInstances: [{ severity: 'warning' }, { severity: 'ok' }],
    })
    const badge = wrapper.find('[data-testid="nav-adapter-warning-count"]')
    expect(badge.exists()).toBe(true)
    expect(badge.text()).toBe('1')
  })

  it('badge uses red styling when any adapter has error severity', async () => {
    const { wrapper } = await mountSidebar({
      adapterInstances: [{ severity: 'error' }],
    })
    const badge = wrapper.find('[data-testid="nav-adapter-warning-count"]')
    expect(badge.classes().join(' ')).toContain('bg-red-500/20')
  })

  it('badge uses amber styling for warnings without errors', async () => {
    const { wrapper } = await mountSidebar({
      adapterInstances: [{ severity: 'warning' }],
    })
    const badge = wrapper.find('[data-testid="nav-adapter-warning-count"]')
    expect(badge.classes().join(' ')).toContain('bg-amber-500/20')
  })
})

// ─── Custom nav links ─────────────────────────────────────────────────────────

describe('Sidebar — custom nav links', () => {
  it('renders custom links from navStore', async () => {
    const { wrapper } = await mountSidebar({
      navLinks: [{ id: 1, url: 'https://example.com', label: 'My Link', icon: '🔗', open_new_tab: true }],
    })
    expect(wrapper.find('[data-testid="nav-custom-link-1"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('My Link')
  })

  it('custom link opens in new tab when open_new_tab=true', async () => {
    const { wrapper } = await mountSidebar({
      navLinks: [{ id: 2, url: 'https://obs.local/docs', label: 'Docs', icon: '📄', open_new_tab: true }],
    })
    expect(wrapper.find('[data-testid="nav-custom-link-2"]').attributes('target')).toBe('_blank')
  })
})

// ─── Mount / unmount behaviour ────────────────────────────────────────────────

describe('Sidebar — mount/unmount', () => {
  it('loads navLinks on mount when logged in and links empty', async () => {
    const { navLoadMock } = await mountSidebar({ isLoggedIn: true, navLinks: [] })
    expect(navLoadMock).toHaveBeenCalled()
  })

  it('does NOT load anything on mount when not logged in', async () => {
    const { navLoadMock, fetchAdaptersMock } = await mountSidebar({ isLoggedIn: false })
    expect(navLoadMock).not.toHaveBeenCalled()
    expect(fetchAdaptersMock).not.toHaveBeenCalled()
  })

  it('starts adapter polling every 30 s on mount', async () => {
    vi.useFakeTimers()
    const { fetchAdaptersMock, wrapper } = await mountSidebar({ isLoggedIn: true })
    const countAfterMount = fetchAdaptersMock.mock.calls.length

    vi.advanceTimersByTime(30000)
    await flushPromises()
    expect(fetchAdaptersMock.mock.calls.length).toBeGreaterThan(countAfterMount)

    wrapper.unmount()
    vi.useRealTimers()
  })

  it('clears adapter polling interval on unmount', async () => {
    vi.useFakeTimers()
    const { fetchAdaptersMock, wrapper } = await mountSidebar({ isLoggedIn: true })
    const countAfterMount = fetchAdaptersMock.mock.calls.length

    wrapper.unmount()
    vi.advanceTimersByTime(30000)
    await flushPromises()
    // No more calls after unmount
    expect(fetchAdaptersMock.mock.calls.length).toBe(countAfterMount)

    vi.useRealTimers()
  })
})
