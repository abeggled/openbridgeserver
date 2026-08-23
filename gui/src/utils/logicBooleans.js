// The boolean rule the backend applies to Logic values, kept in one place so
// the block card and the configuration panel cannot drift apart.
//
// Mirrors GraphExecutor._to_bool: None is false; a string is false only for
// these spellings (case-insensitive, trimmed); anything else follows Python's
// bool(), where an empty collection is false and a non-empty one — even [0] —
// is true.
export const BACKEND_FALSE_WORDS = new Set(['0', 'false', 'no', 'off', ''])

export function isBackendFalse(value) {
  if (value === null || value === undefined) return true
  if (typeof value === 'string') return BACKEND_FALSE_WORDS.has(value.trim().toLowerCase())
  if (Array.isArray(value)) return value.length === 0
  if (typeof value === 'object') return Object.keys(value).length === 0
  return !value
}
