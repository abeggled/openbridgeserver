import { describe, it, expect } from 'vitest'
import { hemsOutputLabels } from '@/utils/logicHemsOutputs'

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
