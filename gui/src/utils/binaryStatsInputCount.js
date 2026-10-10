// Input count of a binary_stats block, normalised exactly like the executor:
// only plain decimal strings are numbers (no hex/binary/octal or "1_0"),
// rounded half up, clamped to 2–30, and the declared default of 2 for an
// empty, non-numeric, non-finite or non-scalar (array/object) value.
const DECIMAL_NUMBER = /^[ \t\n\r\f\v]*[+-]?([0-9]+\.?[0-9]*|\.[0-9]+)([eE][+-]?[0-9]+)?[ \t\n\r\f\v]*$/

export function binaryStatsInputCount(value) {
  const numeric = typeof value === 'number' || typeof value === 'boolean' || (typeof value === 'string' && DECIMAL_NUMBER.test(value))
  const raw = numeric ? Number(value) : NaN
  const count = Number.isFinite(raw) ? Math.round(raw) : 2
  return Math.max(2, Math.min(30, count))
}
