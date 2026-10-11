import { describe, it, expect } from 'vitest'
import { HEMS_DIAGNOSTICS, hemsConsumerName, hemsConsumers, hemsFlag, hemsOutputLabels } from '@/utils/logicHemsOutputs'

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
