import { describe, it, expect } from 'vitest'
import { isBackendFalse, isPythonTruthy } from '@/utils/logicBooleans'

// Mirrors GraphExecutor._to_bool — the editor and the block card both decide
// with this, so it must not drift from the backend.
describe('isBackendFalse', () => {
  it.each(['0', 'false', 'FALSE', ' False ', 'no', 'off', 'OFF', ''])('treats %j as false', v => {
    expect(isBackendFalse(v)).toBe(true)
  })

  it.each(['1', 'true', 'True', 'yes', 'on', 'AN', 'JA', '0.0', 'null'])('treats %j as true', v => {
    expect(isBackendFalse(v)).toBe(false)
  })

  it('treats null and undefined as false, like a missing value', () => {
    expect(isBackendFalse(null)).toBe(true)
    expect(isBackendFalse(undefined)).toBe(true)
  })

  it('follows Python bool() for collections, not their string form', () => {
    // bool([0]) is True in Python even though String([0]) is "0".
    expect(isBackendFalse([0])).toBe(false)
    expect(isBackendFalse([])).toBe(true)
    expect(isBackendFalse({ a: 1 })).toBe(false)
    expect(isBackendFalse({})).toBe(true)
  })

  it('accepts real booleans and numbers, not only strings', () => {
    expect(isBackendFalse(false)).toBe(true)
    expect(isBackendFalse(true)).toBe(false)
    expect(isBackendFalse(0)).toBe(true)
    expect(isBackendFalse(1)).toBe(false)
  })
})

// Mirrors Python's own bool() for the schema settings whose backend consumer is
// a plain `if d.get(...)` — Gate's negate_*, the API client's verify_ssl —
// rather than GraphExecutor._to_bool.
describe('isPythonTruthy', () => {
  it.each([[[]], [{}], [''], [0], [false], [null], [undefined], [NaN]])('treats %j as false', v => {
    expect(isPythonTruthy(v)).toBe(false)
  })

  it.each([[[0]], [{ a: 0 }], ['false'], ['0'], [1], [true], [-1], [0.5]])('treats %j as true', v => {
    expect(isPythonTruthy(v)).toBe(true)
  })

  it('disagrees with JavaScript truthiness exactly on empty collections', () => {
    // The whole reason the helper exists: `!![]` and `!!{}` are true.
    expect(!![]).toBe(true)
    expect(!!{}).toBe(true)
    expect(isPythonTruthy([])).toBe(false)
    expect(isPythonTruthy({})).toBe(false)
  })
})
