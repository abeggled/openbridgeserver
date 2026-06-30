import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi:       { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:   { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
    authApi:     { login: vi.fn(), me: vi.fn() },
  }))
})

afterEach(() => { vi.doUnmock('@/api/client') })

async function mountPanel(type, data) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'n1', type, data },
      nodeTypes: [{ type, label: type, description: '' }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

// ─── string_concat ─────────────────────────────────────────────────────────────

describe('NodeConfigPanel string_concat — count and separator', () => {
  it('renders the correct number of text slot inputs for default count=2', async () => {
    const w = await mountPanel('string_concat', { count: 2 })
    await flushPromises()

    expect(w.find('[data-testid="concat-text-1"]').exists()).toBe(true)
    expect(w.find('[data-testid="concat-text-2"]').exists()).toBe(true)
    expect(w.find('[data-testid="concat-text-3"]').exists()).toBe(false)
    w.unmount()
  })

  it('changing the count input to 4 renders 4 text slot inputs', async () => {
    const w = await mountPanel('string_concat', { count: 2 })
    await flushPromises()

    const countInput = w.find('[data-testid="concat-count"]')
    await countInput.setValue('4')
    await countInput.trigger('change')
    await flushPromises()

    expect(w.find('[data-testid="concat-text-4"]').exists()).toBe(true)
    expect(w.find('[data-testid="concat-text-5"]').exists()).toBe(false)
    w.unmount()
  })

  it('changing the count emits update with new count', async () => {
    const w = await mountPanel('string_concat', { count: 2 })
    await flushPromises()

    const countInput = w.find('[data-testid="concat-count"]')
    await countInput.setValue('3')
    await countInput.trigger('change')
    await flushPromises()

    const updates = w.emitted('update')
    expect(updates).toBeTruthy()
    expect(updates.at(-1)[0].count).toBe(3)
    w.unmount()
  })

  it('count is clamped to 2 minimum', async () => {
    const w = await mountPanel('string_concat', { count: 2 })
    await flushPromises()

    const countInput = w.find('[data-testid="concat-count"]')
    await countInput.setValue('0')
    await countInput.trigger('change')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0].count).toBe(2)
    w.unmount()
  })

  it('count is clamped to 20 maximum', async () => {
    const w = await mountPanel('string_concat', { count: 2 })
    await flushPromises()

    const countInput = w.find('[data-testid="concat-count"]')
    await countInput.setValue('99')
    await countInput.trigger('change')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0].count).toBe(20)
    w.unmount()
  })

  it('changing separator emits update', async () => {
    const w = await mountPanel('string_concat', { count: 2, separator: '' })
    await flushPromises()

    const sepInput = w.find('[data-testid="concat-separator"]')
    await sepInput.setValue(', ')
    await sepInput.trigger('change')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0].separator).toBe(', ')
    w.unmount()
  })

  it('typing in a static text slot emits update', async () => {
    const w = await mountPanel('string_concat', { count: 2 })
    await flushPromises()

    const slot1 = w.find('[data-testid="concat-text-1"]')
    await slot1.setValue('Hallo ')
    await slot1.trigger('change')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0].text_1).toBe('Hallo ')
    w.unmount()
  })
})

// ─── substring_extractor — mode branches ─────────────────────────────────────

describe('NodeConfigPanel substring_extractor — rechts_von', () => {
  it('extracts text to the right of the search delimiter', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'rechts_von', search: '/', occurrence: 'first',
    })
    await flushPromises()

    const ta = w.find('[data-testid="substr-test-input"]')
    await ta.setValue('path/value')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').text()).toContain('value')
    w.unmount()
  })

  it('returns the last occurrence when occurrence=last', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'rechts_von', search: '/', occurrence: 'last',
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('a/b/c')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').text()).toContain('c')
    w.unmount()
  })

  it('shows no-match message when delimiter not found', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'rechts_von', search: '|',
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('no pipe here')
    await flushPromises()

    expect(w.text()).toContain('kein Treffer')
    expect(w.find('[data-testid="substr-test-result"]').exists()).toBe(false)
    w.unmount()
  })
})

describe('NodeConfigPanel substring_extractor — links_von', () => {
  it('extracts text to the left of the search delimiter', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'links_von', search: '/', occurrence: 'first',
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('left/right')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').text()).toContain('left')
    w.unmount()
  })
})

describe('NodeConfigPanel substring_extractor — zwischen', () => {
  it('extracts text between start and end marker', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'zwischen', start_marker: '[', end_marker: ']',
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('prefix [content] suffix')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').text()).toContain('content')
    w.unmount()
  })

  it('shows no-match when end marker is missing', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'zwischen', start_marker: '[', end_marker: ']',
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('no brackets')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').exists()).toBe(false)
    w.unmount()
  })

  it('returns null when start_marker or end_marker is empty', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'zwischen', start_marker: '', end_marker: '',
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('some text')
    await flushPromises()

    // Result should be null → no result element shown
    expect(w.find('[data-testid="substr-test-result"]').exists()).toBe(false)
    w.unmount()
  })
})

describe('NodeConfigPanel substring_extractor — ausschneiden', () => {
  it('extracts a substring by position and length', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'ausschneiden', start: 6, length: 5,
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('Hallo Welt!')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').text()).toContain('Welt!')
    w.unmount()
  })

  it('extracts to end of string when length=-1', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'ausschneiden', start: 3, length: -1,
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('abc_rest')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').text()).toContain('_rest')
    w.unmount()
  })
})

describe('NodeConfigPanel substring_extractor — regex', () => {
  it('extracts group 0 (full match) from regex pattern', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'regex', pattern: '\\d+', flags: '', group: 0,
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('value 42 end')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').text()).toContain('42')
    w.unmount()
  })

  it('extracts a capture group when group=1', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'regex', pattern: 'temp=(\\d+)', flags: 'i', group: 1,
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('sensor Temp=23 outside')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').text()).toContain('23')
    w.unmount()
  })

  it('returns null (no-match) when regex does not match', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'regex', pattern: '^[0-9]+$', flags: '', group: 0,
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('not numbers')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').exists()).toBe(false)
    w.unmount()
  })

  it('returns null when pattern is empty', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'regex', pattern: '', flags: '', group: 0,
    })
    await flushPromises()

    await w.find('[data-testid="substr-test-input"]').setValue('some text')
    await flushPromises()

    expect(w.find('[data-testid="substr-test-result"]').exists()).toBe(false)
    w.unmount()
  })

  it('shows no result when test input is empty', async () => {
    const w = await mountPanel('substring_extractor', {
      mode: 'regex', pattern: '\\d+', flags: '', group: 0,
    })
    await flushPromises()

    // substrTestInput is '' by default and no extractorPreview → result is null
    expect(w.find('[data-testid="substr-test-result"]').exists()).toBe(false)
    w.unmount()
  })
})
