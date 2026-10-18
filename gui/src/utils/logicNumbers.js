import { trimLikeFloat } from '@/utils/pythonWhitespace'

// The numeric rule the backend applies to Logic values, kept in one place so
// the block card and the configuration panel cannot drift apart — the same
// reason logicBooleans.js exists.
//
// Mirrors GraphExecutor._to_num: None falls back to the default, a real
// boolean is 1/0, and anything else goes through Python's float(), which
// raises on a collection and on a non-numeric string and therefore also falls
// back to the default.

// The decimal/scientific syntax Python's float() accepts, minus the special
// inf/nan spellings that make no sense as a configured value.
// Python also accepts digit separators — float('1_000') is 1000.0 — but only
// singly and only between digits: '_1', '1_', '1__0' and '1_.5' all raise.
// float() also accepts any Unicode decimal digit, mixed scripts included —
// float('١٢٣') is 123.0 — but only category Nd; No/Nl such as ½ or Ⅴ raise.
const DIGITS = String.raw`\p{Nd}(?:_?\p{Nd})*`
export const BACKEND_NUMBER_RE = new RegExp(
  String.raw`^[+-]?(?:${DIGITS}(?:\.(?:${DIGITS})?)?|\.${DIGITS})(?:[eE][+-]?${DIGITS})?$`,
  'u',
)

// Every Nd block is ten consecutive code points, but blocks can sit directly
// next to each other (the mathematical digits are five sets in a row), so the
// value is the offset within the whole contiguous run, modulo ten. Needed
// because a number input — and Number() — only understand ASCII digits.
const IS_ND = /\p{Nd}/u
function toAsciiDigit(char) {
  const cp = char.codePointAt(0)
  let start = cp
  while (start > 0 && IS_ND.test(String.fromCodePoint(start - 1))) start--
  return String((cp - start) % 10)
}

export function toBackendNumberText(value, fallback = '0') {
  if (value === null || value === undefined) return fallback
  // A JSON import may carry a native boolean; float() never sees it because
  // _to_num short-circuits on bool first — String(true) would yield "true"
  // and read as 0 here, the opposite of what runs.
  if (typeof value === 'boolean') return value ? '1' : '0'
  // Arrays and objects make float() raise TypeError. Stringifying first would
  // turn an imported [1] into "1", while the backend sends 0.0.
  if (typeof value === 'object') return fallback
  const text = String(value)
  // Deliberately not Number(): JavaScript also accepts 0x/0o/0b literals and
  // "Infinity", which float() rejects — it would coerce them to 0.0 while the
  // editor kept displaying the original spelling. Both checks are needed: the
  // regex rejects spellings float() cannot parse (0x10, Infinity), isFinite
  // rejects ones it parses into infinity (1e309).
  const trimmed = trimLikeFloat(text)
  if (!BACKEND_NUMBER_RE.test(trimmed)) return fallback
  // Number() does not understand separators, so strip them before the finite
  // check — and return the stripped spelling, because a number input cannot
  // display "1_000" and would render blank.
  const plain = trimmed.replace(/_/g, '').replace(/\p{Nd}/gu, toAsciiDigit)
  const coerced = Number(plain)
  if (!Number.isFinite(coerced)) return fallback
  // Always the coerced value's own spelling, never the imported one. Keeping a
  // "valid-looking" spelling was wrong twice over: float() accepts forms the
  // number input rejects ("2.", "+3", ".5"), and a spelling can denote a
  // different number than the double it parses to — "1e-400" underflows to a
  // zero and 9007199254740993 rounds to ...992, both of which the editor would
  // otherwise show as the original, misstating what the actuator receives.
  //
  // String() drops the sign of a negative zero, but float() keeps it — and
  // reaches one from "-0", "-0.0" and an underflowing "-1e-400" alike — so the
  // executor sends a signed zero that a downstream actuator can tell apart.
  return Object.is(coerced, -0) ? '-0' : String(coerced)
}
