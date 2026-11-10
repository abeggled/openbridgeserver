import { describe, it, expect, vi, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import SuggestInput from '@/components/ui/SuggestInput.vue'

const ITEMS = [
  { value: 'lock.shed', label: 'Shed', detail: 'locked' },
  { value: 'lock.door', label: null, detail: null },
]

function mk(props = {}) {
  const fetchSuggestions = props.fetchSuggestions ?? vi.fn().mockResolvedValue(ITEMS)
  const w = mount(SuggestInput, {
    props: { modelValue: '', fetchSuggestions, testid: 's', debounceMs: 0, ...props },
    attachTo: document.body,
  })
  return { w, fetchSuggestions }
}

afterEach(() => { document.body.innerHTML = '' })

describe('SuggestInput', () => {
  it('fetches on focus and selects with the mouse', async () => {
    const { w, fetchSuggestions } = mk({ modelValue: 'lo' })
    await w.find('[data-testid="s"]').trigger('focus')
    await flushPromises()
    expect(fetchSuggestions).toHaveBeenCalledWith('lo')
    expect(w.find('[data-testid="s-option-0"]').text()).toContain('Shed')
    expect(w.find('[data-testid="s-option-0"]').text()).toContain('locked')
    await w.find('[data-testid="s-option-1"]').trigger('mouseenter')
    await w.find('[data-testid="s-option-1"]').trigger('mousedown')
    expect(w.emitted('update:modelValue').at(-1)).toEqual(['lock.door'])
    expect(w.emitted('select')[0][0]).toEqual(ITEMS[1])
    expect(w.find('[data-testid="s-dropdown"]').exists()).toBe(false)
  })

  it('keeps free text and debounces fetching while typing', async () => {
    vi.useFakeTimers()
    try {
      const { w, fetchSuggestions } = mk({ debounceMs: 100 })
      await w.find('[data-testid="s"]').setValue('cus')
      expect(w.emitted('update:modelValue').at(-1)).toEqual(['cus'])
      expect(fetchSuggestions).not.toHaveBeenCalled()
      vi.advanceTimersByTime(100)
      expect(fetchSuggestions).toHaveBeenCalledWith('cus')
      w.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('drops a pending search when the dropdown closes', async () => {
    vi.useFakeTimers()
    try {
      const { w, fetchSuggestions } = mk({ debounceMs: 100 })
      await w.find('[data-testid="s"]').setValue('lo')
      await w.find('[data-testid="s"]').trigger('keydown', { key: 'Escape' })
      vi.advanceTimersByTime(200)
      expect(fetchSuggestions).not.toHaveBeenCalled()
      w.unmount()
    } finally {
      vi.useRealTimers()
    }
  })

  it('navigates with the keyboard', async () => {
    const { w } = mk()
    const input = w.find('[data-testid="s"]')
    await input.trigger('keydown', { key: 'ArrowDown' }) // opens
    await flushPromises()
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'ArrowDown' }) // clamps at the end
    await input.trigger('keydown', { key: 'ArrowUp' })
    await input.trigger('keydown', { key: 'Enter' })
    expect(w.emitted('update:modelValue').at(-1)).toEqual(['lock.shed'])
    await input.trigger('keydown', { key: 'Enter' }) // closed: nothing selected
    expect(w.emitted('update:modelValue')).toHaveLength(1)
    await input.trigger('focus')
    await flushPromises()
    await input.trigger('keydown', { key: 'Escape' })
    expect(w.find('[data-testid="s-dropdown"]').exists()).toBe(false)
  })

  it('shows an empty hint, survives errors and ignores keys without suggestions', async () => {
    const { w } = mk({ fetchSuggestions: vi.fn().mockRejectedValue(new Error('offline')), emptyText: 'Nichts gefunden' })
    const input = w.find('[data-testid="s"]')
    await input.trigger('focus')
    await flushPromises()
    expect(w.find('[data-testid="s-empty"]').text()).toBe('Nichts gefunden')
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'ArrowUp' })
    const other = mk({ fetchSuggestions: vi.fn().mockResolvedValue(null), emptyText: 'Nichts gefunden' })
    await other.w.find('[data-testid="s"]').trigger('focus')
    await flushPromises()
    expect(other.w.find('[data-testid="s-empty"]').exists()).toBe(true)
  })

  it('stays closed on an empty result without an empty text', async () => {
    const { w } = mk({ fetchSuggestions: vi.fn().mockResolvedValue([]) })
    await w.find('[data-testid="s"]').trigger('focus')
    await flushPromises()
    expect(w.find('[data-testid="s-dropdown"]').exists()).toBe(false)
  })

  it('drops stale responses and shows a loading state', async () => {
    let resolveFirst
    let rejectSecond
    const fetchSuggestions = vi.fn()
      .mockImplementationOnce(() => new Promise(r => { resolveFirst = r }))
      .mockImplementationOnce(() => new Promise((_, reject) => { rejectSecond = reject }))
      .mockResolvedValueOnce([ITEMS[1]])
    const { w } = mk({ fetchSuggestions })
    const input = w.find('[data-testid="s"]')
    await input.trigger('focus')
    expect(w.find('[data-testid="s-dropdown"]').text()).toBeTruthy()
    await input.trigger('focus') // rejected but superseded below
    await input.trigger('focus')
    await flushPromises()
    resolveFirst(ITEMS)
    rejectSecond(new Error('late'))
    await flushPromises()
    expect(w.findAll('li')).toHaveLength(1)
  })

  it('closes when focus leaves the field (Tab)', async () => {
    const { w } = mk()
    await w.find('[data-testid="s"]').trigger('focus')
    await flushPromises()
    expect(w.find('[data-testid="s-dropdown"]').exists()).toBe(true)
    await w.find('[data-testid="s"]').trigger('blur')
    expect(w.find('[data-testid="s-dropdown"]').exists()).toBe(false)
  })

  it('closes on outside clicks only', async () => {
    const { w } = mk()
    await w.find('[data-testid="s"]').trigger('focus')
    await flushPromises()
    w.find('[data-testid="s"]').element.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()
    expect(w.find('[data-testid="s-dropdown"]').exists()).toBe(true)
    document.body.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }))
    await flushPromises()
    expect(w.find('[data-testid="s-dropdown"]').exists()).toBe(false)
  })
})
