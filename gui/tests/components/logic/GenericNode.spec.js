import { describe, it, expect, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import GenericNode from '@/components/logic/nodes/GenericNode.vue'

vi.mock('@vue-flow/core', () => ({
  Handle: {
    props: ['id', 'type', 'position'],
    template: '<span class="handle" :data-id="id" :data-type="type" :data-position="position" />',
  },
  Position: { Left: 'left', Right: 'right', Top: 'top', Bottom: 'bottom' },
  useVueFlow: () => ({
    updateNodeData: vi.fn(),
    removeNodes: vi.fn(),
  }),
}))

function mountNode(data) {
  return mount(GenericNode, {
    props: {
      id: 'node-1',
      type: 'api_client',
      data,
    },
  })
}

describe('GenericNode memory rendering', () => {
  it('renders memory input, reset, and output ports', () => {
    const wrapper = mount(GenericNode, {
      props: {
        id: 'mem',
        type: 'memory',
        data: { initial_value: 'false', data_type: 'bool' },
      },
    })

    expect(wrapper.text()).toContain('Speicher')
    expect(wrapper.text()).toContain('Eingang')
    expect(wrapper.text()).toContain('Reset')
    expect(wrapper.text()).toContain('Ausgang')
    expect(wrapper.find('[data-id="reset"][data-type="target"]').exists()).toBe(true)
    expect(wrapper.find('[data-id="out"][data-type="source"]').exists()).toBe(true)
  })
})

describe('GenericNode debug band', () => {
  it('renders compact debug data with the full value as its tooltip', () => {
    const wrapper = mountNode({ _dbg: 'short response', _dbg_title: 'full response body' })

    const band = wrapper.find('[data-testid="debug-band"]')
    expect(band.text()).toBe('short response')
    expect(band.attributes('title')).toBe('full response body')
  })
})

describe('GenericNode string_replace rendering', () => {
  function mountReplace(data) {
    return mount(GenericNode, { props: { id: 'rep', type: 'string_replace', data } })
  }

  it('renders the text input, the result output and a rule summary', () => {
    const wrapper = mountReplace({
      rules: JSON.stringify([
        { search: 'a', replace: 'b', mode: 'plain' },
        { search: 'c', replace: 'd', mode: 'plain' },
      ]),
    })

    expect(wrapper.text()).toContain('String Suchen/Ersetzen')
    expect(wrapper.find('[data-id="text"][data-type="target"]').exists()).toBe(true)
    expect(wrapper.find('[data-id="result"][data-type="source"]').exists()).toBe(true)
    expect(wrapper.text()).toContain('2 Regeln · Plain')
  })

  it('summarises a pure RegEx rule list as RegEx', () => {
    const wrapper = mountReplace({ rules: [{ search: '\\d', replace: '#', mode: 'regex' }] })

    expect(wrapper.text()).toContain('1 Regeln · RegEx')
  })

  it('normalises the rule mode the same way the executor does', () => {
    const wrapper = mountReplace({ rules: [{ search: 'a', mode: ' REGEX ' }, { search: 'b', mode: 'regex' }] })

    expect(wrapper.text()).toContain('2 Regeln · RegEx')
  })

  it('treats a rule without a mode as a plain rule', () => {
    const wrapper = mountReplace({ rules: [{ search: 'a' }, { search: 'b', mode: 'plain' }] })

    expect(wrapper.text()).toContain('2 Regeln · Plain')
  })

  it('drops the mode from the summary when the rules mix both modes', () => {
    const wrapper = mountReplace({
      rules: [{ search: 'a', mode: 'plain' }, { search: 'b', mode: 'regex' }],
    })

    expect(wrapper.text()).toContain('2 Regeln')
    expect(wrapper.text()).not.toContain('Plain')
    expect(wrapper.text()).not.toContain('RegEx')
  })

  it('summarises an unparsable rule list as no rules', () => {
    const wrapper = mountReplace({ rules: '{' })

    expect(wrapper.text()).toContain('0 Regeln')
  })
})

describe('GenericNode binary_stats rendering', () => {
  function mountStats(data) {
    return mount(GenericNode, { props: { id: 'stats', type: 'binary_stats', data } })
  }

  it('generates in1..inN inputs from input_count and renders all seven outputs', () => {
    const wrapper = mountStats({ input_count: 3 })

    expect(wrapper.text()).toContain('Binärstatistik')
    for (const id of ['in1', 'in2', 'in3']) {
      expect(wrapper.find(`[data-id="${id}"][data-type="target"]`).exists()).toBe(true)
    }
    expect(wrapper.find('[data-id="in4"]').exists()).toBe(false)
    for (const id of ['count_true', 'count_false', 'majority_true', 'total', 'percent_true', 'tie', 'threshold_reached']) {
      expect(wrapper.find(`[data-id="${id}"][data-type="source"]`).exists()).toBe(true)
    }
    expect(wrapper.text()).toContain('3 Eingänge')
  })

  it('defaults to two inputs without a summary and clamps to 30', () => {
    const two = mountStats({})
    expect(two.findAll('[data-type="target"]')).toHaveLength(2)
    expect(two.text()).not.toContain('Eingänge')

    const many = mountStats({ input_count: 99 })
    expect(many.findAll('[data-type="target"]')).toHaveLength(30)
  })

  it('offers per-input negation but no output negation', () => {
    const wrapper = mountStats({ input_count: 2, negate_in1: true })

    const buttons = wrapper.findAll('button.gn-port-negate')
    expect(buttons).toHaveLength(2)
    expect(buttons[0].text()).toBe('¬IN 1')
    expect(buttons[1].text()).toBe('IN 2')
    expect(wrapper.find('.gn-port-negate--right').exists()).toBe(false)
  })
})
