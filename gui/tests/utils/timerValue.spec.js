import { existsSync, readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { describe, it, expect } from 'vitest'
import {
  timerValueAsBool,
  timerValueFitsNativeInput,
  timerValueHintKey,
  timerValueInputKind,
  timerValueStep,
  validateTimerValue,
} from '@/utils/timerValue'

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
  ])('maps %s → %s', (dataType, kind) => {
    expect(timerValueInputKind(dataType)).toBe(kind)
  })

  it('is case insensitive', () => {
    expect(timerValueInputKind('float')).toBe('float')
  })

  it('falls back to text for null/empty', () => {
    expect(timerValueInputKind(null)).toBe('text')
    expect(timerValueInputKind('')).toBe('text')
    expect(timerValueInputKind(undefined)).toBe('text')
  })
})

describe('timerValueHintKey', () => {
  it('builds a per-kind key', () => {
    expect(timerValueHintKey('FLOAT')).toBe('adapters.bindingForm.ztOutputValueHint_float')
    expect(timerValueHintKey('STRING')).toBe('adapters.bindingForm.ztOutputValueHint_text')
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

describe('validateTimerValue — STRING/UNKNOWN', () => {
  it.each(['on', '1', '', 'beliebig'])('accepts %s verbatim', (raw) => {
    expect(validateTimerValue(raw, 'STRING')).toBeNull()
    expect(validateTimerValue(raw, 'UNKNOWN')).toBeNull()
  })
})

describe('validateTimerValue — required', () => {
  it.each(['BOOLEAN', 'INTEGER', 'FLOAT', 'DATE', 'TIME', 'DATETIME'])('rejects empty for %s', (dataType) => {
    expect(validateTimerValue('   ', dataType)).toBe('adapters.bindingForm.ztOutputValueErrorRequired')
  })

  it('treats null as empty', () => {
    expect(validateTimerValue(null, 'FLOAT')).toBe('adapters.bindingForm.ztOutputValueErrorRequired')
  })
})

describe('validateTimerValue — BOOLEAN', () => {
  it.each(['1', '0', 'true', 'false', 'on', 'off', 'ein', 'aus', 'ja', 'nein'])('accepts %s', (raw) => {
    expect(validateTimerValue(raw, 'BOOLEAN')).toBeNull()
  })

  it('rejects a number', () => {
    expect(validateTimerValue('50', 'BOOLEAN')).toBe('adapters.bindingForm.ztOutputValueErrorBoolean')
  })
})

describe('validateTimerValue — INTEGER', () => {
  it.each(['0', '1', '50', '-3', '50.0', 'on', 'aus'])('accepts %s', (raw) => {
    expect(validateTimerValue(raw, 'INTEGER')).toBeNull()
  })

  it('rejects a fractional number', () => {
    expect(validateTimerValue('50.5', 'INTEGER')).toBe('adapters.bindingForm.ztOutputValueErrorInteger')
  })

  it('rejects garbage', () => {
    expect(validateTimerValue('abc', 'INTEGER')).toBe('adapters.bindingForm.ztOutputValueErrorInteger')
  })

  // Codex review on PR #1155: integrality is judged on the literal text, because
  // `Number('1.0000000000000001')` is exactly 1 and the backend (Decimal) rejects it.
  it.each(['1.0000000000000001', '1.55e1', '1e-3', '0.1', '.5'])(
    'rejects %s, which binary Number() would round to an integer',
    (raw) => {
      expect(validateTimerValue(raw, 'INTEGER')).toBe('adapters.bindingForm.ztOutputValueErrorInteger')
    },
  )

  it.each(['9007199254740993.0', '1000e-3', '1.5e1', '1.5e2', '5.', '007', '1e3'])(
    'accepts the integral decimal %s',
    (raw) => {
      expect(validateTimerValue(raw, 'INTEGER')).toBeNull()
    },
  )

  it('still accepts a fractional literal as a FLOAT', () => {
    expect(validateTimerValue('1.0000000000000001', 'FLOAT')).toBeNull()
  })
})

  it.each(['1e999', '9'.repeat(400), '-' + '9'.repeat(400), '0e' + '9'.repeat(20)])(
    'accepts %s for INTEGER, which a Python int carries exactly',
    (raw) => {
      expect(validateTimerValue(raw, 'INTEGER')).toBeNull()
    },
  )

describe('validateTimerValue — FLOAT', () => {
  it.each(['0', '1', '50.5', '-3.25', 'ein', 'off'])('accepts %s', (raw) => {
    expect(validateTimerValue(raw, 'FLOAT')).toBeNull()
  })

  it('rejects garbage', () => {
    expect(validateTimerValue('abc', 'FLOAT')).toBe('adapters.bindingForm.ztOutputValueErrorFloat')
  })

  // The backend rejects non-finite values (invalid JSON on the MQTT topic) and
  // does not accept JS-only literals such as 0x10 — the GUI must agree, or it
  // would green-light a value the API answers with 422.
  it.each(['inf', 'Infinity', 'nan', 'NaN', '1e999', '0x10', '0b101'])('rejects %s', (raw) => {
    expect(validateTimerValue(raw, 'FLOAT')).toBe('adapters.bindingForm.ztOutputValueErrorFloat')
  })

  // `1e999` is deliberately absent: it is exactly 10**999 and therefore a valid
  // INTEGER (Python ints are arbitrary-precision) — it is only out of range for a
  // *float*, so it stays in the FLOAT list above. Codex review round 3, PR #1155.
  it.each(['inf', 'Infinity', 'nan', '0x10'])('rejects %s for INTEGER too', (raw) => {
    expect(validateTimerValue(raw, 'INTEGER')).toBe('adapters.bindingForm.ztOutputValueErrorInteger')
  })

  it('accepts scientific notation and a leading dot', () => {
    expect(validateTimerValue('1e3', 'INTEGER')).toBeNull()
    expect(validateTimerValue('1.5e2', 'FLOAT')).toBeNull()
    expect(validateTimerValue('.5', 'FLOAT')).toBeNull()
  })
})

describe('validateTimerValue — DATE/TIME/DATETIME', () => {
  it('accepts ISO values', () => {
    expect(validateTimerValue('2026-12-24', 'DATE')).toBeNull()
    expect(validateTimerValue('08:00', 'TIME')).toBeNull()
    expect(validateTimerValue('08:00:00', 'TIME')).toBeNull()
    expect(validateTimerValue('2026-12-24T08:00:00', 'DATETIME')).toBeNull()
    expect(validateTimerValue('2026-12-24 08:00', 'DATETIME')).toBeNull()
  })

  it('rejects non-ISO values', () => {
    expect(validateTimerValue('1', 'DATE')).toBe('adapters.bindingForm.ztOutputValueErrorDate')
    expect(validateTimerValue('24.12.2026', 'DATE')).toBe('adapters.bindingForm.ztOutputValueErrorDate')
    expect(validateTimerValue('morgens', 'TIME')).toBe('adapters.bindingForm.ztOutputValueErrorTime')
    expect(validateTimerValue('T08:00', 'DATETIME')).toBe('adapters.bindingForm.ztOutputValueErrorDatetime')
  })

  it('accepts a bare date as a datetime', () => {
    // `datetime.fromisoformat('2026-12-24')` yields midnight — rejecting it here
    // would block a value the API accepts.
    expect(validateTimerValue('2026-12-24', 'DATETIME')).toBeNull()
  })

  // Codex review on PR #1155: `date.fromisoformat('0000-01-01')` raises because
  // Python's MINYEAR is 1, so accepting year zero here would green-light a 422.
  it('rejects year zero', () => {
    expect(validateTimerValue('0000-01-01', 'DATE')).toBe('adapters.bindingForm.ztOutputValueErrorDate')
    expect(validateTimerValue('0000-12-24', 'DATETIME')).toBe('adapters.bindingForm.ztOutputValueErrorDatetime')
    expect(validateTimerValue('0000-12-24T08:00', 'DATETIME')).toBe('adapters.bindingForm.ztOutputValueErrorDatetime')
  })

  it('accepts the years at the edge of the supported range', () => {
    expect(validateTimerValue('0001-01-01', 'DATE')).toBeNull()
    expect(validateTimerValue('9999-12-31', 'DATE')).toBeNull()
  })

  it('rejects a lowercase z suffix, which CPython does not parse', () => {
    expect(validateTimerValue('08:00:00z', 'TIME')).toBe('adapters.bindingForm.ztOutputValueErrorTime')
    expect(validateTimerValue('2026-12-24T08:00:00z', 'DATETIME')).toBe('adapters.bindingForm.ztOutputValueErrorDatetime')
  })

  // Shape alone is not enough — these all match the ISO pattern but are rejected
  // by date/time.fromisoformat(), so accepting them would green-light a 422.
  it.each(['2026-02-30', '2026-13-01', '2026-04-31', '2026-00-10', '2026-01-00', '2026-02-29', '1900-02-29'])(
    'rejects the impossible date %s',
    (raw) => {
      expect(validateTimerValue(raw, 'DATE')).toBe('adapters.bindingForm.ztOutputValueErrorDate')
    },
  )

  it.each(['2024-02-29', '2000-02-29', '2026-01-31', '2026-04-30'])('accepts the real date %s', (raw) => {
    expect(validateTimerValue(raw, 'DATE')).toBeNull()
  })

  it.each(['25:00', '08:60', '08:00:60'])('rejects the out-of-range time %s', (raw) => {
    expect(validateTimerValue(raw, 'TIME')).toBe('adapters.bindingForm.ztOutputValueErrorTime')
  })

  it.each(['08:00:00.5', '08:00:00+02:00', '08:00:00Z', '23:59:59'])('accepts the ISO time %s', (raw) => {
    expect(validateTimerValue(raw, 'TIME')).toBeNull()
  })

  it('validates both halves of a datetime', () => {
    expect(validateTimerValue('2026-12-24T08:00:00+02:00', 'DATETIME')).toBeNull()
    expect(validateTimerValue('2026-12-24t08:00', 'DATETIME')).toBeNull()
    expect(validateTimerValue('2026-02-30T08:00', 'DATETIME')).toBe('adapters.bindingForm.ztOutputValueErrorDatetime')
    expect(validateTimerValue('2026-12-24T25:00', 'DATETIME')).toBe('adapters.bindingForm.ztOutputValueErrorDatetime')
  })
})

// ---------------------------------------------------------------------------
// Cross-implementation parity fixture (issue #1008)
// ---------------------------------------------------------------------------

// Resolve the repo-root fixture by walking up from the Vitest cwd. It is read
// with node:fs rather than imported so it does not pass through Vite's module
// resolution, which does not serve files outside this project root.
function loadParityFixture() {
  let dir = process.cwd()
  for (let i = 0; i < 5; i++) {
    const candidate = resolve(dir, 'tests/fixtures/timer_value_parity.json')
    if (existsSync(candidate)) return JSON.parse(readFileSync(candidate, 'utf8'))
    dir = resolve(dir, '..')
  }
  throw new Error('tests/fixtures/timer_value_parity.json:not-found')
}

describe('validateTimerValue — parity with the backend', () => {
  const fixture = loadParityFixture()

  it('never accepts a value the backend rejects', () => {
    // A false-OK is the damaging direction: the GUI would let the user save and
    // the API would answer 422. The reverse is tolerated for exotic ISO spellings.
    const falseOk = []
    for (const dataType of fixture.types) {
      fixture.values.forEach((value, i) => {
        const guiAccepts = validateTimerValue(value, dataType) === null
        if (guiAccepts && !fixture.backendValid[dataType][i]) falseOk.push([dataType, value])
      })
    }
    expect(falseOk).toEqual([])
  })

  it('accepts everything the backend accepts, apart from documented exotic ISO spellings', () => {
    const tolerated = new Set(['20261224', '080000', 'T08:00', '2026-12-24T08', '1_000'])
    const falseRejects = []
    for (const dataType of fixture.types) {
      fixture.values.forEach((value, i) => {
        const guiAccepts = validateTimerValue(value, dataType) === null
        if (!guiAccepts && fixture.backendValid[dataType][i] && !tolerated.has(value)) {
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

describe('validateTimerValue — UTC offsets (Codex review, PR #1155)', () => {
  // `time.fromisoformat()` checks the offset *total*, not its two components:
  // `+00:60` is a valid one-hour offset, `+23:60` is exactly 24 h and is not.
  it.each(['08:00:00+23:59', '08:00:00+00:60', '08:00:00+2359', '08:00:00Z', '08:00:00-23:59'])(
    'accepts %s, which the API accepts too',
    (raw) => {
      expect(validateTimerValue(raw, 'TIME')).toBeNull()
    },
  )

  it.each(['08:00:00+24:00', '08:00:00+23:60', '08:00:00-24:00', '08:00:00+99:00'])(
    'rejects %s, which the API answers with 422',
    (raw) => {
      expect(validateTimerValue(raw, 'TIME')).toBe('adapters.bindingForm.ztOutputValueErrorTime')
    },
  )

  it('applies the same offset limit to the time half of a datetime', () => {
    expect(validateTimerValue('2026-12-24T08:00:00+23:59', 'DATETIME')).toBeNull()
    expect(validateTimerValue('2026-12-24T08:00:00+24:00', 'DATETIME')).toBe('adapters.bindingForm.ztOutputValueErrorDatetime')
  })
})

describe('timerValueFitsNativeInput', () => {
  it.each([
    ['08:00', 'TIME'],
    ['08:00:00', 'TIME'],
    ['', 'TIME'],
    ['2026-12-24T08:00', 'DATETIME'],
    ['2026-12-24T08:00:00', 'DATETIME'],
    ['2026-12-24', 'DATE'],
    ['50', 'FLOAT'],
    ['on', 'STRING'],
  ])('%s is representable in the native %s control', (raw, dataType) => {
    expect(timerValueFitsNativeInput(raw, dataType)).toBe(true)
  })

  // Values the validator still accepts for legacy configs but a native control
  // sanitizes to an empty field — they must fall back to a text input.
  it.each([
    ['08:00:00+02:00', 'TIME'],
    ['08:00:00Z', 'TIME'],
    ['08:00:00.5', 'TIME'],
    ['2026-12-24T08:00:00+02:00', 'DATETIME'],
    ['2026-12-24 08:00', 'DATETIME'],
    ['2026-12-24t08:00', 'DATETIME'],
    ['2026-12-24', 'DATETIME'],
  ])('%s is NOT representable in the native %s control', (raw, dataType) => {
    expect(timerValueFitsNativeInput(raw, dataType)).toBe(false)
  })

  // Codex review round 2 on PR #1155: `<input type="number">` accepts only HTML's
  // "valid floating-point number", so these stored literals — all of which the
  // validator and the API accept — would render as a blank field.
  it.each([
    ['+1', 'FLOAT'],
    ['+1', 'INTEGER'],
    ['.5', 'FLOAT'],
    ['5.', 'FLOAT'],
    ['on', 'FLOAT'],
    ['ein', 'INTEGER'],
    ['1_0', 'INTEGER'],
  ])('%s is NOT representable in the native %s control', (raw, dataType) => {
    expect(timerValueFitsNativeInput(raw, dataType)).toBe(false)
  })

  it.each([
    ['-3', 'INTEGER'],
    ['50', 'INTEGER'],
    ['007', 'INTEGER'],
    ['21.5', 'FLOAT'],
    ['1e3', 'FLOAT'],
    ['1.5e2', 'FLOAT'],
  ])('%s is representable in the native %s control', (raw, dataType) => {
    expect(timerValueFitsNativeInput(raw, dataType)).toBe(true)
  })

  it('treats null/undefined as empty, i.e. fitting', () => {
    expect(timerValueFitsNativeInput(null, 'TIME')).toBe(true)
    expect(timerValueFitsNativeInput(undefined, 'DATETIME')).toBe(true)
  })
})
