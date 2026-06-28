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

async function mountPanel(type, data = {}) {
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

function lastUpdate(wrapper) {
  return wrapper.emitted('update').at(-1)[0]
}

describe('NodeConfigPanel decision', () => {
  it('renders two default condition rows when no conditions are stored', async () => {
    const w = await mountPanel('decision', {})
    await flushPromises()

    expect(w.findAll('[data-testid^="rule-row-"]')).toHaveLength(2)
    const rows = w.findAll('[data-testid^="rule-row-"]')
    expect(rows[0].find('input').element.value).toBe('Ausgang 1')
    expect(rows[1].find('input').element.value).toBe('Ausgang 2')
    expect(w.findAll('button').find(b => b.text() === '−').attributes('disabled')).toBeDefined()
    w.unmount()
  })

  it('falls back to default condition rows when stored JSON is invalid', async () => {
    const w = await mountPanel('decision', { conditions: '{' })
    await flushPromises()

    const rows = w.findAll('[data-testid^="rule-row-"]')
    expect(rows).toHaveLength(2)
    expect(rows[0].find('input').element.value).toBe('Ausgang 1')
    expect(rows[1].find('input').element.value).toBe('Ausgang 2')
    w.unmount()
  })

  it('renders condition rows from array-backed data without overwriting them', async () => {
    const w = await mountPanel('decision', {
      conditions: [
        { handle: 'out_10', name: 'Warm', operator: 'gt', value: '24' },
        { handle: 'out_20', name: 'Cold', operator: 'lt', value: '18' },
      ],
    })
    await flushPromises()

    const rows = w.findAll('[data-testid^="rule-row-"]')
    expect(rows).toHaveLength(2)
    expect(rows[0].find('input').element.value).toBe('Warm')
    expect(rows[1].find('input').element.value).toBe('Cold')
    w.unmount()
  })

  it('adds a condition with the next output handle', async () => {
    const w = await mountPanel('decision', {})
    await flushPromises()

    await w.find('[data-testid="rule-add"]').trigger('click')
    await flushPromises()

    const conditions = JSON.parse(lastUpdate(w).conditions)
    expect(conditions).toHaveLength(3)
    expect(conditions[2]).toMatchObject({ handle: 'out_3', name: 'Ausgang 3', operator: 'eq' })
    w.unmount()
  })

  it('updates condition name, operator and range bounds', async () => {
    const w = await mountPanel('decision', {
      conditions: JSON.stringify([
        { handle: 'out_1', name: 'Low', operator: 'eq', value: '' },
        { handle: 'out_2', name: 'High', operator: 'eq', value: '' },
      ]),
    })
    await flushPromises()

    const firstRow = w.find('[data-testid="rule-row-0"]')
    await firstRow.find('input').setValue('Comfort')
    await firstRow.find('select').setValue('range')
    await flushPromises()
    const rangeInputs = firstRow.findAll('input')
    await rangeInputs[1].setValue('24')
    await rangeInputs[2].setValue('20')
    await flushPromises()

    const conditions = JSON.parse(lastUpdate(w).conditions)
    expect(conditions[0]).toMatchObject({ name: 'Comfort', operator: 'range', min: '20', max: '24' })
    w.unmount()
  })

  it('clears range bounds when switching back to a scalar operator', async () => {
    const w = await mountPanel('decision', {
      conditions: JSON.stringify([
        { handle: 'out_1', name: 'Inside', operator: 'range', min: '18', max: '23', value_to: '23' },
        { handle: 'out_2', name: 'Outside', operator: 'eq', value: 'x' },
      ]),
    })
    await flushPromises()

    await w.find('[data-testid="rule-row-0"]').find('select').setValue('eq')
    await flushPromises()

    const rule = JSON.parse(lastUpdate(w).conditions)[0]
    expect(rule.operator).toBe('eq')
    expect(rule).not.toHaveProperty('min')
    expect(rule).not.toHaveProperty('max')
    expect(rule).not.toHaveProperty('value_to')
    w.unmount()
  })

  it('removes a condition only when more than two exist and preserves remaining handles', async () => {
    const w = await mountPanel('decision', {
      conditions: JSON.stringify([
        { handle: 'out_1', name: 'A', operator: 'eq', value: 'a' },
        { handle: 'out_2', name: 'B', operator: 'eq', value: 'b' },
        { handle: 'out_3', name: 'C', operator: 'eq', value: 'c' },
      ]),
    })
    await flushPromises()

    await w.find('[data-testid="rule-row-1"]').findAll('button').at(-1).trigger('click')
    await flushPromises()

    const conditions = JSON.parse(lastUpdate(w).conditions)
    expect(conditions).toHaveLength(2)
    expect(conditions.map(c => c.handle)).toEqual(['out_1', 'out_3'])
    expect(conditions.map(c => c.name)).toEqual(['A', 'C'])
    w.unmount()
  })

  it('adds a condition with a new handle after a middle condition was removed', async () => {
    const w = await mountPanel('decision', {
      conditions: JSON.stringify([
        { handle: 'out_1', name: 'A', operator: 'eq', value: 'a' },
        { handle: 'out_2', name: 'B', operator: 'eq', value: 'b' },
        { handle: 'out_3', name: 'C', operator: 'eq', value: 'c' },
      ]),
    })
    await flushPromises()

    await w.find('[data-testid="rule-row-1"]').findAll('button').at(-1).trigger('click')
    await flushPromises()
    await w.find('[data-testid="rule-add"]').trigger('click')
    await flushPromises()

    const conditions = JSON.parse(lastUpdate(w).conditions)
    expect(conditions.map(c => c.handle)).toEqual(['out_1', 'out_3', 'out_4'])
    w.unmount()
  })
})

describe('NodeConfigPanel value_mapping', () => {
  it('renders defaults and adds a mapping rule', async () => {
    const w = await mountPanel('value_mapping', {})
    await flushPromises()

    expect(w.findAll('[data-testid^="rule-row-"]')).toHaveLength(2)
    await w.find('[data-testid="rule-add"]').trigger('click')
    await flushPromises()

    const rules = JSON.parse(lastUpdate(w).rules)
    expect(rules).toHaveLength(3)
    expect(rules[2]).toMatchObject({ name: 'Regel 3', operator: 'eq', value: '', result: '' })
    w.unmount()
  })

  it('renders mapping rows from array-backed data', async () => {
    const w = await mountPanel('value_mapping', {
      rules: [
        { name: 'Open', operator: 'eq', value: '1', result: 'yes' },
        { name: 'Closed', operator: 'eq', value: '0', result: 'no' },
      ],
    })
    await flushPromises()

    const rows = w.findAll('[data-testid^="rule-row-"]')
    expect(rows).toHaveLength(2)
    expect(rows[0].find('input').element.value).toBe('Open')
    expect(rows[1].find('input').element.value).toBe('Closed')
    w.unmount()
  })

  it('updates output type and rule result', async () => {
    const w = await mountPanel('value_mapping', {
      output_type: 'string',
      rules: JSON.stringify([
        { name: 'R1', operator: 'eq', value: 'on', result: '1' },
        { name: 'R2', operator: 'eq', value: 'off', result: '0' },
      ]),
    })
    await flushPromises()

    await w.find('[data-testid="mapping-output-type"]').setValue('int')
    await w.find('[data-testid="rule-row-0"]').find('[data-testid="mapping-result"]').setValue('42')
    await flushPromises()

    const update = lastUpdate(w)
    expect(update.output_type).toBe('int')
    expect(JSON.parse(update.rules)[0].result).toBe('42')
    w.unmount()
  })

  it('updates a scalar compare value and removes mapping rules above the minimum', async () => {
    const w = await mountPanel('value_mapping', {
      rules: JSON.stringify([
        { name: 'R1', operator: 'eq', value: 'a', result: 'A' },
        { name: 'R2', operator: 'eq', value: 'b', result: 'B' },
        { name: 'R3', operator: 'eq', value: 'c', result: 'C' },
      ]),
    })
    await flushPromises()

    await w.find('[data-testid="rule-row-0"]').findAll('input')[1].setValue('alpha')
    await flushPromises()
    expect(JSON.parse(lastUpdate(w).rules)[0].value).toBe('alpha')

    await w.find('[data-testid="rule-row-2"]').findAll('button').at(-1).trigger('click')
    await flushPromises()

    const rules = JSON.parse(lastUpdate(w).rules)
    expect(rules).toHaveLength(2)
    expect(rules.map(r => r.name)).toEqual(['R1', 'R2'])
    w.unmount()
  })

  it('shows an existing default value field before toggling', async () => {
    const w = await mountPanel('value_mapping', {
      has_default: true,
      default_value: 'fallback',
      rules: JSON.stringify([
        { name: 'R1', operator: 'eq', value: 'a', result: 'A' },
        { name: 'R2', operator: 'eq', value: 'b', result: 'B' },
      ]),
    })
    await flushPromises()

    expect(w.find('[data-testid="mapping-default"]').element.value).toBe('fallback')
    w.unmount()
  })

  it('toggles and edits the default value', async () => {
    const w = await mountPanel('value_mapping', {
      rules: JSON.stringify([
        { name: 'R1', operator: 'eq', value: 'a', result: 'A' },
        { name: 'R2', operator: 'eq', value: 'b', result: 'B' },
      ]),
      has_default: false,
    })
    await flushPromises()

    await w.find('[data-testid="mapping-has-default"]').setValue(true)
    await flushPromises()
    await w.find('[data-testid="mapping-default"]').setValue('fallback')
    await w.find('[data-testid="mapping-default"]').trigger('change')
    await flushPromises()

    const update = lastUpdate(w)
    expect(update.has_default).toBe(true)
    expect(update.default_value).toBe('fallback')
    w.unmount()
  })

  it('shows case sensitivity for text operators and persists the flag', async () => {
    const w = await mountPanel('value_mapping', {
      rules: JSON.stringify([
        { name: 'R1', operator: 'contains', value: 'open', result: 'yes' },
        { name: 'R2', operator: 'eq', value: 'closed', result: 'no' },
      ]),
    })
    await flushPromises()

    const checkbox = w.find('[data-testid="rule-row-0"] input[type="checkbox"]')
    expect(checkbox.exists()).toBe(true)
    await checkbox.setValue(true)
    await flushPromises()

    expect(JSON.parse(lastUpdate(w).rules)[0].case_sensitive).toBe(true)
    w.unmount()
  })
})
