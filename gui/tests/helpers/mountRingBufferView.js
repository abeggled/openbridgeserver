/**
 * Test helper for mounting RingBufferView.vue in isolation.
 *
 * - Stubs the entire @/api/client module so component code can call
 *   ringbufferApi.* / searchApi.* / hierarchyApi.* without a network.
 * - Stubs the websocket store: returns { connected: ref(false), onRingbufferEntry(fn) }
 *   and captures the live entry handler so tests can simulate WS events.
 * - Stubs the useTz composable so we don't pull in the settings store.
 * - Stubs the UI subcomponents (Badge/Spinner/Modal/RouterLink) as simple
 *   passthroughs to keep mount focused on view logic.
 *
 * The helper deliberately uses dynamic import after vi.doMock() so each test
 * file gets a fresh component instance with its own mocks. This is the
 * pragmatic shape the characterization tests need — see issue #429.
 */
import { vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import { defineComponent, h, ref } from 'vue'

export function makeRingbufferApiMock(overrides = {}) {
  return {
    query: vi.fn().mockResolvedValue({ data: [] }),
    queryV2: vi.fn().mockResolvedValue({ data: [] }),
    exportCsv: vi.fn().mockResolvedValue({ data: new Blob() }),
    stats: vi.fn().mockResolvedValue({
      data: {
        total: 0,
        oldest_ts: null,
        newest_ts: null,
        storage: 'file',
        max_entries: 10000,
        max_file_size_bytes: null,
        max_age: null,
        file_size_bytes: 0,
      },
    }),
    config: vi.fn().mockResolvedValue({ data: {} }),
    listFiltersets: vi.fn().mockResolvedValue({ data: [] }),
    getFilterset: vi.fn().mockResolvedValue({ data: null }),
    createFilterset: vi.fn(),
    updateFilterset: vi.fn(),
    deleteFilterset: vi.fn().mockResolvedValue({ data: {} }),
    cloneFilterset: vi.fn(),
    queryFilterset: vi.fn().mockResolvedValue({ data: [] }),
    patchFiltersetTopbar: vi.fn().mockResolvedValue({ data: {} }),
    patchFiltersetOrder: vi.fn().mockResolvedValue({ data: {} }),
    queryMultiFiltersets: vi.fn().mockResolvedValue({ data: [] }),
    ...overrides,
  }
}

export function makeSearchApiMock(overrides = {}) {
  return {
    search: vi.fn().mockResolvedValue({ data: { items: [] } }),
    ...overrides,
  }
}

export function makeHierarchyApiMock(overrides = {}) {
  return {
    listTrees: vi.fn().mockResolvedValue({ data: [] }),
    getTreeNodes: vi.fn().mockResolvedValue({ data: [] }),
    ...overrides,
  }
}

export async function mountRingBufferView({
  ringbufferApi = makeRingbufferApiMock(),
  searchApi = makeSearchApiMock(),
  hierarchyApi = makeHierarchyApiMock(),
  wsConnected = false,
} = {}) {
  // capture the live entry handler so tests can fire fake WS events
  let liveHandler = null

  vi.doMock('@/api/client', () => ({
    ringbufferApi,
    searchApi,
    hierarchyApi,
  }))

  vi.doMock('@/stores/websocket', () => ({
    useWebSocketStore: () => ({
      connected: wsConnected,
      onRingbufferEntry: (fn) => {
        liveHandler = fn
        return () => {
          liveHandler = null
        }
      },
    }),
  }))

  vi.doMock('@/composables/useTz', () => ({
    useTz: () => ({
      fmtDateTime: (iso) => String(iso ?? ''),
      fmtDate: (iso) => String(iso ?? ''),
    }),
  }))

  const stub = (name) =>
    defineComponent({
      name,
      inheritAttrs: false,
      setup(_, { slots }) {
        return () => h('div', { 'data-stub': name }, slots.default ? slots.default() : [])
      },
    })

  // Stub the small UI children — they are not under test here.
  vi.doMock('@/components/ui/Badge.vue', () => ({ default: stub('Badge') }))
  vi.doMock('@/components/ui/Spinner.vue', () => ({ default: stub('Spinner') }))
  // Modal v-model expects a boolean controlling visibility. Always render slot.
  vi.doMock('@/components/ui/Modal.vue', () => ({
    default: defineComponent({
      name: 'Modal',
      props: ['modelValue', 'title', 'maxWidth'],
      setup(props, { slots }) {
        return () => h('div', { 'data-modal-open': props.modelValue ? 'true' : 'false' }, slots.default ? slots.default() : [])
      },
    }),
  }))

  const pinia = createPinia()
  setActivePinia(pinia)

  const mod = await import('@/views/RingBufferView.vue')
  const RingBufferView = mod.default

  const wrapper = mount(RingBufferView, {
    global: {
      plugins: [pinia],
      stubs: {
        RouterLink: defineComponent({
          name: 'RouterLink',
          props: ['to'],
          setup(_, { slots }) {
            return () => h('a', {}, slots.default ? slots.default() : [])
          },
        }),
        // Stub the topbar components so RingBufferView characterization tests
        // don't see their independent API calls (stats, listFiltersets).
        TopbarStats: { name: 'TopbarStats', template: '<span data-testid="stub-topbar-stats" />' },
        TopbarFilterChips: {
          name: 'TopbarFilterChips',
          props: ['data-testid'],
          emits: ['edit-set', 'new-set', 'changed'],
          // Render the time-filter-slot so RingBufferView.vue's wiring
          // (#438) can be exercised in characterization tests without
          // mounting the real TopbarFilterChips.
          template: '<div data-testid="stub-topbar-chips"><slot name="time-filter-slot" /></div>',
        },
        // Stub the filter editor — it lazy-loads comboboxes which pull the
        // datapoints Pinia store. Tests that target RingBufferView itself
        // do not exercise the editor.
        FilterEditor: {
          name: 'FilterEditor',
          props: ['modelValue', 'setId'],
          emits: ['update:modelValue', 'saved'],
          template: '<div data-testid="stub-filter-editor" />',
        },
      },
    },
  })

  await wrapper.vm.$nextTick()
  await flushPromises()
  await wrapper.vm.$nextTick()

  return {
    wrapper,
    ringbufferApi,
    searchApi,
    hierarchyApi,
    emitLive: (entry) => liveHandler?.(entry),
    hasLiveHandler: () => Boolean(liveHandler),
  }
}

export async function flushPromises() {
  // Flush microtasks across multiple ticks. RingBufferView.vue chains several
  // awaits in onMounted (Promise.all then a nextTick); two macrotasks cover it.
  for (let i = 0; i < 4; i++) {
    await new Promise((resolve) => setTimeout(resolve, 0))
  }
}

export function makeSampleFilterset(overrides = {}) {
  return {
    id: 'fs-1',
    name: 'Sample Set',
    description: 'desc',
    dsl_version: 2,
    is_active: true,
    query: {
      filters: {},
      sort: { field: 'ts', order: 'desc' },
      pagination: { limit: 500, offset: 0 },
    },
    created_at: '2025-01-01T00:00:00Z',
    updated_at: '2025-01-01T00:00:00Z',
    groups: [
      {
        id: 'g-1',
        name: 'Gruppe 1',
        is_active: true,
        group_order: 0,
        created_at: '2025-01-01T00:00:00Z',
        updated_at: '2025-01-01T00:00:00Z',
        rules: [
          {
            id: 'r-1',
            name: 'Regel 1',
            is_active: true,
            rule_order: 0,
            query: {
              filters: { adapters: { any_of: ['api'] } },
              sort: { field: 'ts', order: 'desc' },
              pagination: { limit: 500, offset: 0 },
            },
            created_at: '2025-01-01T00:00:00Z',
            updated_at: '2025-01-01T00:00:00Z',
          },
        ],
      },
    ],
    ...overrides,
  }
}
