/**
 * Integrated help drawer wiring on NodePalette.vue — one HelpButton per
 * documented block type (see help/de/logic/blocks-logic.md and onward per
 * category). A type without an entry in NODE_HELP_IDS gets no button yet
 * rather than one pointing at nonexistent content.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import NodePalette from '@/components/logic/NodePalette.vue'

const NODE_TYPES = [
  { type: 'and',             label: 'AND',           category: 'logic',     color: '#4ade80' },
  { type: 'or',              label: 'OR',            category: 'logic',     color: '#4ade80' },
  { type: 'datapoint_read',  label: 'Objekt lesen',  category: 'datapoint', color: '#0f766e' },
  { type: 'datapoint_write', label: 'Objekt schreiben', category: 'datapoint', color: '#0f766e' },
  { type: 'math_formula',  label: 'Formel',  category: 'math',   color: '#7c3aed' },
  { type: 'string_concat', label: 'String Verketten', category: 'string', color: '#60a5fa' },
]

function mockStorage() {
  const store = {}
  const storage = {
    getItem: vi.fn((k) => store[k] ?? null),
    setItem: vi.fn((k, v) => { store[k] = v }),
  }
  Object.defineProperty(window, 'localStorage', { value: storage, configurable: true })
  Object.defineProperty(globalThis, 'localStorage', { value: storage, configurable: true })
}

function mountPalette() {
  const pinia = createPinia()
  setActivePinia(pinia)
  return mount(NodePalette, { props: { nodeTypes: NODE_TYPES }, global: { plugins: [pinia] } })
}

function helpButton(wrapper, helpId) {
  return wrapper.find(`[data-testid="help-button-${helpId}"]`)
}

describe('NodePalette — per-block help buttons', () => {
  beforeEach(() => mockStorage())

  it.each([
    ['and', 'logic-block-and'],
    ['or', 'logic-block-or'],
    ['datapoint_read', 'logic-block-datapoint-read'],
    ['datapoint_write', 'logic-block-datapoint-write'],
    ['math_formula', 'logic-block-math-formula'],
  ])('renders a help button for the %s block', (_type, helpId) => {
    const wrapper = mountPalette()
    expect(helpButton(wrapper, helpId).exists()).toBe(true)
  })

  it('does not render a help button for a block type with no documented help yet', () => {
    const wrapper = mountPalette()
    // string_concat isn't in NODE_HELP_IDS yet (documented in a later category commit).
    expect(wrapper.find('[data-testid="help-button-logic-block-string-concat"]').exists()).toBe(false)
  })

  it('uses the compact HelpButton size so a documented row does not tower over undocumented ones (issue feedback)', () => {
    const wrapper = mountPalette()
    expect(helpButton(wrapper, 'logic-block-and').classes()).not.toContain('btn-icon')
    expect(helpButton(wrapper, 'logic-block-and').classes()).toContain('p-0.5')
  })

  it.each([
    ['and', 'logic-block-and'],
    ['or', 'logic-block-or'],
    ['datapoint_read', 'logic-block-datapoint-read'],
    ['datapoint_write', 'logic-block-datapoint-write'],
    ['math_formula', 'logic-block-math-formula'],
  ])('opens the help store with %s\'s help_id when its button is clicked', async (_type, helpId) => {
    const wrapper = mountPalette()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, helpId).trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe(helpId)
  })
})
