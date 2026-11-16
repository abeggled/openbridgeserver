// Parity contract with the backend's format_ga (#1296): the table is generated
// from Python by tools/gen_ga_format_parity.py and checked for freshness by
// tests/unit/test_ga_format_parity_table.py.
import { describe, it, expect } from 'vitest'
import table from '../fixtures/ga-format-parity.json'
import { formatGa, GROUP_ADDRESS_STYLES } from '@/utils/groupAddress'

describe('formatGa parity with format_ga', () => {
  it('knows the same styles', () => {
    expect(GROUP_ADDRESS_STYLES).toEqual(table.styles)
  })

  it.each(table.rows.filter(row => row.expected).map(row => [JSON.stringify(row.input), row]))(
    'formats %s like the backend',
    (_label, row) => {
      for (const style of table.styles) expect(formatGa(row.input, style)).toBe(row.expected[style])
    },
  )

  it.each(table.rows.filter(row => row.invalid).map(row => [JSON.stringify(row.input), row]))(
    'returns %s unchanged where the backend rejects it',
    (_label, row) => {
      for (const style of table.styles) expect(formatGa(row.input, style)).toStrictEqual(row.input)
    },
  )

  it('rejects an unknown style like format_ga', () => {
    expect(() => formatGa('1/0/234', 'FourLevel')).toThrow('FourLevel')
    expect(() => formatGa('1/0/234', undefined)).toThrow()
  })
})
