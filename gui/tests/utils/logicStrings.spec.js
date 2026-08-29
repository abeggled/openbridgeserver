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

  it('escapes backslashes and the named control characters', () => {
    expect(toBackendStringText(['back\\slash'])).toBe("['back\\\\slash']")
    expect(toBackendStringText(['new\nline'])).toBe("['new\\nline']")
    expect(toBackendStringText(['tab\there'])).toBe("['tab\\there']")
    expect(toBackendStringText(['cr\rhere'])).toBe("['cr\\rhere']")
  })

  it('escapes every other non-printable by code point width', () => {
    // repr() escapes everything str.isprintable() rejects, not just the three
    // that have a named escape.
    expect(toBackendStringText(['\b'])).toBe("['\\x08']")
    expect(toBackendStringText(['\x00'])).toBe("['\\x00']")
    expect(toBackendStringText(['\x0b'])).toBe("['\\x0b']")
    expect(toBackendStringText(['\x0c'])).toBe("['\\x0c']")
    expect(toBackendStringText(['\x1f'])).toBe("['\\x1f']")
    expect(toBackendStringText(['\x7f'])).toBe("['\\x7f']")
    expect(toBackendStringText(['\x85'])).toBe("['\\x85']")
    expect(toBackendStringText(['\xa0'])).toBe("['\\xa0']")
    expect(toBackendStringText(['\u200b'])).toBe("['\\u200b']")
    expect(toBackendStringText(['\u{10ffff}'])).toBe("['\\U0010ffff']")
  })

  it('leaves printable non-ASCII alone, as repr() does', () => {
    expect(toBackendStringText(['ä'])).toBe("['ä']")
    expect(toBackendStringText(['日本'])).toBe("['日本']")
    expect(toBackendStringText(['\u{1f600}'])).toBe("['\u{1f600}']")
    // The ASCII space is the one printable character in category Zs.
    expect(toBackendStringText(['a b'])).toBe("['a b']")
  })

  it('switches to scientific notation on Python float boundaries', () => {
    // Python uses a fixed range of 1e-4 <= |v| < 1e16, wider on the small end
    // and narrower on the large end than JavaScript's.
    expect(toBackendStringText(0.0001)).toBe('0.0001')
    expect(toBackendStringText(0.00001)).toBe('1e-05')
    expect(toBackendStringText(1e-7)).toBe('1e-07')
    expect(toBackendStringText(5e-324)).toBe('5e-324')
    expect(toBackendStringText(1.7976931348623157e308)).toBe('1.7976931348623157e+308')
  })

  it('keeps negative zero, whose sign JSON.stringify drops', () => {
    // JSON.parse('-0.0') is -0, but JSON.stringify(-0) is "0", so the token
    // rule cannot see the sign. Python prints the float as -0.0.
    expect(Object.is(JSON.parse('-0.0'), -0)).toBe(true)
    expect(toBackendStringText(-0)).toBe('-0.0')
    expect(toBackendStringText([-0])).toBe('[-0.0]')
    expect(toBackendStringText(0)).toBe('0')
  })

  it('reads int vs float from the JSON token the value travels as', () => {
    // The browser writes 1e20 as an integer token, which Python parses as int
    // and prints in full; 1e21 it writes as 1e+21, which Python parses as a
    // float. Re-serializing recovers that distinction.
    expect(JSON.stringify(1e20)).toBe('100000000000000000000')
    expect(toBackendStringText(1e20)).toBe('100000000000000000000')
    expect(JSON.stringify(1e21)).toBe('1e+21')
    expect(toBackendStringText(1e21)).toBe('1e+21')
    expect(toBackendStringText(1.5e20)).toBe('150000000000000000000')
  })

  it('keeps the int spelling for exactly representable integers', () => {
    // The int reading is the common one, and str(int) never uses an exponent.
    expect(toBackendStringText(0)).toBe('0')
    expect(toBackendStringText(-42)).toBe('-42')
    expect(toBackendStringText(Number.MAX_SAFE_INTEGER)).toBe('9007199254740991')
    expect(toBackendStringText([1, 2])).toBe('[1, 2]')
    // Non-integral values are unambiguously floats.
    expect(toBackendStringText(-0.25)).toBe('-0.25')
    expect(toBackendStringText([0.1])).toBe('[0.1]')
  })

  it('uses Python spelling for the non-finite floats', () => {
    // JSON cannot carry these, but the exported helper takes any JS number.
    expect(toBackendStringText(Infinity)).toBe('inf')
    expect(toBackendStringText(-Infinity)).toBe('-inf')
    expect(toBackendStringText(NaN)).toBe('nan')
    expect(toBackendStringText([Infinity, NaN])).toBe('[inf, nan]')
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
