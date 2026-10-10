// Input count of a binary_stats block, normalised exactly like the executor:
// rounded half up, clamped to 2–30, and the declared default of 2 for an
// empty, non-numeric, non-finite or non-scalar (array/object) value.
export function binaryStatsInputCount(value) {
  const raw = ['number', 'string', 'boolean'].includes(typeof value) ? Number(value) : NaN
  const count = Number.isFinite(raw) && raw !== 0 ? Math.round(raw) : 2
  return Math.max(2, Math.min(30, count))
}
