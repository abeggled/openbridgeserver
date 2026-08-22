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

  it('shows "Klemme" for merge type', async () => {
    const w = await mountGN('merge')
    await flushPromises()
    expect(w.find('.gn-title').text()).toBe('Klemme')
  })

  it('falls back to type string for unknown type', async () => {
    const w = await mountGN('mystery_node')
    await flushPromises()
    expect(w.find('.gn-title').text()).toBe('mystery_node')
  })

  it('exposes the full heading when its fixed-width display is truncated', async () => {
    const w = await mountGN('substring_extractor')
    await flushPromises()
    expect(w.find('.gn-title').attributes('title')).toBe(w.find('.gn-title').text())
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

  it('renders 2 target handles for merge type', async () => {
    const w = await mountGN('merge')
    await flushPromises()
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(targets.length).toBe(2)
  })

  it('renders dynamic input count for merge (input_count=5)', async () => {
    const w = await mountGN('merge', { input_count: 5 })
    await flushPromises()
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    expect(targets.length).toBe(5)
  })

  it('shows "Änderungsfilter" label and renders 1 target + 2 source handles for change_filter', async () => {
    const w = await mountGN('change_filter')
    await flushPromises()
    expect(w.find('.gn-title').text()).toBe('Änderungsfilter')
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(targets.length).toBe(1)
    expect(sources.length).toBe(2)
  })

  it('shows "Flankenerkennung" label and renders 2 target + 3 source handles for edge_detect', async () => {
    const w = await mountGN('edge_detect')
    await flushPromises()
    expect(w.find('.gn-title').text()).toBe('Flankenerkennung')
    const targets = w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(targets.map(h => h.attributes('data-id'))).toEqual(['in', 'reset'])
    expect(sources.map(h => h.attributes('data-id'))).toEqual(['out', 'rising', 'falling'])
  })

  it('renders all three edge_detect outputs by default', async () => {
    const w = await mountGN('edge_detect')
    await flushPromises()
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(sources.map(h => h.attributes('data-id'))).toEqual(['out', 'rising', 'falling'])
  })

  it('drops the edge_detect value output when neither direction sends one', async () => {
    const w = await mountGN('edge_detect', { on_rising: 'trigger', on_falling: 'trigger' })
    await flushPromises()
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(sources.map(h => h.attributes('data-id'))).toEqual(['rising', 'falling'])
  })

  it('keeps the edge_detect value output while one direction still sends', async () => {
    const rising = await mountGN('edge_detect', { on_rising: 'value', on_falling: 'trigger' })
    await flushPromises()
    expect(rising.findAll('.handle').filter(h => h.attributes('data-type') === 'source').map(h => h.attributes('data-id')))
      .toEqual(['out', 'rising', 'falling'])

    const falling = await mountGN('edge_detect', { on_rising: 'trigger', on_falling: 'value' })
    await flushPromises()
    expect(falling.findAll('.handle').filter(h => h.attributes('data-type') === 'source').map(h => h.attributes('data-id')))
      .toEqual(['out', 'rising', 'falling'])
  })

  it('drops an edge_detect trigger output for a direction that is off', async () => {
    const noRising = await mountGN('edge_detect', { on_rising: 'off' })
    await flushPromises()
    expect(noRising.findAll('.handle').filter(h => h.attributes('data-type') === 'source').map(h => h.attributes('data-id')))
      .toEqual(['out', 'falling'])

    const noFalling = await mountGN('edge_detect', { on_falling: 'off' })
    await flushPromises()
    expect(noFalling.findAll('.handle').filter(h => h.attributes('data-type') === 'source').map(h => h.attributes('data-id')))
      .toEqual(['out', 'rising'])
  })

  it('leaves an edge_detect with both directions off without any output', async () => {
    const w = await mountGN('edge_detect', { on_rising: 'off', on_falling: 'off' })
    await flushPromises()
    const sources = w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')
    expect(sources).toHaveLength(0)
    // The two inputs stay — the block still tracks its level.
    expect(w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')).toHaveLength(2)
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

  it('shows both edge values for edge_detect, localized, by default', async () => {
    const w = await mountGN('edge_detect')
    await flushPromises()
    expect(w.find('.gn-summary').text()).toBe('\u2191 Wahr  \u2193 Falsch')
  })

  it('shows a non-boolean edge_detect value verbatim', async () => {
    const w = await mountGN('edge_detect', { data_type: 'number', value_rising: '1', value_falling: '0' })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toBe('\u2191 1  \u2193 0')
  })

  it('shows an unrecognized boolean edge_detect value verbatim', async () => {
    const w = await mountGN('edge_detect', { value_rising: 'JA', value_falling: 'NEIN' })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toBe('\u2191 JA  \u2193 NEIN')
  })

  it('omits an edge_detect direction that is switched off', async () => {
    const rising = await mountGN('edge_detect', { on_falling: 'off', data_type: 'number', value_rising: '1' })
    await flushPromises()
    expect(rising.find('.gn-summary').text()).toBe('\u2191 1')

    const falling = await mountGN('edge_detect', { on_rising: 'off', data_type: 'number', value_falling: '0' })
    await flushPromises()
    expect(falling.find('.gn-summary').text()).toBe('\u2193 0')
  })

  it('shows an em dash for an edge_detect direction that only pulses', async () => {
    const w = await mountGN('edge_detect', { on_falling: 'trigger' })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toBe('\u2191 Wahr  \u2193 \u2014')
  })

  it('marks both edge_detect directions with an em dash when neither sends', async () => {
    const w = await mountGN('edge_detect', { on_rising: 'trigger', on_falling: 'trigger' })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toBe('\u2191 \u2014  \u2193 \u2014')
  })

  it('shows an empty edge_detect summary when both directions are off', async () => {
    const w = await mountGN('edge_detect', { on_rising: 'off', on_falling: 'off' })
    await flushPromises()
    expect(w.find('.gn-summary').exists()).toBe(false)
  })

  it('shows formula for math_formula', async () => {
    const w = await mountGN('math_formula', { formula: 'a * 2' })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('a * 2')
    expect(w.find('.gn-summary').attributes('title')).toBe('a * 2')
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

  it('shows value-sequence step summary and its control handles', async () => {
    const w = await mountGN('value_sequence', { run_mode: 'repeat_count', steps: [{}, {}] })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('2')
    expect(w.findAll('.handle').filter(h => h.attributes('data-type') === 'target')).toHaveLength(2)
    expect(w.findAll('.handle').filter(h => h.attributes('data-type') === 'source')).toHaveLength(0)
  })

  it('falls back to the configured value-sequence mode when it is not translated', async () => {
    const w = await mountGN('value_sequence', { run_mode: 'custom', steps: JSON.stringify([{}]) })
    await flushPromises()
    expect(w.find('.gn-summary').text()).toContain('custom')
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

  it('shows datetime outputs and configured or default format summaries', async () => {
    const configured = await mountGN('datetime', { custom_format: 'yyyy/MM/dd' })
    await flushPromises()
    expect(configured.find('.gn-title').text()).toBe('Datum/Zeit')
    expect(configured.find('.gn-summary').text()).toBe('yyyy/MM/dd')
    expect(configured.findAll('.handle').map(handle => handle.attributes('data-id'))).toEqual(['date', 'time', 'custom'])

    const defaults = await mountGN('datetime')
    await flushPromises()
    expect(defaults.find('.gn-summary').text()).toBe('EEEE, MMMM d, yyyy HH:mm:ss')
  })
})

describe('GenericNode — debug band', () => {
  it('shows the compact debug band when data._dbg is set', async () => {
    const w = await mountGN('and', { _dbg: 'true', _dbg_title: 'out=true' })
    await flushPromises()
    const band = w.find('[data-testid="debug-band"]')
    expect(band.text()).toBe('true')
    expect(band.attributes('title')).toBe('out=true')
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

  it('does not show negate buttons for merge — its inputs are values, not booleans', async () => {
    const w = await mountGN('merge')
    await flushPromises()
    expect(w.findAll('.gn-port-negate').length).toBe(0)
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
