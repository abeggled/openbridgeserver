import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  timerValueAsBool,
  timerValueHintKey,
  timerValueInputKind,
  timerValueStep,
  validateTimerValue,
} from './timerValue'

describe('timerValueInputKind', () => {
  it.each([
    ['BOOLEAN', 'boolean'],
    ['INTEGER', 'integer'],
    ['FLOAT', 'float'],
    ['DATE', 'date'],
    ['TIME', 'time'],
    ['DATETIME', 'datetime'],
    ['STRING', 'text'],
    ['UNKNOWN', 'text'],
  ])('maps %s to %s', (dataType, kind) => {
    expect(timerValueInputKind(dataType)).toBe(kind)
  })

  it('is case insensitive', () => {
    expect(timerValueInputKind('float')).toBe('float')
  })

  it('falls back to text for null/empty', () => {
    expect(timerValueInputKind(null)).toBe('text')
    expect(timerValueInputKind(undefined)).toBe('text')
    expect(timerValueInputKind('')).toBe('text')
  })
})

describe('timerValueHintKey', () => {
  it('builds a per-kind key', () => {
    expect(timerValueHintKey('DATE')).toBe('zst.switchValueHint_date')
    expect(timerValueHintKey('STRING')).toBe('zst.switchValueHint_text')
  })
})

describe('timerValueStep', () => {
  it('uses step=1 for integers and any otherwise', () => {
    expect(timerValueStep('INTEGER')).toBe('1')
    expect(timerValueStep('FLOAT')).toBe('any')
  })
})

describe('timerValueAsBool', () => {
  it.each(['true', '1', 'on', 'ein', 'YES', ' ja '])('reads %s as true', (raw) => {
    expect(timerValueAsBool(raw)).toBe(true)
  })

  it.each(['false', '0', 'off', 'aus', '', '50'])('reads %s as false', (raw) => {
    expect(timerValueAsBool(raw)).toBe(false)
  })

  it('treats null/undefined as false', () => {
    expect(timerValueAsBool(null)).toBe(false)
    expect(timerValueAsBool(undefined)).toBe(false)
  })
})

describe('validateTimerValue', () => {
  it.each(['on', '1', '', 'beliebig'])('accepts %s verbatim for STRING and UNKNOWN', (raw) => {
    expect(validateTimerValue(raw, 'STRING')).toBeNull()
    expect(validateTimerValue(raw, 'UNKNOWN')).toBeNull()
  })

  it.each(['BOOLEAN', 'INTEGER', 'FLOAT', 'DATE', 'TIME', 'DATETIME'])('requires a value for %s', (dataType) => {
    expect(validateTimerValue('   ', dataType)).toBe('zst.switchValueErrorRequired')
  })

  it('treats null as empty', () => {
    expect(validateTimerValue(null, 'FLOAT')).toBe('zst.switchValueErrorRequired')
  })

  it.each(['1', '0', 'true', 'false', 'on', 'off', 'ein', 'aus'])('accepts %s for BOOLEAN', (raw) => {
    expect(validateTimerValue(raw, 'BOOLEAN')).toBeNull()
  })

  it('rejects a number for BOOLEAN', () => {
    expect(validateTimerValue('50', 'BOOLEAN')).toBe('zst.switchValueErrorBoolean')
  })

  it.each(['0', '1', '50', '-3', '50.0', 'on', 'aus'])('accepts %s for INTEGER', (raw) => {
    expect(validateTimerValue(raw, 'INTEGER')).toBeNull()
  })

  it('rejects a fractional number for INTEGER', () => {
    expect(validateTimerValue('50.5', 'INTEGER')).toBe('zst.switchValueErrorInteger')
  })

  it('rejects garbage for INTEGER', () => {
    expect(validateTimerValue('abc', 'INTEGER')).toBe('zst.switchValueErrorInteger')
  })

  it.each(['0', '1', '50.5', '-3.25', 'ein', 'off'])('accepts %s for FLOAT', (raw) => {
    expect(validateTimerValue(raw, 'FLOAT')).toBeNull()
  })

  it('rejects garbage for FLOAT', () => {
    expect(validateTimerValue('abc', 'FLOAT')).toBe('zst.switchValueErrorFloat')
  })

  it('accepts ISO temporal values', () => {
    expect(validateTimerValue('2026-12-24', 'DATE')).toBeNull()
    expect(validateTimerValue('08:00', 'TIME')).toBeNull()
    expect(validateTimerValue('08:00:00', 'TIME')).toBeNull()
    expect(validateTimerValue('2026-12-24T08:00:00', 'DATETIME')).toBeNull()
    expect(validateTimerValue('2026-12-24 08:00', 'DATETIME')).toBeNull()
  })

  it('rejects non-ISO temporal values', () => {
    expect(validateTimerValue('1', 'DATE')).toBe('zst.switchValueErrorDate')
    expect(validateTimerValue('morgens', 'TIME')).toBe('zst.switchValueErrorTime')
    expect(validateTimerValue('T08:00', 'DATETIME')).toBe('zst.switchValueErrorDatetime')
  })

  it('accepts a bare date as a datetime', () => {
    // `datetime.fromisoformat('2026-12-24')` yields midnight — rejecting it here
    // would block a value the API accepts.
    expect(validateTimerValue('2026-12-24', 'DATETIME')).toBeNull()
  })

  it('rejects a lowercase z suffix, which CPython does not parse', () => {
    expect(validateTimerValue('08:00:00z', 'TIME')).toBe('zst.switchValueErrorTime')
    expect(validateTimerValue('2026-12-24T08:00:00z', 'DATETIME')).toBe('zst.switchValueErrorDatetime')
  })

  // Shape alone is not enough — these all match the ISO pattern but are rejected
  // by date/time.fromisoformat(), so accepting them would green-light a 422.
  it.each(['2026-02-30', '2026-13-01', '2026-04-31', '2026-00-10', '2026-01-00', '2026-02-29', '1900-02-29'])(
    'rejects the impossible date %s',
    (raw) => {
      expect(validateTimerValue(raw, 'DATE')).toBe('zst.switchValueErrorDate')
    },
  )

  it.each(['2024-02-29', '2000-02-29', '2026-01-31', '2026-04-30'])('accepts the real date %s', (raw) => {
    expect(validateTimerValue(raw, 'DATE')).toBeNull()
  })

  it.each(['25:00', '08:60', '08:00:60'])('rejects the out-of-range time %s', (raw) => {
    expect(validateTimerValue(raw, 'TIME')).toBe('zst.switchValueErrorTime')
  })

  it.each(['08:00:00.5', '08:00:00+02:00', '08:00:00Z', '23:59:59'])('accepts the ISO time %s', (raw) => {
    expect(validateTimerValue(raw, 'TIME')).toBeNull()
  })

  it('validates both halves of a datetime', () => {
    expect(validateTimerValue('2026-12-24T08:00:00+02:00', 'DATETIME')).toBeNull()
    expect(validateTimerValue('2026-12-24t08:00', 'DATETIME')).toBeNull()
    expect(validateTimerValue('2026-02-30T08:00', 'DATETIME')).toBe('zst.switchValueErrorDatetime')
    expect(validateTimerValue('2026-12-24T25:00', 'DATETIME')).toBe('zst.switchValueErrorDatetime')
  })
})

// ---------------------------------------------------------------------------
// Cross-implementation parity fixture (issue #1008)
// ---------------------------------------------------------------------------

interface ParityFixture {
  values: string[]
  types: string[]
  backendValid: Record<string, boolean[]>
}

// Resolve the repo-root fixture by walking up from the Vitest cwd. It is read
// with node:fs rather than imported so it does not pass through Vite's module
// resolution, which does not serve files outside this project root.
function loadParityFixture(): ParityFixture {
  let dir = process.cwd()
  for (let i = 0; i < 5; i++) {
    const candidate = resolve(dir, 'tests/fixtures/timer_value_parity.json')
    if (existsSync(candidate)) return JSON.parse(readFileSync(candidate, 'utf8')) as ParityFixture
    dir = resolve(dir, '..')
  }
  throw new Error('tests/fixtures/timer_value_parity.json:not-found')
}

describe('validateTimerValue — parity with the backend', () => {
  const fixture = loadParityFixture()

  it('never accepts a value the backend rejects', () => {
    // A false-OK is the damaging direction: the Visu would let the user save and
    // the API would answer 422. The reverse is tolerated for exotic ISO spellings.
    const falseOk: string[][] = []
    for (const dataType of fixture.types) {
      fixture.values.forEach((value, i) => {
        const visuAccepts = validateTimerValue(value, dataType) === null
        if (visuAccepts && !fixture.backendValid[dataType][i]) falseOk.push([dataType, value])
      })
    }
    expect(falseOk).toEqual([])
  })

  it('accepts everything the backend accepts, apart from documented exotic ISO spellings', () => {
    const tolerated = new Set(['20261224', '080000', 'T08:00', '2026-12-24T08', '1_000'])
    const falseRejects: string[][] = []
    for (const dataType of fixture.types) {
      fixture.values.forEach((value, i) => {
        const visuAccepts = validateTimerValue(value, dataType) === null
        if (!visuAccepts && fixture.backendValid[dataType][i] && !tolerated.has(value)) {
          falseRejects.push([dataType, value])
        }
      })
    }
    expect(falseRejects).toEqual([])
  })

  it('exercises a meaningful number of cases', () => {
    expect(fixture.values.length * fixture.types.length).toBeGreaterThan(500)
  })
})
