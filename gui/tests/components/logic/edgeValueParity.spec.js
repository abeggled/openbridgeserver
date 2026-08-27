import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

// Same vue-flow stubbing the card's own spec uses; the card cannot mount
// without it.
const { HANDLE_STUB } = vi.hoisted(() => ({
  HANDLE_STUB: { template: '<div class="handle" />', props: ['type', 'id', 'position', 'style'] },
}))
vi.mock('@vue-flow/core', () => ({
  Handle: HANDLE_STUB,
  Position: { Left: 'left', Right: 'right' },
  useVueFlow: () => ({ removeNodes: vi.fn(), updateNodeData: vi.fn() }),
}))

// The configuration panel and the block card must show the same edge value for
// the same node data. They drifted apart four times while each kept its own
// copy of the coercion rule, so this pins them to one another directly.

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi: { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi: { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
  }))
})
afterEach(() => vi.doUnmock('@/api/client'))

const CONFIG_SCHEMA = {
  on_rising: { type: 'string', enum: ['value', 'trigger', 'off'], default: 'value' },
  value_rising: { type: 'string', default: 'true', value_type_field: 'data_type' },
  on_falling: { type: 'string', enum: ['value', 'trigger', 'off'], default: 'value' },
  value_falling: { type: 'string', default: 'false', value_type_field: 'data_type' },
  data_type: { type: 'string', enum: ['bool', 'number', 'string'], default: 'bool' },
}

async function authed() {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
  return pinia
}

// What the panel shows for value_rising, whichever widget renders it.
async function panelRisingValue(data) {
  const pinia = await authed()
  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  const w = mount(mod.default, {
    props: {
      node: { id: 'ed', type: 'edge_detect', data },
      nodeTypes: [{ type: 'edge_detect', config_schema: CONFIG_SCHEMA }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
  })
  await flushPromises()
  const boolSelect = w.findAll('select').find(s => s.findAll('option').some(o => o.attributes('value') === 'true'))
  if (boolSelect) { const v = boolSelect.element.value; w.unmount(); return v }
  const number = w.find('input[type="number"]')
  if (number.exists()) { const v = number.element.value; w.unmount(); return v }
  const text = w.findAll('input[type="text"]:not([data-testid="node-label-input"])')[0]
  const v = text.element.value
  w.unmount()
  return v
}

// What the card shows for the rising direction, with the arrow stripped.
async function cardRisingValue(data) {
  const { default: GenericNode } = await import('@/components/logic/nodes/GenericNode.vue')
  const w = mount(GenericNode, {
    props: { id: 'ed', type: 'edge_detect', data },
    global: { stubs: { Handle: HANDLE_STUB } },
  })
  await flushPromises()
  const text = w.find('.gn-summary').text()
  w.unmount()
  return text.split('↓')[0].replace('↑', '').trim()
}

describe('panel and card agree on the edge value', () => {
  const cases = [
    ['a collection under an uncoerced data_type', { data_type: 42, value_rising: [1] }],
    ['an object under an uncoerced data_type', { data_type: 42, value_rising: { a: 2 } }],
    ['a scalar under an uncoerced data_type', { data_type: 42, value_rising: 'raw' }],
    ['an imported collection as a string', { data_type: 'string', value_rising: [1] }],
    ['an imported boolean as a string', { data_type: 'string', value_rising: true }],
    ['an imported boolean as a number', { data_type: 'number', value_rising: true }],
    ['an imported collection as a number', { data_type: 'number', value_rising: [1] }],
    ['whitespace around a number', { data_type: 'number', value_rising: ' 4 ' }],
    ['an explicit null value', { data_type: 'string', value_rising: null }],
    ['an absent value', { data_type: 'string' }],
  ]

  it.each(cases)('%s', async (_label, data) => {
    const [panel, card] = [await panelRisingValue(data), await cardRisingValue(data)]
    expect({ panel, card }).toEqual({ panel: card, card })
  })

  it('agrees on booleans too, allowing for the card localizing them', async () => {
    for (const value of ['off', 'False', true, [0], null]) {
      const data = { data_type: 'bool', value_rising: value }
      const panel = await panelRisingValue(data)
      const card = await cardRisingValue(data)
      expect({ value, card }).toEqual({ value, card: panel === 'false' ? 'Falsch' : 'Wahr' })
    }
  })
})
