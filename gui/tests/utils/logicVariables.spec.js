import { describe, it, expect } from 'vitest'
import {
  LOGIC_DATE_VARIABLES,
  LOGIC_STANDARD_VARIABLES,
  configuredObsSlots,
  translateVariableIssue,
  translateVariableErrors,
  normaliseObjectVariables,
  unresolvedVariables,
  variableToken,
} from '@/utils/logicVariables'

describe('logicVariables', () => {
  it('lists the documented variables', () => {
    expect(LOGIC_DATE_VARIABLES).toContain('HH')
    expect(LOGIC_DATE_VARIABLES).toContain('MMMM')
    expect(LOGIC_STANDARD_VARIABLES).toEqual(['DATE', 'TIME', 'TS'])
    expect(variableToken('H')).toBe('###H###')
  })

  it('reports only unknown names and unconfigured OBS slots', () => {
    expect(unresolvedVariables('[###H###].a.###mm###', [])).toEqual([])
    expect(unresolvedVariables('###FOO###/###FOO###/###OBS1###/###OBS2###', [1])).toEqual(['FOO', 'OBS2'])
    expect(unresolvedVariables('###h###')).toEqual(['h'])
    expect(unresolvedVariables(undefined)).toEqual([])
  })

  it('collects configured OBS slots', () => {
    expect(configuredObsSlots(undefined)).toEqual([])
    expect(configuredObsSlots([
      { slot: 2, datapoint_id: 'a' },
      { slot: 3, datapoint_id: '' },
      { datapoint_id: 'b' },
      { slot: 'x', datapoint_id: 'c' },
    ])).toEqual([2, 3, 4])
  })

  it('normalises variables from arrays and JSON strings', () => {
    expect(normaliseObjectVariables('not json')).toEqual([])
    expect(normaliseObjectVariables({})).toEqual([])
    expect(normaliseObjectVariables('[{"slot":"5","datapoint_id":"a","datapoint_name":"A"},{}]')).toEqual([
      { slot: 5, datapoint_id: 'a', datapoint_name: 'A' },
      { slot: 2, datapoint_id: '', datapoint_name: '' },
    ])
  })
})

describe('translateVariableIssue', () => {
  const t = (key, params) => `${key}|${JSON.stringify(params)}`
  it.each([
    ['unknown variable ###FOO###', 'unknownVariable', { name: '###FOO###' }],
    ["path 'a.b' not found", 'pathNotFound', { path: 'a.b' }],
    ["invalid XPath 'x[': bad", 'invalidXPath', { path: 'x[', error: 'bad' }],
    ['variable OBS2 is not configured', 'notConfigured', { name: 'OBS2' }],
    ['variable OBS1 object Lamp is not available', 'notAvailable', { name: 'OBS1', object: 'Lamp' }],
    ['variable OBS1 references an invalid object', 'invalidObject', { name: 'OBS1' }],
    ['variable OBS1 object Lamp has no value', 'noValue', { name: 'OBS1', object: 'Lamp' }],
    ['variable value is empty', 'emptyValue', {}],
  ])('maps %s', (msg, key, params) => {
    expect(translateVariableIssue(msg, t)).toBe(`logic.variables.issues.${key}|${JSON.stringify(params)}`)
  })
  it('keeps unknown messages verbatim', () => {
    expect(translateVariableIssue('something else', t)).toBe('something else')
  })
})

describe('translateVariableErrors', () => {
  const t = (key, params) => `${key}|${JSON.stringify(params)}`
  it('translates prefixed manager errors and joined lists', () => {
    expect(translateVariableErrors('Variable variable OBS1 object Lamp has no value; iCal variable OBS2 is not configured', t)).toBe(
      'logic.variables.issues.noValue|{"name":"OBS1","object":"Lamp"}; logic.variables.issues.notConfigured|{"name":"OBS2"}',
    )
    expect(translateVariableErrors('API client variable value is empty', t)).toBe('logic.variables.issues.emptyValue|{}')
  })
  it('keeps a "; " inside an object name within its issue', () => {
    expect(translateVariableErrors('Variable variable OBS1 object Room; Lamp has no value', t)).toBe(
      'logic.variables.issues.noValue|{"name":"OBS1","object":"Room; Lamp"}',
    )
    expect(
      translateVariableErrors('Variable variable OBS1 object Room; Lamp has no value; iCal variable OBS2 is not configured', t),
    ).toBe('logic.variables.issues.noValue|{"name":"OBS1","object":"Room; Lamp"}; logic.variables.issues.notConfigured|{"name":"OBS2"}')
  })
})
