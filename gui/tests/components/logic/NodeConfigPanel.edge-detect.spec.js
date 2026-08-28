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

const EDGE_ACTIONS = ['value', 'trigger', 'off']

const CONFIG_SCHEMA = {
  on_rising: { type: 'string', enum: EDGE_ACTIONS, default: 'value', label: 'Steigende Flanke' },
  value_rising: { type: 'string', default: 'true', label: 'Wert bei steigender Flanke', value_type_field: 'data_type', visible_when: { field: 'on_rising', not_in: ['off', 'trigger'] } },
  on_falling: { type: 'string', enum: EDGE_ACTIONS, default: 'value', label: 'Fallende Flanke' },
  value_falling: { type: 'string', default: 'false', label: 'Wert bei fallender Flanke', value_type_field: 'data_type', visible_when: { field: 'on_falling', not_in: ['off', 'trigger'] } },
  data_type: { type: 'string', enum: ['bool', 'number', 'string'], default: 'bool', label: 'Datentyp' },
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
      node: { id: 'ed1', type: 'edge_detect', data: { on_rising: 'value', on_falling: 'value', data_type: 'bool', value_rising: 'true', value_falling: 'false', ...data } },
      nodeTypes: [{ type: 'edge_detect', label: 'Flankenerkennung', config_schema: CONFIG_SCHEMA }],
      nodeOutputs: {},
    },
    global: { plugins: [pinia] },
    attachTo: document.body,
  })
}

const selects = w => w.findAll('select')
const numbers = w => w.findAll('input[type="number"]')
// The panel header holds the editable block name (issue #1157), which is a
// text input too — keep it out of assertions about the value fields.
const valueTextInputs = w =>
  w.findAll('input[type="text"]:not([data-testid="node-label-input"])')

// The panel runs against the real i18n instance (locale 'de').
describe('NodeConfigPanel edge_detect enum labels', () => {
  it('renders the edge and data type options in German, not as raw identifiers', async () => {
    const w = await mountPanel()
    await flushPromises()

    const optionTexts = w.findAll('option').map(o => o.text())
    expect(optionTexts).toEqual(expect.arrayContaining(['Trigger + Wert', 'Nur Trigger', 'Aus']))
    expect(optionTexts).toEqual(expect.arrayContaining(['Boolean', 'Zahl', 'Text']))
    expect(optionTexts).not.toContain('value')
    expect(optionTexts).not.toContain('trigger')
    expect(optionTexts).not.toContain('off')
    expect(optionTexts).not.toContain('bool')
    w.unmount()
  })

  it('keeps the stable identifier as the option value', async () => {
    const w = await mountPanel()
    await flushPromises()

    const risingSelect = selects(w)[0]
    expect(risingSelect.findAll('option').map(o => o.attributes('value'))).toEqual(['value', 'trigger', 'off'])
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

    // Directly entered values run through the same normalizer as a type
    // switch, which yields the string form the schema declares.
    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: '23' })
    w.unmount()
  })

  it('leaves unrelated enum fields alone when they change', async () => {
    const w = await mountPanel()
    await flushPromises()

    const risingSelect = selects(w)[0]
    await risingSelect.setValue('trigger')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ on_rising: 'trigger', value_rising: 'true', value_falling: 'false' })
    w.unmount()
  })

  it('offers one independent action select per edge direction', async () => {
    const w = await mountPanel()
    await flushPromises()

    const labels = w.findAll('.label').map(l => l.text())
    expect(labels).toContain('Steigende Flanke')
    expect(labels).toContain('Fallende Flanke')
    // No leftover "which edge" enum and no separate send checkboxes.
    expect(labels).not.toContain('Flanke')
    expect(labels.filter(l => l.includes('senden'))).toEqual([])
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

  it('leaves the value untouched when the data type is one it does not coerce', async () => {
    // _coerce_typed_value only converts bool/number/string and returns any
    // other data_type — "auto" on the Memory block — unchanged, so switching
    // to it must not rewrite the stored value.
    const w = await mountSynthetic(
      {
        val: { type: 'string', default: '', label: 'Value', value_type_field: 'dt' },
        dt: { type: 'string', enum: ['auto', 'bool'], default: 'bool', label: 'Type' },
      },
      { val: 'keep me', dt: 'bool' },
    )

    const typeSelect = selects(w).find(x => x.findAll('option').some(o => o.attributes('value') === 'auto'))
    await typeSelect.setValue('auto')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ val: 'keep me', dt: 'auto' })
    w.unmount()
  })

  it('renders an explicit null as empty for a data type it does not coerce', async () => {
    // str(None) would be "None", but an uncoerced null has nothing to show.
    const w = await mountSynthetic(
      {
        val: { type: 'string', default: '', label: 'Value', value_type_field: 'dt' },
        dt: { type: 'string', enum: ['auto', 'bool'], default: 'bool', label: 'Type' },
      },
      { val: null, dt: 'bool' },
    )

    const typeSelect = selects(w).find(x => x.findAll('option').some(o => o.attributes('value') === 'auto'))
    await typeSelect.setValue('auto')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ val: '', dt: 'auto' })
    w.unmount()
  })

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

describe('NodeConfigPanel edge_detect value visibility', () => {
  it('shows both value fields while both directions send a value', async () => {
    const w = await mountPanel()
    await flushPromises()

    const labels = w.findAll('.label').map(l => l.text())
    expect(labels).toContain('Wert bei steigender Flanke')
    expect(labels).toContain('Wert bei fallender Flanke')
    w.unmount()
  })

  it('keeps the value field for a setting the runtime still sends on', async () => {
    // The executor treats anything other than off/trigger as value-sending, so
    // an imported or future setting must not have its value field hidden.
    const w = await mountPanel({ on_rising: 'both' })
    await flushPromises()

    expect(w.findAll('.label').map(l => l.text())).toContain('Wert bei steigender Flanke')
    w.unmount()
  })

  it('hides the value field of a direction that only pulses its trigger', async () => {
    const w = await mountPanel({ on_rising: 'trigger' })
    await flushPromises()

    const labels = w.findAll('.label').map(l => l.text())
    expect(labels).not.toContain('Wert bei steigender Flanke')
    expect(labels).toContain('Wert bei fallender Flanke')
    w.unmount()
  })

  it('hides the value field of a direction that is switched off', async () => {
    const w = await mountPanel({ on_falling: 'off' })
    await flushPromises()

    const labels = w.findAll('.label').map(l => l.text())
    expect(labels).toContain('Wert bei steigender Flanke')
    expect(labels).not.toContain('Wert bei fallender Flanke')
    w.unmount()
  })

  it('shows a value field whose direction is absent from the data, via the schema default', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    // A node saved before on_rising existed carries no value for it at all.
    const w = mount(mod.default, {
      props: {
        node: { id: 'ed2', type: 'edge_detect', data: { data_type: 'bool', value_rising: 'true' } },
        nodeTypes: [{ type: 'edge_detect', label: 'Flankenerkennung', config_schema: CONFIG_SCHEMA }],
        nodeOutputs: {},
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()

    expect(w.findAll('.label').map(l => l.text())).toContain('Wert bei steigender Flanke')
    w.unmount()
  })

  it('still normalizes a hidden value when the data type changes', async () => {
    // Regression: the hidden field is not rendered, but it must not keep the
    // old notation and resurface as "true" in a Number field later.
    const w = await mountPanel({ on_rising: 'trigger' })
    await flushPromises()

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'number'))
    await dataTypeSelect.setValue('number')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: '0', value_falling: '0' })
    w.unmount()
  })
})

describe('NodeConfigPanel uncoerced edge values', () => {
  it('keeps a collection shape when the data type is not one the backend coerces', async () => {
    // _coerce_typed_value returns an unrecognised data_type untouched, so the
    // value stays a list or object. The plain text field would scalarize them
    // into "1" and "[object Object]".
    const w = await mountPanel({ data_type: 42, value_rising: [1], value_falling: { a: 2 } })
    await flushPromises()

    expect(valueTextInputs(w).map(i => i.element.value)).toEqual(['[1]', '{"a":2}'])
    w.unmount()
  })

  it('keeps a scalar uncoerced value as plain text', async () => {
    const w = await mountPanel({ data_type: 42, value_rising: 'raw', value_falling: 7 })
    await flushPromises()

    expect(valueTextInputs(w).map(i => i.element.value)).toEqual(['raw', '7'])
    w.unmount()
  })

  it('renders a negative zero with its sign in the number input', async () => {
    // The widget accepts "-0", and the executor sends a signed zero.
    const w = await mountPanel({ data_type: 'number', value_rising: '-0.0', value_falling: '0' })
    await flushPromises()

    const fields = numbers(w)
    expect(fields.map(i => i.element.value)).toEqual(['-0', '0'])
    expect(fields[0].element.checkValidity()).toBe(true)
    w.unmount()
  })

  it('renders a number edge value without surrounding whitespace', async () => {
    // The widget must be valid, not just parseable: <input type="number">
    // shows blank for " 4 ".
    const w = await mountPanel({ data_type: 'number', value_rising: ' 4 ', value_falling: '1' })
    await flushPromises()

    const first = numbers(w)[0]
    expect(first.element.value).toBe('4')
    expect(first.element.checkValidity()).toBe(true)
    w.unmount()
  })
})

describe('NodeConfigPanel imported string edge values', () => {
  it('shows an imported collection the way the backend stringifies it', async () => {
    // str([1]) is "[1]" and str({'a': 1}) is "{'a': 1}"; JavaScript would
    // render "1" and "[object Object]" and misstate the actuator values.
    const w = await mountPanel({ data_type: 'string', value_rising: [1], value_falling: { a: 1 } })
    await flushPromises()

    expect(valueTextInputs(w).map(i => i.element.value)).toEqual(['[1]', "{'a': 1}"])
    w.unmount()
  })

  it('shows an imported boolean in Python spelling', async () => {
    const w = await mountPanel({ data_type: 'string', value_rising: true, value_falling: false })
    await flushPromises()

    expect(valueTextInputs(w).map(i => i.element.value)).toEqual(['True', 'False'])
    w.unmount()
  })

  it('keeps an ordinary typed string untouched and emits the edit', async () => {
    const w = await mountPanel({ data_type: 'string', value_rising: 'AN', value_falling: 'AUS' })
    await flushPromises()
    expect(valueTextInputs(w).map(i => i.element.value)).toEqual(['AN', 'AUS'])

    const rising = valueTextInputs(w)[0]
    await rising.setValue('EIN')
    await rising.trigger('change')
    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: 'EIN' })
    w.unmount()
  })
})

describe('NodeConfigPanel schema defaults for a bare node', () => {
  // LogicGraphImport accepts a node with empty {} data; the backend then runs
  // it on the schema defaults, so the panel must show those rather than blank
  // controls — otherwise it misstates three active settings.
  async function mountBareNode() {
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    const w = mount(mod.default, {
      props: {
        node: { id: 'ed4', type: 'edge_detect', data: {} },
        nodeTypes: [{ type: 'edge_detect', label: 'Flankenerkennung', config_schema: CONFIG_SCHEMA }],
        nodeOutputs: {},
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()
    return w
  }

  it('renders the enum default instead of a blank select', async () => {
    const w = await mountBareNode()

    const actions = selects(w)
      .filter(s => s.findAll('option').some(o => o.attributes('value') === 'trigger'))
      .map(s => s.element.value)
    expect(actions).toEqual(['value', 'value'])
    w.unmount()
  })

  it('renders the boolean default instead of an unchecked box', async () => {
    const w = await mountBareNode()

    expect(w.find('input[type="checkbox"]').element.checked).toBe(true)
    w.unmount()
  })

  it('still emits an explicit pick over the displayed default', async () => {
    const w = await mountBareNode()

    // The controls are bound with :value/:checked, not v-model, so the change
    // handlers own the write — a missing one would silently drop the edit.
    const rising = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'trigger'))
    await rising.setValue('trigger')
    expect(w.emitted('update').at(-1)[0]).toMatchObject({ on_rising: 'trigger' })

    const persist = w.find('input[type="checkbox"]')
    await persist.setValue(false)
    expect(w.emitted('update').at(-1)[0]).toMatchObject({ persist_state: false })
    w.unmount()
  })

  it('shows no setting when an enum value is explicitly null', async () => {
    // The backend reads d.get(key, default): an explicit null is a configured
    // value that matches no listed setting, and a <select> whose value matches
    // no option would otherwise fall back to displaying the first one.
    const w = await mountPanel({ on_rising: null })
    await flushPromises()

    const rising = selects(w).find(x => x.findAll('option').some(o => o.attributes('value') === 'trigger'))
    expect(rising.element.value).toBe('')
    expect(rising.findAll('option')[0].attributes('disabled')).toBeDefined()
    w.unmount()
  })

  it('shows no setting for a stored enum value the schema does not list', async () => {
    // Same path for a setting written by a newer version of the block.
    const w = await mountPanel({ on_rising: 'from_the_future' })
    await flushPromises()

    const rising = selects(w).find(x => x.findAll('option').some(o => o.attributes('value') === 'trigger'))
    expect(rising.element.value).toBe('')
    w.unmount()
  })

  it('shows the declared default when a boolean is explicitly null', async () => {
    // An explicit null is not a boolean, and every consumer of these fields
    // falls back to its own default for one: LogicManager excludes a node from
    // persistence only for a literal False (manager.py, `is False`), so a null
    // persist_state still persists and the box must be checked.
    const w = await mountPanel({ persist_state: null })
    await flushPromises()

    expect(w.find('input[type="checkbox"]').element.checked).toBe(true)
    w.unmount()
  })

  it('still unchecks a boolean that is explicitly false', async () => {
    const w = await mountPanel({ persist_state: false })
    await flushPromises()

    expect(w.find('input[type="checkbox"]').element.checked).toBe(false)
    w.unmount()
  })

  it.each([[0], [''], [[]]])('keeps persistence checked for the non-boolean %j the backend still persists', async (stored) => {
    // LogicManager opts out only for a literal False (`is False`), so 0, "" and
    // [] — which an import or a direct API client can supply — leave the node
    // state saved and restored. JavaScript truthiness alone would show an
    // unchecked box for a setting that is in fact active.
    const w = await mountPanel({ persist_state: stored })
    await flushPromises()

    expect(w.find('input[type="checkbox"]').element.checked).toBe(true)
    w.unmount()
  })

  it('keeps persistence checked for the string "false", which is not Python False', async () => {
    const w = await mountPanel({ persist_state: 'false' })
    await flushPromises()

    expect(w.find('input[type="checkbox"]').element.checked).toBe(true)
    w.unmount()
  })

  it.each([[[]], [{}]])('reads an imported empty collection %j as the backend does: false', async (stored) => {
    // Python's bool() is false for an empty list or dict, but JavaScript's `!!`
    // is true for both — a Gate whose negate_enable arrived as [] does not
    // invert, so showing it as enabled states the opposite of what runs.
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    const w = mount(mod.default, {
      props: {
        node: { id: 'g1', type: 'gate', data: { negate_enable: stored } },
        nodeTypes: [{ type: 'gate', config_schema: { negate_enable: { type: 'boolean', default: false, label: 'Negieren' } } }],
        nodeOutputs: {},
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()

    expect(w.find('input[type="checkbox"]').element.checked).toBe(false)
    w.unmount()
  })

  it('reads a non-empty imported collection as true, as Python does', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    const w = mount(mod.default, {
      props: {
        node: { id: 'g2', type: 'gate', data: { negate_enable: [0] } },
        nodeTypes: [{ type: 'gate', config_schema: { negate_enable: { type: 'boolean', default: false, label: 'Negieren' } } }],
        nodeOutputs: {},
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()

    expect(w.find('input[type="checkbox"]').element.checked).toBe(true)
    w.unmount()
  })

  it('reads a plain truthiness boolean field by truthiness, not by identity', async () => {
    // The identity rule is scoped to the fields whose backend consumer uses
    // it. A generic boolean setting keeps ordinary truthiness, so a stored 0
    // still renders unchecked.
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    const w = mount(mod.default, {
      props: {
        node: { id: 'ed6', type: 'edge_detect', data: { negate_enable: 0 } },
        nodeTypes: [{
          type: 'edge_detect',
          config_schema: { negate_enable: { type: 'boolean', default: true, label: 'Negieren' } },
        }],
        nodeOutputs: {},
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()

    expect(w.find('input[type="checkbox"]').element.checked).toBe(false)
    w.unmount()
  })

  it('renders unchecked when the boolean field declares no default at all', async () => {
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    const { default: _d, ...persistNoDefault } = CONFIG_SCHEMA.persist_state
    const w = mount(mod.default, {
      props: {
        node: { id: 'ed5', type: 'edge_detect', data: {} },
        nodeTypes: [{ type: 'edge_detect', config_schema: { ...CONFIG_SCHEMA, persist_state: persistNoDefault } }],
        nodeOutputs: {},
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()

    expect(w.find('input[type="checkbox"]').element.checked).toBe(false)
    w.unmount()
  })

  it('keeps a stored value that differs from the schema default', async () => {
    const w = await mountPanel({ on_rising: 'off', persist_state: false })
    await flushPromises()

    const rising = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'trigger'))
    expect(rising.element.value).toBe('off')
    expect(w.find('input[type="checkbox"]').element.checked).toBe(false)
    w.unmount()
  })
})

describe('NodeConfigPanel empty value conversion', () => {
  it('converts a cleared text value to false, not to the schema default', async () => {
    // _to_bool('') is false. Falling back to the field default ('true' for the
    // rising edge) would invert what the actuator receives.
    const w = await mountPanel({ data_type: 'string', value_rising: '', value_falling: 'x' })
    await flushPromises()

    const dataType = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'bool'))
    await dataType.setValue('bool')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: 'false', value_falling: 'true' })
    w.unmount()
  })

  it('still applies the schema default when the field is genuinely absent', async () => {
    // The key must be missing, not set to undefined: the backend reads it as
    // d.get(key, default), so only an absent key takes the default.
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    const w = mount(mod.default, {
      props: {
        node: { id: 'ed6', type: 'edge_detect', data: { data_type: 'string', on_rising: 'value', on_falling: 'value' } },
        nodeTypes: [{ type: 'edge_detect', config_schema: CONFIG_SCHEMA }],
        nodeOutputs: {},
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()

    // data_type is string, so the declared 'true'/'false' defaults are what
    // the executor sends — they must be visible, not two blank inputs.
    expect(valueTextInputs(w).map(i => i.element.value)).toEqual(['true', 'false'])
    w.unmount()
  })

  it('treats an explicit null as a configured value, not a missing field', async () => {
    // LogicGraphImport accepts JSON null; the key then exists, so d.get finds
    // it and _to_bool(None) is False — the rising default must NOT apply.
    const w = await mountPanel({ data_type: 'bool', value_rising: null, value_falling: null })
    await flushPromises()

    const bools = selects(w).filter(x => x.findAll('option').some(o => o.attributes('value') === 'true'))
    expect(bools.map(x => x.element.value)).toEqual(['false', 'false'])
    w.unmount()
  })

  it('leaves the value uncoerced when data_type is explicitly null', async () => {
    // _coerce_typed_value only converts bool/number/string; an explicit null
    // is an unrecognised type and the configured value is sent unchanged.
    const w = await mountPanel({ data_type: null, value_rising: 'off', value_falling: 'raw' })
    await flushPromises()

    expect(valueTextInputs(w).map(i => i.element.value)).toEqual(['off', 'raw'])
    expect(selects(w).filter(x => x.findAll('option').some(o => o.attributes('value') === 'true'))).toHaveLength(0)
    w.unmount()
  })
})

describe('NodeConfigPanel imported number edge values', () => {
  // LogicGraphImport accepts native JSON values, so a Number edge value can
  // arrive as a real boolean or a collection. The panel must show what
  // GraphExecutor._to_num will actually send, not the HTML/JS coercion of the
  // raw value — otherwise the editor misstates the actuator value.
  it('shows an imported boolean as the number the backend sends', async () => {
    const w = await mountPanel({ data_type: 'number', value_rising: true, value_falling: false })
    await flushPromises()

    // Raw binding rendered "" for true, because "true" is not a valid
    // number-input value; the backend sends 1.0.
    expect(numbers(w).map(i => i.element.value)).toEqual(['1', '0'])
    w.unmount()
  })

  it('shows an imported collection as 0, the value float() falls back to', async () => {
    const w = await mountPanel({ data_type: 'number', value_rising: [1], value_falling: [] })
    await flushPromises()

    // Raw binding rendered "1" for [1] via String([1]); float([1]) raises, so
    // the backend sends 0.0.
    expect(numbers(w).map(i => i.element.value)).toEqual(['0', '0'])
    w.unmount()
  })

  it('normalizes an edited value through the backend rule on change', async () => {
    const w = await mountPanel({ data_type: 'number', value_rising: 1, value_falling: 0 })
    await flushPromises()

    const rising = numbers(w)[0]
    await rising.setValue('2.5')
    await rising.trigger('change')
    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: '2.5' })

    // The input is no longer bound with v-model, so the change handler has to
    // read the DOM — a stale localData read would re-emit the old value.
    expect(numbers(w)[0].element.value).toBe('2.5')
    w.unmount()
  })

  it('rejects an edited overflow spelling instead of storing Infinity', async () => {
    const w = await mountPanel({ data_type: 'number', value_rising: 1, value_falling: 0 })
    await flushPromises()

    const rising = numbers(w)[0]
    await rising.setValue('1e309')
    await rising.trigger('change')
    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: '0' })
    w.unmount()
  })
})

describe('NodeConfigPanel typed value backend agreement', () => {
  async function mountBare(data) {
    const pinia = createPinia()
    setActivePinia(pinia)
    const { useAuthStore } = await import('@/stores/auth')
    useAuthStore().user = { id: 'u1', username: 'admin', is_admin: true }
    const mod = await import('@/components/logic/NodeConfigPanel.vue')
    const w = mount(mod.default, {
      props: {
        node: { id: 'ed3', type: 'edge_detect', data },
        nodeTypes: [{ type: 'edge_detect', label: 'Flankenerkennung', config_schema: CONFIG_SCHEMA }],
        nodeOutputs: {},
      },
      global: { plugins: [pinia] },
    })
    await flushPromises()
    return w
  }

  it('applies the schema default when an imported node omits the type field', async () => {
    // LogicGraphImport accepts {} node data; the backend then defaults
    // data_type to bool, so the panel must not fall back to free text.
    const w = await mountBare({})

    const boolSelects = selects(w).filter(s => s.findAll('option').some(o => o.attributes('value') === 'true'))
    expect(boolSelects).toHaveLength(2)
    // The header carries the editable block name (issue #1157); only the value
    // fields matter here — none of them may fall back to free text.
    expect(valueTextInputs(w)).toHaveLength(0)
    w.unmount()
  })

  it('renders an imported boolean spelling in the dropdown', async () => {
    // LogicGraphImport accepts "False"/"off" verbatim; neither matches an
    // option value, so a v-model binding would leave both selects blank.
    const w = await mountBare({ data_type: 'bool', value_rising: 'False', value_falling: 'off' })

    const boolSelects = selects(w).filter(s => s.findAll('option').some(o => o.attributes('value') === 'true'))
    expect(boolSelects.map(s => s.element.value)).toEqual(['false', 'false'])
    w.unmount()
  })

  it('writes the canonical spelling once the dropdown is used', async () => {
    const w = await mountBare({ data_type: 'bool', value_rising: 'False', value_falling: 'off' })

    const boolSelects = selects(w).filter(s => s.findAll('option').some(o => o.attributes('value') === 'true'))
    await boolSelects[0].setValue('true')

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: 'true' })
    w.unmount()
  })

  it('rejects numeric notation the backend cannot parse', async () => {
    // Number("0x10") is finite in JavaScript, but Python's float() raises and
    // the executor would silently emit 0.0 while the editor showed "0x10".
    const w = await mountBare({ data_type: 'string', value_rising: '0x10', value_falling: 'Infinity' })

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'number'))
    await dataTypeSelect.setValue('number')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: '0', value_falling: '0' })
    w.unmount()
  })

  it('keeps the backend meaning of a boolean spelling when switching to Boolean', async () => {
    // GraphExecutor._to_bool('False') and ('off') are both false; an exact
    // "true"/"false" match would fall back to the default and invert them.
    const w = await mountBare({ data_type: 'string', value_rising: 'False', value_falling: 'off' })

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'bool'))
    await dataTypeSelect.setValue('bool')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: 'false', value_falling: 'false' })
    w.unmount()
  })

  it('maps any other text to true when switching to Boolean, as the backend does', async () => {
    const w = await mountBare({ data_type: 'string', value_rising: 'AN', value_falling: '' })

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'bool'))
    await dataTypeSelect.setValue('bool')
    await flushPromises()

    // "AN" is truthy for _to_bool; an empty value has no meaning, so the
    // field's own default applies.
    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: 'true', value_falling: 'false' })
    w.unmount()
  })

  it('rejects an overflowing number typed directly into the field', async () => {
    // The type-switch normalizer is not enough: this input emits on its own,
    // and Infinity serializes to null on the way to the backend.
    const w = await mountPanel({ data_type: 'number', value_rising: '1', value_falling: '0' })
    await flushPromises()

    const numberInput = w.findAll('input[type="number"]')[0]
    await numberInput.setValue('1e309')
    await numberInput.trigger('change')

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: '0' })
    w.unmount()
  })

  it('rejects a number that the backend would parse into infinity', async () => {
    // float('1e309') is inf in Python; the actuator must not receive that.
    const w = await mountBare({ data_type: 'string', value_rising: '1e309', value_falling: '-1e309' })

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'number'))
    await dataTypeSelect.setValue('number')
    await flushPromises()

    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: '0', value_falling: '0' })
    w.unmount()
  })

  it('keeps decimal and scientific notation, which the backend does parse', async () => {
    const w = await mountBare({ data_type: 'string', value_rising: '-1.5e3', value_falling: '.25' })

    const dataTypeSelect = selects(w).find(s => s.findAll('option').some(o => o.attributes('value') === 'number'))
    await dataTypeSelect.setValue('number')
    await flushPromises()

    // Both are canonicalized to the value the executor sends: the widget
    // rejects ".25", and keeping any "valid-looking" spelling is what let
    // underflowing and precision-losing imports display a value the actuator
    // never receives.
    expect(Number('-1.5e3')).toBe(Number('-1500'))
    expect(Number('.25')).toBe(Number('0.25'))
    expect(w.emitted('update').at(-1)[0]).toMatchObject({ value_rising: '-1500', value_falling: '0.25' })
    w.unmount()
  })
})
