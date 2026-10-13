// Parity with the backend (#1260): tests/fixtures/dpt-keep-parity.json is generated
// from Python by tools/gen_dpt_keep_parity.py and checked for freshness by
// tests/unit/test_dpt_keep_parity_table.py.
import { describe, it, expect } from 'vitest'
import table from '../fixtures/dpt-keep-parity.json'
import { keepsStoredSubtype } from '@/utils/dpt'

describe('keepsStoredSubtype parity with keeps_stored_subtype', () => {
  it('has rows with both outcomes', () => {
    expect(table.rows.some(row => row.keep)).toBe(true)
    expect(table.rows.some(row => !row.keep)).toBe(true)
  })

  it.each(table.rows.map(row => [JSON.stringify(row.new), JSON.stringify(row.stored), row]))(
    'new %s over stored %s decides like the backend',
    (_new, _stored, row) => {
      expect(keepsStoredSubtype(row.new, row.stored)).toBe(row.keep)
    },
  )
})
