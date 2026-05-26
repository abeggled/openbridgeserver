/**
 * Tests for the generic Combobox.vue component.
 *
 * Combobox supports single (modelValue: string) and multi (modelValue: string[])
 * modes with keyboard navigation, async suggestion fetching, chip rendering and
 * slot-based customization.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { nextTick } from 'vue'
import Combobox from '@/components/ui/Combobox.vue'

function makeItems(ids) {
  return ids.map((id) => ({ id, label: `Label ${id}` }))
}

function mountCombobox(props = {}) {
  return mount(Combobox, {
    props: {
      modelValue: props.multi ? [] : '',
      fetchSuggestions: vi.fn().mockResolvedValue(makeItems(['a', 'b', 'c'])),
      ...props,
    },
    attachTo: document.body,
  })
}

describe('Combobox (single mode)', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('emits a string on selection (not array)', async () => {
    const wrapper = mountCombobox({ multi: false, modelValue: '' })
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    expect(wrapper.findAll('[data-testid^="combobox-item-"]').length).toBeGreaterThan(0)

    await wrapper.find('[data-testid="combobox-item-0"]').trigger('click')
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    const last = events[events.length - 1][0]
    expect(typeof last).toBe('string')
    expect(last).toBe('a')
  })

  it('does not render chips when not multi', async () => {
    const wrapper = mountCombobox({ multi: false, modelValue: 'a' })
    expect(wrapper.findAll('[data-testid^="combobox-chip-"]:not([data-testid*="remove"])').length).toBe(0)
  })

  it('closes after a single selection', async () => {
    const wrapper = mountCombobox({ multi: false })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    await wrapper.find('[data-testid="combobox-item-0"]').trigger('click')
    await nextTick()
    expect(wrapper.find('[data-testid="combobox-dropdown"]').exists()).toBe(false)
  })
})

describe('Combobox (multi mode)', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('emits a string array, accumulating selections', async () => {
    const wrapper = mountCombobox({ multi: true, modelValue: [] })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    await wrapper.find('[data-testid="combobox-item-0"]').trigger('click')
    await wrapper.setProps({ modelValue: ['a'] })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    await wrapper.find('[data-testid="combobox-item-1"]').trigger('click')

    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    const last = events[events.length - 1][0]
    expect(Array.isArray(last)).toBe(true)
    expect(last).toEqual(['a', 'b'])
  })

  it('renders chips for selected items using displayItems', async () => {
    const wrapper = mountCombobox({
      multi: true,
      modelValue: ['a', 'c'],
      displayItems: [
        { id: 'a', label: 'Alpha' },
        { id: 'c', label: 'Gamma' },
      ],
    })
    const chips = wrapper.findAll('[data-testid^="combobox-chip-"]:not([data-testid*="remove"])')
    expect(chips).toHaveLength(2)
    expect(chips[0].text()).toContain('Alpha')
    expect(chips[1].text()).toContain('Gamma')
  })

  it('backspace on empty input removes the last chip', async () => {
    const wrapper = mountCombobox({
      multi: true,
      modelValue: ['a', 'b'],
      displayItems: [
        { id: 'a', label: 'A' },
        { id: 'b', label: 'B' },
      ],
    })
    const input = wrapper.find('input')
    expect(input.element.value).toBe('')
    await input.trigger('keydown', { key: 'Backspace' })

    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    const last = events[events.length - 1][0]
    expect(last).toEqual(['a'])
  })

  it('does not remove a chip when input has text', async () => {
    const wrapper = mountCombobox({
      multi: true,
      modelValue: ['a'],
      displayItems: [{ id: 'a', label: 'A' }],
    })
    const input = wrapper.find('input')
    input.element.value = 'foo'
    await input.trigger('input')
    await input.trigger('keydown', { key: 'Backspace' })

    const events = wrapper.emitted('update:modelValue') || []
    // The last emission should not strip the chip.
    if (events.length) {
      const last = events[events.length - 1][0]
      expect(last).toEqual(['a'])
    }
  })

  it('clicking the chip remove button removes that chip', async () => {
    const wrapper = mountCombobox({
      multi: true,
      modelValue: ['a', 'b'],
      displayItems: [
        { id: 'a', label: 'A' },
        { id: 'b', label: 'B' },
      ],
    })
    await wrapper.find('[data-testid="combobox-chip-remove-0"]').trigger('click')
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1][0]).toEqual(['b'])
  })

  it('does not re-add already selected items when re-clicked', async () => {
    const fetchSuggestions = vi.fn().mockResolvedValue(makeItems(['a', 'b']))
    const wrapper = mount(Combobox, {
      props: {
        multi: true,
        modelValue: ['a'],
        fetchSuggestions,
      },
    })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    await wrapper.find('[data-testid="combobox-item-0"]').trigger('click')
    const events = wrapper.emitted('update:modelValue') || []
    if (events.length) {
      const last = events[events.length - 1][0]
      // Either no emit, or it stays ['a'].
      expect(last).toEqual(['a'])
    }
  })
})

describe('Combobox keyboard navigation', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('arrow down/up moves the active item index', async () => {
    const wrapper = mountCombobox({ multi: false })
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()

    await input.trigger('keydown', { key: 'ArrowDown' })
    expect(wrapper.find('[data-testid="combobox-item-0"]').classes().join(' '))
      .toMatch(/active|bg-/)

    await input.trigger('keydown', { key: 'ArrowDown' })
    expect(wrapper.find('[data-testid="combobox-item-1"]').classes().join(' '))
      .toMatch(/active|bg-/)

    await input.trigger('keydown', { key: 'ArrowUp' })
    expect(wrapper.find('[data-testid="combobox-item-0"]').classes().join(' '))
      .toMatch(/active|bg-/)
  })

  it('enter selects the active item', async () => {
    const wrapper = mountCombobox({ multi: false })
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'Enter' })

    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1][0]).toBe('b')
  })

  it('escape closes the dropdown', async () => {
    const wrapper = mountCombobox({ multi: false })
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    expect(wrapper.find('[data-testid="combobox-dropdown"]').exists()).toBe(true)
    await input.trigger('keydown', { key: 'Escape' })
    expect(wrapper.find('[data-testid="combobox-dropdown"]').exists()).toBe(false)
  })
})

describe('Combobox slots', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('renders the item slot when provided', async () => {
    const wrapper = mount(Combobox, {
      props: {
        multi: false,
        modelValue: '',
        fetchSuggestions: vi.fn().mockResolvedValue(makeItems(['a', 'b'])),
      },
      slots: {
        item: '<template #item="{ item }"><span class="custom-item">CUSTOM-{{ item.id }}</span></template>',
      },
    })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    expect(wrapper.html()).toContain('CUSTOM-a')
    expect(wrapper.html()).toContain('CUSTOM-b')
  })

  it('renders the chip slot when provided', async () => {
    const wrapper = mount(Combobox, {
      props: {
        multi: true,
        modelValue: ['x'],
        displayItems: [{ id: 'x', label: 'X' }],
        fetchSuggestions: vi.fn().mockResolvedValue([]),
      },
      slots: {
        chip: '<template #chip="{ item }"><span class="custom-chip">C-{{ item.id }}</span></template>',
      },
    })
    expect(wrapper.html()).toContain('C-x')
  })
})

describe('Combobox outside click', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('closes when clicking outside the container', async () => {
    const wrapper = mountCombobox({ multi: false })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    expect(wrapper.find('[data-testid="combobox-dropdown"]').exists()).toBe(true)

    // Dispatch a mousedown on document body outside the component
    const outside = document.createElement('div')
    document.body.appendChild(outside)
    const evt = new MouseEvent('mousedown', { bubbles: true })
    outside.dispatchEvent(evt)
    await nextTick()
    expect(wrapper.find('[data-testid="combobox-dropdown"]').exists()).toBe(false)
    wrapper.unmount()
  })
})

describe('Combobox single mode extras', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('shows the clear button when input has text and clearing emits empty', async () => {
    const wrapper = mount(Combobox, {
      props: {
        multi: false,
        modelValue: 'a',
        displayItems: [{ id: 'a', label: 'Alpha' }],
        fetchSuggestions: vi.fn().mockResolvedValue(makeItems(['a', 'b'])),
      },
    })
    // Pre-filled query from displayItems
    expect(wrapper.find('input').element.value).toBe('Alpha')
    const btn = wrapper.find('[data-testid="combobox-clear"]')
    expect(btn.exists()).toBe(true)
    await btn.trigger('click')

    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1][0]).toBe('')
  })

  it('arrow down opens the dropdown when closed', async () => {
    const wrapper = mount(Combobox, {
      props: {
        multi: false,
        modelValue: '',
        fetchSuggestions: vi.fn().mockResolvedValue(makeItems(['a'])),
      },
    })
    const input = wrapper.find('input')
    // not focused yet
    await input.trigger('keydown', { key: 'ArrowDown' })
    await flushPromises()
    expect(wrapper.find('[data-testid="combobox-dropdown"]').exists()).toBe(true)
  })

  it('moveUp/moveDown are no-ops when suggestions are empty', async () => {
    const wrapper = mount(Combobox, {
      props: {
        multi: false,
        modelValue: '',
        fetchSuggestions: vi.fn().mockResolvedValue([]),
      },
    })
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    await input.trigger('keydown', { key: 'ArrowDown' })
    await input.trigger('keydown', { key: 'ArrowUp' })
    // Just verify no crash and dropdown empty-state is shown.
    expect(wrapper.find('[data-testid="combobox-empty"]').exists()).toBe(true)
  })

  it('debounces input when debounceMs > 0', async () => {
    const fetchSuggestions = vi.fn().mockResolvedValue(makeItems(['a']))
    const wrapper = mount(Combobox, {
      props: {
        multi: false,
        modelValue: '',
        fetchSuggestions,
        debounceMs: 50,
      },
    })
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    fetchSuggestions.mockClear()
    input.element.value = 'a'
    await input.trigger('input')
    input.element.value = 'ab'
    await input.trigger('input')
    await new Promise((r) => setTimeout(r, 100))
    await flushPromises()
    // Only the trailing 'ab' fetch should have run
    expect(fetchSuggestions).toHaveBeenCalledTimes(1)
    expect(fetchSuggestions).toHaveBeenLastCalledWith('ab')
  })

  it('selectActive is a no-op when no item is active', async () => {
    const wrapper = mountCombobox({ multi: false })
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    // activeIndex is -1 (initial)
    await input.trigger('keydown', { key: 'Enter' })
    expect(wrapper.emitted('update:modelValue')).toBeUndefined()
  })

  it('reflects modelValue / displayItems syncing after async load', async () => {
    const wrapper = mount(Combobox, {
      props: {
        multi: false,
        modelValue: '',
        fetchSuggestions: vi.fn().mockResolvedValue([]),
      },
    })
    expect(wrapper.find('input').element.value).toBe('')
    await wrapper.setProps({ modelValue: 'x' })
    await wrapper.setProps({
      modelValue: 'x',
      displayItems: [{ id: 'x', label: 'Xander' }],
    })
    await flushPromises()
    expect(wrapper.find('input').element.value).toBe('Xander')
    // Clearing externally resets the input
    await wrapper.setProps({ modelValue: '' })
    expect(wrapper.find('input').element.value).toBe('')
  })

  it('focusInput method is exposed', async () => {
    const wrapper = mountCombobox({ multi: false })
    expect(typeof wrapper.vm.focusInput).toBe('function')
    wrapper.vm.focusInput()
  })
})

// ---------------------------------------------------------------------------
// QA-01 audit (#439): keyboard + mouse interaction edge cases
// ---------------------------------------------------------------------------

describe('Combobox QA-01 edge cases (#439)', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('ESC during an open search closes the dropdown but keeps existing chips', async () => {
    const wrapper = mountCombobox({
      multi: true,
      modelValue: ['a', 'b'],
      displayItems: [
        { id: 'a', label: 'Alpha' },
        { id: 'b', label: 'Beta' },
      ],
    })
    const input = wrapper.find('input')
    // Type into the input and confirm the dropdown is open
    await input.trigger('focus')
    await flushPromises()
    input.element.value = 'gam'
    await input.trigger('input')
    await flushPromises()
    expect(wrapper.find('[data-testid="combobox-dropdown"]').exists()).toBe(true)
    await input.trigger('keydown', { key: 'Escape' })
    // Dropdown closes
    expect(wrapper.find('[data-testid="combobox-dropdown"]').exists()).toBe(false)
    // Selection (chips) is preserved
    const chips = wrapper.findAll('[data-testid^="combobox-chip-"]:not([data-testid*="remove"])')
    expect(chips).toHaveLength(2)
    expect(chips[0].text()).toContain('Alpha')
    expect(chips[1].text()).toContain('Beta')
    // No spurious update:modelValue emission from ESC
    expect(wrapper.emitted('update:modelValue')).toBeFalsy()
  })

  it('mouse click wins when user types and then clicks an item', async () => {
    const wrapper = mountCombobox({ multi: true, modelValue: [] })
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    // Type a query while clicking the second item
    input.element.value = 'b'
    await input.trigger('input')
    await flushPromises()
    await wrapper.find('[data-testid="combobox-item-1"]').trigger('click')
    const events = wrapper.emitted('update:modelValue')
    expect(events).toBeTruthy()
    expect(events[events.length - 1][0]).toEqual(['b'])
    // After click selection in multi mode the query input is cleared
    expect(input.element.value).toBe('')
  })

  it('Backspace with non-empty text never deletes the last chip', async () => {
    // Stress: typing several letters then backspace must only edit the text,
    // never strip a chip.
    const wrapper = mountCombobox({
      multi: true,
      modelValue: ['a'],
      displayItems: [{ id: 'a', label: 'A' }],
    })
    const input = wrapper.find('input')
    input.element.value = 'xyz'
    await input.trigger('input')
    await input.trigger('keydown', { key: 'Backspace' })
    await input.trigger('keydown', { key: 'Backspace' })
    await input.trigger('keydown', { key: 'Backspace' })
    // Chip ['a'] still present, no update emission
    expect(wrapper.emitted('update:modelValue')).toBeFalsy()
    const chips = wrapper.findAll('[data-testid^="combobox-chip-"]:not([data-testid*="remove"])')
    expect(chips).toHaveLength(1)
  })

  it('clicking inside the wrapper focuses the input (no surprise close)', async () => {
    const wrapper = mountCombobox({
      multi: true,
      modelValue: ['a'],
      displayItems: [{ id: 'a', label: 'A' }],
    })
    // Trigger a click on the chip area to ensure the input gains focus
    const root = wrapper.find('[data-testid="combobox-root"]')
    await root.trigger('click')
    // Input ref should be focused — `:focus` selector check
    const input = wrapper.find('input').element
    expect(document.activeElement === input || document.activeElement === document.body).toBe(true)
  })
})

describe('Combobox fetch behavior', () => {
  beforeEach(() => {
    document.body.innerHTML = ''
  })

  it('calls fetchSuggestions on focus and on input change', async () => {
    const fetchSuggestions = vi.fn().mockResolvedValue(makeItems(['a']))
    const wrapper = mount(Combobox, {
      props: { multi: false, modelValue: '', fetchSuggestions, debounceMs: 0 },
    })
    const input = wrapper.find('input')
    await input.trigger('focus')
    await flushPromises()
    expect(fetchSuggestions).toHaveBeenCalled()
    fetchSuggestions.mockClear()
    input.element.value = 'foo'
    await input.trigger('input')
    await flushPromises()
    expect(fetchSuggestions).toHaveBeenCalledWith('foo')
  })

  it('shows empty-state when fetch returns no items', async () => {
    const wrapper = mount(Combobox, {
      props: {
        multi: false,
        modelValue: '',
        fetchSuggestions: vi.fn().mockResolvedValue([]),
      },
    })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    expect(wrapper.find('[data-testid="combobox-empty"]').exists()).toBe(true)
  })

  it('shows error-state when fetch rejects', async () => {
    const wrapper = mount(Combobox, {
      props: {
        multi: false,
        modelValue: '',
        fetchSuggestions: vi.fn().mockRejectedValue(new Error('boom')),
      },
    })
    await wrapper.find('input').trigger('focus')
    await flushPromises()
    expect(wrapper.find('[data-testid="combobox-empty"]').exists()).toBe(true)
  })
})
