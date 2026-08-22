import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'

beforeEach(() => {
  vi.resetModules()
  vi.doMock('@/api/client', () => ({
    dpApi:      { list: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    searchApi:  { search: vi.fn().mockResolvedValue({ data: { items: [] } }) },
    securityApi: { checkUrlTarget: vi.fn(), addUrlTarget: vi.fn() },
  }))
})

afterEach(() => {
  vi.doUnmock('@/api/client')
})

const CONFIG_SCHEMA = {
  mode: { type: 'string', enum: ['both', 'rising', 'falling'], default: 'both', label: 'Flanke' },
  value_rising: { type: 'string', default: 'true', label: 'Wert bei steigender Flanke', value_type_field: 'data_type' },
  value_falling: { type: 'string', default: 'false', label: 'Wert bei fallender Flanke', value_type_field: 'data_type' },
  data_type: { type: 'string', enum: ['bool', 'number', 'string'], default: 'bool', label: 'Datentyp' },
  send_on_rising: { type: 'boolean', default: true, label: 'Bei steigender Flanke senden' },
  send_on_falling: { type: 'boolean', default: true, label: 'Bei fallender Flanke senden' },
  persist_state: { type: 'boolean', default: true, label: 'Zustand nach Neustart wiederherstellen' },
}

async function mountPanel(data = {}) {
  const pinia = createPinia()
  setActivePinia(pinia)
  const { useAuthStore } = await import('@/stores/auth')
  useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }

  const mod = await import('@/components/logic/NodeConfigPanel.vue')
  return mount(mod.default, {
    props: {
      node: { id: 'ed1', type: 'edge_detect', data: { mode: 'both', data_type: 'bool', value_rising: 'true', value_falling: 'false', ...data } },
      nodeTypes: [{ type: 'edge_detect', label: 'Flankenerkennung', config_schema: CONFIG_SCHEMA }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

const selects = w => w.findAll('select')

// The panel runs against the real i18n instance (locale 'de').
describe('NodeConfigPanel edge_detect enum labels', () => {
  it('renders the edge and data type options in German, not as raw identifiers', async () => {
    const w = await mountPanel()
    await flushPromises()

    const optionTexts = w.findAll('option').map(o => o.text())
    expect(optionTexts).toEqual(expect.arrayContaining(['Beide', 'Steigend', 'Fallend']))
    expect(optionTexts).toEqual(expect.arrayContaining(['Boolean', 'Zahl', 'Text']))
    expect(optionTexts).not.toContain('both')
    expect(optionTexts).not.toContain('rising')
    expect(optionTexts).not.toContain('bool')
    w.unmount()
  })

  it('keeps the stable identifier as the option value', async () => {
    const w = await mountPanel()
    await flushPromises()

    const modeSelect = selects(w)[0]
    expect(modeSelect.findAll('option').map(o => o.attributes('value'))).toEqual(['both', 'rising', 'falling'])
    w.unmount()
  })

  it('falls back to the raw identifier when a schema declares no option labels', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    const w = mount(mod.default, {
      props: {
        node: { id: 'x1', type: 'clamp', data: { style: 'alpha' } },
        nodeTypes: [{ type: 'clamp', label: 'Limiter', config_schema: { style: { type: 'string', enum: ['alpha', 'beta'], default: 'alpha' } } }],
        nodeOutputs: {},
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()

    expect(w.findAll('option').map(o => o.text())).toEqual(['alpha', 'beta'])
    w.unmount()
  })
})

describe('NodeConfigPanel edge_detect typed edge values', () => {
  it('offers a localized true/false dropdown while the data type is bool', async () => {
    const w = await mountPanel()
    await flushPromises()

    // mode, data_type and the two edge-value dropdowns.
    const valueSelects = selects(w).filter(s => s.findAll('option').some(o => o.attributes('value') === 'true'))
    expect(valueSelects).toHaveLength(2)
    expect(valueSelects[0].findAll('option').map(o => o.text())).toEqual(['Wahr', 'Falsch'])
    expect(w.findAll('input[type="number"]')).toHaveLength(0)
    w.unmount()
  })

  it('offers a number input while the data type is number', async () => {
    const w = await mountPanel({ data_type: 'number', value_rising: '1', value_falling: '0' })
    await flushPromises()

    const numbers = w.findAll('input[type="number"]')
    expect(numbers).toHaveLength(2)
    expect(numbers.map(i => i.element.value)).toEqual(['1', '0'])
    w.unmount()
  })

  it('offers free text while the data type is string', async () => {
    const w = await mountPanel({ data_type: 'string', value_rising: 'AN', value_falling: 'AUS' })
    await flushPromises()

    const texts = w.findAll('input[type="text"]')
    expect(texts.map(i => i.element.value)).toEqual(expect.arrayContaining(['AN', 'AUS']))
    expect(w.findAll('input[type="number"]')).toHaveLength(0)
    w.unmount()
  })

  it('rewrites the edge values when the data type switches to number', async () => {
    const w = await mountPanel()
    await flushPromises()

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'number'))
    await dataTypeSelect.setValue('number')
    await flushPromises()

    // "true"/"false" are not numbers — normalized rather than left behind.
    expect(w.emitted('update').at(-1)[0]).toMatchObject({ data_type: 'number', value_rising: '0', value_falling: '0' })
    w.unmount()
  })

  it('keeps a numeric edge value when the data type switches to number', async () => {
    const w = await mountPanel({ data_type: 'string', value_rising: '42', value_falling: '' })
    await flushPromises()

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'number'))
    await dataTypeSelect.setValue('number')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: '42', value_falling: '0' })
    w.unmount()
  })

  it('restores each field own default when the data type switches back to bool', async () => {
    const w = await mountPanel({ data_type: 'number', value_rising: '7', value_falling: '0' })
    await flushPromises()

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'bool'))
    await dataTypeSelect.setValue('bool')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: 'true', value_falling: 'false' })
    w.unmount()
  })

  it('keeps an already boolean edge value when the data type switches back to bool', async () => {
    const w = await mountPanel({ data_type: 'string', value_rising: 'false', value_falling: 'true' })
    await flushPromises()

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'bool'))
    await dataTypeSelect.setValue('bool')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: 'false', value_falling: 'true' })
    w.unmount()
  })

  it('leaves the edge values untouched when the data type switches to string', async () => {
    const w = await mountPanel()
    await flushPromises()

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'string'))
    await dataTypeSelect.setValue('string')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: 'true', value_falling: 'false' })
    w.unmount()
  })

  it('emits the update when a boolean edge value is picked', async () => {
    const w = await mountPanel()
    await flushPromises()

    const valueSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'true'))
    await valueSelect.setValue('false')
    await valueSelect.trigger('change')

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: 'false' })
    w.unmount()
  })

  it('emits the update when a numeric edge value is typed', async () => {
    const w = await mountPanel({ data_type: 'number', value_rising: '1', value_falling: '0' })
    await flushPromises()

    const numberInput = w.findAll('input[type="number"]')[0]
    await numberInput.setValue('23')
    await numberInput.trigger('change')

    // Vue casts a type="number" v-model to a real number; the executor's
    // _to_num accepts either, and normaliseTypedValue stringifies on a switch.
    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: 23 })
    w.unmount()
  })

  it('leaves unrelated enum fields alone when they change', async () => {
    const w = await mountPanel()
    await flushPromises()

    const modeSelect = selects(w)[0]
    await modeSelect.setValue('rising')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ mode: 'rising', value_rising: 'true', value_falling: 'false' })
    w.unmount()
  })
})

// value_type_field is a generic schema hint, so the panel must cope with
// schemas that use it less tidily than edge_detect does.
describe('NodeConfigPanel typed value fallbacks', () => {
  async function mountSynthetic(schema, data) {
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    const w = mount(mod.default, {
      props: {
        node: { id: 'syn1', type: 'clamp', data },
        nodeTypes: [{ type: 'clamp', label: 'Synthetic', config_schema: schema }],
        nodeOutputs: {},
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()
    return w
  }

  it('renders plain text when the named type field is absent from the data', async () => {
    const w = await mountSynthetic(
      { val: { type: 'string', default: '', label: 'Value', value_type_field: 'not_in_data' } },
      { val: 'x' },
    )

    expect(w.findAll('input[type="text"]').map(i => i.element.value)).toContain('x')
    expect(w.findAll('input[type="number"]')).toHaveLength(0)
    expect(w.findAll('select')).toHaveLength(0)
    w.unmount()
  })

  it('falls back to false for a boolean field that has neither value nor default', async () => {
    const w = await mountSynthetic(
      {
        kind: { type: 'string', enum: ['bool', 'number'], default: 'number', label: 'Kind' },
        val: { type: 'string', label: 'Value', value_type_field: 'kind' },
      },
      { kind: 'number' },
    )

    const kindSelect = w.findAll('select')[0]
    await kindSelect.setValue('bool')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ kind: 'bool', val: 'false' })
    w.unmount()
  })
})
