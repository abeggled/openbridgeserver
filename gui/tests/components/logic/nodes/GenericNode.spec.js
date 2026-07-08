import { describe, it, expect, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const { HANDLE_STUB, removeNodesMock, updateNodeDataMock } = vi.hoisted(() => ({
  HANDLE_STUB:       { template: '<div class="handle" :data-type="type" :data-id="id" />', props: ['type', 'id', 'position', 'style'] },
  removeNodesMock:   vi.fn(),
  updateNodeDataMock: vi.fn(),
}))

vi.mock('@vue-flow/core', () => ({
  Handle:     HANDLE_STUB,
  Position:   { Left: 'left', Right: 'right' },
  useVueFlow: () => ({ removeNodes: removeNodesMock, updateNodeData: updateNodeDataMock }),
}))

async function mountGN(type, data = {}, extraProps = {}) {
  const { default: GenericNode } = await import('@/components/logic/nodes/GenericNode.vue')
  return mount(GenericNode, {
    props: { id: 'gn-1', type, data, ...extraProps },
    global: { stubs: { Handle: HANDLE_STUB } },
  })
}

describe('GenericNode — label from NODE_DEFS', () => {
  beforeEach(() => { removeNodesMock.mockClear(); updateNodeDataMock.mockClear() })

  it('shows "Festwert" for const_value', async () => {
    const w = await mountGN('const_value')
    await flushPromises()
    expect(w.find('.gn-title').text()).toBe('Festwert')
  })

  it('shows "AND" for and type', async () => {
    const w = await mountGN('and')
    await flushPromises()
    expect(w.find('.gn-title').text()).toBe('AND')
  })

  it('falls back to type string for unknown type', async () => {
    const w = await mountGN('mystery_node')
    await flushPromises()
    expect(w.find('.gn-title').text()).toBe('mystery_node')
  })
})

describe('GenericNode — handles', () => {
  it('renders no target handles for const_value (no inputs)', async () => {
    const w = await mountGN('const_value')
    await flushPromises()
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(targets.length).toBe(0)
  })

  it('renders 1 source handle for const_value', async () => {
    const w = await mountGN('const_value')
    await flushPromises()
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(sources.length).toBe(1)
  })

  it('renders 2 target handles for and type', async () => {
    const w = await mountGN('and')
    await flushPromises()
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(targets.length).toBe(2)
  })

  it('renders dynamic input count for AND gate (input_count=4)', async () => {
    const w = await mountGN('and', { input_count: 4 })
    await flushPromises()
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(targets.length).toBe(4)
  })

  it('renders two default source handles for decision', async () => {
    const w = await mountGN('decision')
    await flushPromises()
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(sources.map(h => h.attributes('data-id'))).toEqual(['out_1', 'out_2'])
  })

  it('renders decision source handles from configured conditions', async () => {
    const w = await mountGN('decision', {
      conditions: JSON.stringify([
        { handle: 'low', name: 'Low' },
        { handle: 'ok', name: 'OK' },
        { handle: 'high', name: 'High' },
      ]),
    })
    await flushPromises()
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(sources.map(h => h.attributes('data-id'))).toEqual(['low', 'ok', 'high'])
    expect(w.text()).toContain('Low')
    expect(w.text()).toContain('High')
  })

  it('renders decision source handles from array-backed conditions', async () => {
    const w = await mountGN('decision', {
      conditions: [
        { handle: 'out_10', name: 'Warm' },
        { handle: 'out_20', name: 'Cold' },
      ],
    })
    await flushPromises()
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(sources.map(h => h.attributes('data-id'))).toEqual(['out_10', 'out_20'])
    expect(w.text()).toContain('Warm')
    expect(w.text()).toContain('Cold')
  })
})

describe('GenericNode — summary', () => {
  it('shows summary for const_value', async () => {
    const w = await mountGN('const_value', { data_type: 'number', value: '42' })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('42')
  })

  it('shows formula for math_formula', async () => {
    const w = await mountGN('math_formula', { formula: 'a * 2' })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('a * 2')
  })

  it('shows compare summary: A > B by default', async () => {
    const w = await mountGN('compare')
    await flushPromises()
    expect(w.find('.gn-summary').text()).toBe('A > B')
  })

  it('shows delay_s for timer_delay', async () => {
    const w = await mountGN('timer_delay', { delay_s: 5 })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('5')
  })

  it('shows decision rule count summary', async () => {
    const w = await mountGN('decision', {
      conditions: JSON.stringify([
        { handle: 'a', name: 'A' },
        { handle: 'b', name: 'B' },
        { handle: 'c', name: 'C' },
      ]),
    })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('3 Regeln')
  })

  it('shows mapping output type and rule count summary', async () => {
    const w = await mountGN('value_mapping', {
      output_type: 'int',
      rules: JSON.stringify([
        { name: 'A' },
        { name: 'B' },
      ]),
    })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('int')
    expect(w.find('.gn-summary').text()).toContain('2 Regeln')
  })

  it('counts array-backed decision and mapping rules in summaries', async () => {
    const decision = await mountGN('decision', {
      conditions: [{ handle: 'a' }, { handle: 'b' }, { handle: 'c' }],
    })
    await flushPromises()
    expect(decision.find('.gn-summary').text()).toContain('3 Regeln')

    const mapping = await mountGN('value_mapping', {
      output_type: 'float',
      rules: [{ result: '1' }, { result: '2' }, { result: '3' }, { result: '4' }],
    })
    await flushPromises()
    expect(mapping.find('.gn-summary').text()).toContain('4 Regeln')
  })

  it('shows host for host_check', async () => {
    const w = await mountGN('host_check', { host: '192.168.1.1' })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('192.168.1.1')
  })

  it('shows — for host_check with no host configured', async () => {
    const w = await mountGN('host_check', {})
    await flushPromises()
    expect(w.find('.gn-summary').text()).toBe('—')
  })
})

describe('GenericNode — debug band', () => {
  it('shows debug band when data._dbg is set', async () => {
    const w = await mountGN('and', { _dbg: 'true' })
    await flushPromises()
    expect(w.find('[data-testid="debug-band"]').exists()).toBe(true)
    expect(w.find('[data-testid="debug-band"]').text()).toBe('true')
  })

  it('hides debug band when no _dbg', async () => {
    const w = await mountGN('and')
    await flushPromises()
    expect(w.find('[data-testid="debug-band"]').exists()).toBe(false)
  })
})

describe('GenericNode — gate node negate', () => {
  it('shows negate buttons for AND gate inputs', async () => {
    const w = await mountGN('and')
    await flushPromises()
    const negateBtns = w.findAll('.gn-port-negate')
    expect(negateBtns.length).toBeGreaterThanOrEqual(2)
  })

  it('calls updateNodeData when negate button is clicked', async () => {
    const w = await mountGN('and', { negate_in1: false })
    await flushPromises()
    const negateBtns = w.findAll('.gn-port-negate')
    await negateBtns[0].trigger('click')
    expect(updateNodeDataMock).toHaveBeenCalledWith('gn-1', expect.objectContaining({ negate_in1: true }))
  })
})

describe('GenericNode — delete', () => {
  it('calls removeNodes on delete button click', async () => {
    const w = await mountGN('and')
    await flushPromises()
    await w.find('.gn-del').trigger('click')
    expect(removeNodesMock).toHaveBeenCalledWith(['gn-1'])
  })
})
