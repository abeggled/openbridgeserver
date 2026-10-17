import { describe, it, expect } from 'vitest'
import { toBackendNumberText } from '@/utils/logicNumbers'
import { isBackendFalse } from '@/utils/logicBooleans'
import fixture from './pythonWhitespace.fixture.json'

// JavaScript's trim() is not Python's whitespace, and the two backend paths do
// not agree with each other either: str.strip(), behind GraphExecutor._to_bool,
// removes U+001C-U+001F, which float(), behind _to_num, rejects. The fixture
// records what CPython actually does (regenerate with
// tools/gen-python-whitespace-fixture.py); these suites then check every code
// point in the scanned range against it, so the helpers cannot drift back to
// JavaScript's own notion of whitespace.
describe('whitespace parity with CPython', () => {
  const floatSkips = new Set(fixture.floatSkips)
  const stripRemoves = new Set(fixture.stripRemoves)
  const codePoints = [
    ...Array.from({ length: fixture.scannedTo }, (_, cp) => cp),
    ...fixture.extraScanned,
  ].filter(cp => {
    const ch = String.fromCodePoint(cp)
    // Same exclusion the generator applies: Python's str.isdigit() is
    // Unicode-aware, so a digit from any script would make the wrapped value
    // a different number rather than testing whitespace.
    return !/\p{Nd}/u.test(ch) && !'+-.eE_'.includes(ch)
  })

  it('scans the whole recorded range', () => {
    expect(codePoints.length).toBeGreaterThan(12000)
    expect(floatSkips.size).toBe(25)
    // The four ASCII separators str.strip() removes but float() rejects.
    expect(stripRemoves.size).toBe(floatSkips.size + 4)
  })

  it('skips exactly what float() skips around a number', () => {
    const wrong = codePoints.filter(cp => {
      const ch = String.fromCodePoint(cp)
      return toBackendNumberText(ch + '4' + ch) !== (floatSkips.has(cp) ? '4' : '0')
    })
    expect(wrong.map(cp => 'U+' + cp.toString(16))).toEqual([])
  })

  it('strips exactly what str.strip() removes around a boolean', () => {
    const wrong = codePoints.filter(cp => {
      const ch = String.fromCodePoint(cp)
      return isBackendFalse(ch + 'false' + ch) !== stripRemoves.has(cp)
    })
    expect(wrong.map(cp => 'U+' + cp.toString(16))).toEqual([])
  })

  it('records the two divergences from JavaScript that caused the defects', () => {
    // trim() strips the BOM, which Python does not; it leaves NEL, which
    // Python strips. Asserted here so the premise is visible, not implied.
    const BOM = 0xfeff
    const NEL = 0x0085
    expect(String.fromCodePoint(BOM) + 'x' + String.fromCodePoint(BOM)).toSatisfy(s => s.trim() === 'x')
    expect(floatSkips.has(BOM)).toBe(false)
    expect(stripRemoves.has(BOM)).toBe(false)
    expect(String.fromCodePoint(NEL) + 'x' + String.fromCodePoint(NEL)).toSatisfy(s => s.trim() !== 'x')
    expect(floatSkips.has(NEL)).toBe(true)
    expect(stripRemoves.has(NEL)).toBe(true)
  })
})
