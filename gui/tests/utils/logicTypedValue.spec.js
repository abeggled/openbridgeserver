import { describe, it, expect } from 'vitest'
import { coercedValueText } from '@/utils/logicTypedValue'

// The one rule the configuration panel and the block card share. Every
// expectation is what GraphExecutor._coerce_typed_value produces for the same
// input, so this suite is the contract against the backend.
describe('coercedValueText', () => {
  it('applies the boolean rule, including the backend spellings', () => {
    expect(coercedValueText('off', 'bool')).toBe('false')
    expect(coercedValueText('False', 'bool')).toBe('false')
    expect(coercedValueText(null, 'bool')).toBe('false')
    expect(coercedValueText([0], 'bool')).toBe('true')
    expect(coercedValueText('AN', 'bool')).toBe('true')
  })

  it('applies the numeric rule', () => {
    expect(coercedValueText(true, 'number')).toBe('1')
    expect(coercedValueText([1], 'number')).toBe('0')
    expect(coercedValueText(' 4 ', 'number')).toBe('4')
    expect(coercedValueText(null, 'number')).toBe('0')
  })

  it('applies the string rule', () => {
    expect(coercedValueText([1], 'string')).toBe('[1]')
    expect(coercedValueText({ a: 1 }, 'string')).toBe("{'a': 1}")
    expect(coercedValueText(true, 'string')).toBe('True')
    expect(coercedValueText(null, 'string')).toBe('')
  })

  describe('a data_type the backend does not coerce', () => {
    // _coerce_typed_value returns the value untouched, so a collection stays a
    // collection. String() would scalarize it and misstate what is sent.
    it.each([
      [[1], '[1]'],
      [{ a: 2 }, '{"a":2}'],
      [[], '[]'],
      [{}, '{}'],
      ['raw', 'raw'],
      [7, '7'],
      [null, ''],
      [undefined, ''],
    ])('renders %j as %j', (value, expected) => {
      expect(coercedValueText(value, 'auto')).toBe(expected)
    })
  })
})
