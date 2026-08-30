/**
 * Zeitschaltuhr switching value — type-aware input kind, hint and validation.
 *
 * Mirrors the backend helper `coerce_text_value_for_type()` in `obs/models/types.py`
 * (issue #1008). The invariant is one-directional: this must never accept a value
 * the backend rejects, or the GUI would green-light a request the API answers with
 * 422. The reverse is tolerated — a few exotic ISO spellings CPython still parses
 * (`2026-12-24T08`, `20261224`) are reported as invalid here; no picker emits them.
 *
 * Pure utility: returns i18n **keys**, never translated strings — translate at the callsite.
 */

export const TIMER_TRUE_LITERALS = ['true', '1', 'on', 'ein', 'yes', 'ja']
export const TIMER_FALSE_LITERALS = ['false', '0', 'off', 'aus', 'no', 'nein']

const DECIMAL_RE = /^[+-]?(\d+\.?\d*|\.\d+)([eE][+-]?\d+)?$/
// Shape gates. Fractional seconds and a timezone suffix are tolerated because
// `fromisoformat()` accepts them and legacy configs may carry them. `Z` is
// uppercase-only and the time half of a datetime is optional, both matching
// CPython. The separator accepts `T`, `t` and a space, likewise per CPython.
const DATE_RE = /^(\d{4})-(\d{2})-(\d{2})$/
const TIME_RE = /^(\d{2}):(\d{2})(?::(\d{2})(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?$/
const DATETIME_RE = /^(\d{4}-\d{2}-\d{2})(?:[Tt ](.+))?$/
const DAYS_IN_MONTH = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]

const ERROR_KEYS = {
  boolean:  'adapters.bindingForm.ztOutputValueErrorBoolean',
  integer:  'adapters.bindingForm.ztOutputValueErrorInteger',
  float:    'adapters.bindingForm.ztOutputValueErrorFloat',
  date:     'adapters.bindingForm.ztOutputValueErrorDate',
  time:     'adapters.bindingForm.ztOutputValueErrorTime',
  datetime: 'adapters.bindingForm.ztOutputValueErrorDatetime',
}

const REQUIRED_KEY = 'adapters.bindingForm.ztOutputValueErrorRequired'

/**
 * Which input control fits a DataPoint `data_type`.
 * @returns {'boolean'|'integer'|'float'|'date'|'time'|'datetime'|'text'}
 */
export function timerValueInputKind(dataType) {
  switch (String(dataType || 'UNKNOWN').toUpperCase()) {
    case 'BOOLEAN':  return 'boolean'
    case 'INTEGER':  return 'integer'
    case 'FLOAT':    return 'float'
    case 'DATE':     return 'date'
    case 'TIME':     return 'time'
    case 'DATETIME': return 'datetime'
    default:         return 'text'
  }
}

/** i18n key of the hint text shown below the switching value input. */
export function timerValueHintKey(dataType) {
  return `adapters.bindingForm.ztOutputValueHint_${timerValueInputKind(dataType)}`
}

/** HTML `step` attribute for numeric inputs. */
export function timerValueStep(dataType) {
  return timerValueInputKind(dataType) === 'integer' ? '1' : 'any'
}

/** Interpret a stored switching value as a boolean (for the BOOLEAN toggle). */
export function timerValueAsBool(raw) {
  return TIMER_TRUE_LITERALS.includes(String(raw ?? '').trim().toLowerCase())
}

/**
 * Parse a decimal numeric literal, mapping boolean literals to 1/0.
 * Returns `null` when unparsable.
 *
 * Deliberately stricter than `Number()`: JS reads `0x10` as 16 and `Infinity`
 * as a number, while Python's `int()`/`float()` do not accept the former and
 * the backend rejects the latter (non-finite values serialize to invalid JSON).
 */
function parseNumber(trimmed, lowered) {
  if (DECIMAL_RE.test(trimmed)) {
    const n = Number(trimmed)
    if (Number.isFinite(n)) return n
  }
  if (TIMER_TRUE_LITERALS.includes(lowered)) return 1
  if (TIMER_FALSE_LITERALS.includes(lowered)) return 0
  return null
}

/**
 * Is a decimal literal free of a fractional part, judged on the text itself?
 *
 * `Number()` cannot answer this: it rounds `1.0000000000000001` to `1` and
 * `9007199254740993.0` to `...992`, so `Number.isInteger()` would call both
 * integral while the backend — which parses INTEGER with `Decimal` — rejects the
 * first as lossy. Splits the literal instead and checks that every mantissa digit
 * right of the exponent-shifted decimal point is a zero.
 *
 * Only called for text `DECIMAL_RE` has already matched, so the split always yields
 * a mantissa; a missing exponent or fractional part defaults to a no-op.
 */
function isIntegralDecimal(trimmed) {
  const [mantissa, exponent = '0'] = trimmed.replace(/^[+-]/, '').split(/[eE]/)
  const [intPart, fracPart = ''] = mantissa.split('.')
  const pointAt = intPart.length + Number(exponent)
  return !/[1-9]/.test((intPart + fracPart).slice(Math.max(pointAt, 0)))
}

/**
 * Calendar-correct date check — the shape regex alone would pass `2026-02-30`,
 * which `date.fromisoformat()` rejects, so the GUI would green-light a 422. Year `0000`
 * is out of range for the same reason: Python's `MINYEAR` is 1, and the four-digit
 * shape gate caps the other end at 9999.
 */
function isValidDate(value) {
  const m = DATE_RE.exec(value)
  if (!m) return false
  const year = Number(m[1])
  const month = Number(m[2])
  const day = Number(m[3])
  if (year < 1 || month < 1 || month > 12 || day < 1) return false
  const leap = (year % 4 === 0 && year % 100 !== 0) || year % 400 === 0
  return day <= (month === 2 && leap ? 29 : DAYS_IN_MONTH[month - 1])
}

/** Range-correct time check — the shape regex alone would pass `25:00` and `08:60`. */
function isValidTime(value) {
  const m = TIME_RE.exec(value)
  if (!m) return false
  if (Number(m[1]) > 23 || Number(m[2]) > 59) return false
  return m[3] === undefined || Number(m[3]) <= 59
}

function isValidDateTime(value) {
  const m = DATETIME_RE.exec(value)
  if (m === null || !isValidDate(m[1])) return false
  // A bare date is a valid datetime — `datetime.fromisoformat('2026-12-24')`
  // yields midnight, so rejecting it here would block a value the API accepts.
  return m[2] === undefined || isValidTime(m[2])
}

/**
 * Validate a switching value against the target DataPoint type.
 * @returns {string|null} i18n key of the error message, or `null` when valid.
 */
export function validateTimerValue(raw, dataType) {
  const kind = timerValueInputKind(dataType)
  // STRING / UNKNOWN take the value verbatim — even '', 'on' or '1'.
  if (kind === 'text') return null

  const trimmed = String(raw ?? '').trim()
  if (trimmed === '') return REQUIRED_KEY
  const lowered = trimmed.toLowerCase()

  switch (kind) {
    case 'boolean':
      return TIMER_TRUE_LITERALS.includes(lowered) || TIMER_FALSE_LITERALS.includes(lowered)
        ? null
        : ERROR_KEYS.boolean
    case 'integer': {
      const n = parseNumber(trimmed, lowered)
      if (n === null) return ERROR_KEYS.integer
      // A boolean literal already mapped to 1/0 and is integral by construction;
      // a decimal literal is judged on its text, see `isIntegralDecimal`.
      return DECIMAL_RE.test(trimmed) && !isIntegralDecimal(trimmed) ? ERROR_KEYS.integer : null
    }
    case 'float':
      return parseNumber(trimmed, lowered) === null ? ERROR_KEYS.float : null
    case 'date':
      return isValidDate(trimmed) ? null : ERROR_KEYS.date
    case 'time':
      return isValidTime(trimmed) ? null : ERROR_KEYS.time
    default:
      return isValidDateTime(trimmed) ? null : ERROR_KEYS.datetime
  }
}
