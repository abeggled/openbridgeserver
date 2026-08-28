import { describe, it, expect } from 'vitest'
import { toBackendNumberText } from '@/utils/logicNumbers'

// Mirrors GraphExecutor._to_num — the editor and the block card both decide
// with this, so it must not drift from the backend.
describe('toBackendNumberText', () => {
  it.each(['0', '1', '-2', '1.5', '1e3', '1E-3', '007'])(
    'keeps %j, which float() parses and a number input already accepts',
    v => {
      expect(toBackendNumberText(v)).toBe(v)
    },
  )

  it.each(['', 'abc', 'true', '0x10', '0o7', '0b1', 'Infinity', 'NaN', '1,5', '1e309'])(
    'falls back to 0 for %j, which float() rejects or overflows',
    v => {
      expect(toBackendNumberText(v)).toBe('0')
    },
  )

  it.each([
    ['2.', '2'],
    ['+3', '3'],
    ['.5', '0.5'],
    ['+.5', '0.5'],
    ['-.5', '-0.5'],
    ['1.', '1'],
    ['2.e3', '2000'],
  ])('canonicalizes %j to %j, which float() accepts but the widget does not', (input, expected) => {
    // <input type="number"> rejects a trailing point, a leading plus and a
    // bare fraction, so an imported node would show a blank, invalid field for
    // a value the executor runs happily.
    expect(toBackendNumberText(input)).toBe(expected)
  })

  it('trims exactly what float() skips, not what JavaScript trim() strips', () => {
    // The two sets differ in both directions. trim() also removes U+FEFF,
    // which float() rejects — the panel showed 4 while the executor sent 0.0 —
    // and it leaves U+0085, which float() skips, so the panel showed 0 while
    // the executor sent the number.
    expect('\ufeff4\ufeff'.trim()).toBe('4')
    expect(toBackendNumberText('\ufeff4\ufeff')).toBe('0')
    expect('\u00854\u0085'.trim()).not.toBe('4')
    expect(toBackendNumberText('\u00854\u0085')).toBe('4')
  })

  it.each(['\t', '\n', '\v', '\f', '\r', ' ', '\u00a0', '\u1680', '\u2000', '\u200a', '\u2028', '\u2029', '\u202f', '\u205f', '\u3000'])(
    'skips %j around a number, as float() does',
    ws => {
      expect(toBackendNumberText(ws + '4' + ws)).toBe('4')
    },
  )

  it.each(['\u200b', '\u180e', '\ufeff'])('rejects %j, which float() does not skip', ws => {
    expect(toBackendNumberText(ws + '4' + ws)).toBe('0')
  })

  it('returns the canonical spelling so a number input can display it', () => {
    // float() ignores surrounding whitespace, but <input type="number"> cannot
    // display " 4 " and would render blank and invalid.
    expect(toBackendNumberText(' 4 ')).toBe('4')
    expect(toBackendNumberText('\t2\n')).toBe('2')
    expect(toBackendNumberText(' 1_0 ')).toBe('10')
  })

  it('accepts Python digit separators and strips them for display', () => {
    // float('1_000') is 1000.0. The raw spelling has to be stripped, because
    // a number input cannot display "1_000" and would render blank.
    expect(toBackendNumberText('1_000')).toBe('1000')
    expect(toBackendNumberText('1_000_000')).toBe('1000000')
    expect(toBackendNumberText('1_000.5')).toBe('1000.5')
    // Also drops the leading plus, which the widget rejects.
    expect(toBackendNumberText('+1_0')).toBe('10')
    expect(toBackendNumberText('1_0.5_5e1_0')).toBe('10.55e10')
    expect(toBackendNumberText('0_1')).toBe('01')
  })

  it.each(['_1', '1_', '1__0', '1_.5', '1._5', '1e_5', '_.5', '1._'])(
    'rejects %j, the separator placements float() also rejects',
    v => {
      expect(toBackendNumberText(v)).toBe('0')
    },
  )

  it('accepts Unicode decimal digits, as float() does', () => {
    // float('١٢٣') is 123.0; mixed scripts are allowed too.
    expect(toBackendNumberText('٣')).toBe('3')
    expect(toBackendNumberText('١٢٣')).toBe('123')
    expect(toBackendNumberText('１２３')).toBe('123')
    expect(toBackendNumberText('١٢٣.٥')).toBe('123.5')
    expect(toBackendNumberText('1٢3')).toBe('123')
    // Digits from a block that sits directly next to another Nd block.
    expect(toBackendNumberText('\u{1D7D9}')).toBe('1')
  })

  it.each(['½', 'Ⅴ', '²', '〇'])('rejects %j — No/Nl are not Nd, and float() raises', v => {
    expect(toBackendNumberText(v)).toBe('0')
  })

  it('treats null and undefined as the default, like a missing value', () => {
    expect(toBackendNumberText(null)).toBe('0')
    expect(toBackendNumberText(undefined)).toBe('0')
  })

  it('maps real booleans to 1/0, not to their string form', () => {
    // _to_num short-circuits on bool before float() ever sees it, so "true"
    // must not read as the non-numeric string it stringifies to.
    expect(toBackendNumberText(true)).toBe('1')
    expect(toBackendNumberText(false)).toBe('0')
  })

  it('rejects collections, which make float() raise TypeError', () => {
    // String([1]) is "1", but the backend sends 0.0.
    expect(toBackendNumberText([1])).toBe('0')
    expect(toBackendNumberText([])).toBe('0')
    expect(toBackendNumberText({ a: 1 })).toBe('0')
  })

  it('accepts real numbers, not only strings', () => {
    expect(toBackendNumberText(5)).toBe('5')
    expect(toBackendNumberText(-0.25)).toBe('-0.25')
    expect(toBackendNumberText(Infinity)).toBe('0')
  })

  it('honours an explicit fallback', () => {
    expect(toBackendNumberText('abc', '7')).toBe('7')
    expect(toBackendNumberText(null, '7')).toBe('7')
  })
})
