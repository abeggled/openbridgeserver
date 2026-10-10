import { describe, it, expect } from 'vitest'
import { binaryStatsInputCount } from '@/utils/binaryStatsInputCount'

describe('binaryStatsInputCount', () => {
  it('rounds half up and clamps to 2–30', () => {
    expect(binaryStatsInputCount(2.5)).toBe(3)
    expect(binaryStatsInputCount('7')).toBe(7)
    expect(binaryStatsInputCount(1)).toBe(2)
    expect(binaryStatsInputCount(99)).toBe(30)
  })

  it('falls back to the default of 2 for empty, zero or non-finite values', () => {
    for (const value of [undefined, null, '', 'abc', 0, NaN, Infinity, [3], { a: 3 }]) {
      expect(binaryStatsInputCount(value)).toBe(2)
    }
  })

  it('accepts plain decimal strings only, like the executor', () => {
    for (const value of ['0x10', '0b11', '0o7', '1_0']) {
      expect(binaryStatsInputCount(value)).toBe(2)
    }
    expect(binaryStatsInputCount(' 12 ')).toBe(12)
    expect(binaryStatsInputCount('7.5')).toBe(8)
    expect(binaryStatsInputCount('1e1')).toBe(10)
    expect(binaryStatsInputCount(true)).toBe(2)
    for (const value of ['\x1c5', '\ufeff5', '\x855']) {
      expect(binaryStatsInputCount(value)).toBe(2)
    }
  })
})
