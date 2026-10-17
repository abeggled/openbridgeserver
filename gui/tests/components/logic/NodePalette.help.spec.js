/**
 * Integrated help drawer wiring on NodePalette.vue — one HelpButton per
 * documented block type. `help_id` is served by the backend as part of each
 * node type's definition (`GET /api/v1/logic/node-types`, see
 * `obs/logic/models.py::NodeTypeDef.help_id`) and points at the matching
 * anchor in help/de/logic/blocks-*.md and onward per category. A type whose
 * definition carries no `help_id` gets no button yet rather than one
 * pointing at nonexistent content.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import NodePalette from '@/components/logic/NodePalette.vue'

const NODE_TYPES = [
  { type: 'and',             label: 'AND',           category: 'logic',     color: '#4ade80', help_id: 'logic-block-and' },
  { type: 'or',              label: 'OR',            category: 'logic',     color: '#4ade80', help_id: 'logic-block-or' },
  { type: 'datapoint_read',  label: 'Objekt lesen',  category: 'datapoint', color: '#0f766e', help_id: 'logic-block-datapoint-read' },
  { type: 'datapoint_write', label: 'Objekt schreiben', category: 'datapoint', color: '#0f766e', help_id: 'logic-block-datapoint-write' },
  { type: 'math_formula',  label: 'Formel',  category: 'math',   color: '#7c3aed', help_id: 'logic-block-math-formula' },
  { type: 'string_concat', label: 'String Verketten', category: 'string', color: '#0891b2', help_id: 'logic-block-string-concat' },
  { type: 'timer_delay',   label: 'Verzögerung', category: 'timer', color: '#b45309', help_id: 'logic-block-timer-delay' },
  { type: 'astro_sun',     label: 'Astro Sonne', category: 'astro', color: '#f59e0b', help_id: 'logic-block-astro-sun' },
  { type: 'notify_message', label: 'Benachrichtigung', category: 'notification', color: '#dc2626', help_id: 'logic-block-notify-message' },
  { type: 'message_archive', label: 'Meldungsarchiv', category: 'notification', color: '#2563eb', help_id: 'logic-block-message-archive' },
  { type: 'wake_on_lan', label: 'Wake on LAN', category: 'integration', color: '#0369a1', help_id: 'logic-block-wake-on-lan' },
  { type: 'host_check', label: 'Host Check (Ping)', category: 'integration', color: '#0369a1', help_id: 'logic-block-host-check' },
  { type: 'json_extractor', label: 'JSON Extractor', category: 'integration', color: '#0369a1', help_id: 'logic-block-json-extractor' },
  { type: 'xml_extractor', label: 'XML Extractor', category: 'integration', color: '#0369a1', help_id: 'logic-block-xml-extractor' },
  { type: 'substring_extractor', label: 'Substring / RegEx', category: 'integration', color: '#0369a1', help_id: 'logic-block-substring-extractor' },
  { type: 'ical', label: 'iCalendar', category: 'integration', color: '#0369a1', help_id: 'logic-block-ical' },
  { type: 'api_client', label: 'API Client', category: 'integration', color: '#0e7490', help_id: 'logic-block-api-client' },
  { type: 'python_script', label: 'Python Script', category: 'script', color: '#65a30d', help_id: 'logic-block-python-script' },
  { type: 'ai_logic', label: 'AI Logic', category: 'ai', color: '#9333ea', help_id: 'logic-block-ai-logic' },
  // Synthetic type, not a real backend node — exercises the "no help_id on
  // the node type definition" branch. edge_detect/notify_pushover/notify_sms
  // are the real-world equivalent today (see issue #1200 for edge_detect).
  { type: 'not_yet_documented', label: 'Not Yet Documented', category: 'ai', color: '#9333ea' },
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

  it('does not render a help button for a block type whose definition carries no help_id', () => {
    const wrapper = mountPalette()
    // not_yet_documented is a synthetic type with no help_id — every real
    // registered node type is now documented (all 10 categories shipped).
    expect(wrapper.find('[data-testid="help-button-logic-block-not-yet-documented"]').exists()).toBe(false)
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
