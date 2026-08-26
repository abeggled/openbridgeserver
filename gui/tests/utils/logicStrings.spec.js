import { describe, it, expect } from 'vitest'
import { toBackendStringText } from '@/utils/logicStrings'

// Mirrors GraphExecutor._coerce_typed_value for data_type "string". Every
// expectation below was taken from real CPython output for the JSON value on
// the left, so this suite is the contract against the backend.
describe('toBackendStringText', () => {
  it('maps a missing value to the empty string, not to "None"', () => {
    expect(toBackendStringText(null)).toBe('')
    expect(toBackendStringText(undefined)).toBe('')
  })

  it('returns a plain string unchanged', () => {
    expect(toBackendStringText('plain')).toBe('plain')
    expect(toBackendStringText('')).toBe('')
  })

  it('uses Python spelling for booleans', () => {
    expect(toBackendStringText(true)).toBe('True')
    expect(toBackendStringText(false)).toBe('False')
  })

  it('renders lists the way str() does, not the way JavaScript does', () => {
    // String([1]) is "1" in JavaScript; Python prints "[1]".
    expect(toBackendStringText([1])).toBe('[1]')
    expect(toBackendStringText([])).toBe('[]')
    expect(toBackendStringText(['a', 'b'])).toBe("['a', 'b']")
    expect(toBackendStringText([[1], [2]])).toBe('[[1], [2]]')
    expect(toBackendStringText([true, false, null])).toBe('[True, False, None]')
  })

  it('renders objects as dicts, not as "[object Object]"', () => {
    expect(toBackendStringText({ a: 1 })).toBe("{'a': 1}")
    expect(toBackendStringText({})).toBe('{}')
    expect(toBackendStringText({ b: 'v', a: 1 })).toBe("{'b': 'v', 'a': 1}")
    expect(toBackendStringText({ a: [1, 2] })).toBe("{'a': [1, 2]}")
    expect(toBackendStringText({ n: null })).toBe("{'n': None}")
    expect(toBackendStringText([{ a: [1, { b: 'c' }] }])).toBe("[{'a': [1, {'b': 'c'}]}]")
  })

  it('quotes nested strings the way repr() does', () => {
    // Python switches to double quotes when the string holds a single quote
    // and no double quote, and escapes otherwise.
    expect(toBackendStringText(["it's"])).toBe('["it\'s"]')
    expect(toBackendStringText(['say "hi"'])).toBe('[\'say "hi"\']')
    expect(toBackendStringText(['both \' and "'])).toBe('[\'both \\\' and "\']')
    expect(toBackendStringText([''])).toBe("['']")
  })

  it('escapes backslashes and control characters inside collections', () => {
    expect(toBackendStringText(['back\\slash'])).toBe("['back\\\\slash']")
    expect(toBackendStringText(['new\nline'])).toBe("['new\\nline']")
    expect(toBackendStringText(['tab\there'])).toBe("['tab\\there']")
    expect(toBackendStringText(['cr\rhere'])).toBe("['cr\\rhere']")
  })

  it('falls back to plain stringification for a non-JSON member', () => {
    // JSON.parse cannot produce a BigInt, but the fallback keeps the function
    // total — without it such a member would render as "undefined".
    expect(toBackendStringText([10n])).toBe('[10]')
  })

  it('pads a float exponent to two digits, as Python does', () => {
    expect(toBackendStringText(1e-7)).toBe('1e-07')
    expect(toBackendStringText([1e-7])).toBe('[1e-07]')
    expect(toBackendStringText(1e21)).toBe('1e+21')
    expect(toBackendStringText(0)).toBe('0')
    expect(toBackendStringText(1.5)).toBe('1.5')
  })
})
