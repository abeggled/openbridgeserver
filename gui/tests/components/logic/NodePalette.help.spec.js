/**
 * Integrated help drawer wiring on NodePalette.vue — one HelpButton per block
 * type the palette offers (see help/de/logic/blocks-logic.md and onward per
 * category). The help_id is derived from the node type rather than looked up
 * in a hand-maintained map; tools/check_help_contract.py enumerates the same
 * node types from obs.logic.registry and fails CI when one of them has no
 * help section, which is what makes every derived id safe to render.
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
  { type: 'string_concat', label: 'String Verketten', category: 'string', color: '#0891b2' },
  { type: 'timer_delay',   label: 'Verzögerung', category: 'timer', color: '#b45309' },
  { type: 'astro_sun',     label: 'Astro Sonne', category: 'astro', color: '#f59e0b' },
  { type: 'notify_message', label: 'Benachrichtigung', category: 'notification', color: '#dc2626' },
  { type: 'message_archive', label: 'Meldungsarchiv', category: 'notification', color: '#2563eb' },
  { type: 'wake_on_lan', label: 'Wake on LAN', category: 'integration', color: '#0369a1' },
  { type: 'host_check', label: 'Host Check (Ping)', category: 'integration', color: '#0369a1' },
  { type: 'json_extractor', label: 'JSON Extractor', category: 'integration', color: '#0369a1' },
  { type: 'xml_extractor', label: 'XML Extractor', category: 'integration', color: '#0369a1' },
  { type: 'substring_extractor', label: 'Substring / RegEx', category: 'integration', color: '#0369a1' },
  { type: 'ical', label: 'iCalendar', category: 'integration', color: '#0369a1' },
  { type: 'api_client', label: 'API Client', category: 'integration', color: '#0e7490' },
  { type: 'python_script', label: 'Python Script', category: 'script', color: '#65a30d' },
  { type: 'ai_logic', label: 'AI Logic', category: 'ai', color: '#9333ea' },
  // Multi-underscore type: the derivation has to replace every separator, not
  // just the first (`replaceAll`, not `replace`).
  { type: 'edge_detect', label: 'Flankenerkennung', category: 'logic', color: '#1d4ed8' },
  { type: 'min_max_tracker', label: 'Min/Max', category: 'math', color: '#7c3aed' },
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
    ['string_concat', 'logic-block-string-concat'],
    ['timer_delay', 'logic-block-timer-delay'],
    ['astro_sun', 'logic-block-astro-sun'],
    ['notify_message', 'logic-block-notify-message'],
    ['message_archive', 'logic-block-message-archive'],
    ['wake_on_lan', 'logic-block-wake-on-lan'],
    ['host_check', 'logic-block-host-check'],
    ['json_extractor', 'logic-block-json-extractor'],
    ['xml_extractor', 'logic-block-xml-extractor'],
    ['substring_extractor', 'logic-block-substring-extractor'],
    ['ical', 'logic-block-ical'],
    ['api_client', 'logic-block-api-client'],
    ['python_script', 'logic-block-python-script'],
    ['ai_logic', 'logic-block-ai-logic'],
  ])('renders a help button for the %s block', (_type, helpId) => {
    const wrapper = mountPalette()
    expect(helpButton(wrapper, helpId).exists()).toBe(true)
  })

  it('renders a help button for every offered block type', () => {
    const wrapper = mountPalette()

    for (const { type } of NODE_TYPES) {
      const helpId = `logic-block-${type.replaceAll('_', '-')}`
      expect(helpButton(wrapper, helpId).exists()).toBe(true)
    }
  })

  it('replaces every underscore in the derived help_id, not just the first', () => {
    const wrapper = mountPalette()

    expect(helpButton(wrapper, 'logic-block-min-max-tracker').exists()).toBe(true)
    expect(helpButton(wrapper, 'logic-block-edge-detect').exists()).toBe(true)
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
    ['string_concat', 'logic-block-string-concat'],
    ['timer_delay', 'logic-block-timer-delay'],
    ['astro_sun', 'logic-block-astro-sun'],
    ['notify_message', 'logic-block-notify-message'],
    ['message_archive', 'logic-block-message-archive'],
    ['wake_on_lan', 'logic-block-wake-on-lan'],
    ['host_check', 'logic-block-host-check'],
    ['json_extractor', 'logic-block-json-extractor'],
    ['xml_extractor', 'logic-block-xml-extractor'],
    ['substring_extractor', 'logic-block-substring-extractor'],
    ['ical', 'logic-block-ical'],
    ['api_client', 'logic-block-api-client'],
    ['python_script', 'logic-block-python-script'],
    ['ai_logic', 'logic-block-ai-logic'],
  ])('opens the help store with %s\'s help_id when its button is clicked', async (_type, helpId) => {
    const wrapper = mountPalette()
    const { useHelpStore } = await import('@/stores/help')
    const helpStore = useHelpStore()

    await helpButton(wrapper, helpId).trigger('click')

    expect(helpStore.isOpen).toBe(true)
    expect(helpStore.currentHelpId).toBe(helpId)
  })
})
