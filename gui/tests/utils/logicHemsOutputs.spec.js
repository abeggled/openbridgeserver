import { describe, it, expect } from 'vitest'
import { HEMS_DIAGNOSTICS, hemsAgeCheckActive, hemsConsumerName, hemsConsumers, hemsFlag, hemsGridMode, hemsInputPorts, hemsOutputLabels } from '@/utils/logicHemsOutputs'

const t = (key, params) => (params ? `${key}:${JSON.stringify(params)}` : key)
const node = data => ({ type: 'hems_surplus', data })

describe('hemsOutputLabels', () => {
  it('ignores other block types', () => {
    expect(hemsOutputLabels({ type: 'and', data: {} }, t)).toEqual({})
    expect(hemsOutputLabels(undefined, t)).toEqual({})
  })

  it('labels consumer outputs with the consumer name and a status suffix', () => {
    const labels = hemsOutputLabels(node({ consumers: [{ id: 'k1', name: ' Boiler ' }] }), t)
    expect(labels.c_k1).toBe('Boiler')
    expect(labels.c_k1_status).toBe('Boiler: logic.portLabels.hems.status')
  })

  it('falls back to a numbered name and reads JSON-string storage', () => {
    const labels = hemsOutputLabels(node({ consumers: JSON.stringify([{ id: 'a' }, { id: 'b', name: '' }]) }), t)
    expect(labels.c_a).toBe('logic.portLabels.hems.consumer:{"n":1}')
    expect(labels.c_b).toBe('logic.portLabels.hems.consumer:{"n":2}')
  })

  it('always names the diagnostic outputs, even without consumers or with garbage storage', () => {
    for (const consumers of [undefined, 'nope', '{"a":1}', [null, [], 'x']]) {
      const labels = hemsOutputLabels(node({ consumers }), t)
      expect(labels.surplus).toBe('logic.portLabels.hems.surplus')
      expect(Object.keys(labels)).toHaveLength(7)
    }
    expect(hemsOutputLabels({ type: 'hems_surplus' }, t).status).toBe('logic.portLabels.hems.status')
    expect(hemsOutputLabels(node({ consumers: '' }), t).warning).toBe('logic.portLabels.hems.warning')
  })
})

describe('hemsConsumers', () => {
  it('normalises ids and modes exactly like the engine', () => {
    const list = hemsConsumers([{ id: 'a-b', mode: 'onoff' }, { id: 'ab' }, null, { id: '' }, { id: 'ab', mode: 'trigger' }, 'x'])
    expect(list.map(c => c.id)).toEqual(['ab', 'ab_', 'n4', 'ab__'])
    expect(list.map(c => c.mode)).toEqual(['onoff', 'percent', 'percent', 'trigger'])
  })

  it('spells non-text ids like the engine (true, 2 not 2.0)', () => {
    expect(hemsConsumers([{ id: 2.0 }, { id: 2.5 }, { id: true }, { id: false }, { id: 0 }]).map(c => c.id)).toEqual(['2', '25', 'true', 'n4', 'n5'])
    const odd = hemsConsumers([{ id: 1e21 }, { id: 1e-7 }, { id: Infinity }, { id: { x: 1 } }, { id: [1, 2] }]).map(c => c.id)
    expect(odd).toEqual(['1e21', '1e7', 'n3', 'n4', 'n5'])
    // the same spellings tests/unit/test_logic_hems_surplus_engine.py expects from the engine
    const big = hemsConsumers([{ id: 12345678901234567890 }, { id: 9007199254740991 }, { id: 0.000001 }, { id: 123.456 }]).map(c => c.id)
    expect(big).toEqual(['12345678901234567000', '9007199254740991', '0000001', '123456'])
  })

  it('moves an id ending in a port suffix off another consumer\'s ports, like the engine', () => {
    const ids = hemsConsumers([{ id: 'a' }, { id: 'a_status' }, { id: 'a_max' }, { id: 'x_power' }, { id: 'r_reset' }, { id: 'a_maxi' }]).map(c => c.id)
    expect(ids).toEqual(['a', 'a_status_', 'a_max_', 'x_power_', 'r_reset_', 'a_maxi'])
  })

  it('keeps the other fields and reads JSON storage', () => {
    expect(hemsConsumers(JSON.stringify([{ id: 'k', name: 'Boiler', power_w: 2000 }]))).toEqual([
      { id: 'k', name: 'Boiler', power_w: 2000, mode: 'percent' },
    ])
    expect(hemsConsumers('{')).toEqual([])
    expect(hemsConsumers('')).toEqual([])
    expect(hemsConsumers({ a: 1 })).toEqual([])
  })

  it('labels the outputs under the normalised id', () => {
    const labels = hemsOutputLabels(node({ consumers: [{ id: 'a-b', name: 'Pool' }] }), t)
    expect(labels.c_ab).toBe('Pool')
    expect(labels['c_a-b']).toBeUndefined()
  })
})

describe('hemsFlag', () => {
  it('treats unset as yes and strings like the engine', () => {
    expect(hemsFlag(undefined)).toBe(true)
    expect(hemsFlag(null)).toBe(true)
    for (const yes of [true, 1, '1', 'true', ' Yes ', 'on', 'OK', 'running', [0]]) expect(hemsFlag(yes)).toBe(true)
    for (const no of [false, 0, '', '0', 'false', 'no', ' Off ', [], {}]) expect(hemsFlag(no)).toBe(false)
  })
})

describe('hemsConsumerName', () => {
  it('trims the name and numbers a blank one', () => {
    expect(hemsConsumerName({ name: ' Pool ' }, 0, t)).toBe('Pool')
    expect(hemsConsumerName({ name: '   ' }, 2, t)).toBe('logic.portLabels.hems.consumer:{"n":3}')
    expect(hemsConsumerName({}, 0, t)).toBe('logic.portLabels.hems.consumer:{"n":1}')
  })

  it('exports the diagnostic outputs once for card and debug tab', () => {
    expect(HEMS_DIAGNOSTICS).toHaveLength(7)
  })
})

describe('hemsAgeCheckActive', () => {
  it('reads max_age_s like the engine', () => {
    for (const on of [60, '60', '1_000', ' 5 ']) expect(hemsAgeCheckActive(on)).toBe(true)
    for (const off of [0, '0', '', null, undefined, true, false, '0x3c', 'Infinity', -5, [60]]) expect(hemsAgeCheckActive(off)).toBe(false)
  })
})

describe('enum settings', () => {
  it('are read case- and whitespace-insensitively, like the engine', () => {
    const [c] = hemsConsumers([{ id: 'a', mode: ' Trigger ', power_source: 'Input' }])
    expect([c.mode, c.power_source]).toEqual(['trigger', 'input'])
    expect(hemsConsumers([{ id: 'a', power_source: 'fixed' }])[0].power_source).toBe('fixed')
    expect(hemsGridMode({ grid_mode: 'Split' })).toBe('split')
    expect(hemsGridMode({ grid_mode: 3 })).toBe('bidirectional')
    expect(hemsGridMode(undefined)).toBe('bidirectional')
  })
})

describe('hemsInputPorts', () => {
  it('lists the meter, interlock and consumer inputs in card order', () => {
    const ids = data => hemsInputPorts(data, t).map(p => p.id)
    expect(ids({})).toEqual(['grid', 'enable', 'target', 'plant_ok'])
    expect(ids(undefined)).toEqual(['grid', 'enable', 'target', 'plant_ok'])
    expect(ids({ grid_mode: 'feed_in_only', max_age_s: 30 })).toEqual(['feed_in', 'feed_in_changed', 'enable', 'target', 'plant_ok'])
    expect(ids({ consumers: [{ id: 'a', mode: 'onoff' }, { id: 'b', mode: 'percent', power_source: 'input' }, { id: 'w', mode: 'trigger' }] })).toEqual([
      'grid', 'enable', 'target', 'plant_ok', 'c_a_power', 'c_b_power', 'c_b_max_power', 'c_w_reset',
    ])
    expect(hemsInputPorts({ consumers: [{ id: 'a', name: 'Boiler', mode: 'onoff' }] }, t).at(-1).label).toBe('Boiler: logic.portLabels.hems.measuredPower')
  })
})
